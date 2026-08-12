"""Client control channel — the persistent TCP link to the mediation server.

Responsibilities (DESIGN.md §4.3):

* register → authenticate → command loop
* create/join/leave/list networks
* request peer endpoints for hole punching
* heartbeat every 30s
* deliver push events (PEER_ONLINE, PEER_OFFLINE, relay frames) to the daemon
* automatic reconnection with exponential backoff (1s → 60s)
"""

from __future__ import annotations

import asyncio
import binascii
import logging
import time
import uuid
from typing import Any, AsyncIterator, List, Optional, Tuple

from common import constants
from common.messages import (
    AuthResponse,
    CreateNetwork,
    ErrorMessage,
    Heartbeat,
    HeartbeatAck,
    JoinNetwork,
    LeaveNetwork,
    ListNetworks,
    Message,
    PeerEndpoints,
    RegisterMessage,
    RelayFrame,
    RelayGranted,
    RelayRequest,
    RequestPeerConn,
    deserialize,
    make_message,
    serialize,
)
from client.identity import sign_challenge

log = logging.getLogger("localnetwork.client.channel")


class ControlChannelError(RuntimeError):
    """Base error for control-channel failures."""


class AuthError(ControlChannelError):
    """Raised when registration/authentication fails."""


class ChannelClosedError(ControlChannelError):
    """Raised when operating on a closed channel."""


class ControlChannel:
    """Async TCP client for the mediation server control protocol."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = constants.SERVER_DEFAULT_PORT,
        heartbeat_interval: float = constants.HEARTBEAT_INTERVAL,
        reconnect_base_delay: float = constants.RECONNECT_BASE_DELAY,
        reconnect_max_delay: float = constants.RECONNECT_MAX_DELAY,
        client_id: Optional[str] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.heartbeat_interval = heartbeat_interval
        self.reconnect_base_delay = reconnect_base_delay
        self.reconnect_max_delay = reconnect_max_delay
        self.client_id = client_id or str(uuid.uuid4())

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._public_key_pem: Optional[str] = None
        self._private_key = None  # set by authenticate()
        self._authenticated = False
        self._connected = False
        self._events: asyncio.Queue = asyncio.Queue()
        self._responses: asyncio.Queue = asyncio.Queue()
        self._expected_type: Optional[str] = None
        # True while the background reader task owns the stream
        self._reader_owned = False
        self._event_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._backoff = reconnect_base_delay
        self._stopped = False
        self._network_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    async def connect(self) -> None:
        """Open the TCP connection (no auth yet)."""
        self._reader, self._writer = await asyncio.open_connection(
            self.host, self.port
        )
        self._connected = True
        self._backoff = self.reconnect_base_delay

    async def _reconnect_loop(self) -> None:
        """Background reconnection with exponential backoff."""
        while not self._stopped:
            await asyncio.sleep(self._backoff)
            try:
                log.info(
                    "reconnecting to %s:%s (backoff %.1fs)",
                    self.host,
                    self.port,
                    self._backoff,
                )
                await self.connect()
                if self._public_key_pem and self._private_key is not None:
                    await self.authenticate(self._private_key, self._public_key_pem)
                else:
                    await self.register(self.client_id, self._public_key_pem or "")
                self._backoff = self.reconnect_base_delay
            except Exception as exc:
                self._backoff = min(self._backoff * 2, self.reconnect_max_delay)
                log.warning("reconnect attempt failed: %r", exc)

    async def start_reconnect(self) -> None:
        """Begin the background reconnect loop (after a drop)."""
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    def stop_reconnect(self) -> None:
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            self._reconnect_task = None

    # ------------------------------------------------------------------
    # Message I/O
    # ------------------------------------------------------------------
    async def send_message(self, msg: Message) -> None:
        if self._writer is None:
            raise ChannelClosedError("channel is not connected")
        self._writer.write(serialize(msg))
        await self._writer.drain()

    async def recv_message(self, timeout: Optional[float] = None) -> Message:
        """Read exactly one message; optionally bounded by ``timeout``."""
        if self._reader is None:
            raise ChannelClosedError("channel is not connected")
        try:
            if timeout is not None:
                data = await asyncio.wait_for(self._reader.readexactly(4), timeout)
            else:
                data = await self._reader.readexactly(4)
        except asyncio.TimeoutError:
            raise TimeoutError(f"no message within {timeout}s") from None
        length = int.from_bytes(data, "big")
        if length > constants.MAX_MESSAGE_SIZE:
            raise ControlChannelError(f"server sent oversized message ({length} bytes)")
        body = await self._reader.readexactly(length)
        # deserialize expects the full length-prefixed buffer
        return deserialize(data + body)

    async def request(
        self, msg: Message, expected: str, timeout: Optional[float] = None
    ) -> Message:
        """Send a command and await a specific response type.

        Requests are serialized with a lock. When the background reader owns
        the stream (post-auth), responses arrive via ``_responses``; before
        that the method reads the stream directly.

        Raises:
            ControlChannelError: If the server replies with ERROR.
            TimeoutError: If no reply arrives within ``timeout``.
        """
        async with self._network_lock:
            # Set the expected type before sending so a fast reply is still
            # routed to _responses by the background reader.
            self._expected_type = expected
            try:
                await self.send_message(msg)
                while True:
                    if self._reader_owned:
                        reply = await asyncio.wait_for(
                            self._responses.get(), timeout
                        )
                        if reply is None:
                            raise ChannelClosedError("connection lost")
                    else:
                        reply = await self.recv_message(timeout=timeout)
                    if reply.type == constants.MSG_ERROR:
                        payload = reply.payload
                        raise ControlChannelError(
                            f"{payload.get('code', 'ERROR')}: "
                            f"{payload.get('message', '')}"
                        )
                    if reply.type == expected:
                        return reply
            finally:
                self._expected_type = None

    # ------------------------------------------------------------------
    # Registration & authentication
    # ------------------------------------------------------------------
    async def register(self, client_id: str, public_key_pem: str) -> None:
        """Send REGISTER and await the AUTH_CHALLENGE."""
        self.client_id = client_id
        self._public_key_pem = public_key_pem
        challenge = await self.request(
            make_message(
                RegisterMessage,
                client_id=client_id,
                public_key=public_key_pem,
                version="0.1.0",
            ),
            expected=constants.MSG_AUTH_CHALLENGE,
        )
        self._pending_challenge = challenge.payload.get("challenge", "")

    async def authenticate(self, private_key, public_key_pem: str) -> None:
        """Sign the pending challenge and complete authentication.

        After success, starts the event pump and heartbeat coroutines.
        """
        await self.register(self.client_id, public_key_pem)
        challenge_hex = self._pending_challenge
        challenge = binascii.unhexlify(challenge_hex)
        signature = sign_challenge(private_key, challenge)
        reply = await self.request(
            make_message(
                AuthResponse,
                signature=binascii.hexlify(signature).decode("ascii"),
                challenge=challenge_hex,
            ),
            expected=constants.MSG_AUTH_OK,
        )
        self._authenticated = True
        self._private_key = private_key
        log.info("authenticated with server as %s", self.client_id)
        self._start_background_tasks()

    # ------------------------------------------------------------------
    # Network operations
    # ------------------------------------------------------------------
    async def create_network(
        self, name: str, password: str, topology: str = constants.DEFAULT_TOPOLOGY
    ) -> str:
        reply = await self.request(
            make_message(CreateNetwork, name=name, password=password, topology=topology),
            expected=constants.MSG_NETWORK_CREATED,
        )
        return reply.payload.get("network_id", "")

    async def join_network(self, network_id: str, password: str) -> str:
        reply = await self.request(
            make_message(JoinNetwork, network_id=network_id, password=password),
            expected=constants.MSG_NETWORK_JOINED,
        )
        return reply.payload.get("virtual_ip") or ""

    async def leave_network(self, network_id: str) -> None:
        await self.request(
            make_message(LeaveNetwork, network_id=network_id),
            expected=constants.MSG_NETWORK_LEFT,
        )

    async def list_networks(self) -> List[dict]:
        reply = await self.request(
            make_message(ListNetworks), expected=constants.MSG_NETWORK_LIST
        )
        return list(reply.payload.get("networks", []))

    async def request_peer_endpoints(self, peer_id: str) -> List[Tuple[str, int]]:
        reply = await self.request(
            make_message(RequestPeerConn, peer_id=peer_id),
            expected=constants.MSG_PEER_ENDPOINTS,
        )
        endpoints = reply.payload.get("endpoints", [])
        return [(host, int(port)) for host, port in endpoints]

    async def request_relay(self, peer_id: str) -> None:
        """Request a relay path; expects RELAY_GRANTED or raises."""
        await self.request(
            make_message(RelayRequest, peer_id=peer_id),
            expected=constants.MSG_RELAY_GRANTED,
        )

    async def send_heartbeat(self) -> None:
        """Send one heartbeat and await the ack."""
        if not self._connected:
            return
        await self.request(
            make_message(Heartbeat, ts=time.time()),
            expected=constants.MSG_HEARTBEAT_ACK,
            timeout=5.0,
        )

    # ------------------------------------------------------------------
    # Event stream (push notifications)
    # ------------------------------------------------------------------
    async def listen_events(self) -> AsyncIterator[Message]:
        """Yield push events as they arrive (PEER_ONLINE, PEER_OFFLINE, …)."""
        while True:
            event = await self._events.get()
            yield event

    def _start_background_tasks(self) -> None:
        # Transfer stream ownership to the background reader *now* so no
        # request() call can race it with a direct readexactly() afterwards.
        self._reader_owned = True
        if self._event_task is None or self._event_task.done():
            self._event_task = asyncio.create_task(self._event_loop())
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _event_loop(self) -> None:
        """Background reader: the sole owner of the stream after auth.

        Classifies each message: a reply matching the pending request (or an
        ERROR) goes to ``_responses``; everything else becomes a push event.
        """
        self._reader_owned = True
        try:
            while self._connected and not self._stopped:
                msg = await self.recv_message()
                expected = self._expected_type
                if expected is not None and msg.type in (
                    expected,
                    constants.MSG_ERROR,
                ):
                    await self._responses.put(msg)
                else:
                    await self._events.put(msg)
        except (ConnectionError, asyncio.IncompleteReadError, ChannelClosedError):
            await self._on_connection_lost()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log.warning("event loop error: %r", exc)
            await self._on_connection_lost()
        finally:
            self._reader_owned = False

    async def _heartbeat_loop(self) -> None:
        while self._connected and not self._stopped:
            await asyncio.sleep(self.heartbeat_interval)
            try:
                await self.send_heartbeat()
            except Exception as exc:
                log.warning("heartbeat failed: %r", exc)
                await self._on_connection_lost()

    async def _on_connection_lost(self) -> None:
        """Handle a dropped connection and schedule reconnection."""
        if self._stopped or not self._connected:
            return
        self._connected = False
        self._authenticated = False
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        self._reader = None
        # Wake any waiter blocked on a response
        await self._responses.put(None)
        log.warning("control channel lost; reconnecting in %.1fs", self._backoff)
        await self.start_reconnect()

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------
    async def close(self) -> None:
        self._stopped = True
        self.stop_reconnect()
        for task in (self._event_task, self._heartbeat_task):
            if task is not None:
                task.cancel()
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (ConnectionError, OSError):
                pass
        self._writer = None
        self._reader = None
        self._connected = False
        self._authenticated = False
        self._reader_owned = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def authenticated(self) -> bool:
        return self._authenticated


__all__ = [
    "ControlChannel",
    "ControlChannelError",
    "AuthError",
    "ChannelClosedError",
]

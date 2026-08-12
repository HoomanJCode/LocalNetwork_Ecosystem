"""P2P tunnel manager: create, accept, and manage encrypted UDP tunnels.

Design (DESIGN.md §5):

Each tunnel = one UDP socket + AES-256-GCM session cipher (derived via ECDH
during hole punching).  The manager bridges between the async control channel
and the synchronous UDP data plane.

Lifecycle::

    CREATING → CONNECTED → CLOSED
                    ↓
                  STALE

"""
from __future__ import annotations

import asyncio
import logging
import socket
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Set, Tuple

from client.nat_traversal import NatTraversal, PunchState, UDP_RECV_BUFFER
from client.encryption import (
    CipherContext,
    derive_session_key_from_bytes,
    ecdh_public_bytes,
    generate_ecdh_keypair,
)
from common.constants import (
    FRAME_CLOSE,
    FRAME_DATA,
    FRAME_KEEPALIVE,
    FRAME_PUNCH,
    FRAME_PUNCH_ACK,
    KEEPALIVE_INTERVAL,
    TUNNEL_STALE_TIMEOUT,
)
from common.frame import (
    FrameError,
    make_close_frame,
    make_keepalive_frame,
    make_punch_ack_frame,
    pack_frame,
    unpack_frame,
)

log = logging.getLogger("localnetwork.client.tunnels")

# ── Tunnel state dataclass ──────────────────────────────────────────────


@dataclass
class PeerTunnel:
    """One encrypted P2P tunnel to a peer."""

    peer_id: str
    peer_ip: str
    state: PunchState = PunchState.IDLE
    socket: Optional[socket.socket] = None
    cipher: Optional[CipherContext] = None
    last_rx: float = 0.0
    tx_seq: int = 0
    rx_seq: int = 0
    remote_addr: Optional[Tuple[str, int]] = None
    fallback_relay: bool = False
    ecdh_private: Any = None  # X25519PrivateKey

    @property
    def alive(self) -> bool:
        return self.state in (PunchState.PUNCHING, PunchState.CONNECTED)


# ── Tunnel manager ──────────────────────────────────────────────────────


class TunnelManager:
    """Create, accept and manage P2P tunnels."""

    _control: Any = None  # ControlChannel (set via inject_control)

    def __init__(
        self,
        nat: Optional[NatTraversal] = None,
        stale_timeout: float = TUNNEL_STALE_TIMEOUT,
    ) -> None:
        self._nat = nat or NatTraversal()
        self._tunnels: Dict[str, PeerTunnel] = {}
        self._stale_timeout = stale_timeout
        self._running = False
        self._recv_queue: asyncio.Queue = asyncio.Queue()

    # ── lifecycle ──────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._running

    def inject_control(self, control: Any) -> None:
        """Give the manager access to the async ControlChannel."""
        self._control = control

    async def recv_loop(self) -> AsyncIterator[Tuple[str, bytes]]:
        """Background coroutine that polls tunnel sockets for incoming data.

        Yields:
            ``(peer_id, decrypted_ip_packet)`` for each valid data frame.
        """
        self._running = True
        loop = asyncio.get_running_loop()
        while self._running:
            for tunnel in list(self._tunnels.values()):
                if tunnel.state != PunchState.CONNECTED:
                    continue
                sock = tunnel.socket
                if sock is None:
                    continue
                try:
                    # Non-blocking poll: only read if data is ready
                    data, addr = await asyncio.wait_for(
                        loop.sock_recvfrom(sock, UDP_RECV_BUFFER), timeout=0.01
                    )
                except asyncio.TimeoutError:
                    continue
                except OSError:
                    continue
                except Exception:
                    continue

                frame = self._handle_frame(tunnel, data, addr)
                if frame is not None:
                    yield tunnel.peer_id, frame
            await asyncio.sleep(0.001)  # Yield to event loop
        self._running = False

    # ── active side ────────────────────────────────────────────────────

    async def create_tunnel(
        self,
        peer_id: str,
        peer_ip: str,
        peer_endpoints: List[Tuple[str, int]],
    ) -> PeerTunnel:
        """Create a tunnel to a peer via hole punching.

        1. Generate ECDH keypair, bind UDP socket.
        2. Punch peer, embedding our public key in PUNCH frames.
        3. On PUNCH_ACK: derive session key, create cipher.
        4. On failure: request RELAY from the mediationserver.
        """
        tunnel = PeerTunnel(peer_id=peer_id, peer_ip=peer_ip)
        # Remove any previous tunnel for the same peer.
        self._tunnels.pop(peer_id, None)

        ecdh = generate_ecdh_keypair()
        pubkey = ecdh_public_bytes(ecdh)
        tunnel.ecdh_private = ecdh

        sock = self._nat.bind_udp_socket()
        tunnel.socket = sock
        tunnel.state = PunchState.PUNCHING
        self._tunnels[peer_id] = tunnel

        loop = asyncio.get_running_loop()
        success: bool = False
        transport_addr: Optional[Tuple[str, int]] = None
        peer_pubkey: bytes = b""
        try:
            success, transport_addr, peer_pubkey = await loop.run_in_executor(
                None,
                self._nat.punch_peer,
                sock,
                peer_endpoints,
                pubkey,
            )
        except OSError as exc:
            log.debug("punch to %s failed: %r", peer_id, exc)

        if success and transport_addr is not None:
            # Derive session key from the peer's ECDH public key.
            if peer_pubkey:
                session_key = derive_session_key_from_bytes(ecdh, peer_pubkey)
                tunnel.cipher = CipherContext(session_key)
            tunnel.state = PunchState.CONNECTED
            tunnel.remote_addr = transport_addr
            tunnel.last_rx = time.monotonic()
            log.info("tunnel to %s (ip %s) established via P2P", peer_id, peer_ip)
            return tunnel

        # ── fallback: relay ────────────────────────────────────────────
        if self._control is not None and not success:
            log.info("punch to %s failed, falling back to relay", peer_id)
            await self._control.request_relay(peer_id)
            tunnel.fallback_relay = True
            tunnel.state = PunchState.CONNECTED
            tunnel.last_rx = time.monotonic()
            return tunnel

        tunnel.state = PunchState.FAILED
        return tunnel

    # ── passive side ───────────────────────────────────────────────────

    async def accept_tunnel(
        self,
        peer_id: str,
        peer_ip: str,
        sock: socket.socket,
        ecdh_private: Any,
    ) -> Optional[PeerTunnel]:
        """Passive side: accept a punch and establish the tunnel."""
        tunnel = PeerTunnel(
            peer_id=peer_id,
            peer_ip=peer_ip,
            socket=sock,
            state=PunchState.PUNCHING,
            ecdh_private=ecdh_private,
        )
        self._tunnels[peer_id] = tunnel

        loop = asyncio.get_running_loop()
        pubkey = ecdh_public_bytes(ecdh_private)
        result = await loop.run_in_executor(
            None,
            self._nat.accept_punch,
            sock,
            pubkey,
        )
        if result is None:
            tunnel.state = PunchState.FAILED
            return None

        peer_addr, peer_pubkey = result
        if peer_pubkey:
            session_key = derive_session_key_from_bytes(ecdh_private, peer_pubkey)
            tunnel.cipher = CipherContext(session_key)

        tunnel.state = PunchState.CONNECTED
        tunnel.remote_addr = peer_addr
        tunnel.last_rx = time.monotonic()
        log.info("accepted tunnel from %s (ip %s)", peer_id, peer_ip)
        return tunnel

    # ── data plane I/O ─────────────────────────────────────────────────

    def send_data(self, tunnel: PeerTunnel, raw_ip_packet: bytes) -> int:
        """Encrypt an IP packet and send it through the tunnel.

        Returns:
            Number of bytes sent (including framing), or 0 on failure.
        """
        if tunnel.state != PunchState.CONNECTED:
            return 0
        sock = tunnel.socket
        if sock is None or tunnel.remote_addr is None:
            return 0
        tunnel.tx_seq += 1
        if tunnel.cipher is not None:
            ciphertext, tag = tunnel.cipher.encrypt(raw_ip_packet)
            frame = pack_frame(FRAME_DATA, ciphertext, seq_num=tunnel.tx_seq, auth_tag=tag)
        else:
            # Fallback for relay mode — plaintext data frame
            frame = pack_frame(FRAME_DATA, raw_ip_packet, seq_num=tunnel.tx_seq)
        try:
            return sock.sendto(frame, tunnel.remote_addr)
        except OSError:
            return 0

    def send_keepalive(self, tunnel: PeerTunnel) -> bool:
        """Send a KEEPALIVE frame. Returns True on success."""
        if tunnel.state != PunchState.CONNECTED:
            return False
        sock = tunnel.socket
        if sock is None or tunnel.remote_addr is None:
            return False
        frame = make_keepalive_frame(seq_num=tunnel.tx_seq)
        try:
            sock.sendto(frame, tunnel.remote_addr)
            return True
        except OSError:
            return False

    def recv_data(self, tunnel: PeerTunnel) -> Optional[bytes]:
        """Non-blocking read from a tunnel. Returns decrypted payload or None."""
        sock = tunnel.socket
        if sock is None or tunnel.state != PunchState.CONNECTED:
            return None
        sock.settimeout(0.0)
        try:
            data, addr = sock.recvfrom(UDP_RECV_BUFFER)
        except (BlockingIOError, socket.timeout):
            return None
        except OSError:
            return None
        return self._handle_frame(tunnel, data, addr)

    # ── frame handling ─────────────────────────────────────────────────

    def _handle_frame(
        self, tunnel: PeerTunnel, data: bytes, addr: Tuple[str, int]
    ) -> Optional[bytes]:
        """Decode a raw frame from the wire and handle non-DATA types inline.

        Returns:
            Decrypted payload for DATA frames; ``None`` for control frames.
        """
        try:
            header, payload, tag = unpack_frame(data)
        except FrameError:
            return None

        if header.type == FRAME_DATA:
            if tunnel.cipher is not None:
                try:
                    plaintext = tunnel.cipher.decrypt(payload, tag)
                except Exception:
                    log.debug("decryption failed from %s", tunnel.peer_id)
                    return None
            else:
                plaintext = payload
            tunnel.rx_seq = header.seq_num
            tunnel.last_rx = time.monotonic()
            # Update remote address in case the NAT mapping changed
            if tunnel.remote_addr != addr:
                tunnel.remote_addr = addr
            return plaintext

        if header.type == FRAME_KEEPALIVE:
            tunnel.last_rx = time.monotonic()
            return None

        if header.type == FRAME_CLOSE:
            log.info("received CLOSE from %s", tunnel.peer_id)
            tunnel.state = PunchState.CLOSED
            return None

        if header.type == FRAME_PUNCH:
            # Late-arriving PUNCH — send PUNCH_ACK
            try:
                sock = tunnel.socket
                if sock is not None:
                    sock.sendto(make_punch_ack_frame(), addr)
            except OSError:
                pass
            return None

        if header.type == FRAME_PUNCH_ACK:
            tunnel.last_rx = time.monotonic()
            return None

        return None

    # ── tunnel management ──────────────────────────────────────────────

    def close_tunnel(self, tunnel: PeerTunnel) -> None:
        """Send CLOSE frame and release resources."""
        sock = tunnel.socket
        if sock is not None and tunnel.remote_addr is not None:
            try:
                sock.sendto(make_close_frame(seq_num=tunnel.tx_seq), tunnel.remote_addr)
            except OSError:
                pass
        peer_id = tunnel.peer_id
        if peer_id in self._tunnels:
            del self._tunnels[peer_id]
        tunnel.state = PunchState.CLOSED
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        tunnel.socket = None

    def get_tunnel(self, peer_id: str) -> Optional[PeerTunnel]:
        return self._tunnels.get(peer_id)

    def list_tunnels(self) -> List[PeerTunnel]:
        return list(self._tunnels.values())

    def prune_stale(self, timeout: Optional[float] = None) -> List[PeerTunnel]:
        """Close tunnels that have been silent for longer than *timeout*.

        Tunnels with ``last_rx == 0`` (never received data) are only pruned
        if they have been in CONNECTED state for longer than *timeout*.
        """
        timeout = timeout or self._stale_timeout
        now = time.monotonic()
        stale: List[PeerTunnel] = []
        for tunnel in list(self._tunnels.values()):
            if tunnel.state != PunchState.CONNECTED:
                continue
            if tunnel.last_rx > 0 and now - tunnel.last_rx > timeout:
                self.close_tunnel(tunnel)
                stale.append(tunnel)
        return stale

    # ── cleanup ────────────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Close all tunnels and stop the recv loop."""
        self._running = False
        for tunnel in list(self._tunnels.values()):
            self.close_tunnel(tunnel)
        self._tunnels.clear()


__all__ = ["PeerTunnel", "TunnelManager"]

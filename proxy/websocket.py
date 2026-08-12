"""WebSocket upgrade support for the reverse proxy.

Implements RFC 6455 WebSocket protocol:
- Upgrade handshake (HTTP 101 Switching Protocols)
- Bidirectional frame relay between client and upstream
- Ping/pong keep-alive forwarding
- Graceful close frame handling
- Configurable timeouts and max message size
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import struct
from typing import Any, Optional, Tuple

log = logging.getLogger("localnetwork.proxy.websocket")

# WebSocket opcodes
OP_CONTINUATION = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

# Magic GUID for the WebSocket accept key (RFC 6455 §4.2.2)
WS_MAGIC_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Defaults
DEFAULT_WS_TIMEOUT = 300.0  # 5 minutes idle timeout
DEFAULT_WS_MAX_MSG = 1024 * 1024  # 1 MiB
DEFAULT_WS_PING_INTERVAL = 30.0  # send pings every 30s


class WebSocketError(Exception):
    """WebSocket protocol error."""


def compute_accept_key(client_key: str) -> str:
    """Compute the Sec-WebSocket-Accept response key per RFC 6455 §4.2.2."""
    combined = (client_key + WS_MAGIC_GUID.decode()).encode()
    sha1 = hashlib.sha1(combined).digest()
    return base64.b64encode(sha1).decode()


def is_websocket_upgrade(headers: dict) -> bool:
    """Check if an HTTP request is a WebSocket upgrade request."""
    upgrade = headers.get("upgrade", "").lower()
    connection = headers.get("connection", "").lower()
    return upgrade == "websocket" and "upgrade" in connection


class WebSocketRelay:
    """Bidirectional WebSocket relay between client and upstream.

    Handles:
    - Upgrade handshake (101 Switching Protocols)
    - Bidirectional frame forwarding
    - Ping/pong keep-alive
    - Close frame propagation
    - Timeout enforcement
    """

    def __init__(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
        *,
        timeout: float = DEFAULT_WS_TIMEOUT,
        max_message_size: int = DEFAULT_WS_MAX_MSG,
        ping_interval: float = DEFAULT_WS_PING_INTERVAL,
    ) -> None:
        self.client_reader = client_reader
        self.client_writer = client_writer
        self.upstream_reader = upstream_reader
        self.upstream_writer = upstream_writer
        self.timeout = timeout
        self.max_message_size = max_message_size
        self.ping_interval = ping_interval
        self._closing = False

    async def perform_upgrade(
        self, client_key: str, client_protocol: Optional[str] = None
    ) -> None:
        """Send the 101 Switching Protocols response to the client."""
        accept_key = compute_accept_key(client_key)
        response = (
            f"HTTP/1.1 101 Switching Protocols\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept_key}\r\n"
        )
        if client_protocol:
            response += f"Sec-WebSocket-Protocol: {client_protocol}\r\n"
        response += "\r\n"
        self.client_writer.write(response.encode())
        await self.client_writer.drain()

    async def relay(self) -> None:
        """Run the bidirectional relay until either side closes."""
        done = asyncio.Event()

        async def _relay_one_way(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            label: str,
        ) -> None:
            """Relay frames from reader → writer."""
            try:
                while not done.is_set():
                    frame = await self._read_frame(reader, label)
                    if frame is None:
                        done.set()
                        break

                    opcode, payload = frame
                    if opcode == OP_CLOSE:
                        # Forward the close frame, then shut down
                        await self._send_frame(writer, OP_CLOSE, payload)
                        done.set()
                        break
                    elif opcode == OP_PING:
                        # Respond with pong (keep-alive)
                        await self._send_frame(writer, OP_PONG, payload)
                    elif opcode == OP_PONG:
                        # Forward pong silently
                        await self._send_frame(writer, OP_PONG, payload)
                    else:
                        # Forward data/text/continuation frames
                        await self._send_frame(writer, opcode, payload)
            except (ConnectionError, asyncio.IncompleteReadError, OSError):
                done.set()
            except WebSocketError:
                done.set()

        async def _ping_loop() -> None:
            """Periodically send ping frames to keep the upstream alive."""
            try:
                while not done.is_set():
                    await asyncio.sleep(self.ping_interval)
                    if done.is_set():
                        break
                    await self._send_frame(self.upstream_writer, OP_PING, b"")
            except (ConnectionError, OSError):
                done.set()

        # Run both directions + ping loop concurrently
        tasks = [
            asyncio.create_task(_relay_one_way(self.client_reader, self.upstream_writer, "client→upstream")),
            asyncio.create_task(_relay_one_way(self.upstream_reader, self.client_writer, "upstream→client")),
            asyncio.create_task(_ping_loop()),
        ]

        # Wait for first task to set the done event
        await done.wait()

        # Cancel remaining tasks
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _read_frame(
        self, reader: asyncio.StreamReader, label: str
    ) -> Optional[Tuple[int, bytes]]:
        """Read one WebSocket frame. Returns (opcode, payload) or None on EOF."""
        try:
            header = await asyncio.wait_for(
                reader.readexactly(2), timeout=self.timeout
            )
        except asyncio.TimeoutError:
            log.debug("websocket read timeout on %s", label)
            raise WebSocketError("read timeout")

        if not header:
            return None

        byte0, byte1 = header[0], header[1]
        opcode = byte0 & 0x0F
        masked = (byte1 & 0x80) != 0
        payload_len = byte1 & 0x7F

        # Extended payload length
        if payload_len == 126:
            ext = await asyncio.wait_for(reader.readexactly(2), timeout=self.timeout)
            payload_len = struct.unpack("!H", ext)[0]
        elif payload_len == 127:
            ext = await asyncio.wait_for(reader.readexactly(8), timeout=self.timeout)
            payload_len = struct.unpack("!Q", ext)[0]

        # Sanity check
        if payload_len > self.max_message_size:
            log.warning("websocket frame too large (%d bytes) on %s", payload_len, label)
            raise WebSocketError("frame too large")

        # Masking key (client frames must be masked per RFC 6455)
        mask_key = b""
        if masked:
            mask_key = await asyncio.wait_for(reader.readexactly(4), timeout=self.timeout)

        # Payload
        payload = b""
        if payload_len > 0:
            payload = await asyncio.wait_for(
                reader.readexactly(payload_len), timeout=self.timeout
            )

        # Unmask if needed
        if masked and mask_key:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        return opcode, payload

    @staticmethod
    async def _send_frame(
        writer: asyncio.StreamWriter, opcode: int, payload: bytes
    ) -> None:
        """Send a WebSocket frame (unmasked — server→client direction)."""
        frame = bytearray()
        frame.append(0x80 | (opcode & 0x0F))  # FIN + opcode

        plen = len(payload)
        if plen < 126:
            frame.append(plen)
        elif plen < 65536:
            frame.append(126)
            frame.extend(struct.pack("!H", plen))
        else:
            frame.append(127)
            frame.extend(struct.pack("!Q", plen))

        if payload:
            frame.extend(payload)

        writer.write(bytes(frame))
        await writer.drain()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Send a close frame to both sides."""
        if self._closing:
            return
        self._closing = True

        payload = struct.pack("!H", code) + reason.encode("utf-8", errors="replace")
        try:
            await self._send_frame(self.client_writer, OP_CLOSE, payload)
        except (ConnectionError, OSError):
            pass
        try:
            await self._send_frame(self.upstream_writer, OP_CLOSE, payload)
        except (ConnectionError, OSError):
            pass


async def handle_websocket_upgrade(
    connection: Any,  # Connection instance
    request: Any,  # HttpRequest instance
    location: Any,  # LocationBlock instance
    upstream_data: Tuple[asyncio.StreamReader, asyncio.StreamWriter],
) -> None:
    """Handle a WebSocket upgrade request end-to-end.

    1. Validate upgrade headers
    2. Connect to upstream and forward the upgrade request
    3. Read upstream's 101 response
    4. Relay the 101 to the client
    5. Enter bidirectional frame relay

    Args:
        connection: The Connection instance (for logging/status hooks).
        request: The parsed HTTP upgrade request.
        location: The matched LocationBlock.
        upstream_data: (reader, writer) connected to the upstream.
    """
    upstream_reader, upstream_writer = upstream_data

    # Validate client upgrade request
    client_key = request.headers.get("sec-websocket-key", "")
    if not client_key:
        await connection._send_error(400, "Bad Request")
        return

    client_protocol = request.headers.get("sec-websocket-protocol")

    # Forward the upgrade request to the upstream
    req_headers = dict(request.headers)
    # Keep upgrade headers intact (don't strip)
    req_headers["host"] = request.headers.get("host", "")

    req_line = f"{request.method} {request.path} HTTP/1.1\r\n"
    header_block = "".join(f"{k}: {v}\r\n" for k, v in req_headers.items())
    header_block += "\r\n"

    upstream_writer.write(req_line.encode() + header_block.encode())
    await upstream_writer.drain()

    # Read upstream's 101 response
    try:
        response_data = await asyncio.wait_for(
            upstream_reader.readuntil(b"\r\n\r\n"), timeout=10.0
        )
    except (asyncio.TimeoutError, asyncio.IncompleteReadError):
        await connection._send_error(502, "Bad Gateway")
        return

    response_text = response_data.decode("utf-8", errors="replace")
    lines = response_text.split("\r\n")

    # Parse status line
    if not lines:
        await connection._send_error(502, "Bad Gateway")
        return

    status_parts = lines[0].split(" ", 2)
    if len(status_parts) < 2:
        await connection._send_error(502, "Bad Gateway")
        return

    status_code = int(status_parts[1])
    if status_code != 101:
        # Upstream didn't accept the upgrade — relay the response as-is
        body = response_data
        await connection.writer.write(body)
        await connection.writer.drain()
        return

    # Parse response headers for Sec-WebSocket-Accept
    upstream_accept = ""
    upstream_protocol = ""
    response_headers: dict = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        key = k.strip().lower()
        val = v.strip()
        response_headers[key] = val
        if key == "sec-websocket-accept":
            upstream_accept = val
        elif key == "sec-websocket-protocol":
            upstream_protocol = val

    # Compute our own accept key
    accept_key = compute_accept_key(client_key)

    # If upstream returned a different accept key, use theirs
    # (some websocket servers transform the key)
    if upstream_accept and upstream_accept != accept_key:
        accept_key = upstream_accept

    # Get websocket config from location
    ws_timeout = getattr(location, "ws_timeout", DEFAULT_WS_TIMEOUT)
    ws_max_msg = getattr(location, "ws_max_message_size", DEFAULT_WS_MAX_MSG)
    ws_ping = getattr(location, "ws_ping_interval", DEFAULT_WS_PING_INTERVAL)

    relay = WebSocketRelay(
        connection.reader,
        connection.writer,
        upstream_reader,
        upstream_writer,
        timeout=ws_timeout,
        max_message_size=ws_max_msg,
        ping_interval=ws_ping,
    )

    # Send 101 to client
    await relay.perform_upgrade(accept_key, upstream_protocol or client_protocol)

    log.info(
        "websocket upgrade: %s → %s (timeout=%ds, max_msg=%d)",
        request.path,
        request.host,
        int(ws_timeout),
        ws_max_msg,
    )

    if connection.status_collector:
        connection.status_collector.increment_handled()

    # Enter bidirectional relay (blocks until close)
    await relay.relay()

    # Cleanup
    await relay.close()
    try:
        upstream_writer.close()
    except OSError:
        pass


__all__ = [
    "WebSocketRelay",
    "WebSocketError",
    "compute_accept_key",
    "is_websocket_upgrade",
    "handle_websocket_upgrade",
    "WS_MAGIC_GUID",
    "OP_TEXT",
    "OP_BINARY",
    "OP_CLOSE",
    "OP_PING",
    "OP_PONG",
]

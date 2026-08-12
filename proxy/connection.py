"""HTTP connection state machine for the reverse proxy (DESIGN.md Phase 18).

State machine: READ_REQUEST → MATCH_ROUTE → CONNECT_UPSTREAM → FORWARD → RESPOND

Features:
* HTTP/1.1 request parser (hand-rolled, no external deps)
* Header management (X-Real-IP, X-Forwarded-For, hop-by-hop stripping)
* Chunked transfer encoding support
* Keep-alive persistent connections
* Request timeout handling
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

log = logging.getLogger("localnetwork.proxy.connection")

# Maximum sizes
MAX_HEADER_SIZE = 16384   # 16 KB
MAX_BODY_MEMORY = 65536   # 64 KB before spilling to temp file
DEFAULT_TIMEOUT = 30.0    # seconds


class ConnState(Enum):
    READ_REQUEST = auto()
    MATCH_ROUTE = auto()
    CONNECT_UPSTREAM = auto()
    FORWARD = auto()
    RESPOND = auto()
    CLOSED = auto()


@dataclass
class HttpRequest:
    """Parsed HTTP request."""

    method: str = "GET"
    path: str = "/"
    version: str = "HTTP/1.1"
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    client_ip: str = "127.0.0.1"

    @property
    def host(self) -> str:
        return self.headers.get("host", "")

    @property
    def content_length(self) -> int:
        try:
            return int(self.headers.get("content-length", "0"))
        except ValueError:
            return 0

    @property
    def is_chunked(self) -> bool:
        return self.headers.get("transfer-encoding", "").lower() == "chunked"

    @property
    def is_keepalive(self) -> bool:
        conn = self.headers.get("connection", "").lower()
        if self.version == "HTTP/1.0":
            return conn == "keep-alive"
        return conn != "close"


@dataclass
class HttpResponse:
    """HTTP response to send back to the client."""

    status: int = 200
    reason: str = "OK"
    headers: Dict[str, str] = field(default_factory=dict)
    body: bytes = b""


# Hop-by-hop headers (must be stripped when forwarding)
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailer",
    "transfer-encoding", "upgrade",
}


class HttpParser:
    """Hand-rolled HTTP/1.1 request parser."""

    def __init__(self) -> None:
        self._buffer = b""

    def feed(self, data: bytes) -> Optional[HttpRequest]:
        """Feed raw bytes; returns a parsed request or None if incomplete."""
        self._buffer += data
        return self._try_parse()

    def _try_parse(self) -> Optional[HttpRequest]:
        # Find end of headers
        idx = self._buffer.find(b"\r\n\r\n")
        if idx == -1:
            if len(self._buffer) > MAX_HEADER_SIZE:
                raise ValueError("request headers too large")
            return None

        header_block = self._buffer[:idx].decode("utf-8", errors="replace")
        lines = header_block.split("\r\n")
        if not lines:
            return None

        # Parse request line
        parts = lines[0].split(" ", 2)
        if len(parts) < 2:
            raise ValueError(f"invalid request line: {lines[0]}")

        method = parts[0].upper()
        path = parts[1]
        version = parts[2] if len(parts) > 2 else "HTTP/1.1"

        # Parse headers
        headers: Dict[str, str] = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()

        request = HttpRequest(method=method, path=path, version=version, headers=headers)

        # Parse body
        body_start = idx + 4
        if request.is_chunked:
            body, consumed = self._parse_chunked(self._buffer[body_start:])
            if body is None:
                return None  # incomplete
            request.body = body
            self._buffer = self._buffer[body_start + consumed:]
        elif request.content_length > 0:
            remaining = len(self._buffer) - body_start
            if remaining < request.content_length:
                return None  # incomplete
            request.body = self._buffer[body_start:body_start + request.content_length]
            self._buffer = self._buffer[body_start + request.content_length:]
        else:
            self._buffer = b""

        return request

    def _parse_chunked(self, data: bytes) -> Tuple[Optional[bytes], int]:
        """Parse chunked transfer encoding. Returns (body, bytes_consumed)."""
        result = bytearray()
        pos = 0
        while pos < len(data):
            # Find chunk size line
            eol = data.find(b"\r\n", pos)
            if eol == -1:
                return None, 0
            try:
                chunk_size = int(data[pos:eol], 16)
            except ValueError:
                raise ValueError("invalid chunk size")
            pos = eol + 2

            if chunk_size == 0:
                # Last chunk — skip trailers
                pos = data.find(b"\r\n\r\n", pos)
                if pos == -1:
                    return None, 0
                pos += 4
                return bytes(result), pos

            if pos + chunk_size + 2 > len(data):
                return None, 0

            result.extend(data[pos:pos + chunk_size])
            pos += chunk_size + 2  # skip \r\n after chunk

        return None, 0  # more chunks needed


class Connection:
    """Per-client HTTP connection coroutine."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        config: Any,
        upstreams: Dict[str, Any],
        balancers: Dict[str, Any],
        health_monitor: Any,
        compressor: Any = None,
        access_logger: Any = None,
        status_collector: Any = None,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.config = config
        self.upstreams = upstreams
        self.balancers = balancers
        self.health_monitor = health_monitor
        self.compressor = compressor
        self.access_logger = access_logger
        self.status_collector = status_collector
        self.state = ConnState.READ_REQUEST
        self._parser = HttpParser()
        self._request_count = 0
        self._closed = False

        peername = writer.get_extra_info("peername")
        self.client_ip = peername[0] if peername else "127.0.0.1"

    async def handle(self) -> None:
        """Main connection loop."""
        if self.status_collector:
            self.status_collector.increment_accepted()

        try:
            while not self._closed:
                self.state = ConnState.READ_REQUEST
                request = await self._read_request()
                if request is None:
                    break  # EOF

                self.state = ConnState.MATCH_ROUTE
                location = self._match_route(request.path)
                if location is None:
                    await self._send_error(404, "Not Found")
                    if not request.is_keepalive:
                        break
                    continue

                # Static file serving (root: directive)
                root = getattr(location, "root", "")
                if root and request.method == "GET":
                    response = await self._serve_static_file(request.path, root, location)
                    self.state = ConnState.RESPOND
                    await self._send_response(response)
                    if self.access_logger:
                        await self.access_logger.log(
                            method=request.method, path=request.path,
                            status=response.status, body_size=len(response.body),
                            duration_ms=0, client_ip=self.client_ip,
                        )
                    if self.status_collector:
                        self.status_collector.increment_handled()
                    if not request.is_keepalive:
                        break
                    continue

                # WebSocket upgrade detection
                from proxy.websocket import is_websocket_upgrade, handle_websocket_upgrade

                ws_enabled = getattr(location, "ws_enabled", True)
                if ws_enabled and is_websocket_upgrade(request.headers):
                    self.state = ConnState.CONNECT_UPSTREAM
                    upstream_data = await self._connect_upstream(request, location)
                    if upstream_data is None:
                        if not request.is_keepalive:
                            break
                        continue

                    # Delegate to WebSocket handler (blocks until close)
                    await handle_websocket_upgrade(
                        self, request, location,
                        (upstream_data[0], upstream_data[1]),
                    )
                    # Close the upstream writer
                    try:
                        upstream_data[1].close()
                    except OSError:
                        pass
                    break  # WebSocket connections are not keep-alive

                self.state = ConnState.CONNECT_UPSTREAM
                upstream_data = await self._connect_upstream(request, location)
                if upstream_data is None:
                    if not request.is_keepalive:
                        break
                    continue

                self.state = ConnState.FORWARD
                response = await self._forward_request(request, upstream_data, location)

                self.state = ConnState.RESPOND
                await self._send_response(response)

                if self.access_logger:
                    await self.access_logger.log(
                        method=request.method,
                        path=request.path,
                        status=response.status,
                        body_size=len(response.body),
                        duration_ms=0,
                        client_ip=self.client_ip,
                    )

                if self.status_collector:
                    self.status_collector.increment_handled()

                if not request.is_keepalive:
                    break
        except (ConnectionError, asyncio.IncompleteReadError, OSError):
            pass
        except Exception as exc:
            log.debug("connection error: %r", exc)
        finally:
            await self._close()

    async def _read_request(self) -> Optional[HttpRequest]:
        """Read and parse one HTTP request."""
        try:
            while True:
                data = await asyncio.wait_for(
                    self.reader.read(4096), timeout=DEFAULT_TIMEOUT
                )
                if not data:
                    return None
                request = self._parser.feed(data)
                if request is not None:
                    request.client_ip = self.client_ip
                    return request
        except asyncio.TimeoutError:
            return None

    def _match_route(self, path: str) -> Optional[Any]:
        """Match a request path against configured locations."""
        locations = getattr(self.config, "locations", [])
        best_match = None
        best_len = -1
        for loc in locations:
            loc_path = getattr(loc, "path", "/")
            if path.startswith(loc_path) and len(loc_path) > best_len:
                best_match = loc
                best_len = len(loc_path)
        return best_match

    async def _connect_upstream(
        self, request: HttpRequest, location: Any
    ) -> Optional[Tuple[asyncio.StreamReader, asyncio.StreamWriter, Any]]:
        """Connect to an upstream backend server."""
        upstream_name = getattr(location, "upstream", "")
        upstream = self.upstreams.get(upstream_name)
        if upstream is None:
            await self._send_error(502, "Bad Gateway")
            return None

        balancer = self.balancers.get(upstream_name)
        if balancer is None:
            await self._send_error(502, "Bad Gateway")
            return None

        servers = getattr(upstream, "servers", [])
        server = balancer.select(servers, client_ip=request.client_ip)
        if server is None:
            await self._send_error(502, "Bad Gateway")
            return None

        host = getattr(server, "host", "")
        port = getattr(server, "port", 80)

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=5.0,
            )
            if self.health_monitor:
                self.health_monitor.record_success(host, port)
            if isinstance(balancer, object) and hasattr(balancer, "increment"):
                balancer.increment(server)
            return reader, writer, server
        except (OSError, asyncio.TimeoutError) as exc:
            if self.health_monitor:
                self.health_monitor.record_failure(host, port)
            log.debug("upstream connect to %s:%d failed: %r", host, port, exc)
            await self._send_error(502, "Bad Gateway")
            return None

    async def _forward_request(
        self,
        request: HttpRequest,
        upstream_data: Tuple[asyncio.StreamReader, asyncio.StreamWriter, Any],
        location: Any,
    ) -> HttpResponse:
        """Forward the request to upstream and read the response."""
        upstream_reader, upstream_writer, server = upstream_data
        try:
            # Prepare upstream headers
            headers = dict(request.headers)
            headers["host"] = f"{getattr(server, 'host', '')}:{getattr(server, 'port', 80)}"
            headers["x-real-ip"] = request.client_ip

            existing = headers.get("x-forwarded-for", "")
            headers["x-forwarded-for"] = (
                f"{existing}, {request.client_ip}" if existing else request.client_ip
            )

            # Strip hop-by-hop headers
            for h in HOP_BY_HOP:
                headers.pop(h, None)

            # Build upstream request
            req_line = f"{request.method} {request.path} HTTP/1.1\r\n"
            header_block = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
            header_block += "\r\n"

            upstream_writer.write(req_line.encode() + header_block.encode())
            if request.body:
                upstream_writer.write(request.body)
            await upstream_writer.drain()

            # Read upstream response
            response = await self._read_upstream_response(upstream_reader)

            # Apply compression if configured
            if self.compressor and getattr(location, "compress", True):
                ct = response.headers.get("content-type", "")
                ae = request.headers.get("accept-encoding", "")
                if self.compressor.should_compress(ct, ae):
                    compressed = self.compressor.compress(response.body)
                    if len(compressed) < len(response.body):
                        response.body = compressed
                        response.headers["content-encoding"] = "gzip"
                        response.headers["content-length"] = str(len(compressed))

            return response
        finally:
            if isinstance(self.balancers.get(getattr(location, "upstream", "")), object):
                balancer = self.balancers.get(getattr(location, "upstream", ""))
                if hasattr(balancer, "decrement"):
                    balancer.decrement(server)
            try:
                upstream_writer.close()
            except OSError:
                pass

    async def _read_upstream_response(
        self, reader: asyncio.StreamReader
    ) -> HttpResponse:
        """Read HTTP response from upstream."""
        data = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30.0)
        header_block = data.decode("utf-8", errors="replace")
        lines = header_block.split("\r\n")

        # Status line
        parts = lines[0].split(" ", 2)
        status = int(parts[1]) if len(parts) > 1 else 200
        reason = parts[2] if len(parts) > 2 else "OK"

        # Headers
        headers: Dict[str, str] = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()

        # Body
        body = b""
        cl = int(headers.get("content-length", "0"))
        if cl > 0:
            body = await asyncio.wait_for(reader.readexactly(cl), timeout=30.0)
        elif headers.get("transfer-encoding", "").lower() == "chunked":
            body = await self._read_chunked_body(reader)

        return HttpResponse(status=status, reason=reason, headers=headers, body=body)

    async def _read_chunked_body(self, reader: asyncio.StreamReader) -> bytes:
        """Read a chunked response body."""
        result = bytearray()
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=30.0)
            try:
                chunk_size = int(line.strip(), 16)
            except ValueError:
                break
            if chunk_size == 0:
                await reader.readline()  # trailing \r\n
                break
            chunk = await asyncio.wait_for(reader.readexactly(chunk_size + 2), timeout=30.0)
            result.extend(chunk[:-2])  # strip trailing \r\n
        return bytes(result)

    async def _send_response(self, response: HttpResponse) -> None:
        """Send the HTTP response back to the client."""
        status_line = f"HTTP/1.1 {response.status} {response.reason}\r\n"

        # Set defaults
        if "content-length" not in response.headers:
            response.headers["content-length"] = str(len(response.body))
        if "server" not in response.headers:
            response.headers["server"] = "LocalNetworkProxy/0.1.0"
        if "connection" not in response.headers:
            response.headers["connection"] = "keep-alive"

        header_block = "".join(f"{k}: {v}\r\n" for k, v in response.headers.items())
        header_block += "\r\n"

        self.writer.write(status_line.encode() + header_block.encode())
        self.writer.write(response.body)
        await self.writer.drain()

    async def _serve_static_file(
        self, path: str, root: str, location: Any
    ) -> HttpResponse:
        """Serve a static file from the root directory.

        Resolves the request path relative to the root, prevents directory
        traversal, serves index.html for directory requests, and sets
        appropriate Content-Type based on file extension.
        """
        import mimetypes
        import os

        # Map request path to filesystem path
        loc_path = getattr(location, "path", "/")
        relative = path[len(loc_path):] if path.startswith(loc_path) else path.lstrip("/")
        if not relative or relative == "/":
            relative = "index.html"

        # Prevent directory traversal
        filepath = os.path.normpath(os.path.join(root, relative.lstrip("/")))
        if not filepath.startswith(os.path.normpath(root)):
            return HttpResponse(status=403, reason="Forbidden",
                               headers={"content-type": "text/plain", "connection": "close"},
                               body=b"403 Forbidden")

        if not os.path.isfile(filepath):
            # Try index.html for directory requests
            if os.path.isdir(filepath):
                filepath = os.path.join(filepath, "index.html")
            if not os.path.isfile(filepath):
                return HttpResponse(status=404, reason="Not Found",
                                   headers={"content-type": "text/plain", "connection": "close"},
                                   body=b"404 Not Found")

        try:
            with open(filepath, "rb") as f:
                body = f.read()
        except OSError:
            return HttpResponse(status=403, reason="Forbidden",
                               headers={"content-type": "text/plain", "connection": "close"},
                               body=b"403 Forbidden")

        # Detect content type
        content_type, _ = mimetypes.guess_type(filepath)
        if content_type is None:
            content_type = "application/octet-stream"

        # Set caching headers
        cache_ttl = getattr(location, "cache_ttl", 0)
        headers = {
            "content-type": content_type,
            "content-length": str(len(body)),
            "cache-control": f"max-age={cache_ttl}" if cache_ttl > 0 else "no-cache",
        }

        return HttpResponse(status=200, reason="OK", headers=headers, body=body)

    async def _send_error(self, status: int, message: str) -> None:
        """Send an error response."""
        body = f"<html><body><h1>{status} {message}</h1><hr><em>LocalNetwork Proxy</em></body></html>"
        response = HttpResponse(
            status=status,
            reason=message,
            headers={"content-type": "text/html", "connection": "close"},
            body=body.encode(),
        )
        await self._send_response(response)

    async def _close(self) -> None:
        """Close the client connection."""
        self._closed = True
        self.state = ConnState.CLOSED
        if self.status_collector:
            self.status_collector.decrement_active()
        try:
            self.writer.close()
        except OSError:
            pass


__all__ = [
    "ConnState",
    "HttpRequest",
    "HttpResponse",
    "HttpParser",
    "Connection",
    "HOP_BY_HOP",
]

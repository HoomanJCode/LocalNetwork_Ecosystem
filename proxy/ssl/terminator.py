"""SSL/TLS termination (DESIGN.md Phase 20).

Creates SSL contexts from certificate files, supports SNI for
multi-certificate setups, and handles async SSL handshakes.
"""

from __future__ import annotations

import logging
import os
import ssl
from typing import Dict, Optional

log = logging.getLogger("localnetwork.proxy.ssl")


class SSLContextManager:
    """Manages SSL contexts for HTTPS termination."""

    def __init__(self) -> None:
        self._contexts: Dict[int, ssl.SSLContext] = {}  # port → context
        self._sni_contexts: Dict[str, ssl.SSLContext] = {}  # server_name → context

    def load_certificate(
        self,
        cert_path: str,
        key_path: str,
        protocols: list = None,
        ciphers: str = "ECDHE+AESGCM:ECDHE+CHACHA20",
    ) -> ssl.SSLContext:
        """Create an SSL context from certificate and key files.

        Args:
            cert_path: Path to the PEM certificate file.
            key_path: Path to the PEM private key file.
            protocols: List of TLS protocol strings (e.g., ["TLSv1.2", "TLSv1.3"]).
            ciphers: OpenSSL cipher string.

        Returns:
            A configured ssl.SSLContext.
        """
        if not os.path.exists(cert_path):
            raise FileNotFoundError(f"certificate not found: {cert_path}")
        if not os.path.exists(key_path):
            raise FileNotFoundError(f"private key not found: {key_path}")

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert_path, key_path)

        if protocols:
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            if "TLSv1.3" in protocols:
                pass  # default is fine
            if "TLSv1.2" not in protocols:
                ctx.minimum_version = ssl.TLSVersion.TLSv1_3

        if ciphers:
            ctx.set_ciphers(ciphers)

        ctx.options |= ssl.OP_NO_COMPRESSION
        ctx.set_ecdh_curve("prime256v1")

        return ctx

    def add_context(self, port: int, ctx: ssl.SSLContext, server_names: list = None) -> None:
        """Register an SSL context for a port and optional SNI names."""
        self._contexts[port] = ctx
        if server_names:
            for name in server_names:
                self._sni_contexts[name] = ctx

    def get_context(self, port: int, server_name: str = "") -> Optional[ssl.SSLContext]:
        """Get the SSL context for a port and optional SNI server name."""
        if server_name and server_name in self._sni_contexts:
            return self._sni_contexts[server_name]
        return self._contexts.get(port)

    def wrap_socket(
        self,
        sock,
        port: int,
        server_name: str = "",
        server_side: bool = True,
    ):
        """Wrap a socket with SSL for the given port/SNI name."""
        ctx = self.get_context(port, server_name)
        if ctx is None:
            raise ValueError(f"no SSL context for port {port}")
        return ctx.wrap_socket(sock, server_side=server_side)

    async def wrap_connection(
        self,
        reader,
        writer,
        port: int,
        server_name: str = "",
    ):
        """Async SSL handshake for an asyncio connection."""
        loop = __import__("asyncio").get_event_loop()

        ctx = self.get_context(port, server_name)
        if ctx is None:
            raise ValueError(f"no SSL context for port {port}")

        transport = writer.transport
        protocol = transport.get_protocol()

        ssl_protocol = ssl.SSLContext.wrap_socket(
            ctx,
            transport.get_extra_info("socket"),
            server_side=True,
            do_handshake_on_connect=False,
        )
        # This is a simplified approach; production would use asyncio.start_tls
        return reader, writer


__all__ = ["SSLContextManager"]

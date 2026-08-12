"""Reverse proxy configuration (DESIGN.md §7, Phase 17).

Parses a YAML configuration file and returns a validated :class:`ProxyConfig`.
All fields have sensible defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class UpstreamServer:
    """One backend server inside an upstream group."""

    host: str
    port: int = 80
    weight: int = 1
    max_conns: int = 0  # 0 = unlimited
    backup: bool = False
    down: bool = False


@dataclass
class UpstreamBlock:
    """An upstream group (pool of backend servers)."""

    name: str
    servers: List[UpstreamServer] = field(default_factory=list)
    algorithm: str = "round_robin"  # round_robin, least_conn, ip_hash, random
    max_failures: int = 3
    fail_timeout: int = 10  # seconds before retrying a failed server
    keepalive: int = 32  # max idle keep-alive connections per server


@dataclass
class LocationBlock:
    """One location block (path-based routing rule)."""

    path: str = "/"
    upstream: str = ""  # name of the upstream group
    root: str = ""  # static file root directory
    ssl: bool = False
    cache: bool = False
    cache_ttl: int = 300  # seconds
    compress: bool = True
    rate_limit: int = 0  # requests/second (0 = unlimited)
    basic_auth: str = ""  # path to htpasswd file
    # WebSocket support
    ws_enabled: bool = True
    ws_timeout: float = 300.0  # idle timeout in seconds
    ws_max_message_size: int = 1024 * 1024  # 1 MiB
    ws_ping_interval: float = 30.0  # seconds between pings


@dataclass
class SSLBlock:
    """SSL/TLS configuration for a server block."""

    cert_path: str = ""
    key_path: str = ""
    protocols: List[str] = field(default_factory=lambda: ["TLSv1.2", "TLSv1.3"])
    ciphers: str = "ECDHE+AESGCM:ECDHE+CHACHA20"
    session_timeout: int = 300


@dataclass
class ProxyConfig:
    """Root proxy configuration."""

    workers: int = 0  # 0 = auto (CPU count)
    worker_connections: int = 1024

    # Listen ports
    http: List[int] = field(default_factory=lambda: [80])
    https: List[int] = field(default_factory=lambda: [443])

    # Server blocks
    upstreams: List[UpstreamBlock] = field(default_factory=list)
    locations: List[LocationBlock] = field(default_factory=list)
    ssl_blocks: Dict[int, SSLBlock] = field(default_factory=dict)  # port → SSL config

    # Cache
    cache_enabled: bool = False
    cache_path: str = "/tmp/lnproxy-cache"
    cache_max_size: int = 1024 * 1024 * 100  # 100 MB

    # Compression
    gzip_enabled: bool = True
    gzip_min_length: int = 256
    gzip_level: int = 6

    # Rate limiting
    rate_limit_zone_size: int = 10000  # number of tracked IPs

    # Access
    access_log: str = ""  # "" = stdout
    error_log: str = ""
    log_format: str = "combined"  # combined, json

    # Admin panel
    admin_port: int = 54010


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
def load_config(path: str) -> ProxyConfig:
    """Parse a YAML/JSON config file and return a validated :class:`ProxyConfig`.

    Args:
        path: Path to the configuration file.

    Returns:
        A validated proxy configuration.

    Raises:
        ValueError: If the config contains validation errors.
        FileNotFoundError: If the file doesn't exist.
    """
    import json

    if not os.path.exists(path):
        raise FileNotFoundError(f"config file not found: {path}")

    with open(path) as f:
        if path.endswith(".json"):
            raw = json.load(f)
        else:
            try:
                import yaml
                raw = yaml.safe_load(f)
            except ImportError:
                # Fallback: treat as JSON
                f.seek(0)
                raw = json.load(f)

    if raw is None:
        raw = {}

    return _parse_config(raw)


def _parse_config(raw: dict) -> ProxyConfig:
    """Parse raw dict into ProxyConfig with validation."""
    config = ProxyConfig()

    if "workers" in raw:
        config.workers = int(raw["workers"])
    if "worker_connections" in raw:
        config.worker_connections = int(raw["worker_connections"])
    if "http" in raw:
        config.http = _parse_port_list(raw["http"])
    if "https" in raw:
        config.https = _parse_port_list(raw["https"])

    # Upstreams
    for upstream_raw in raw.get("upstreams", []):
        servers = []
        for s in upstream_raw.get("servers", []):
            if isinstance(s, str):
                host, _, port = s.partition(":")
                servers.append(UpstreamServer(host=host, port=int(port) if port else 80))
            elif isinstance(s, dict):
                servers.append(UpstreamServer(
                    host=s.get("host", ""),
                    port=s.get("port", 80),
                    weight=s.get("weight", 1),
                    max_conns=s.get("max_conns", 0),
                    backup=s.get("backup", False),
                    down=s.get("down", False),
                ))
        config.upstreams.append(UpstreamBlock(
            name=upstream_raw.get("name", ""),
            servers=servers,
            algorithm=upstream_raw.get("algorithm", "round_robin"),
            max_failures=upstream_raw.get("max_failures", 3),
            fail_timeout=upstream_raw.get("fail_timeout", 10),
        ))

    # Locations
    for loc_raw in raw.get("locations", []):
        config.locations.append(LocationBlock(
            path=loc_raw.get("path", "/"),
            upstream=loc_raw.get("upstream", ""),
            root=loc_raw.get("root", ""),
            ssl=loc_raw.get("ssl", False),
            cache=loc_raw.get("cache", False),
            cache_ttl=loc_raw.get("cache_ttl", 300),
            compress=loc_raw.get("compress", True),
            rate_limit=loc_raw.get("rate_limit", 0),
            basic_auth=loc_raw.get("basic_auth", ""),
            ws_enabled=loc_raw.get("ws_enabled", True),
            ws_timeout=loc_raw.get("ws_timeout", 300.0),
            ws_max_message_size=loc_raw.get("ws_max_message_size", 1024 * 1024),
            ws_ping_interval=loc_raw.get("ws_ping_interval", 30.0),
        ))

    # SSL
    for port_str, ssl_raw in raw.get("ssl", {}).items():
        port = int(port_str)
        config.ssl_blocks[port] = SSLBlock(
            cert_path=ssl_raw.get("cert", ""),
            key_path=ssl_raw.get("key", ""),
            protocols=ssl_raw.get("protocols", ["TLSv1.2", "TLSv1.3"]),
        )

    # Cache
    if "cache" in raw:
        c = raw["cache"]
        config.cache_enabled = True
        config.cache_path = c.get("path", config.cache_path)
        config.cache_max_size = c.get("max_size", config.cache_max_size)

    # Gzip
    if "gzip" in raw:
        g = raw["gzip"]
        config.gzip_enabled = g.get("enabled", True)
        config.gzip_min_length = g.get("min_length", 256)
        config.gzip_level = g.get("level", 6)

    # Logging
    if "access_log" in raw:
        config.access_log = raw["access_log"]
    if "error_log" in raw:
        config.error_log = raw["error_log"]
    if "log_format" in raw:
        config.log_format = raw["log_format"]

    # Admin
    if "admin" in raw:
        config.admin_port = int(raw.get("admin", {}).get("port", 54010))

    return config


def _parse_port_list(value: Any) -> List[int]:
    """Parse a list of ports from various formats."""
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        return [int(p) for p in value]
    if isinstance(value, str):
        return [int(p) for p in value.split(",")]
    return [80]


__all__ = [
    "ProxyConfig",
    "UpstreamServer",
    "UpstreamBlock",
    "LocationBlock",
    "SSLBlock",
    "load_config",
]

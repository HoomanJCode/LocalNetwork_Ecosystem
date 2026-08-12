"""Shared pytest fixtures for the LocalNetwork Ecosystem test suite."""

import asyncio
import socket
import sys
from pathlib import Path

import pytest

# Ensure the project root is importable regardless of how pytest is invoked.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def unused_port():
    """Return a TCP port that is currently free on the loopback interface."""

    def _get() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    return _get


@pytest.fixture
def tmp_project_dir(tmp_path):
    """A scratch directory that mimics a project checkout."""
    (tmp_path / "server").mkdir()
    (tmp_path / "client").mkdir()
    (tmp_path / "common").mkdir()
    (tmp_path / "tests").mkdir()
    return tmp_path


@pytest.fixture
def identity_dir(tmp_path):
    """Dedicated temp directory for identity keys."""
    d = tmp_path / "identity"
    d.mkdir()
    return d# ---- Async helpers ----------------------------------------------------------

def run_async(coro):
    """Run a coroutine to completion inside a fresh event loop (sync tests)."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            loop.close()
        except Exception:
            pass


async def wait_for_port(host: str, port: int, timeout: float = 5.0) -> bool:
    """Poll until a TCP server is accepting connections on host:port."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            reader, writer = await asyncio.open_connection(host, port)
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, ConnectionRefusedError):
            await asyncio.sleep(0.05)
    return False


async def read_exact(reader: asyncio.StreamReader, n: int) -> bytes:
    """Read exactly n bytes from a stream reader."""
    buf = bytearray()
    while len(buf) < n:
        chunk = await reader.read(n - len(buf))
        if not chunk:
            raise ConnectionError("stream closed before expected byte count")
        buf.extend(chunk)
    return bytes(buf)


@pytest.fixture
def async_runner():
    """Factory to run async test bodies synchronously."""
    return run_async

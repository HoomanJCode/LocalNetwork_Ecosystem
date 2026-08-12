"""Loopback-only end-to-end smoke test.

Starts a real mediation server on 127.0.0.1 (ephemeral port), connects two
clients, creates a network, joins it, and verifies peer discovery.

Everything runs on the loopback interface with temporary ports. No system
network configuration (routes, firewall, TUN, adapters) is touched.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from client import identity  # noqa: E402
from client.control_channel import ControlChannel  # noqa: E402
from common import constants  # noqa: E402
from server.config import ServerConfig  # noqa: E402
from server.main import MediationServer  # noqa: E402


def make_identity(identity_dir):
    private_key, public_key = identity.generate_identity()
    identity.save_identity(private_key, public_key, path=identity_dir)
    from cryptography.hazmat.primitives import serialization

    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    client_id = identity.client_id_for_public_key(public_key)
    return private_key, public_pem, client_id


async def wait_for_server(port):
    for _ in range(100):
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.05)
    raise RuntimeError("server did not start")


async def main():
    # ── start server on an ephemeral loopback port ────────────────────
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        config = ServerConfig(
            host="127.0.0.1", port=0, web_port=0, heartbeat_timeout=600
        )
        server = MediationServer(config)
        task = asyncio.create_task(server.start())
        # port 0 → server picks a free one; read it back
        await asyncio.sleep(0.2)
        port = server._server.sockets[0].getsockname()[1]
        await wait_for_server(port)
        print(f"[1/4] server listening on 127.0.0.1:{port}")

        # ── connect two clients ────────────────────────────────────────
        priv_a, pub_a, id_a = make_identity(tmp / "a")
        priv_b, pub_b, id_b = make_identity(tmp / "b")

        ch_a = ControlChannel(host="127.0.0.1", port=port, client_id=id_a)
        ch_b = ControlChannel(host="127.0.0.1", port=port, client_id=id_b)
        await ch_a.connect()
        await ch_b.connect()
        await ch_a.authenticate(priv_a, pub_a)
        await ch_b.authenticate(priv_b, pub_b)
        print(f"[2/4] clients registered+authed (A={id_a[:8]}… B={id_b[:8]}…)")

        # ── A creates a network; B joins ──────────────────────────────
        created = await ch_a.create_network("smoketest", "secret", "mesh")
        network_id = created["network_id"]
        print(f"[3/4] A created network {network_id} (invite {created['invite_code']})")

        # B listens for PEER_ONLINE for A; A listens for PEER_ONLINE for B
        event_a = asyncio.create_task(ch_a.listen_events().__anext__())
        await ch_b.join_network(network_id, "secret")
        print("[4/4] B joined network")

        event = await asyncio.wait_for(event_a, timeout=5)
        assert event.type == constants.MSG_PEER_ONLINE, event.type
        assert event.payload["peer_id"] == id_b, event.payload
        print(f"      A received PEER_ONLINE for B ({id_b[:8]}...) [OK]")

        # ── peer discovery: A asks for B's endpoints ──────────────────
        endpoints = await ch_a.request_peer_endpoints(id_b)
        assert endpoints, "expected endpoints for B"
        host, port_num = endpoints[0]
        assert host == "127.0.0.1"
        print(f"      A discovered B at {host}:{port_num} [OK]")

        # ── cleanup ────────────────────────────────────────────────────
        await ch_a.close()
        await ch_b.close()
        await server.shutdown()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

        print()
        print("SMOKE TEST PASSED - all on loopback, nothing changed on your PC.")


if __name__ == "__main__":
    asyncio.run(main())

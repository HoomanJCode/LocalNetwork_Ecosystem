"""Tests for client.tunnel_manager and client.keepalive."""

import asyncio
import socket
import time
from unittest import mock

import pytest

from client.encryption import (
    CipherContext,
    ecdh_public_bytes,
    generate_ecdh_keypair,
)
from client.keepalive import KeepAliveManager, KEEPALIVE_SUSPECT_TIMEOUT
from client.nat_traversal import NatTraversal, PunchState
from client.tunnel_manager import PeerTunnel, TunnelManager
from common.constants import (
    FRAME_CLOSE,
    FRAME_DATA,
    FRAME_KEEPALIVE,
    TUNNEL_STALE_TIMEOUT,
)
from common.frame import (
    make_close_frame,
    make_keepalive_frame,
    make_punch_ack_frame,
    make_punch_frame,
    pack_frame,
    unpack_frame,
)


# ── helpers ────────────────────────────────────────────────────────────


def _unused_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _drain_recv_loop(tm: TunnelManager, count: int, max_wait: float = 3.0):
    """Collect *count* items from the recv loop."""
    items = []
    it = tm.recv_loop().__aiter__()
    deadline = time.monotonic() + max_wait
    while len(items) < count and time.monotonic() < deadline:
        try:
            peer_id, data = await asyncio.wait_for(it.__anext__(), timeout=0.2)
            items.append((peer_id, data))
        except (asyncio.TimeoutError, StopAsyncIteration):
            continue
    return items


# ── PeerTunnel dataclass ───────────────────────────────────────────────


class TestPeerTunnel:
    def test_defaults(self):
        t = PeerTunnel(peer_id="p1", peer_ip="25.1.0.2")
        assert t.peer_id == "p1"
        assert t.peer_ip == "25.1.0.2"
        assert t.state == PunchState.IDLE
        assert t.socket is None
        assert t.cipher is None
        assert t.tx_seq == 0
        assert t.rx_seq == 0
        assert t.fallback_relay is False

    def test_alive_in_punching_and_connected(self):
        t = PeerTunnel(peer_id="p1", peer_ip="25.1.0.2")
        t.state = PunchState.PUNCHING
        assert t.alive is True
        t.state = PunchState.CONNECTED
        assert t.alive is True

    def test_not_alive_in_other_states(self):
        t = PeerTunnel(peer_id="p1", peer_ip="25.1.0.2")
        t.state = PunchState.IDLE
        assert t.alive is False
        t.state = PunchState.FAILED
        assert t.alive is False
        t.state = PunchState.CLOSED
        assert t.alive is False


# ── TunnelManager: tunnel lifecycle ────────────────────────────────────


class TestTunnelManagerLifecycle:
    def test_get_tunnel_returns_none_for_unknown(self):
        tm = TunnelManager()
        assert tm.get_tunnel("ghost") is None

    def test_list_tunnels_starts_empty(self):
        tm = TunnelManager()
        assert tm.list_tunnels() == []

    def test_shutdown_clears_all(self):
        tm = TunnelManager()
        t = PeerTunnel(peer_id="p1", peer_ip="25.1.0.2", state=PunchState.CONNECTED)
        tm._tunnels["p1"] = t
        tm.shutdown()
        assert tm.list_tunnels() == []
        assert tm._running is False


# ── TunnelManager: create_tunnel with mocked NAT ───────────────────────


class TestCreateTunnel:
    @pytest.mark.asyncio
    async def test_punch_succeeds_establishes_tunnel(self):
        """When punch_peer returns True, the tunnel transitions to CONNECTED."""
        nat = NatTraversal()
        nat.bind_udp_socket = mock.MagicMock(return_value=_make_udp_socket())

        # Simulate punch success with peer pubkey embedded in return
        peer_key = generate_ecdh_keypair()
        peer_pub = ecdh_public_bytes(peer_key)

        with mock.patch.object(
            nat, "punch_peer", return_value=(True, ("127.0.0.1", 9999), peer_pub)
        ):
            tm = TunnelManager(nat=nat)
            tunnel = await tm.create_tunnel(
                "peer-a", "25.1.0.2", [("127.0.0.1", 9999)]
            )

        assert tunnel.peer_id == "peer-a"
        assert tunnel.peer_ip == "25.1.0.2"
        assert tunnel.state == PunchState.CONNECTED
        assert tunnel.remote_addr == ("127.0.0.1", 9999)
        assert tunnel.cipher is not None
        assert tunnel.fallback_relay is False

    @pytest.mark.asyncio
    async def test_punch_fails_falls_back_to_relay(self):
        """When punch fails and control channel is available, request relay."""
        nat = NatTraversal()
        nat.bind_udp_socket = mock.MagicMock(return_value=_make_udp_socket())

        mock_control = mock.AsyncMock()
        mock_control.request_relay = mock.AsyncMock()

        with mock.patch.object(nat, "punch_peer", return_value=(False, None, b"")):
            tm = TunnelManager(nat=nat)
            tm.inject_control(mock_control)
            tunnel = await tm.create_tunnel(
                "peer-a", "25.1.0.2", [("127.0.0.1", 9999)]
            )

        assert tunnel.fallback_relay is True
        assert tunnel.state == PunchState.CONNECTED
        mock_control.request_relay.assert_awaited_once_with("peer-a")

    @pytest.mark.asyncio
    async def test_punch_fails_no_control_marks_failed(self):
        """Without a control channel, failed punch → FAILED state."""
        nat = NatTraversal()
        nat.bind_udp_socket = mock.MagicMock(return_value=_make_udp_socket())

        with mock.patch.object(nat, "punch_peer", return_value=(False, None, b"")):
            tm = TunnelManager(nat=nat)
            tunnel = await tm.create_tunnel(
                "peer-a", "25.1.0.2", [("127.0.0.1", 9999)]
            )

        assert tunnel.state == PunchState.FAILED

    @pytest.mark.asyncio
    async def test_replaces_existing_tunnel_for_same_peer(self):
        """Creating a tunnel for a peer with an existing one replaces it."""
        nat = NatTraversal()
        nat.bind_udp_socket = mock.MagicMock(return_value=_make_udp_socket())

        with mock.patch.object(nat, "punch_peer", return_value=(False, None, b"")):
            tm = TunnelManager(nat=nat)
            await tm.create_tunnel("peer-a", "25.1.0.2", [])
            await tm.create_tunnel("peer-a", "25.1.0.3", [])

        # Only one tunnel for peer-a
        assert len(tm.list_tunnels()) == 1


# ── TunnelManager: accept_tunnel ───────────────────────────────────────


class TestAcceptTunnel:
    @pytest.mark.asyncio
    async def test_accept_succeeds(self):
        """Passive accept with a successful accept_punch."""
        nat = NatTraversal()

        ecdh = generate_ecdh_keypair()
        pubkey = ecdh_public_bytes(ecdh)
        peer_key = generate_ecdh_keypair()
        peer_pub = ecdh_public_bytes(peer_key)

        with mock.patch.object(
            nat, "accept_punch", return_value=(("127.0.0.1", 8888), peer_pub)
        ):
            tm = TunnelManager(nat=nat)
            sock = _make_udp_socket()
            tunnel = await tm.accept_tunnel("peer-b", "25.1.0.3", sock, ecdh)

        assert tunnel is not None
        assert tunnel.peer_id == "peer-b"
        assert tunnel.state == PunchState.CONNECTED
        assert tunnel.cipher is not None
        assert tunnel.remote_addr == ("127.0.0.1", 8888)

    @pytest.mark.asyncio
    async def test_accept_fails_returns_none(self):
        """When accept_punch returns None, the tunnel is FAILED."""
        nat = NatTraversal()
        ecdh = generate_ecdh_keypair()

        with mock.patch.object(nat, "accept_punch", return_value=None):
            tm = TunnelManager(nat=nat)
            sock = _make_udp_socket()
            tunnel = await tm.accept_tunnel("peer-b", "25.1.0.3", sock, ecdh)

        assert tunnel is None
        # Tunnel is in FAILED state in the manager
        t = tm.get_tunnel("peer-b")
        assert t is not None
        assert t.state == PunchState.FAILED


# ── TunnelManager: send/recv data round-trip ──────────────────────────


class TestSendRecvData:
    def test_send_data_encrypts_and_sends(self):
        """send_data encrypts payload and sends it as a FRAME_DATA."""
        peer_key = generate_ecdh_keypair()
        my_key = generate_ecdh_keypair()
        from client.encryption import derive_session_key

        session = derive_session_key(my_key, peer_key.public_key())
        cipher = CipherContext(session)

        sock = _make_udp_socket()
        recv = _make_udp_socket()
        recv_addr = recv.getsockname()

        tunnel = PeerTunnel(
            peer_id="p1",
            peer_ip="25.1.0.2",
            state=PunchState.CONNECTED,
            socket=sock,
            cipher=cipher,
            remote_addr=recv_addr,
        )

        # Set up a reciprocal cipher on the receive side
        recv_cipher = CipherContext(session)

        tm = TunnelManager()
        payload = b"IP_PACKET_DATA_HERE"
        sent = tm.send_data(tunnel, payload)
        assert sent > 0

        # Receive on the other side
        recv.settimeout(0.5)
        data, _ = recv.recvfrom(4096)
        header, ct, tag = unpack_frame(data)
        assert header.type == FRAME_DATA
        assert header.seq_num == 1  # tx_seq was incremented

        # Decrypt
        plaintext = recv_cipher.decrypt(ct, tag)
        assert plaintext == payload

    def test_send_data_returns_zero_when_no_cipher(self):
        """Unencrypted data sends work for relay mode."""
        sock = _make_udp_socket()
        recv = _make_udp_socket()
        recv_addr = recv.getsockname()

        tunnel = PeerTunnel(
            peer_id="p1",
            peer_ip="25.1.0.2",
            state=PunchState.CONNECTED,
            socket=sock,
            remote_addr=recv_addr,
        )
        tm = TunnelManager()
        sent = tm.send_data(tunnel, b"raw_data")
        assert sent > 0

        recv.settimeout(0.5)
        data, _ = recv.recvfrom(4096)
        header, payload, _ = unpack_frame(data)
        assert header.type == FRAME_DATA
        assert payload == b"raw_data"

    def test_send_data_not_connected_returns_zero(self):
        tunnel = PeerTunnel(peer_id="p1", peer_ip="25.1.0.2")
        tm = TunnelManager()
        assert tm.send_data(tunnel, b"data") == 0

    def test_recv_data_reads_decrypted_frame(self):
        """recv_data reads and decrypts incoming data."""
        peer_key = generate_ecdh_keypair()
        my_key = generate_ecdh_keypair()
        from client.encryption import derive_session_key

        session = derive_session_key(my_key, peer_key.public_key())
        cipher = CipherContext(session)

        sock = _make_udp_socket()
        sock_addr = sock.getsockname()

        tunnel = PeerTunnel(
            peer_id="p1",
            peer_ip="25.1.0.2",
            state=PunchState.CONNECTED,
            socket=sock,
            cipher=cipher,
        )

        # Send a data frame to the socket
        sender = _make_udp_socket()
        ciphertext, tag = cipher.encrypt(b"hello_world")
        frame = pack_frame(FRAME_DATA, ciphertext, seq_num=42, auth_tag=tag)
        sender.sendto(frame, sock_addr)

        tm = TunnelManager()
        result = tm.recv_data(tunnel)
        assert result == b"hello_world"
        assert tunnel.rx_seq == 42
        assert tunnel.last_rx > 0

    def test_recv_returns_none_when_not_connected(self):
        tunnel = PeerTunnel(peer_id="p1", peer_ip="25.1.0.2")
        tunnel.socket = _make_udp_socket()
        tm = TunnelManager()
        assert tm.recv_data(tunnel) is None


# ── TunnelManager: keepalive ──────────────────────────────────────────


class TestKeepalive:
    def test_send_keepalive_sends_frame(self):
        sock = _make_udp_socket()
        recv = _make_udp_socket()
        recv_addr = recv.getsockname()

        tunnel = PeerTunnel(
            peer_id="p1",
            peer_ip="25.1.0.2",
            state=PunchState.CONNECTED,
            socket=sock,
            remote_addr=recv_addr,
        )
        tm = TunnelManager()
        assert tm.send_keepalive(tunnel) is True

        recv.settimeout(0.5)
        data, _ = recv.recvfrom(4096)
        header, _, _ = unpack_frame(data)
        assert header.type == FRAME_KEEPALIVE

    def test_send_keepalive_when_not_connected(self):
        tunnel = PeerTunnel(peer_id="p1", peer_ip="25.1.0.2")
        tm = TunnelManager()
        assert tm.send_keepalive(tunnel) is False


# ── TunnelManager: close_tunnel ────────────────────────────────────────


class TestCloseTunnel:
    def test_close_sends_close_frame_and_removes(self):
        sock = _make_udp_socket()
        recv = _make_udp_socket()
        recv_addr = recv.getsockname()

        tunnel = PeerTunnel(
            peer_id="p1",
            peer_ip="25.1.0.2",
            state=PunchState.CONNECTED,
            socket=sock,
            remote_addr=recv_addr,
        )
        tm = TunnelManager()
        tm._tunnels["p1"] = tunnel
        tm.close_tunnel(tunnel)

        assert tunnel.state == PunchState.CLOSED
        assert tm.get_tunnel("p1") is None

        # Verify CLOSE frame was sent
        recv.settimeout(0.3)
        data, _ = recv.recvfrom(4096)
        header, _, _ = unpack_frame(data)
        assert header.type == FRAME_CLOSE


# ── TunnelManager: prune_stale ─────────────────────────────────────────


class TestPruneStale:
    def test_prune_closes_stale_tunnels(self):
        tm = TunnelManager(stale_timeout=1.0)
        old = PeerTunnel(
            peer_id="old",
            peer_ip="25.1.0.2",
            state=PunchState.CONNECTED,
            last_rx=time.monotonic() - 10.0,
        )
        fresh = PeerTunnel(
            peer_id="fresh",
            peer_ip="25.1.0.3",
            state=PunchState.CONNECTED,
            last_rx=time.monotonic(),
        )
        tm._tunnels["old"] = old
        tm._tunnels["fresh"] = fresh

        stale = tm.prune_stale()
        assert len(stale) == 1
        assert stale[0].peer_id == "old"
        assert tm.get_tunnel("old") is None
        assert tm.get_tunnel("fresh") is not None

    def test_prune_ignores_non_connected(self):
        tm = TunnelManager(stale_timeout=0.1)
        t = PeerTunnel(
            peer_id="p1",
            peer_ip="25.1.0.2",
            state=PunchState.IDLE,
            last_rx=time.monotonic() - 100.0,
        )
        tm._tunnels["p1"] = t
        assert tm.prune_stale() == []

    def test_prune_ignores_zero_last_rx(self):
        """Tunnels that never received data (last_rx=0) are not pruned."""
        tm = TunnelManager(stale_timeout=0.1)
        t = PeerTunnel(
            peer_id="p1",
            peer_ip="25.1.0.2",
            state=PunchState.CONNECTED,
            last_rx=0.0,
        )
        tm._tunnels["p1"] = t
        assert tm.prune_stale() == []


# ── TunnelManager: frame handling ──────────────────────────────────────


class TestHandleFrame:
    def test_data_frame_decrypts(self):
        tm = TunnelManager()
        my_key = generate_ecdh_keypair()
        peer_key = generate_ecdh_keypair()
        from client.encryption import derive_session_key

        session = derive_session_key(my_key, peer_key.public_key())
        cipher = CipherContext(session)

        tunnel = PeerTunnel(
            peer_id="p1",
            peer_ip="25.1.0.2",
            state=PunchState.CONNECTED,
            cipher=cipher,
        )

        ct, tag = cipher.encrypt(b"data_payload")
        frame = pack_frame(FRAME_DATA, ct, seq_num=5, auth_tag=tag)

        result = tm._handle_frame(tunnel, frame, ("127.0.0.1", 9999))
        assert result == b"data_payload"
        assert tunnel.rx_seq == 5
        assert tunnel.last_rx > 0

    def test_keepalive_frame_updates_last_rx(self):
        tm = TunnelManager()
        tunnel = PeerTunnel(
            peer_id="p1", peer_ip="25.1.0.2", state=PunchState.CONNECTED
        )
        frame = make_keepalive_frame()
        result = tm._handle_frame(tunnel, frame, ("127.0.0.1", 1234))
        assert result is None
        assert tunnel.last_rx > 0

    def test_close_frame_sets_state(self):
        tm = TunnelManager()
        tunnel = PeerTunnel(
            peer_id="p1",
            peer_ip="25.1.0.2",
            state=PunchState.CONNECTED,
        )
        frame = make_close_frame()
        result = tm._handle_frame(tunnel, frame, ("127.0.0.1", 5678))
        assert result is None
        assert tunnel.state == PunchState.CLOSED

    def test_punch_frame_sends_punch_ack(self):
        tm = TunnelManager()
        sock = _make_udp_socket()
        sender = _make_udp_socket()
        sender_addr = sender.getsockname()

        tunnel = PeerTunnel(
            peer_id="p1",
            peer_ip="25.1.0.2",
            state=PunchState.CONNECTED,
            socket=sock,
        )

        frame = make_punch_frame()
        result = tm._handle_frame(tunnel, frame, sender_addr)
        assert result is None

        # Sender should get PUNCH_ACK
        sender.settimeout(0.3)
        data, _ = sender.recvfrom(4096)
        header, _, _ = unpack_frame(data)
        assert header.type == 0x06  # FRAME_PUNCH_ACK

    def test_invalid_frame_returns_none(self):
        tm = TunnelManager()
        tunnel = PeerTunnel(
            peer_id="p1", peer_ip="25.1.0.2", state=PunchState.CONNECTED
        )
        result = tm._handle_frame(tunnel, b"garbage", ("127.0.0.1", 1234))
        assert result is None


# ── Sequence numbers ───────────────────────────────────────────────────


class TestSequenceNumbers:
    def test_tx_seq_monotonic(self):
        """tx_seq increments with each send_data call."""
        sock = _make_udp_socket()
        recv = _make_udp_socket()
        recv_addr = recv.getsockname()

        tunnel = PeerTunnel(
            peer_id="p1",
            peer_ip="25.1.0.2",
            state=PunchState.CONNECTED,
            socket=sock,
            remote_addr=recv_addr,
        )
        tm = TunnelManager()

        for i in range(5):
            tm.send_data(tunnel, bytes([i]))
            assert tunnel.tx_seq == i + 1


# ── KeepAliveManager ───────────────────────────────────────────────────


class TestKeepAliveManager:
    def test_suspect_detection(self):
        """Tunnels silent for > suspect_timeout are tracked."""
        tm = TunnelManager()
        fresh = PeerTunnel(
            peer_id="fresh",
            peer_ip="25.1.0.2",
            state=PunchState.CONNECTED,
            last_rx=time.monotonic(),
        )
        suspect = PeerTunnel(
            peer_id="suspect",
            peer_ip="25.1.0.3",
            state=PunchState.CONNECTED,
            last_rx=time.monotonic() - KEEPALIVE_SUSPECT_TIMEOUT - 1,
        )
        tm._tunnels["fresh"] = fresh
        tm._tunnels["suspect"] = suspect

        # Mock send_keepalive so it doesn't need real sockets
        with mock.patch.object(tm, "send_keepalive", return_value=True):
            km = KeepAliveManager(tm)
            km._tick()

        assert "suspect" in km.suspect_peers
        assert "fresh" not in km.suspect_peers

    def test_stale_tunnel_closed(self):
        """Tunnels silent for > stale_timeout are closed."""
        tm = TunnelManager()
        stale = PeerTunnel(
            peer_id="stale",
            peer_ip="25.1.0.2",
            state=PunchState.CONNECTED,
            last_rx=time.monotonic() - TUNNEL_STALE_TIMEOUT - 1,
        )
        tm._tunnels["stale"] = stale

        with mock.patch.object(tm, "send_keepalive", return_value=True):
            km = KeepAliveManager(tm, stale_timeout=TUNNEL_STALE_TIMEOUT)
            km._tick()

        assert tm.get_tunnel("stale") is None

    def test_recovering_tunnel_removed_from_suspect(self):
        """A tunnel that was suspect but starts receiving again."""
        tm = TunnelManager()
        recovering = PeerTunnel(
            peer_id="recov",
            peer_ip="25.1.0.2",
            state=PunchState.CONNECTED,
            last_rx=time.monotonic(),  # fresh
        )
        tm._tunnels["recov"] = recovering

        km = KeepAliveManager(tm)
        km._suspect_peers.add("recov")  # was previously suspect
        with mock.patch.object(tm, "send_keepalive", return_value=True):
            km._tick()

        assert "recov" not in km.suspect_peers

    def test_skips_non_connected_tunnels(self):
        """Only CONNECTED tunnels receive keepalives."""
        tm = TunnelManager()
        idle = PeerTunnel(
            peer_id="idle",
            peer_ip="25.1.0.2",
            state=PunchState.IDLE,
            last_rx=time.monotonic(),
        )
        tm._tunnels["idle"] = idle

        km = KeepAliveManager(tm)
        with mock.patch.object(tm, "send_keepalive", return_value=True) as mock_send:
            km._tick()
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_and_start(self):
        tm = TunnelManager()
        km = KeepAliveManager(tm)
        km.start()
        assert km._task is not None
        km.stop()
        assert km._running is False


# ── Helpers ────────────────────────────────────────────────────────────


def _make_udp_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(0.0)
    return sock

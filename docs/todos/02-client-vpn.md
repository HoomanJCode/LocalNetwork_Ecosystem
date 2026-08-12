# TODO — Phase 5–9: Client Core & Virtual LAN

> Part of the [LocalNetwork Ecosystem implementation plan](../TODO.md).
> Each task is a checkbox. Work through phases in order; items within a phase
> can be parallelized. Testing tasks are listed alongside the feature they test.

---

## Phase 5 — Client Core

- [x] 5.1 **`client/platform_detection.py`** — Platform capability detection
  - `PlatformCapabilities` dataclass: os_name, has_root, tun_available, raw_sockets, privileged_ports, is_termux
  - `detect_platform() -> PlatformCapabilities`:
    1. Detect OS via `platform.system()`
    2. Detect Termux: check `$PREFIX` env var for `/data/data/com.termux/files/usr`
    3. Detect root: `os.geteuid() == 0` (Unix) or `ctypes.windll.shell32.IsUserAnAdmin()` (Windows)
    4. Detect TUN: try `open('/dev/net/tun')` (Linux), check utun (macOS), check wintun (Windows). Termux → always False.
    5. Detect raw sockets: try `socket(AF_PACKET, SOCK_RAW)` (non-Termux Linux only)
    6. Detect privileged ports: if not root, try bind port 80 → catch PermissionError
  - `print_capabilities()` — pretty-print what's available and what's disabled
  - Degradation rules: if `tun_available == False` → TUN mode disabled, service exposure only; if `has_root == False` → privileged features off; if `is_termux == True` → TUN permanently disabled

- [x] 5.2 **`client/config.py`**
  - `ClientConfig` dataclass: server_host, server_port, identity_dir, virtual_ip (optional)
  - Load from env / `.env` / CLI args
  - Store `PlatformCapabilities` reference
  - On load: if TUN requested but not available → warn, fall back to service-only mode

- [x] 5.3 **`client/control_channel.py`**
  - `ControlChannel` class:
    - `connect(host, port) -> (reader, writer)` — async TCP
    - `send_message(msg: Message)` — length-prefixed JSON
    - `recv_message() -> Message` — read length prefix, decode
    - `register(client_id, public_key_pem)`
    - `authenticate(private_key)` — handle challenge/response
    - `create_network(name, password, topology) -> network_id`
    - `join_network(network_id, password)`
    - `leave_network(network_id)`
    - `list_networks() -> list`
    - `request_peer_endpoints(peer_id) -> PeerEndpoints`
    - `send_heartbeat()`
    - `listen_events() -> AsyncIterator[Message]` — yield push events (PEER_ONLINE, PEER_OFFLINE)
    - `close()`
  - Automatic reconnection with exponential backoff (start 1s, max 60s)
  - Heartbeat coroutine (fires every 30s)

- [x] 5.4 **`client/main.py`** — CLI entry point
  - `localnetwork-server` command — starts mediation server
  - `localnetwork-client` command — starts client daemon
  - `localnetwork-cli` — management CLI:
    - `create <name> [--password] [--topology mesh|hub|gateway]`
    - `join <network-id> [--password]`
    - `leave <network-id>`
    - `list`
    - `status`
    - `info <network-id>`
  - Signal handling for graceful shutdown

- [x] 5.5 **Write tests:**
  - `tests/test_platform_detection.py`
    - Detect current platform correctly
    - Root detection matches actual UID
    - Termux detection via PREFIX env var
    - TUN detection: positive when /dev/net/tun exists
    - TUN detection: negative on Termux regardless of root
    - Degradation: tun_available=False → TUN mode disabled flag set
    - Degradation: has_root=False → privileged ports disabled
  - `tests/test_control_channel.py`
    - Full register → auth → join network flow against a test server
    - Receive PEER_ONLINE notification
    - Heartbeat keeps connection alive
    - Reconnect after server restart

---

## Phase 6 — NAT Traversal (UDP Hole Punching)

- [x] 6.1 **`client/nat_traversal.py`**
  - `NatTraversal` class:
    - `__init__(local_port_range: tuple[int, int])`
    - `bind_udp_socket() -> socket.socket` — bind to an available port
    - `punch_peer(our_socket, peer_endpoints: list[tuple[str, int]]) -> bool`
      1. Send PUNCH frames to each endpoint in rapid succession
      2. Listen for incoming PUNCH frame from peer (timeout 5s)
      3. On receive: send PUNCH_ACK, return True
      4. On timeout: return False
    - `accept_punch(socket) -> tuple[str, int] | None` — passive side: wait for PUNCH, reply
    - `PunchState` enum: IDLE, PUNCHING, CONNECTED, FAILED
  - `determine_nat_type(stun_server=...) -> NatType` — optional STUN-based NAT classification
    (for diagnostics). Enum: OPEN, FULL_CONE, RESTRICTED, PORT_RESTRICTED, SYMMETRIC

- [x] 6.2 **Write tests:**
  - `tests/test_nat_traversal.py`
    - Two local sockets on localhost: hole-punch succeeds
    - Timeout when no peer responds
    - PUNCH frame format validated
    - PUNCH_ACK transitions to CONNECTED

---

## Phase 7 — P2P Tunnel Manager

- [x] 7.1 **`client/tunnel_manager.py`**
  - `PeerTunnel` dataclass: peer_id, peer_ip, state, socket, cipher, last_rx, tx_seq, rx_seq
  - `TunnelManager` class:
    - `create_tunnel(peer_id, peer_ip, peer_endpoints) -> PeerTunnel`
      1. Generate ECDH keypair
      2. Call `NatTraversal.punch_peer()` — embed ECDH public key in PUNCH frame
      3. On success: derive session key, create `CipherContext`, transition to CONNECTED
      4. On failure: request RELAY from server
    - `accept_tunnel(peer_id, peer_ip, incoming_punch) -> PeerTunnel` — passive side
    - `send_data(tunnel, raw_ip_packet: bytes)` — encrypt + frame + send on tunnel socket
    - `recv_data(tunnel) -> bytes | None` — non-blocking read, decrypt, verify seq
    - `send_keepalive(tunnel)` — send KEEPALIVE frame
    - `close_tunnel(tunnel)` — send CLOSE frame, clean up
    - `get_tunnel(peer_id) -> PeerTunnel | None`
    - `list_tunnels() -> list[PeerTunnel]`
    - `prune_stale(timeout)` — close tunnels with no rx for > timeout
  - `recv_loop()` — background asyncio task: poll all tunnel sockets, decrypt, dispatch to TUN

- [x] 7.2 **`client/keepalive.py`**
  - `KeepAliveManager`:
    - Every 10s: send KEEPALIVE frame on each tunnel
    - Track `last_rx`; if > 30s since last rx, mark tunnel as suspect
    - If > 60s, close tunnel and notify server

- [x] 7.3 **Write tests:**
  - `tests/test_tunnel_manager.py`
    - Create two TunnelManagers, punch tunnel between them
    - Send/receive data round-trip
    - Sequence number monotonic
    - Stale tunnel pruned
    - Relay fallback triggered when punch fails

---

## Phase 8 — TUN Virtual Interface

- [ ] 8.1 **`client/tun_interface.py`**
  - `TunInterface` abstract base class:
    - `open(ip: str, netmask: str, mtu: int)`
    - `read() -> bytes` — read one IP packet
    - `write(data: bytes)` — inject one IP packet
    - `close()`
    - `get_ip() -> str`

  - `LinuxTunInterface(TunInterface)`:
    - Open `/dev/net/tun`
    - `ioctl(TUNSETIFF)` with `IFF_TUN | IFF_NO_PI`
    - `ioctl(SIOCSIFADDR)`, `ioctl(SIOCSIFNETMASK)`, `ioctl(SIOCSIFMTU)`
    - `ifconfig <name> up` equivalent via `ioctl`

  - `WindowsTunInterface(TunInterface)`:
    - Use `pywintun` or raw WinTun API
    - Create adapter, set IP, netmask, MTU
    - Read/write via the adapter handle

  - `MacTunInterface(TunInterface)`:
    - Open socket `AF_SYSTEM` / `SYSPROTO_CONTROL`
    - Connect to `utun` control socket
    - `ifconfig utunN` equivalent

  - `create_tun_interface() -> TunInterface` — factory based on `platform.system()`

- [ ] 8.2 **Routing integration** (Linux-specific for MVP):
  - After TUN is up: add route for `25.0.0.0/8` via the TUN interface
  - On shutdown: remove the route
  - Skip on Windows/macOS initially (manual route setup instructions in README)

- [ ] 8.3 **Main loop integration:**
  - `TunInterface.read()` → `TunnelManager.send_data()` (to appropriate peer)
  - `TunnelManager.recv_data()` → `TunInterface.write()`
  - Need IP→peer_id mapping: parse dst IP from IP header, look up which peer has that virtual IP
  - ARP handling (basic): respond to ARP requests for our virtual IP

- [ ] 8.4 **Write tests:**
  - `tests/test_tun_interface.py`
    - Skip on CI unless running as root (mark with `pytest.mark.skipif`)
    - Test on Linux with sudo: open → assign IP → read/write loopback
    - Test MTU setting
    - Test close cleanup

---

## Phase 9 — Network Topologies

- [ ] 9.1 **Mesh topology**
  - Default mode. Client opens tunnels to ALL online peers in the network.
  - On PEER_ONLINE: create tunnel to new peer.
  - On PEER_OFFLINE: close tunnel to departed peer.

- [ ] 9.2 **Hub-and-Spoke topology**
  - Client checks if it is the designated Hub.
  - If Hub: accept tunnels from all spokes. Forward traffic between spokes
    (destination NAT at L3: rewrite dst IP, forward out correct tunnel).
  - If Spoke: only open tunnel to Hub. Route all traffic through Hub.
  - Server enforces: spokes don't receive each other's endpoints.

- [ ] 9.3 **Gateway topology**
  - Gateway client opens TUN interface AND bridges to physical LAN.
  - Enable IP forwarding on gateway: `sysctl net.ipv4.ip_forward=1`.
  - ARP proxy: gateway responds to ARP requests for virtual IPs on the physical LAN.
  - Remote clients route `0.0.0.0/0` (or specific subnets) through the gateway's tunnel.
  - NAT/masquerade outbound traffic from VPN to physical LAN (iptables MASQUERADE).

- [ ] 9.4 **Write tests:**
  - `tests/test_topologies.py`
    - Mesh: 3 clients, all can ping each other
    - Hub-and-spoke: spokes can ping hub; spokes CANNOT ping each other directly
    - Gateway: remote client can ping a device on gateway's physical LAN (mock)

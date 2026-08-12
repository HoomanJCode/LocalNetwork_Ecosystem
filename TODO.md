# TODO — LocalNetwork Ecosystem Implementation Plan

> **Status:** Planning complete. Implementation not started.
>
> Each task is a checkbox. Work through phases in order; items within a phase can
> be parallelized. Testing tasks are listed alongside the feature they test.

---

## Phase 0 — Project Skeleton & Tooling

- [ ] 0.1 Create directory structure (`server/`, `client/`, `proxy/`, `common/`, `common/web_static/`, `tests/`)
- [ ] 0.2 Create `requirements.txt` with `cryptography`, `bcrypt`, `python-dotenv`, `pyyaml`, `aiohttp`, `jinja2`
- [ ] 0.3 Create `README.md` with project overview, quickstart, and CLI usage
- [ ] 0.4 Create `common/__init__.py`, `server/__init__.py`, `client/__init__.py`, `proxy/__init__.py`, `tests/__init__.py`
- [ ] 0.5 Create shared UI foundation — `common/web_static/css/variables.css` with all design tokens (colors, typography, spacing, radius, shadows), `reset.css`, `layout.css` shell template, `components.css` (cards, tables, badges, forms, buttons, modals, toasts), `utilities.css`
- [ ] 0.6 Create shared JS — `common/web_static/js/sse.js` (EventSource helper), `dashboard.js` (counters, sparklines), `tables.js` (sortable/filterable), `ui.js` (toasts, modals, sidebar)
- [ ] 0.7 Create `tests/conftest.py` — shared fixtures (unused port finder, temp dirs, event loop)
- [ ] 0.8 Set up `pytest` as test runner; verify `python -m pytest tests/` runs (0 tests)

---

## Phase 1 — Common: Protocol Constants, Messages & Frame Definitions

- [ ] 1.1 **`common/constants.py`**
  - `SERVER_DEFAULT_HOST = "0.0.0.0"`
  - `SERVER_DEFAULT_PORT = 54000`
  - `HEARTBEAT_INTERVAL = 30` (seconds)
  - `HOLE_PUNCH_TIMEOUT = 5` (seconds)
  - `VIRTUAL_MTU = 1400`
  - `VIRTUAL_SUBNET = "25.0.0.0/8"`
  - `FRAME_VERSION = 0x01`
  - Frame type constants: `FRAME_DATA = 0x01`, `FRAME_PUNCH = 0x02`, `FRAME_KEEPALIVE = 0x03`, `FRAME_CLOSE = 0x04`
  - Control message type strings

- [ ] 1.2 **`common/messages.py`** — dataclasses for control-channel messages
  - `Message` base dataclass with `type: str` and `payload: dict`
  - `serialize(msg) -> bytes` — JSON encode + 4-byte BE length prefix
  - `deserialize(data: bytes) -> Message` — read length prefix, JSON decode
  - All message type dataclasses: `RegisterMessage`, `AuthChallenge`, `AuthResponse`, `CreateNetwork`, `JoinNetwork`, `PeerOnline`, `PeerOffline`, `PeerEndpoints`, `Heartbeat`, etc.

- [ ] 1.3 **`common/frame.py`** — data-plane frame struct
  - `FrameHeader` dataclass: version, type, payload_length, seq_num
  - `pack_frame(header, encrypted_payload, auth_tag) -> bytes`
  - `unpack_frame(data: bytes) -> tuple[FrameHeader, bytes, bytes]` (header, ciphertext, tag)
  - `FRAME_HEADER_SIZE = 8`
  - `GCM_TAG_SIZE = 16`

- [ ] 1.4 **Write tests:**
  - `tests/test_protocol.py` — serialize/deserialize round-trip for all message types
  - `tests/test_packet.py` — pack/unpack round-trip, invalid frame rejection, boundary cases

---

## Phase 2 — Cryptography Foundation

- [ ] 2.1 **`client/identity.py`**
  - `generate_identity() -> tuple[RSAPrivateKey, RSAPublicKey]` — RSA-2048
  - `save_identity(private_key, public_key, path="~/.localnetwork/")` — PEM format, `0600` perms
  - `load_identity(path="~/.localnetwork/") -> tuple[RSAPrivateKey, RSAPublicKey]`
  - `load_public_key(path) -> RSAPublicKey`
  - `sign_challenge(private_key, challenge: bytes) -> bytes` — SHA-256 + RSA sign
  - `verify_challenge(public_key, challenge: bytes, signature: bytes) -> bool`

- [ ] 2.2 **`client/encryption.py`**
  - `generate_ecdh_keypair() -> ec.EllipticCurvePrivateKey` — X25519 or P-256
  - `derive_session_key(our_private, peer_public) -> bytes` — ECDH + HKDF-SHA256 → 32-byte key
  - `CipherContext` class:
    - `__init__(session_key: bytes)`
    - `encrypt(plaintext: bytes, associated_data: bytes) -> tuple[bytes, bytes]` → (ciphertext, tag)
    - `decrypt(ciphertext: bytes, tag: bytes, associated_data: bytes) -> bytes` or raises
  - Uses AES-256-GCM with 12-byte random IV (prepended to ciphertext)

- [ ] 2.3 **Write tests:**
  - `tests/test_identity.py`
    - Generate → save → load round-trip
    - Sign → verify round-trip
    - Tampered signature rejected
    - Wrong public key fails verification
  - `tests/test_encryption.py`
    - Encrypt → decrypt round-trip
    - Wrong key fails decrypt
    - Tampered ciphertext fails (GCM auth)
    - Tampered associated data fails
    - ECDH key agreement: both sides derive same session key
    - Different key pairs produce different session keys

---

## Phase 3 — Mediation Server (Core)

- [ ] 3.1 **`server/config.py`**
  - `ServerConfig` dataclass: host, port, max_clients, heartbeat_timeout
  - Load from env vars / `.env` via `python-dotenv`
  - Default values

- [ ] 3.2 **`server/protocol.py`**
  - `parse_message(data: bytes) -> Message` — wraps `common.messages.deserialize`
  - `build_message(msg: Message) -> bytes` — wraps `common.messages.serialize`
  - Validation helpers: `validate_register`, `validate_create_network`, etc.

- [ ] 3.3 **`server/registry.py`**
  - `ClientRecord` dataclass: client_id, public_key_pem, public_endpoint, last_heartbeat, online, networks
  - `ClientRegistry` class:
    - `register(client_id, public_key_pem)`
    - `unregister(client_id)` — mark offline
    - `get(client_id) -> ClientRecord | None`
    - `get_online() -> list[ClientRecord]`
    - `update_endpoint(client_id, addr)`
    - `heartbeat(client_id)` — bump timestamp
    - `prune_stale(timeout)` — mark timed-out clients offline

- [ ] 3.4 **`server/network_manager.py`**
  - `NetworkRecord` dataclass: network_id, name, password_hash, owner_id, topology, members, hub_id, gateway_id
  - `NetworkManager` class:
    - `create(network_id, name, password, owner_id, topology)`
    - `join(network_id, client_id, password) -> bool` — verify bcrypt hash
    - `leave(network_id, client_id)`
    - `get_peers(network_id, client_id) -> list[ClientRecord]` — other online members
    - `list_for_client(client_id) -> list[NetworkRecord]`
    - `delete(network_id, requester_id)`

- [ ] 3.5 **`server/auth.py`**
  - `generate_challenge() -> bytes` — 32 random bytes
  - `create_auth_challenge(client_id) -> AuthChallenge`
  - `verify_auth_response(public_key_pem, challenge, signature) -> bool`
  - `AuthSession` — tracks pending challenges (TTL 60s)

- [ ] 3.6 **`server/main.py`** — asyncio TCP server
  - `MediationServer` class:
    - `start()` — bind, listen, accept loop
    - `handle_client(reader, writer)` — per-connection coroutine
      - Parse length-prefixed messages
      - Dispatch: REGISTER → AUTH_CHALLENGE → AUTH_RESPONSE → command loop
      - Command loop: CREATE_NETWORK, JOIN_NETWORK, LEAVE_NETWORK, LIST_NETWORKS,
        REQUEST_PEER_CONN, HEARTBEAT, etc.
    - `notify_peer_online(network_id, client_id)` — push to all other members
    - `notify_peer_offline(network_id, client_id)`
    - On disconnect: mark client offline, notify peers
  - Graceful shutdown (SIGINT/SIGTERM)

- [ ] 3.7 **Write tests:**
  - `tests/test_registry.py`
    - Register → get → found
    - Unregister → get → None
    - Heartbeat updates timestamp
    - Prune removes stale clients
  - `tests/test_network_manager.py`
    - Create network, join with correct password, join with wrong password
    - Leave network, get_peers excludes self
    - Delete by owner, delete by non-owner rejected

---

## Phase 4 — Server Relay Fallback

- [ ] 4.1 **`server/relay.py`**
  - `RelayForwarder` class:
    - In-memory mapping: `(src_client_id, dst_client_id) -> asyncio.Queue`
    - `register_relay_path(src_id, dst_id)` — allocate relay channel
    - `relay_frame(src_id, dst_id, raw_frame: bytes)` — queue frame for dst
    - `consume_frames(client_id) -> AsyncIterator[tuple[str, bytes]]` — yield (src_id, frame)
  - Integrated into `handle_client`: if a client has active relay paths, yield relayed frames
    on that client's control channel (multiplexed with control messages via a relay frame wrapper)

- [ ] 4.2 **Write tests:**
  - `tests/test_relay.py` — relay frame from A→B, consume on B, verify ordering

---

## Phase 5 — Client Core

- [ ] 5.1 **`client/platform_detection.py`** — Platform capability detection
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

- [ ] 5.2 **`client/config.py`**
  - `ClientConfig` dataclass: server_host, server_port, identity_dir, virtual_ip (optional)
  - Load from env / `.env` / CLI args
  - Store `PlatformCapabilities` reference
  - On load: if TUN requested but not available → warn, fall back to service-only mode

- [ ] 5.3 **`client/control_channel.py`**
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

- [ ] 5.4 **`client/main.py`** — CLI entry point
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

- [ ] 5.5 **Write tests:**
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

- [ ] 6.1 **`client/nat_traversal.py`**
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

- [ ] 6.2 **Write tests:**
  - `tests/test_nat_traversal.py`
    - Two local sockets on localhost: hole-punch succeeds
    - Timeout when no peer responds
    - PUNCH frame format validated
    - PUNCH_ACK transitions to CONNECTED

---

## Phase 7 — P2P Tunnel Manager

- [ ] 7.1 **`client/tunnel_manager.py`**
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

- [ ] 7.2 **`client/keepalive.py`**
  - `KeepAliveManager`:
    - Every 10s: send KEEPALIVE frame on each tunnel
    - Track `last_rx`; if > 30s since last rx, mark tunnel as suspect
    - If > 60s, close tunnel and notify server

- [ ] 7.3 **Write tests:**
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

---

## Phase 10 — User Experience & Ease of Use

- [ ] 10.1 **Setup wizard** — `client/setup_wizard.py`
  - Interactive terminal wizard on first launch (or when no config exists)
  - Four paths: Join network, Create network, Set up hub, Set up proxy
  - Each path asks max 3-4 simple questions in plain language
  - Auto-detects platform and shows only relevant options
  - Generates config file at the end so subsequent launches skip the wizard
  - `--skip-wizard` flag for automated deployments

- [ ] 10.2 **Plain language error messages** — `common/errors.py`
  - `UserFacingError` base class: title, plain_description, suggestions (list), severity
  - Error catalog: `ConnectionRefused`, `AuthFailed`, `TunnelFailed`, `PortInUse`, `ConfigInvalid`, `PlatformUnsupported`, `FirewallBlock`
  - Each error maps technical exceptions to user-friendly messages
  - Severity levels: info, success, warning, error, critical — with consistent icons
  - `format_for_terminal()` — colored output with suggestions
  - `format_for_web()` — JSON with title, description, suggestions array

- [ ] 10.3 **Status indicator** — `client/status_indicator.py`
  - System tray / menu bar icon (platform-specific: `pystray` on Windows/Linux, `rumps` on macOS)
  - Four states: green (all good), yellow (degraded), red (disconnected), gray (idle)
  - Hover tooltip: network name, peer count, connection type
  - Right-click menu: Open dashboard, Share service, Quit
  - Falls back to terminal status line if GUI not available (Termux, headless)

- [ ] 10.4 **Friendly logging** — `common/logging.py`
  - Dual output: machine logs (JSON) + human logs (colored terminal)
  - Human log format: `[time] [icon] Plain language message`
  - Hide technical details unless `--verbose`
  - Examples:
    - `12:34:56 ✅ Connected to "My Network" — 3 peers online`
    - `12:35:01 ⚠️ Direct connection to Alice failed — using relay (slightly slower)`
    - `12:35:10 ℹ️ Bob shared a new service: Minecraft (port 25565)`

- [ ] 10.5 **Web panel user-friendly labels**
  - All technical labels replaced with plain language throughout web templates
  - Consistent terminology: use the terminology map from DESIGN.md §11.2
  - Contextual help `?` icon on every page
  - "Having trouble?" link opens the troubleshooting wizard
  - Progressive disclosure: advanced options hidden behind "Show advanced" toggle

- [ ] 10.6 **One-click workflows in web panels**
  - "Share a service" button: pick type → enter port → done
  - "Connect to a service" button: browse available services → click → mapped
  - "Invite someone" button: generates shareable invite code
  - "Test my connection" button: runs diagnostics, shows simple pass/fail
  - "Fix connection issues" button: guided step-by-step troubleshooting

---

## Phase 11 — CLI & UX Polish

- [ ] 11.1 Rich status display: `localnetwork-cli status`
  - Show online/offline
  - List networks and peer count
  - Tunnel states (CONNECTED vs RELAY vs CONNECTING)
  - Virtual IP

- [ ] 11.2 Colored/logged output with levels: `--verbose`, `--quiet`
- [ ] 11.3 Daemon mode: `localnetwork-client --daemon` (fork to background, PID file)
- [ ] 11.4 `--version` flag
- [ ] 11.5 Configuration file: `~/.localnetwork/config.yaml` or `.env` format
- [ ] 11.6 Log to file: `~/.localnetwork/client.log`, `~/.localnetwork/server.log`

---

## Phase 12 — Integration & End-to-End Testing

- [ ] 12.1 **`tests/test_integration.py`**
  - **Setup:** Start a mediation server on localhost, spawn 3 client instances
    (each in its own asyncio task with separate identity and ports).
  - **Test: Full registration flow**
    - All 3 clients register and authenticate
  - **Test: Network create & join**
    - Client A creates "testnet"
    - Clients B and C join with password
    - Verify all get PEER_ONLINE notifications
  - **Test: Peer discovery**
    - Client A requests peer endpoints for B and C
    - Gets correct (IP, port) pairs
  - **Test: P2P data exchange**
    - Client A sends frame to Client B directly (mock TUN — inject raw data)
    - Client B decrypts and verifies
  - **Test: Relay fallback**
    - Simulate punch failure → data flows through relay
    - Verify encryption end-to-end (server can't read data)

- [ ] 12.2 **`tests/test_e2e.py`**
  - Requires root/admin for TUN setup (skip on CI without `--e2e` flag)
  - **Test: Ping between two virtual clients**
    1. Start mediation server
    2. Start Client A (with TUN, IP 25.1.0.1)
    3. Start Client B (with TUN, IP 25.1.0.2)
    4. Both join same network
    5. Wait for P2P tunnel establishment
    6. From host: `ping 25.1.0.2` (via A's TUN) → expect replies from B
  - **Test: TCP connection over VPN**
    - B runs `nc -l 25.1.0.2 9999`
    - A runs `echo hello | nc 25.1.0.2 9999`
    - B receives "hello"
  - **Test: Mesh broadcast**
    - 3 clients, A sends broadcast frame, B and C both receive

- [ ] 12.3 **Performance benchmarks:**
  - Measure latency: P2P vs relay vs direct LAN
  - Measure throughput: iperf3 over the VPN tunnel
  - Measure tunnel setup time (from PEER_ONLINE to CONNECTED)

---

## Phase 13 — Hardening & Edge Cases

- [ ] 13.1 **Error handling**
  - Server crash → clients reconnect and re-establish tunnels
  - Peer crash → tunnel timeout, clean up, notify server
  - Partial messages (TCP split) → buffer and reassemble
  - Invalid messages → log warning, don't crash

- [ ] 13.2 **Security hardening**
  - Rate limiting on auth attempts (prevent brute-force)
  - Max message size limit (prevent DoS)
  - Replay protection window (track recent sequence numbers, reject old)
  - Input validation on all message fields

- [ ] 13.3 **Concurrency & resource limits**
  - Max simultaneous tunnels per client
  - Socket buffer tuning
  - Memory limits on relay queues

- [ ] 13.4 **Graceful degradation**
  - If TUN interface can't be created → warn but still enable relay-only mode
  - If ECDH fails → fall back to RSA key exchange
  - If relay queue is full → backpressure to sender

---

## Phase 14 — Service Exposure (Port Forwarding)

- [ ] 13.1 **`server/network_manager.py`** — Service Registry extension
  - `ServiceRecord` dataclass: service_id, network_id, provider_id, name, protocol, local_host, local_port, created_at
  - `NetworkManager` new methods:
    - `expose_service(network_id, provider_id, name, protocol, local_host, local_port) -> service_id`
    - `unexpose_service(network_id, service_id)`
    - `list_services(network_id) -> list[ServiceRecord]`
    - `get_service(service_id) -> ServiceRecord | None`
  - On service exposed: push `SERVICE_ADDED` to all network members
  - On service removed: push `SERVICE_REMOVED` to all network members
  - On client disconnect: auto-remove all their exposed services

- [ ] 13.2 **New control messages** in `common/messages.py`
  - `ExposeService` / `UnexposeService` / `ServiceList` / `ServiceAdded` / `ServiceRemoved` / `MapService` / `UnmapService`

- [ ] 13.3 **New frame type** in `common/frame.py`
  - `FRAME_FORWARDED_STREAM = 0x05` — extended header includes `stream_id` (UUID, 16B) and `service_id` (UUID, 16B) in the associated data field

- [ ] 13.4 **`client/service_exposure.py`** — Expose local services
  - `ServiceExposureManager` class:
    - `expose(name, protocol, local_host, local_port) -> service_id` — registers with server
    - `unexpose(service_id)` — unregisters from server
    - `handle_incoming_stream(service_id, stream_id, peer_id)` — accepts new stream from peer:
      1. Opens TCP connection (or UDP socket) to `local_host:local_port`
      2. Reads from local service, writes encrypted frames to P2P tunnel
      3. Reads from P2P tunnel, writes to local service
      4. Stream lifecycle: open → active → closed (either side disconnects)
    - `list_exposed() -> list`
  - TCP handling: `asyncio.open_connection()` + `asyncio.gather(forward(), reverse())` per stream
  - UDP handling: single UDP socket per service, track `(peer_addr, src_port)` → forward back
  - Stream multiplexing: `stream_id` disambiguates multiple simultaneous connections to the same service

- [ ] 13.5 **`client/service_consumer.py`** — Map remote services to local ports
  - `ServiceConsumer` class:
    - `map_service(service_id, local_port=None, strategy="auto") -> local_port`
      1. Pick local port based on strategy (same-port / auto / manual)
      2. Create local TCP or UDP listener on `127.0.0.1:local_port`
      3. Notify server of mapping (for tracking)
    - `unmap_service(service_id)` — close local listener
    - `handle_local_connection(reader, writer, service_id, provider_id)`:
      1. Generate unique `stream_id`
      2. Read from local client, wrap as FORWARDED_STREAM frames, send through tunnel
      3. Read from tunnel, unwrap, write to local client
    - `list_mapped() -> list` — show what's mapped on which local port
  - TCP: `asyncio.start_server()` on `127.0.0.1:local_port`
  - UDP: `asyncio.DatagramTransport` for local UDP socket
  - Auto-reconnect if tunnel drops: re-establish stream when tunnel reconnects

- [ ] 13.6 **Integration with `tunnel_manager.py`**
  - `TunnelManager.send_forwarded_stream(tunnel, service_id, stream_id, data)` — convenience wrapper
  - `TunnelManager` dispatches incoming `FRAME_FORWARDED_STREAM` to `ServiceExposureManager.handle_incoming_stream()` or `ServiceConsumer` depending on direction
  - Frame routing: each FORWARDED_STREAM frame carries `service_id` + `stream_id` in the associated data portion of the encrypted payload

- [ ] 13.7 **Write tests:**
  - `tests/test_service_exposure.py`
    - Expose a TCP service → service appears in network service list
    - Unexpose → service removed
    - Consumer maps service → local listener created on desired port
    - Consumer unmaps → local listener closed
    - TCP stream: consumer connects to local port → data reaches service host → response returns
    - Multiple concurrent streams to same service (e.g., 3 clients all connecting)
    - Stream close from consumer side → cleanup on both ends
    - Stream close from provider side → consumer local listener gets connection reset
    - UDP datagram: consumer sends → received by service host → reply returned
    - Service auto-removed when provider disconnects from network
    - Auto port strategy picks a free port
    - Same-port strategy falls back to auto when port is occupied

---

## Phase 15 — Server Web Admin Panel

- [ ] 14.1 **`server/web/app.py`** — aiohttp web application setup
  - Create `aiohttp.web.Application` with routes registered
  - Start HTTP server on configurable port (default 54001) alongside the mediation server
  - Share references to `ClientRegistry`, `NetworkManager`, `RelayForwarder` for data access

- [ ] 14.2 **`server/web/auth.py`** — Admin authentication
  - Admin credentials: `LNSERVER_ADMIN_USER` / `LNSERVER_ADMIN_PASS` env vars
  - On first launch with no env vars: generate random password, print to console
  - Session management with secure cookies (`aiohttp_session`)
  - Login/logout routes
  - Auth middleware that protects all panel routes

- [ ] 14.3 **Server web routes:**
  - **Dashboard** (`/`): uptime, total/online clients, network count, relay stats, CPU/mem
  - **Clients** (`/clients`): table of all clients with status, join date, networks. Detail view per client
  - **Networks** (`/networks`): table of all networks with member count, owner, topology. Detail view per network
  - **Relay** (`/relay`): active relay paths, bytes relayed, queue depths
  - **Configuration** (`/config`): form to edit server settings, save to config
  - **Logs** (`/logs`): live log tail via SSE
  - **Access Control** (`/access`): ban/unban client IDs and IP ranges
  - **API endpoints:** JSON API for each page (used by JS for live updates via SSE/polling)

- [ ] 14.4 **Server HTML templates** — Jinja2
  - `base.html`: shared layout (sidebar nav, header, footer, CSS/JS includes)
  - One template per page (dashboard, clients, client_detail, networks, network_detail, relay, config, logs, access)
  - Dark theme, responsive design

- [ ] 14.5 **Server static assets**
  - `admin.css`: dark theme, table styles, status badges (online green/offline red), form styles, responsive
  - `dashboard.js`: SSE client, auto-refresh counters, chart for relay throughput (simple canvas or pure CSS)
  - `sse.js`: reusable SSE helper for live log tail and data updates

- [ ] 14.6 **Write tests:**
  - `tests/test_server_web.py`
    - Login with correct credentials → session cookie set
    - Login with wrong password → 401
    - Protected routes redirect to login when unauthenticated
    - Dashboard returns 200 with expected data
    - Clients page lists registered clients
    - Networks page lists networks
    - Config POST updates settings
    - Log SSE endpoint streams log lines
    - Ban client → client can't reconnect

---

## Phase 16 — Client Web Admin Panel

- [ ] 15.1 **`client/web/app.py`** — aiohttp web application setup
  - Create `aiohttp.web.Application` bound to `127.0.0.1` only (security)
  - Start HTTP server on configurable port (default 54002) alongside the client daemon
  - Share references to `ControlChannel`, `TunnelManager`, `NatTraversal` for data access

- [ ] 15.2 **Client web routes:**
  - **Dashboard** (`/`): server connection status, virtual IP, uptime, active tunnels, bytes tx/rx, NAT type
  - **Networks** (`/networks`): list joined networks, create/join/leave actions
  - **Peers** (`/peers`): table of connected peers with state, latency, throughput. Per-peer detail view
  - **Configuration** (`/config`): form to edit client settings (server addr, identity dir, log level, TUN name)
  - **Logs** (`/logs`): live log tail via SSE
  - **NAT Diagnostics** (`/nat-diag`): run NAT type detection, show results, P2P compatibility matrix
  - **API endpoints:** JSON API for each page

- [ ] 15.3 **Client HTML templates** — Jinja2
  - `base.html`: shared layout (sidebar nav, header, footer, CSS/JS includes)
  - One template per page (dashboard, networks, peers, peer_detail, config, logs, nat_diag)
  - Dark theme matching the server panel, responsive design

- [ ] 15.4 **Client static assets**
  - `admin.css`: dark theme, status badges, table styles, form styles, responsive
  - `dashboard.js`: SSE client for live counters, tunnel state indicators
  - `sse.js`: reusable SSE helper

- [ ] 15.5 **Write tests:**
  - `tests/test_client_web.py`
    - Dashboard returns 200 with connection status
    - Networks page lists joined networks
    - Create network via POST → appears in list
    - Join network via POST with password
    - Peers page shows connected peers
    - Config POST updates settings
    - Log SSE endpoint streams log lines
    - NAT diag page returns results
    - Panel only accessible from localhost (reject external IPs)

---

## Phase 17 — Reverse Proxy: Core Architecture

- [ ] 16.1 **`proxy/config.py`** — Configuration loader
  - `ProxyConfig` dataclass mirroring the YAML schema:
    workers, worker_connections, http blocks, upstream blocks, ssl blocks,
    cache settings, rate_limiting, access_control, compression, logging, admin port
  - `load_config(path: str) -> ProxyConfig` — parse YAML/JSON, validate
  - Sensible defaults for all optional fields

- [ ] 16.2 **`proxy/master.py`** — Master process
  - `MasterProcess` class:
    - `start()` — read config, bind listen sockets, spawn N workers
    - `reload()` — handle SIGHUP: read new config, spawn new workers, gracefully kill old
    - `shutdown()` — handle SIGINT/SIGTERM: stop all workers, close sockets
    - Track worker PIDs, restart any that crash unexpectedly
  - Use `os.fork()` or `multiprocessing` for worker processes
  - `SO_REUSEPORT` on listen sockets so kernel distributes across workers

- [ ] 16.3 **`proxy/worker.py`** — Worker event loop
  - `WorkerProcess` class:
    - `start(listen_sockets)` — create asyncio event loop, accept connections
    - `accept_loop()` — accept new connections, spawn `Connection` coroutines
    - Track active connection count for status reporting
    - Graceful shutdown: stop accepting, drain existing connections
  - Optional `uvloop` integration for performance

- [ ] 16.4 **`proxy/main.py`** — CLI entry point
  - `localnetwork-proxy [--config PATH] [--workers N]` command
  - Signal handlers for SIGHUP (reload), SIGINT/SIGTERM (shutdown)
  - `--version` flag
  - `--validate-config` flag: parse and validate config, exit

- [ ] 15.5 **Write tests:**
  - `tests/test_proxy_config.py`
    - Load minimal valid config → all defaults filled
    - Load full config → all values parsed correctly
    - Invalid YAML → clear error message
    - Missing required field → validation error
    - `--validate-config` exits 0 on valid, non-zero on invalid

---

## Phase 18 — Reverse Proxy: Connection Handling & HTTP Processing

- [ ] 17.1 **`proxy/connection.py`** — HTTP connection state machine
  - `Connection` class (per-client coroutine):
    - `handle()` — state machine: READ_REQUEST → MATCH_ROUTE → CONNECT_UPSTREAM → FORWARD → RESPOND
    - `read_request()` — async parse HTTP request line + headers
    - `read_body()` — buffer body (memory up to 64KB, then temp file)
    - `match_route()` — match request path against configured locations
    - HTTP/1.1 persistent connections (Connection: keep-alive)
    - Request timeout (close idle connections after configurable timeout)
  - HTTP request parser (no external library — hand-rolled for control):
    - Parse method, path, HTTP version from request line
    - Parse headers into dict (case-insensitive keys)
    - Handle chunked Transfer-Encoding
  - HTTP response writer:
    - Status line + headers + body
    - Chunked transfer encoding for upstream streaming

- [ ] 17.2 **`proxy/upstream.py`** — Upstream backend management
  - `UpstreamPool` class:
    - `get_server(algorithm)` — select backend using configured load balancer
    - `connect(server)` — async TCP connect + optional TLS handshake
    - Connection pooling: keep-alive connections reused across requests
    - `release(server, connection)` — return connection to pool or close
    - Track per-server: active connections, failure count, last failure time
  - `UpstreamServer` dataclass: host, port, weight, max_conns, backup, down, state (up/down/unavailable)

- [ ] 17.3 **Header management in `connection.py`:**
  - Set `Host` header to upstream target
  - Add `X-Real-IP` with original client address
  - Append to `X-Forwarded-For`
  - Set `X-Forwarded-Proto` based on original scheme
  - Strip hop-by-hop headers: Connection, Keep-Alive, Transfer-Encoding, TE, Trailer

- [ ] 17.4 **Write tests:**
  - `tests/test_proxy_integration.py` (part 1)
    - Start proxy with a single backend → HTTP request proxied correctly
    - Response from backend reaches client unmodified
    - `X-Real-IP` and `X-Forwarded-For` headers set
    - 502 returned when backend is unreachable
    - Static file served from `root` path
    - Multiple locations route to different upstreams

---

## Phase 19 — Reverse Proxy: Load Balancing & Health Checks

- [ ] 18.1 **`proxy/load_balancer.py`**
  - `LoadBalancer` abstract base class with `select(servers: list) -> UpstreamServer`
  - `RoundRobinBalancer`: stateful index, weighted with `itertools.cycle` approach
  - `LeastConnBalancer`: select server with `min(active_connections / weight)`
  - `IpHashBalancer`: `hash(client_ip) % total_weight` → deterministic server
  - `RandomBalancer`: `random.choices(servers, weights=[s.weight])`
  - Respect `backup` and `down` flags

- [ ] 18.2 **`proxy/health_check.py`** — Passive health checks
  - `HealthMonitor` class:
    - On upstream connection failure: increment failure counter for that server
    - After N consecutive failures within a time window → mark `unavailable`
    - After `fail_timeout` seconds → mark as retrying, send single probe request
    - On probe success → mark `available`, reset counters
    - Configurable: `max_failures`, `fail_timeout` per upstream/server
  - `check_all()` — periodic passive sweep that detects timed-out unavailable servers

- [ ] 18.3 **Write tests:**
  - `tests/test_load_balancer.py`
    - Round robin: N requests cycle through servers in order
    - Weighted round robin: heavier servers get proportionally more requests
    - Least connections: request goes to server with fewest active
    - IP hash: same IP always maps to same server
    - Backup server: used when all primaries are down
    - Down server: never selected
  - `tests/test_health_check.py`
    - Consecutive failures → server marked unavailable
    - Single failure within window → not enough to mark down
    - After fail_timeout → server retried
    - Successful probe → server available again

---

## Phase 20 — Reverse Proxy: SSL, Caching, Compression & Security

- [ ] 19.1 **`proxy/ssl/terminator.py`**
  - `SSLContextManager`:
    - Load certificate + private key from PEM files
    - Create `ssl.SSLContext` with configured protocols and ciphers
    - SNI callback: select correct cert based on `server_name`
    - OCSP stapling support (load OCSP response file)
    - Session ticket keys for stateless resumption
  - Wrap accepted sockets with `ssl_context.wrap_socket()`
  - Async SSL handshake integrated with event loop

- [ ] 18.2 **`proxy/cache/`** — Response caching
  - `proxy/cache/storage.py`:
    - On-disk storage: `{cache_path}/{two-char}/{full-key-hash}`
    - Each entry: header block (status, headers, TTL) + body
    - LRU eviction: track access times, evict oldest when over `max_size`
    - `store(key, status, headers, body, ttl)`
    - `retrieve(key) -> (status, headers, body) | None`
    - `purge(key_pattern)` — delete matching entries
  - `proxy/cache/manager.py`:
    - `CacheManager` class:
      - `get_cache_key(method, path, vary_headers) -> str` — SHA-256 based key
      - `is_cacheable(status, response_headers) -> bool` — respect Cache-Control
      - `is_stale(entry) -> bool` — check TTL
      - `is_fresh(entry) -> bool` — not yet expired
  - `proxy/cache/metadata.py`:
    - In-memory `dict[key -> (filepath, size, created, last_access, ttl)]`
    - Fast lookup without disk I/O
    - Rebuilt on startup by scanning cache directory

- [ ] 18.3 **`proxy/compression.py`**
  - `GzipCompressor`:
    - `should_compress(content_type, accept_encoding) -> bool`
    - `compress(data: bytes, level: int) -> bytes` — gzip via `zlib`
    - Minimum size threshold (default 256 bytes) — don't compress tiny responses
    - Add `Content-Encoding: gzip` and adjust `Content-Length`
    - Remove `Content-Length` if chunked (when streaming)

- [ ] 18.4 **`proxy/security/rate_limiter.py`**
  - `RateLimiter` class:
    - Sliding window counters per key (typically client IP)
    - `allow(key) -> bool` — check against rate + burst, decrement window
    - On exceed: return HTTP 429 with `Retry-After` header
    - Configurable per location: rate (r/s), burst, zone name
    - In-memory storage (per-worker; approximate enforcement across workers)

- [ ] 18.5 **`proxy/security/access.py`**
  - `AccessControl` class:
    - `check(client_ip) -> bool` — evaluate allow/deny rules in order
    - CIDR matching for IPv4 and IPv6
    - First matching rule wins
    - On deny: return HTTP 403

- [ ] 18.6 **`proxy/security/auth.py`**
  - `BasicAuth` class:
    - `check(authorization_header) -> bool`
    - Load htpasswd-style file (bcrypt hashed)
    - Return 401 with `WWW-Authenticate: Basic realm="..."` on failure
    - Configurable per location

- [ ] 18.7 **Write tests:**
  - SSL: Client connects via HTTPS → request proxied to HTTP backend
  - Cache: First request → MISS, cache populated; second request → HIT
  - Cache: Expired entry → stale served while revalidating
  - Cache: Cache-Control: no-cache → bypass cache
  - Compression: `Accept-Encoding: gzip` → response compressed
  - Compression: No Accept-Encoding header → response uncompressed
  - Rate limiter: Within limit → 200; exceed → 429
  - Rate limiter: Burst allows short spikes
  - Access: Denied IP → 403; allowed → proxied
  - Auth: No credentials → 401; wrong → 401; correct → proxied

---

## Phase 21 — Reverse Proxy: Stream Proxy, Logging & Status

- [ ] 20.1 **`proxy/stream_proxy.py`** — TCP/UDP stream proxying
  - `StreamProxy` class:
    - `handle_tcp(client_reader, client_writer, upstream_server)`:
      connect to upstream TCP, bidirectional pipe (`asyncio.gather` read+write)
    - `handle_udp(client_data, client_addr, upstream_server)`:
      forward datagram, cache upstream response for reply
    - Supports all load balancing algorithms
    - No HTTP-level processing applies

- [ ] 19.2 **`proxy/logging.py`**
  - `AccessLogger`:
    - `log(request, response, upstream, duration_ms)` — write one line
    - Formats: `combined` (Apache style), `json` (structured), custom template
    - Async writes via dedicated writer coroutine + `asyncio.Queue` (never blocks workers)
    - Log rotation: daily rotation with configurable retention count
  - `ErrorLogger`:
    - Severity-filtered: `log(level, message, **context)`
    - Worker ID and timestamp auto-prepended
    - Output: stdout, stderr, or file

- [ ] 19.3 **`proxy/status.py`** — Stub status endpoint
  - `StatusCollector` class:
    - Track: active connections, accepted total, handled total, total requests
    - Per-upstream server stats: state, active connections, failure count
    - `get_stats() -> dict` — return JSON-serializable stats snapshot
  - Exposed as `GET /proxy-status` on admin port

- [ ] 19.4 **Write tests:**
  - Stream proxy: TCP echo server behind proxy → client sends data, gets it back
  - Stream proxy: upstream unreachable → client connection closed cleanly
  - Logging: request logged in combined format with correct fields
  - Logging: JSON format produces valid JSON
  - Status endpoint: returns valid stats with expected keys

---

## Phase 22 — Reverse Proxy: Web Admin Panel

- [ ] 21.1 **`proxy/web/app.py`** — aiohttp admin panel
  - Bind to configurable port (default 54010)
  - Share references to `UpstreamPool`, `HealthMonitor`, `CacheManager`, `RateLimiter`
  - Optional basic auth for panel access

- [ ] 20.2 **Proxy admin routes:**
  - **Dashboard** (`/`): uptime, active connections, requests/sec, upstream health summary
  - **Upstreams** (`/upstreams`): per-upstream table with server states, active conns, failures
  - **Cache** (`/cache`): cache size, hit/miss ratio, purge controls
  - **Configuration** (`/config`): view current config (read-only from file)
  - **Logs** (`/logs`): live access/error log tail via SSE
  - **API endpoints:** JSON API for each page

- [ ] 20.3 **Proxy HTML templates** — Jinja2
  - `base.html`, `dashboard.html`, `upstream.html`, `cache.html`, `config.html`, `logs.html`
  - Dark theme consistent with server/client panels

- [ ] 20.4 **Proxy static assets**
  - `admin.css`: dark theme, upstream status indicators (green up / red down / yellow degraded)
  - `dashboard.js`: SSE live counters, upstream health grid
  - `sse.js`: reusable SSE helper

- [ ] 20.5 **Write tests:**
  - Dashboard returns 200 with expected metrics
  - Upstreams page shows all configured backends with states
  - Cache page shows hit/miss stats
  - Log SSE streams log lines

---

## Phase 23 — Documentation & README

- [ ] 22.1 Write comprehensive `README.md`
  - Project description and motivation
  - Architecture diagram (ASCII or link to image)
  - Requirements: Python 3.10+, OS-specific TUN setup
  - Quickstart: 5-minute guide for virtual LAN and reverse proxy
  - Full CLI reference (server, client, proxy, management CLI)
  - Configuration reference (YAML for proxy)
  - Troubleshooting section

- [ ] 21.2 Add docstrings to all public APIs
- [ ] 21.3 Create `CONTRIBUTING.md`

---

## Summary

| Phase | Description                        | Est. Effort |
|-------|------------------------------------|-------------|
| 0     | Skeleton & tooling                 | Small       |
| 1     | Protocol & frame definitions       | Small       |
| 2     | Cryptography                       | Medium      |
| 3     | Mediation server core              | Large       |
| 4     | Server relay fallback              | Medium      |
| 5     | Client core                        | Large       |
| 6     | NAT traversal                      | Medium      |
| 7     | P2P tunnel manager                 | Large       |
| 8     | TUN virtual interface              | Large       |
| 9     | Network topologies                 | Medium      |
| 10    | User experience & ease of use      | Large       |
| 11    | CLI & UX polish                    | Small       |
| 12    | Integration & E2E testing          | Large       |
| 13    | Hardening                          | Medium      |
| 14    | Service exposure (port forwarding) | Large       |
| 15    | Server web admin panel             | Large       |
| 16    | Client web admin panel             | Large       |
| 17    | Reverse proxy: core architecture   | Large       |
| 18    | Reverse proxy: connections & HTTP  | Large       |
| 19    | Reverse proxy: LB & health checks  | Medium      |
| 20    | Reverse proxy: SSL/cache/compress  | Large       |
| 21    | Reverse proxy: stream/log/status   | Medium      |
| 22    | Reverse proxy: web admin panel     | Medium      |
| 23    | Documentation                      | Small       |

**Total estimated phases:** 23  
**MVP scope (demo-worthy):** Phases 0–8 + 11 (basic mesh, two-client E2E)

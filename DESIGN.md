# LocalNetwork Ecosystem — Design Document

## Overview

A Python-based virtual LAN system that allows geographically dispersed computers
to communicate as if they were on the same physical LAN, with zero-configuration
NAT traversal, strong encryption, and flexible network topologies.

---

## 1. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     MEDIATION SERVER                            │
│  ┌──────────┐  ┌──────────────┐  ┌────────────┐  ┌───────────┐ │
│  │ Client   │  │ Network      │  │ Public Key │  │ Relay     │ │
│  │ Registry │  │ Manager      │  │ Directory  │  │ Forwarder │ │
│  └──────────┘  └──────────────┘  └────────────┘  └───────────┘ │
│                        │ TCP (control channel)                  │
└────────────────────────┼────────────────────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
   ┌────▼────┐      ┌────▼────┐      ┌────▼────┐
   │ CLIENT A│◄────►│ CLIENT B│◄────►│ CLIENT C│
   └─────────┘ UDP  └─────────┘ UDP  └─────────┘
        P2P tunnels (AES-256 encrypted)
```

### Key Principles
- **Server-assisted rendezvous, direct P2P data:** The server orchestrates connections but
  data flows directly between peers.
- **UDP transport for data:** Avoids "TCP meltdown" by tunneling over UDP.
- **Zero-trust encryption:** The server never possesses private keys and cannot decrypt
  peer traffic.

---

## 2. Protocol Stack

```
┌──────────────────────────────────┐
│   Application (games, SSH, etc.) │
├──────────────────────────────────┤
│   Virtual TUN/TAP Interface      │  ← 25.0.0.0/8 IP range
├──────────────────────────────────┤
│   AES-256-GCM Encryption         │
├──────────────────────────────────┤
│   Frame encapsulation + MSS clamp│
├──────────────────────────────────┤
│   P2P Tunnel (UDP)               │  ← NAT-traversed
├──────────────────────────────────┤
│   Physical NIC (IP)              │
└──────────────────────────────────┘
```

### 2.1 Control Protocol (TCP to Server)

All messages are JSON, length-prefixed (4-byte big-endian length header).

| Message Type          | Direction     | Purpose                                   |
|-----------------------|---------------|-------------------------------------------|
| `REGISTER`            | Client→Server | Register identity + public key             |
| `AUTH_CHALLENGE`      | Server→Client | Server sends nonce to sign                 |
| `AUTH_RESPONSE`       | Client→Server | Client returns signed nonce                |
| `AUTH_OK` / `AUTH_FAIL` | Server→Client | Authentication result                    |
| `CREATE_NETWORK`      | Client→Server | Create a new virtual network               |
| `JOIN_NETWORK`        | Client→Server | Join an existing network                   |
| `LEAVE_NETWORK`       | Client→Server | Leave a network                            |
| `LIST_NETWORKS`       | Client→Server | List networks client belongs to            |
| `NETWORK_PEERS`       | Server→Client | Current peer list with endpoints            |
| `PEER_ONLINE`         | Server→Client | A peer just came online                    |
| `PEER_OFFLINE`        | Server→Client | A peer just went offline                   |
| `REQUEST_PEER_CONN`   | Client→Server | Request peer connection (mediation)        |
| `PEER_ENDPOINTS`      | Server→Client | Peer's public endpoint(s) for hole-punch   |
| `RELAY_REQUEST`       | Client→Server | Fallback: request relay for a peer         |
| `HEARTBEAT`           | Client→Server | Keep-alive ping                            |
| `HEARTBEAT_ACK`       | Server→Client | Keep-alive ack                             |

### 2.2 Data Protocol (UDP P2P / Relay)

All data frames are binary, with a compact header:

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
| Version(1B)  |  Type(1B)    |         Payload Length (2B)      |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                        Sequence Number (4B)                    |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                                                               |
|            Encrypted Payload (AES-256-GCM)                    |
|        [Inner IP Packet or Keep-Alive or Control]              |
+                                              ...             ...
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                     GCM Auth Tag (16B)                         |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

Frame Types:
- `0x01` — DATA: Encapsulated IP packet
- `0x02` — PUNCH: UDP hole-punching probe
- `0x03` — KEEPALIVE: Connection keep-alive
- `0x04` — CLOSE: Graceful tunnel close
- `0x05` — FORWARDED_STREAM: TCP stream data for a forwarded service (carries service_id + stream_id in associated data)

### 2.3 MSS Clamping

TCP MSS is clamped on the virtual adapter to leave room for encryption overhead.
Our TUN interface will advertise MTU = 1400 (instead of 1500) to leave headroom for
the 16-byte GCM auth tag + 8-byte frame header.

---

## 3. Server Component Design

### 3.1 `MediationServer` (asyncio TCP)

```
server/
├── __init__.py
├── main.py              # Entry point, arg parsing, server startup
├── config.py            # Server configuration
├── registry.py          # ClientRegistry — tracks online clients
├── network_manager.py   # NetworkManager — networks, memberships
├── auth.py              # Authentication challenge/response
├── relay.py             # RelayForwarder — fallback data relay
└── protocol.py          # Message parsing / validation
```

**ClientRegistry** stores per-client:
- `client_id` (UUID)
- `public_key` (RSA PEM)
- `public_endpoint` (IP, port as seen by server)
- `last_heartbeat` timestamp
- `online` status
- `networks` set

**NetworkManager** stores per-network:
- `network_id` (UUID)
- `name`, `password_hash`
- `owner_client_id`
- `members` → set of client_ids
- `topology` (mesh | hub_and_spoke | gateway)
- `hub_client_id` (for hub-and-spoke)
- `gateway_client_id` (for gateway mode)

### 3.2 Relay Forwarder

When P2P hole-punching fails, the server relays encrypted data between peers.
The server **cannot decrypt** the payload (it only sees the outer frame header).

---

## 4. Client Component Design

### 4.1 `VirtualLanClient`

```
client/
├── __init__.py
├── main.py              # Entry point, CLI, daemon mode
├── config.py            # Client configuration
├── platform_detection.py # Detect OS, root, TUN, Termux capabilities
├── identity.py          # RSA key generation, storage, loading
├── control_channel.py   # TCP connection to mediation server
├── tunnel_manager.py    # Manages P2P tunnels to peers
├── nat_traversal.py     # UDP hole-punching logic
├── encryption.py        # AES-256-GCM encrypt/decrypt
├── tun_interface.py     # TUN/TAP virtual adapter (platform-specific)
├── packet.py            # Frame serialization/deserialization
├── keepalive.py         # Peer keep-alive probing
└── relay_client.py      # Relay-fallback data path
```

### 4.2 Identity Module (`identity.py`)

- On first run: generate RSA-2048 key pair
- Store private key in `~/.localnetwork/identity.pem` (0600 permissions)
- Store public key in `~/.localnetwork/identity.pub`
- Server only receives the public key
- Private key used to sign auth challenges

### 4.3 Control Channel (`control_channel.py`)

- Persistent TCP connection to mediation server
- Handles: register → auth → join/create network → peer discovery
- Receives push events: PEER_ONLINE, PEER_OFFLINE
- Heartbeat every 30s

### 4.4 NAT Traversal (`nat_traversal.py`)

The UDP hole-punching state machine:

```
  IDLE ──► PUNCHING ──► CONNECTED ──► CLOSED
               │              │
               └── FAILED ────┘
```

**Hole-punch process:**
1. Client A asks server for Client B's endpoints
2. Both clients receive each other's public IP:port
3. Both simultaneously send PUNCH frames to each other
4. First to receive a PUNCH from the other replies with PUNCH_ACK
5. Tunnel transitions to CONNECTED

**Fallback:** After 5s timeout without success → request RELAY from server.

### 4.5 TUN Interface (`tun_interface.py`)

Platform abstraction:
- **Linux:** `/dev/net/tun` via `fcntl` + `TUNSETIFF`
- **Windows:** `wintun` adapter or `tap-windows` driver (use `pywintun` or
  manual setup)
- **macOS:** `utun` interface via socket `SYSPROTO_CONTROL`

Virtual adapter:
- IP: from `25.0.0.0/8` range (assigned by server)
- MTU: 1400 (MSS clamped)
- Netmask: `255.0.0.0`

### 4.6 Tunnel Manager (`tunnel_manager.py`)

Manages a pool of `PeerTunnel` objects:

```python
@dataclass
class PeerTunnel:
    peer_id: str
    peer_ip: str          # Virtual LAN IP
    state: TunnelState    # CONNECTING | CONNECTED | RELAY | CLOSED
    socket: socket.socket  # UDP socket for this peer
    cipher: CipherContext
    last_rx: float        # Last packet received timestamp
    tx_seq: int           # Outgoing sequence number
    rx_seq: int           # Last received sequence number
```

### 4.7 Service Exposure (Port Forwarding)

A lightweight alternative to the full TUN-based virtual LAN. Clients can expose
specific local TCP/UDP services to other network members without any system
network configuration changes — no TUN interface, no root, no route tables.

The daemon on each client acts as a local TCP/UDP proxy, mapping virtual services
to local ports and tunneling traffic through existing P2P connections.

#### Architecture

```
┌─ Client1 (service host) ─────────────────────────────┐
│                                                       │
│  ┌──────────┐    ┌─────────────┐    ┌──────────────┐ │
│  │ Minecraft│───►│Forward Rule │───►│ Tunnel Manager│ │
│  │ :25565   │    │minecraft →  │    │  → Client2    │ │
│  │          │    │localhost:   │    │  → Client3    │ │
│  │          │    │25565 (TCP)  │    │               │ │
│  └──────────┘    └─────────────┘    └───────┬───────┘ │
│                                              │ P2P    │
└──────────────────────────────────────────────┼────────┘
                                               │
┌─ Client2 (consumer) ─────────────────────────┼────────┐
│                                              │        │
│  ┌──────────┐    ┌─────────────┐    ┌───────▼───────┐ │
│  │ Minecraft│◄───│Local Listener│◄───│ Tunnel Manager│ │
│  │ client   │    │127.0.0.1:   │    │  ← Client1    │ │
│  │          │    │25565 (TCP)  │    │               │ │
│  └──────────┘    └─────────────┘    └───────────────┘ │
│                                                       │
└───────────────────────────────────────────────────────┘
```

#### How It Works (Step by Step)

1. **Client1 registers a service** with the mediation server:
   `{name: "minecraft", protocol: "tcp", local_host: "127.0.0.1", local_port: 25565}`

2. **Server stores the service** in the network's service registry and notifies all peers.

3. **Client2 lists available services** and maps one:
   `map service "minecraft" → listen on 127.0.0.1:25565`

4. **Client2's daemon creates a local TCP listener** on `127.0.0.1:25565`.

5. **Client2's app connects to `127.0.0.1:25565`** — it thinks it's talking to a local server.

6. **The daemon intercepts the connection**, wraps the TCP stream as data frames,
   and sends them through the P2P tunnel (or relay) to Client1.

7. **Client1's daemon receives the frames**, connects to `localhost:25565`,
   and bidirectionally pipes data between the P2P tunnel and the local service.

#### Protocol Extensions

New control messages for service discovery:

| Message Type | Direction | Purpose |
|-------------|-----------|---------|
| `EXPOSE_SERVICE` | Client→Server | Register a service: name, protocol, local_host, local_port |
| `UNEXPOSE_SERVICE` | Client→Server | Remove a service registration |
| `SERVICE_LIST` | Server→Client | Full list of services available on the network |
| `SERVICE_ADDED` | Server→Client | Push: a new service was exposed |
| `SERVICE_REMOVED` | Server→Client | Push: a service was removed |
| `MAP_SERVICE` | Client→Server | Client wants to consume a service (reserves virtual mapping) |
| `UNMAP_SERVICE` | Client→Server | Client stops consuming a service |

New data frame type:
- `0x05` — FORWARDED_STREAM: TCP stream data for a forwarded service (identified by service_id in the associated data)

#### Service Registry (Server-side)

Per-network service table:

```python
@dataclass
class ServiceRecord:
    service_id: str       # UUID
    network_id: str
    provider_id: str      # client_id of the host
    name: str             # human-readable: "minecraft", "web", "ssh"
    protocol: str         # "tcp" or "udp"
    local_host: str       # where the service actually runs on the host
    local_port: int
    created_at: float
```

#### Port Mapping Strategy

When Client2 maps a service, the daemon needs a free local port to listen on.
Three strategies (configurable):

| Strategy | Description | Example |
|----------|-------------|---------|
| **Same port** | Use the same port number as the remote service | Remote :25565 → local :25565 |
| **Auto** | Pick the next available port starting from a range | Remote :25565 → local :50001 |
| **Manual** | User explicitly chooses the local port | Remote :25565 → local :25000 |

"Same port" only works if the port is free locally. If occupied, fall back to Auto.

#### Multi-Connection Handling

For TCP services, multiple clients may connect simultaneously (e.g., multiple
players on a Minecraft server). Each connection spawns a separate stream:

```
Client2 connection 1 → stream_id: abc-1 ─┐
Client2 connection 2 → stream_id: abc-2 ─┤──► P2P Tunnel ──► Client1 daemon
Client3 connection 1 → stream_id: def-1 ─┘     │              │
                                           ┌────▼────┐    ┌───▼────┐
                                           │multiplex │    │connect │
                                           │over UDP  │    │to each │
                                           │          │    │:25565  │
                                           └─────────┘    └────────┘
```

Streams are multiplexed over the single P2P tunnel using `stream_id` in the frame header.

#### UDP Service Support

For UDP services (game servers often use UDP):
- Client2 daemon creates a local UDP socket on the mapped port
- Each datagram is wrapped in a FORWARDED_STREAM frame with the client's source port
- Client1 daemon relays to the real service, caches the source port for return traffic
- Return datagrams are sent back through the tunnel to the correct source port

#### Client Module

```
client/
├── service_exposure.py   # ServiceExposureManager — expose/unexpose local services
└── service_consumer.py   # ServiceConsumer — map/unmap remote services, manage local listeners
```

**`ServiceExposureManager`:**
- `expose(name, protocol, local_host, local_port) -> service_id`
- `unexpose(service_id)`
- `handle_incoming_stream(service_id, stream_id, tunnel)` — accept forwarded connection, connect to local service, pipe data
- `list_exposed() -> list[ServiceRecord]`

**`ServiceConsumer`:**
- `map_service(service_id, local_port=None, strategy="auto") -> local_port`
- `unmap_service(service_id)` — close local listener
- `handle_outgoing_connection(local_port, service_id, tunnel)` — accept local connection, wrap as stream, send through tunnel
- `list_mapped() -> list`

#### Benefits Over TUN Mode

| Aspect | TUN Mode | Service Exposure |
|--------|----------|-----------------|
| Root required | Yes | No |
| System config changes | Yes (interface, routes) | No |
| Scope | All IP traffic | Specific TCP/UDP ports |
| Setup complexity | High | Low |
| Use case | Full LAN emulation | Exposing specific services (games, DB, SSH) |
| Cross-platform friction | High (driver installs) | None |

Service exposure can be used **alongside** or **instead of** TUN mode on the same client.

---

### 4.8 Network Topologies

**Mesh (default):**
- Every peer opens a direct tunnel to every other peer in the network.
- Broadcast traffic reaches everyone.

**Hub-and-Spoke:**
- Spoke clients only connect to the designated Hub.
- The Hub forwards traffic between spokes (optionally).
- Spokes never connect directly to other spokes.

**Gateway:**
- One client (the gateway) bridges the virtual network to its physical LAN.
- Remote clients can reach devices on the gateway's physical subnet.
- Requires ARP proxying and IP forwarding on the gateway machine.

---

## 5. Security Model

### 5.1 Identity & Authentication
1. Client generates RSA-2048 key pair locally.
2. Public key posted to server during registration.
3. Server challenges client: sends random 256-bit nonce.
4. Client signs nonce with private key, returns signature.
5. Server verifies with stored public key → authenticates.

### 5.2 Tunnel Encryption
1. During hole-punch, peers exchange ephemeral ECDH public keys (in PUNCH frames).
2. Both derive a shared AES-256-GCM session key via HKDF.
3. All subsequent data frames use this session key.
4. Sequence numbers prevent replay attacks.
5. Perfect Forward Secrecy: session keys are ephemeral.

### 5.3 Network Access Control
- Networks are password-protected (bcrypt hash stored on server).
- Admin must approve join requests (configurable).
- Network membership is enforced by the mediation server.

---

## 6. Web Admin Panels

Both server and client ship with dedicated, separate web-based administration panels.
They run as lightweight HTTP servers inside the same process (no external web server needed)
and are accessible via browser.

### 6.1 Server Admin Panel

A web dashboard for the server administrator to monitor and configure the mediation server.

**Access:** `http://<server-host>:54001` (default; configurable port)

**Authentication:** Admin username + password (set via env vars or on first launch).
Session-based auth with secure cookies.

**Pages & features:**

| Page | Description |
|------|-------------|
| **Dashboard** | Server uptime, total clients online/offline, network count, relay throughput, active tunnel count, CPU/memory usage |
| **Clients** | Table of all registered clients: ID, public key fingerprint, status (online/offline), connected since, networks joined. Actions: view details, disconnect, ban |
| **Networks** | Table of all networks: name, owner, topology, member count, created date. Actions: view members, delete network |
| **Relay Status** | Active relay paths, bytes relayed per path, relay queue depths |
| **Configuration** | Edit server settings: port, max clients, heartbeat timeout, log level. Saved to config file |
| **Logs** | Live log tail with filtering by level and component |
| **Access Control** | Manage banned client IDs and IP ranges |

**Tech stack:**
- HTTP server: `aiohttp` (already in the asyncio event loop)
- Frontend: vanilla HTML/CSS/JS with Server-Sent Events (SSE) for live updates
- Templates: Jinja2 for server-rendered pages
- Assets: self-contained, no CDN dependencies

**Directory:**
```
server/
├── web/
│   ├── __init__.py
│   ├── app.py              # aiohttp web application setup
│   ├── auth.py             # Admin session auth
│   ├── routes/
│   │   ├── dashboard.py
│   │   ├── clients.py
│   │   ├── networks.py
│   │   ├── relay.py
│   │   ├── config.py
│   │   └── logs.py
│   ├── templates/
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── clients.html
│   │   ├── client_detail.html
│   │   ├── networks.html
│   │   ├── network_detail.html
│   │   ├── relay.html
│   │   ├── config.html
│   │   └── logs.html
│   └── static/
│       ├── css/
│       │   └── admin.css
│       └── js/
│           ├── dashboard.js
│           └── sse.js          # SSE client helper
```

### 6.2 Client Admin Panel

A local web dashboard for the user to manage their own client — networks, peers, tunnels, and settings.

**Access:** `http://localhost:54002` (default; configurable port; bound to localhost only for security)

**Authentication:** None by default (localhost-only). Optional token auth can be enabled.

**Pages & features:**

| Page | Description |
|------|-------------|
| **Dashboard** | Connection status to server, virtual IP, uptime, active tunnels count, data sent/received, NAT type |
| **Networks** | List networks the client belongs to. Actions: create network, join network (by ID + password), leave network, view network details |
| **Peers** | Table of connected peers: peer ID, virtual IP, tunnel state (CONNECTED/RELAY/CONNECTING), latency, bytes tx/rx. Actions: view tunnel details, disconnect |
| **Tunnel Details** | Per-tunnel: cipher suite, session key age, sequence numbers, packet loss %, keepalive stats |
| **Configuration** | Edit client settings: server address, identity directory, log level, TUN interface name. Saved to local config |
| **Logs** | Live log tail with filtering |
| **NAT Diagnostics** | Run NAT type detection, view results, see P2P compatibility matrix |

**Tech stack:**
- HTTP server: `aiohttp` (shared event loop with client networking)
- Frontend: vanilla HTML/CSS/JS with SSE for live updates
- Templates: Jinja2
- Bound to `127.0.0.1` only

**Directory:**
```
client/
├── web/
│   ├── __init__.py
│   ├── app.py              # aiohttp web application setup
│   ├── routes/
│   │   ├── dashboard.py
│   │   ├── networks.py
│   │   ├── peers.py
│   │   ├── config.py
│   │   ├── logs.py
│   │   └── nat_diag.py
│   ├── templates/
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── networks.html
│   │   ├── peers.html
│   │   ├── peer_detail.html
│   │   ├── config.html
│   │   ├── logs.html
│   │   └── nat_diag.html
│   └── static/
│       ├── css/
│       │   └── admin.css
│       └── js/
│           ├── dashboard.js
│           └── sse.js
```

### 6.3 Shared UI Design System

All three admin panels (server, client, proxy) share a common design language.
A single shared assets directory under `common/web_static/` provides the base
CSS, JS, and Jinja2 macros used by all panels.

#### 6.3.1 Color Palette

A dark-first professional palette with semantic status colors.

| Token | Hex | Usage |
|-------|-----|-------|
| `--bg-primary` | `#0f1117` | Main background |
| `--bg-secondary` | `#1a1d27` | Cards, sidebar, panels |
| `--bg-tertiary` | `#242836` | Input backgrounds, hover states |
| `--bg-elevated` | `#2d3245` | Modals, dropdowns, tooltips |
| `--text-primary` | `#e4e6f0` | Headings, body text |
| `--text-secondary` | `#8b90a5` | Labels, descriptions, muted text |
| `--text-tertiary` | `#5c6178` | Placeholders, disabled text |
| `--accent` | `#6366f1` | Primary buttons, links, active nav items |
| `--accent-hover` | `#818cf8` | Hover state for accent elements |
| `--accent-subtle` | `#1e1b4b` | Accent tinted backgrounds |
| `--border` | `#2d3245` | Card borders, table borders, dividers |
| `--border-light` | `#3a4058` | Input borders, subtle separators |

**Status colors:**

| Token | Hex | Usage |
|-------|-----|-------|
| `--success` | `#22c55e` | Online, connected, healthy, up |
| `--success-bg` | `#052e16` | Success badge background |
| `--warning` | `#f59e0b` | Degraded, connecting, retrying |
| `--warning-bg` | `#451a03` | Warning badge background |
| `--danger` | `#ef4444` | Offline, failed, down, banned |
| `--danger-bg` | `#450a0a` | Danger badge background |
| `--info` | `#3b82f6` | Info, neutral, relay mode |
| `--info-bg` | `#0c1929` | Info badge background |

#### 6.3.2 Typography

| Token | Value | Usage |
|-------|-------|-------|
| `--font-family` | `'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif` | All text |
| `--font-mono` | `'JetBrains Mono', 'Fira Code', 'Consolas', monospace` | Code, logs, IPs, IDs |
| `--text-xs` | `0.75rem` / 12px | Badges, labels |
| `--text-sm` | `0.875rem` / 14px | Table cells, descriptions |
| `--text-base` | `1rem` / 16px | Body, form inputs |
| `--text-lg` | `1.125rem` / 18px | Card titles |
| `--text-xl` | `1.25rem` / 20px | Section headings |
| `--text-2xl` | `1.5rem` / 24px | Page headings |
| `--text-3xl` | `1.875rem` / 30px | Dashboard metric values |

Font weights: 400 (regular), 500 (medium), 600 (semibold), 700 (bold).

#### 6.3.3 Spacing & Layout

| Token | Value | Usage |
|-------|-------|-------|
| `--space-1` | 4px | Tight gaps |
| `--space-2` | 8px | Icon gaps, inline spacing |
| `--space-3` | 12px | Element padding |
| `--space-4` | 16px | Card padding, section gaps |
| `--space-5` | 20px | Large gaps |
| `--space-6` | 24px | Section margins |
| `--space-8` | 32px | Page padding |
| `--sidebar-width` | 240px | Side navigation width |
| `--header-height` | 56px | Top header bar height |

#### 6.3.4 Borders, Radius & Shadows

| Token | Value |
|-------|-------|
| `--radius-sm` | 4px (inputs, badges) |
| `--radius-md` | 8px (cards, buttons, modals) |
| `--radius-lg` | 12px (large cards, panels) |
| `--radius-full` | 9999px (pills, status dots) |
| `--shadow-sm` | `0 1px 2px rgba(0,0,0,0.3)` |
| `--shadow-md` | `0 4px 12px rgba(0,0,0,0.4)` |
| `--shadow-lg` | `0 8px 24px rgba(0,0,0,0.5)` |

#### 6.3.5 Master Layout

Every panel page shares the same shell:

```
┌──────────────────────────────────────────────────────────────┐
│  HEADER BAR: logo · panel name · user menu · (56px)          │
├────────────┬─────────────────────────────────────────────────┤
│            │                                                 │
│  SIDEBAR   │           MAIN CONTENT AREA                     │
│  (240px)   │                                                 │
│            │                                                 │
│  • Nav     │                                                 │
│    items   │                                                 │
│    with    │                                                 │
│    icons   │                                                 │
│  • Active  │                                                 │
│    state   │                                                 │
│  • Footer  │                                                 │
│    version │                                                 │
│            │                                                 │
└────────────┴─────────────────────────────────────────────────┘
```

**Header bar details:**
- Left: product icon + panel label ("Server Admin" / "Client Admin" / "Proxy Admin")
- Center: (unused, reserved for search)
- Right: status indicator dot (green/orange/red) + username dropdown (logout)

**Sidebar details:**
- Navigation items with 20px icons + label text
- Active item: accent background (`--accent-subtle`) + accent left border (3px)
- Hover: `--bg-tertiary` background
- Collapsed state for mobile (hamburger toggle, slides over content)
- Bottom: version badge (e.g., "v1.0.0")

#### 6.3.6 Component Library

**Stat Card** — used on dashboards for key metrics:
```
┌─────────────────────┐
│  Label          Icon│
│                     │
│  1,243              │  ← large `--text-3xl` number
│  ↑ 12% from last hr │  ← small trend indicator
└─────────────────────┘
```

**Data Table** — used on clients, peers, networks, upstream pages:
- Header row: `--bg-tertiary` background, uppercase `--text-xs` labels, sticky top
- Rows: alternating transparent / `--bg-secondary` (subtle zebra)
- Row hover: `--bg-tertiary`
- Clickable rows: cursor pointer, highlight on hover
- Status column: colored dot + label ("Online", "Offline", "Connected", "Down")
- Actions column: icon buttons (view, edit, delete, disconnect)
- Pagination: "Showing 1-20 of 156" + prev/next buttons
- Empty state: centered illustration + "No data yet" message

**Status Badge:**
```
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐
│ ● Online │  │ ○ Offline│  │ ◐ Relay  │  │ ● Error  │
│  green   │  │  gray    │  │  blue    │  │  red     │
└──────────┘  └──────────┘  └──────────┘  └──────────┘
```
- Dot + text label, `--radius-full`, 12px font
- Color variants: success, danger, warning, info, neutral

**Form Elements:**
- Text input: `--bg-tertiary` bg, `--border-light` border, focus ring in `--accent`
- Select dropdown: same styling, custom chevron icon
- Toggle switch: off = `--bg-tertiary`, on = `--accent`, smooth transition
- Button variants:
  - Primary: `--accent` bg, white text, `--radius-md`
  - Secondary: transparent bg, `--border` border, `--text-primary` text
  - Danger: `--danger` bg, white text (used for disconnect, ban, delete)
  - Ghost: transparent, `--text-secondary`, hover `--bg-tertiary`
- Button sizes: sm (28px), md (36px), lg (44px)

**Modal / Dialog:**
- Overlay: `rgba(0,0,0,0.6)` backdrop with blur
- Card: `--bg-secondary`, `--shadow-lg`, `--radius-lg`
- Header: title + close (×) button
- Body: message or form content
- Footer: action buttons (Cancel + Confirm)

**Toast Notifications:**
- Position: top-right corner, stacked with gap
- Auto-dismiss after 5s, manual close button
- Variants: success (green), error (red), warning (yellow), info (blue)
- Slide-in animation from right

**Log Viewer:**
- Monospace font (`--font-mono`)
- `--bg-primary` background (darker than page for contrast)
- Color-coded lines: INFO=white, WARN=yellow, ERROR=red, DEBUG=gray
- Auto-scroll to bottom, pause on manual scroll up
- Filter bar: level checkboxes + text search

#### 6.3.7 Panel-Specific Page Designs

**Server Admin — Dashboard:**
```
┌──────────────────────────────────────────────────────────────┐
│  [Stat Card]  [Stat Card]  [Stat Card]  [Stat Card]          │
│  Uptime       Online       Networks     Relay BW             │
│  3d 12h       14           8            2.4 MB/s             │
├────────────────────────────┬─────────────────────────────────┤
│                            │                                 │
│  Client Activity Timeline  │  Network Distribution (pie)     │
│  (sparkline bar chart)     │                                 │
│                            │                                 │
├────────────────────────────┴─────────────────────────────────┤
│                                                               │
│  Recent Events (compact table: time, type, client, message)   │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

**Server Admin — Clients page:**
- Search bar + status filter dropdown at top
- Table: Client ID (truncated), Public Key Fingerprint, Status, Networks, Connected Since, Actions
- Click row → slide-out detail panel: full ID, full key, all networks, IP, heartbeat stats, ban button

**Client Admin — Dashboard:**
```
┌──────────────────────────────────────────────────────────────┐
│  [Stat Card]  [Stat Card]  [Stat Card]  [Stat Card]          │
│  Status       Virtual IP   Active       Data                 │
│  ● Connected  25.1.0.1     Tunnels: 3   ↓ 12MB ↑ 8MB        │
├────────────────────────────┬─────────────────────────────────┤
│                            │                                 │
│  Tunnel States             │  NAT Info                       │
│  ● Alice  CONNECTED 12ms   │  Type: Full Cone                │
│  ● Bob    CONNECTED 45ms   │  P2P: ✓ Possible                │
│  ◐ Carol  RELAY     89ms   │                                 │
│                            │                                 │
├────────────────────────────┴─────────────────────────────────┤
│  Network List (cards: name, role, member count, actions)      │
└──────────────────────────────────────────────────────────────┘
```

**Client Admin — Peers page:**
- Table: Peer IP, Peer ID, State (badge), Latency (ms bar), TX/RX bytes, Actions
- State color coding: CONNECTED=green, RELAY=blue, CONNECTING=yellow
- Click row → detail: cipher, session age, seq numbers, packet loss %, keepalive stats

**Proxy Admin — Dashboard:**
```
┌──────────────────────────────────────────────────────────────┐
│  [Stat Card]  [Stat Card]  [Stat Card]  [Stat Card]          │
│  Active       Requests/s   Upstreams    Cache Hit            │
│  Conn: 142    1,243/s      3/3 UP       Rate: 78%           │
├────────────────────────────┬─────────────────────────────────┤
│                            │                                 │
│  Requests Per Second       │  Upstream Health Grid           │
│  (live sparkline)          │  3×3 grid: name, state dot,     │
│                            │  active conns, failures         │
│                            │                                 │
├────────────────────────────┴─────────────────────────────────┤
│  Connection States:  Reading: 12  Writing: 28  Waiting: 2    │
└──────────────────────────────────────────────────────────────┘
```

**Proxy Admin — Upstreams page:**
- Table per upstream group (expandable sections)
- Each section: upstream name, algorithm, server rows with state dots
- Server row: host:port, state (up/down/unavailable), active conns, failure count, weight, actions
- Force health check button per server

#### 6.3.8 Responsive Breakpoints

| Breakpoint | Width | Behavior |
|-----------|-------|----------|
| Desktop | ≥ 1024px | Full sidebar + content |
| Tablet | 768–1023px | Collapsed sidebar (icons only) + content |
| Mobile | < 768px | Hidden sidebar (hamburger toggle), single column, stacked cards |

- Stat cards: 4-col → 2-col → 1-col
- Tables: horizontal scroll on mobile
- Modals: full-screen on mobile

#### 6.3.9 Animations & Motion

| Element | Animation | Duration |
|---------|-----------|----------|
| Page transition | Fade in (opacity 0→1) | 150ms ease |
| Sidebar toggle | Slide + fade | 200ms ease |
| Modal open | Scale 0.95→1 + fade backdrop | 200ms ease |
| Toast enter | Slide from right + fade | 300ms ease-out |
| Toast exit | Fade out + shrink | 200ms ease-in |
| Row hover | Background color transition | 100ms |
| Stat counter | Count-up animation | 500ms ease-out |
| Status dot | Pulse animation (when active) | 2s infinite |
| Spinner | Rotate 360° | 1s linear infinite |
| Skeleton loader | Shimmer sweep | 1.5s ease infinite |

#### 6.3.10 Accessibility

- All interactive elements are keyboard navigable (Tab, Enter, Escape)
- Focus rings: 2px `--accent` outline with 2px offset
- ARIA labels on icon-only buttons
- `role="status"` on live regions (SSE-updated stat cards)
- `role="alert"` on toast notifications
- Table headers use `<th scope="col">`
- Color is never the sole indicator of state (always paired with icon + text)
- Minimum contrast ratio: 4.5:1 for normal text, 3:1 for large text
- `prefers-reduced-motion` respected: disable animations, use instant transitions

#### 6.3.11 Shared Assets Directory

```
common/
├── web_static/
│   ├── css/
│   │   ├── variables.css      # CSS custom properties (all tokens)
│   │   ├── reset.css          # CSS reset / normalize
│   │   ├── layout.css         # Shell layout (header, sidebar, main)
│   │   ├── components.css     # Cards, tables, badges, forms, buttons, modals, toasts
│   │   └── utilities.css      # Spacing, flex, grid helpers
│   └── js/
│       ├── sse.js             # Reusable EventSource helper
│       ├── dashboard.js       # Stat counters, sparklines, auto-refresh
│       ├── tables.js          # Sortable, filterable, paginated tables
│       └── ui.js              # Toasts, modals, sidebar toggle, theme
```

Each panel's own `static/` directory only contains panel-specific overrides;
the shared base is symlinked or imported.

---

## 7. Reverse Proxy

A standalone, high-performance reverse proxy and load balancer. Built on the same
asyncio event-driven architecture, it routes HTTP/HTTPS and TCP/UDP traffic to
backend servers with load balancing, caching, SSL termination, rate limiting,
and health checks.

### 7.1 Master-Worker Process Model

**Master process:**
- Reads and validates the proxy configuration file
- Binds to privileged ports (80, 443, etc.)
- Manages worker processes (spawn, monitor, graceful reload)
- Does not handle client traffic directly
- Handles SIGHUP for configuration reload without dropping connections

**Worker processes:**
- One per CPU core (configurable)
- Each runs its own asyncio event loop
- Single-threaded, non-blocking I/O
- Handles all client connections, upstream communication, caching, logging
- OS-level `SO_REUSEPORT` distributes incoming connections across workers

### 7.2 Event-Driven Architecture

- **Event loop:** `asyncio` with `uvloop` (optional drop-in acceleration)
- **Non-blocking I/O:** All socket operations are async — connect, read, write, close
- **Event notification:** Platform-optimal (`epoll` on Linux, `kqueue` on macOS, `IOCP` on Windows via `asyncio`'s ProactorEventLoop)
- **Connection multiplexing:** A single worker handles thousands of concurrent client and upstream connections simultaneously
- **Zero-copy where possible:** `sendfile()` for static file serving, `splice()` between sockets

### 7.3 Directory Structure

```
proxy/
├── __init__.py
├── main.py              # Entry point, master process, arg parsing
├── config.py            # Configuration loader (YAML/JSON config file)
├── master.py            # Master process: bind ports, spawn workers, reload
├── worker.py            # Worker process: event loop, accept connections
├── connection.py        # HTTP connection state machine (parse, proxy, respond)
├── upstream.py           # Upstream backend pool management
├── load_balancer.py     # Load balancing algorithms
├── health_check.py       # Passive health checks (active in future)
├── cache/
│   ├── __init__.py
│   ├── storage.py       # On-disk cache storage with LRU eviction
│   ├── manager.py        # Cache key generation, validation, purging
│   └── metadata.py      # In-memory cache metadata index
├── ssl/
│   ├── __init__.py
│   └── terminator.py    # SSL/TLS termination with cert management
├── security/
│   ├── __init__.py
│   ├── rate_limiter.py  # Per-IP and per-route rate limiting
│   ├── access.py        # IP allow/deny rules
│   └── auth.py          # HTTP Basic auth, token validation
├── compression.py       # Gzip response compression
├── stream_proxy.py      # TCP/UDP stream proxying
├── logging.py           # Access and error log writers
├── status.py            # Stub status module for monitoring
└── web/
    ├── __init__.py
    ├── app.py           # aiohttp admin panel
    ├── routes/
    │   ├── dashboard.py
    │   ├── upstream.py
    │   ├── cache.py
    │   ├── config.py
    │   └── logs.py
    ├── templates/
    │   ├── base.html
    │   ├── dashboard.html
    │   ├── upstream.html
    │   ├── cache.html
    │   ├── config.html
    │   └── logs.html
    └── static/
        ├── css/admin.css
        └── js/
            ├── dashboard.js
            └── sse.js
```

### 7.4 Configuration Model

Proxy configuration is defined in a YAML (or JSON) file. Example:

```yaml
proxy:
  workers: auto                # auto = CPU count
  worker_connections: 1024     # max connections per worker

  http:
    - listen: 80
      server_name: example.com

      # Route requests to backend group
      locations:
        - path: /
          upstream: app_backend
          
      # Serve static files directly
        - path: /static/
          root: /var/www/static

  upstreams:
    - name: app_backend
      algorithm: round_robin    # round_robin | least_conn | ip_hash | random
      servers:
        - host: 10.0.0.1
          port: 3000
          weight: 1
          max_conns: 100
        - host: 10.0.0.2
          port: 3000
          weight: 2
          max_conns: 100
        - host: 10.0.0.3
          port: 3000
          weight: 1
          backup: true           # used only when primaries are down

  ssl:
    - listen: 443
      server_name: example.com
      certificate: /etc/ssl/example.com.pem
      private_key: /etc/ssl/example.com.key
      protocols: [TLSv1.2, TLSv1.3]
      ciphers: HIGH:!aNULL:!MD5

  cache:
    enabled: true
    path: /var/cache/proxy
    max_size: 1GB
    default_ttl: 300             # seconds

  rate_limiting:
    enabled: true
    zone: client_ip
    rate: 100r/s                 # requests per second per IP
    burst: 20

  access_control:
    - allow: 10.0.0.0/8
    - allow: 192.168.0.0/16
    - deny: all

  compression:
    enabled: true
    types: [text/html, text/css, application/javascript, application/json]
    level: 6

  logging:
    access_log: /var/log/proxy/access.log
    error_log: /var/log/proxy/error.log
    format: combined             # combined | json | custom

  admin:
    port: 54010                  # web admin panel port
```

### 7.5 Connection Handling

Each client connection flows through a state machine:

```
ACCEPT → READ_REQUEST_HEADERS → (optionally: READ_BODY)
    → SELECT_UPSTREAM → CONNECT_UPSTREAM → FORWARD_REQUEST
    → READ_UPSTREAM_RESPONSE → FORWARD_RESPONSE → CLOSE (or KEEPALIVE)
```

**Client-side buffering:**
- Request headers read into memory
- Request body buffered (in memory up to threshold, then to temp file)
- Protects slow backends from slow clients

**Upstream connection pooling:**
- Keep-alive connections to backends reused across requests
- Configurable idle timeout and max requests per connection
- Reduces TCP and TLS handshake overhead

**Header management:**
- `Host` header set to upstream target
- `X-Real-IP` added with original client address
- `X-Forwarded-For` appended for proxy chain tracking
- `X-Forwarded-Proto` set to original scheme (http/https)

### 7.6 Load Balancing

**Algorithms:**

| Algorithm | Description | Best for |
|-----------|-------------|----------|
| **Round Robin** | Sequential distribution through server list; weighted variant distributes proportionally | Similar-capacity backends |
| **Least Connections** | Routes to server with fewest active connections | Workloads with varying request duration |
| **IP Hash** | Hashes client IP to deterministically pick a server | Session persistence without shared storage |
| **Random** | Uniform random selection with optional two-phase weighted choice | Stateless workloads needing fairness |

**Server attributes:**
- `weight` — relative capacity (higher = more traffic)
- `max_conns` — hard cap on concurrent connections
- `backup` — only used when all non-backup servers are down
- `down` — administratively disabled

### 7.7 Health Checks

**Passive health checks** (monitor real traffic):
- Track connection failures and timeouts per upstream server
- After N consecutive failures within a window → mark server `unavailable`
- Wait `fail_timeout` seconds, then retry with a single probe request
- On success → mark server `available` again
- Zero additional traffic overhead

**Active health checks** (future enhancement):
- Periodic synthetic requests to health endpoint
- Configurable interval, path, expected status code
- Faster failure detection than passive-only

### 7.8 SSL/TLS Termination

- Handles TLS handshake at the proxy, forwarding plain HTTP to backends
- Certificate management: PEM file or directory of certs
- SNI support for multi-domain configurations
- OCSP stapling for efficient certificate revocation checking
- Session resumption via session IDs and session tickets
- Configurable protocol versions and cipher suites

### 7.9 Caching

- On-disk cache storage with hierarchical directory structure
- In-memory metadata index for O(1) cache lookup
- Cache key derived from: method + URI + select headers
- Respects upstream `Cache-Control` headers (max-age, no-cache, no-store, private)
- Conditional requests: `If-Modified-Since` / `If-None-Match` forwarded to backend
- Stale-while-revalidate: serve stale content while fetching fresh in background
- LRU eviction when disk space exceeds `max_size`
- Manual purge via admin panel or API

### 7.10 Security

**Rate limiting:**
- Per-IP rate tracking with sliding window counters
- Configurable rate (requests per second) and burst allowance
- Excess requests get HTTP 429 or are delayed
- Multiple rate zones can be defined (e.g., per-IP, per-route)

**Access control:**
- IP allow/deny rules evaluated in order (first match wins)
- Supports CIDR notation (IPv4 and IPv6)
- `allow all` / `deny all` catch-all rules

**Basic authentication:**
- Per-location HTTP Basic auth
- Credentials stored in htpasswd-style file (bcrypt hashed)
- Returns 401 with `WWW-Authenticate` header

### 7.11 Compression

- Gzip compression for text-based response bodies
- Applied only when client sends `Accept-Encoding: gzip`
- Configurable compression level (1–9)
- Selective: only compress listed MIME types
- Minimum response size threshold to avoid compressing tiny payloads

### 7.12 Stream Proxy (TCP/UDP)

- Proxies raw TCP and UDP traffic to backend servers
- Configured separately from HTTP in `stream:` blocks
- Supports all load balancing algorithms
- No HTTP-level processing (headers, caching, compression don't apply)
- Use cases: database connections, DNS, SMTP, custom TCP protocols

### 7.13 Logging

**Access log:**
- One line per request: timestamp, client IP, method, path, status, bytes, upstream, response time
- Configurable format: `combined` (Apache-style), `json` (structured), or custom template
- Async writes via dedicated writer task (never blocks the event loop)

**Error log:**
- Severity levels: `debug`, `info`, `notice`, `warn`, `error`, `crit`
- Worker ID, timestamp, and message

### 7.14 Status & Monitoring

**Stub status endpoint** (`GET /proxy-status` on admin port):
```json
{
  "active_connections": 42,
  "accepted_connections": 105823,
  "handled_connections": 105823,
  "total_requests": 512034,
  "reading": 12,
  "writing": 28,
  "waiting": 2,
  "upstreams": [
    {
      "name": "app_backend",
      "servers": [
        {"host": "10.0.0.1:3000", "state": "up", "active": 14, "failures": 0},
        {"host": "10.0.0.2:3000", "state": "up", "active": 15, "failures": 0},
        {"host": "10.0.0.3:3000", "state": "down", "active": 0, "failures": 3}
      ]
    }
  ]
}
```

### 7.15 Graceful Reload

- `SIGHUP` signal triggers configuration reload
- Master reads new config, opens new listen sockets
- New workers spawned with new config
- Old workers finish existing connections, then exit
- No dropped connections during reload

---

## 8. Project Dependencies

```
# requirements.txt
cryptography>=41.0        # RSA, AES-GCM, ECDH, HKDF
bcrypt>=4.0              # Password hashing
python-dotenv>=1.0       # Config from .env
pyyaml>=6.0              # YAML config parsing (proxy, settings)
aiohttp>=3.9             # Async HTTP server (web admin panels)
jinja2>=3.1              # HTML templates (web admin panels)
```

Optional / platform-specific:
```
# Linux
pyroute2>=0.7            # TUN interface management

# Windows
pywintun>=0.1            # Wintun adapter bindings
```

---

## 9. Directory Layout

```
LocalNetwork_Ecosystem/
├── DESIGN.md                 # This document
├── TODO.md                   # Implementation plan index (links to docs/todos/)
├── docs/
│   └── todos/                # Split implementation phases
│       ├── 00-foundation.md       # Phases 0–2
│       ├── 01-server.md           # Phases 3–4
│       ├── 02-client-vpn.md       # Phases 5–9
│       ├── 03-service-exposure.md # Phase 14
│       ├── 04-web-panels.md       # Phases 15–16
│       ├── 05-reverse-proxy.md    # Phases 17–22
│       ├── 06-ux-cli.md           # Phases 10–11
│       ├── 07-testing-hardening.md# Phases 12–13
│       └── 08-docs.md             # Phase 23
├── requirements.txt
├── README.md
├── server/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── registry.py
│   ├── network_manager.py
│   ├── auth.py
│   ├── relay.py
│   ├── protocol.py
│   └── web/
│       ├── __init__.py
│       ├── app.py
│       ├── auth.py
│       ├── routes/
│       │   ├── dashboard.py
│       │   ├── clients.py
│       │   ├── networks.py
│       │   ├── relay.py
│       │   ├── config.py
│       │   └── logs.py
│       ├── templates/
│       │   ├── base.html
│       │   ├── dashboard.html
│       │   ├── clients.html
│       │   ├── client_detail.html
│       │   ├── networks.html
│       │   ├── network_detail.html
│       │   ├── relay.html
│       │   ├── config.html
│       │   └── logs.html
│       └── static/
│           ├── css/admin.css
│           └── js/
│               ├── dashboard.js
│               └── sse.js
├── client/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── platform_detection.py  # Platform capabilities detection
│   ├── identity.py
│   ├── control_channel.py
│   ├── tunnel_manager.py
│   ├── nat_traversal.py
│   ├── encryption.py
│   ├── tun_interface.py
│   ├── packet.py
│   ├── keepalive.py
│   ├── relay_client.py
│   ├── service_exposure.py   # Expose local services to network
│   ├── service_consumer.py   # Map remote services to local ports
│   └── web/
│       ├── __init__.py
│       ├── app.py
│       ├── routes/
│       │   ├── dashboard.py
│       │   ├── networks.py
│       │   ├── peers.py
│       │   ├── services.py       # Service exposure management
│       │   ├── config.py
│       │   ├── logs.py
│       │   └── nat_diag.py
│       ├── templates/
│       │   ├── base.html
│       │   ├── dashboard.html
│       │   ├── networks.html
│       │   ├── peers.html
│       │   ├── peer_detail.html
│       │   ├── services.html     # Service exposure page
│       │   ├── config.html
│       │   ├── logs.html
│       │   └── nat_diag.html
│       └── static/
│           ├── css/admin.css
│           └── js/
│               ├── dashboard.js
│               └── sse.js
├── common/
│   ├── __init__.py
│   ├── constants.py          # Protocol constants, magic numbers
│   ├── messages.py           # Control message dataclasses
│   ├── frame.py              # Data frame struct definitions
│   └── web_static/
│       ├── css/
│       │   ├── variables.css     # CSS custom properties
│       │   ├── reset.css         # CSS reset
│       │   ├── layout.css        # Shell layout
│       │   ├── components.css    # Shared components
│       │   └── utilities.css     # Spacing/flex/grid helpers
│       └── js/
│           ├── sse.js            # EventSource helper
│           ├── dashboard.js      # Counters, sparklines
│           ├── tables.js         # Sortable/filterable tables
│           └── ui.js             # Toasts, modals, sidebar
├── proxy/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── master.py
│   ├── worker.py
│   ├── connection.py
│   ├── upstream.py
│   ├── load_balancer.py
│   ├── health_check.py
│   ├── cache/
│   │   ├── __init__.py
│   │   ├── storage.py
│   │   ├── manager.py
│   │   └── metadata.py
│   ├── ssl/
│   │   ├── __init__.py
│   │   └── terminator.py
│   ├── security/
│   │   ├── __init__.py
│   │   ├── rate_limiter.py
│   │   ├── access.py
│   │   └── auth.py
│   ├── compression.py
│   ├── stream_proxy.py
│   ├── logging.py
│   ├── status.py
│   └── web/
│       ├── __init__.py
│       ├── app.py
│       ├── routes/
│       │   ├── dashboard.py
│       │   ├── upstream.py
│       │   ├── cache.py
│       │   ├── config.py
│       │   └── logs.py
│       ├── templates/
│       │   ├── base.html
│       │   ├── dashboard.html
│       │   ├── upstream.html
│       │   ├── cache.html
│       │   ├── config.html
│       │   └── logs.html
│       └── static/
│           ├── css/admin.css
│           └── js/
│               ├── dashboard.js
│               └── sse.js
└── tests/
    ├── __init__.py
    ├── conftest.py            # Fixtures: test server, test clients
    ├── test_identity.py
    ├── test_encryption.py
    ├── test_protocol.py
    ├── test_registry.py
    ├── test_network_manager.py
    ├── test_nat_traversal.py
    ├── test_tunnel_manager.py
    ├── test_control_channel.py
    ├── test_packet.py
    ├── test_platform_detection.py  # Platform capability detection tests
    ├── test_service_exposure.py   # Port forwarding tests
    ├── test_server_web.py     # Server web panel tests
    ├── test_client_web.py     # Client web panel tests
    ├── test_load_balancer.py  # Reverse proxy LB algorithm tests
    ├── test_health_check.py   # Reverse proxy health check tests
    ├── test_rate_limiter.py   # Reverse proxy rate limiting tests
    ├── test_proxy_config.py   # Reverse proxy config parsing tests
    ├── test_proxy_integration.py  # Reverse proxy integration tests
    ├── test_integration.py    # Multi-client integration tests
    └── test_e2e.py            # End-to-end: ping between two clients
```

---

## 10. Platform Support & Feature Matrix

All features target maximum compatibility across Windows, Linux, macOS, and Termux (Android).
Features that require root or OS-level capabilities are gracefully degraded when unavailable.

### 10.1 Feature Compatibility Matrix

| Feature | Linux | macOS | Windows | Termux (Android) |
|---------|:-----:|:-----:|:-------:|:----------------:|
| **Mediation server** | ✅ | ✅ | ✅ | ✅ |
| **Client daemon (core)** | ✅ | ✅ | ✅ | ✅ |
| **NAT traversal (UDP hole punch)** | ✅ | ✅ | ✅ | ✅ |
| **P2P encrypted tunnels** | ✅ | ✅ | ✅ | ✅ |
| **Service exposure (port forward)** | ✅ | ✅ | ✅ | ✅ |
| **Reverse proxy** | ✅ | ✅ | ✅ | ✅ |
| **Web admin panels** | ✅ | ✅ | ✅ | ✅ |
| **Virtual LAN — TUN mode** | ✅ root | ✅ root | ✅ admin | ❌ |
| **Virtual LAN — raw sockets / ARP** | ✅ root | ✅ root | ⚠️ limited | ❌ |
| **Gateway mode (LAN bridging)** | ✅ root | ✅ root | ⚠️ limited | ❌ |
| **IP forwarding / NAT** | ✅ root | ✅ root | ⚠️ | ❌ |

✅ = fully supported, no special requirements  
✅ root = requires root/administrator  
⚠️ = partially supported or needs manual setup  
❌ = not supported on this platform

### 10.2 Per-Platform Details

#### Linux
- **TUN:** `/dev/net/tun` available on all modern kernels. Requires `root` or `CAP_NET_ADMIN`.
- **Routing:** `ip route` commands need root.
- **Service exposure:** Fully supported with `127.0.0.1` listeners.
- **Reverse proxy:** Can bind to privileged ports (80, 443) with root or `CAP_NET_BIND_SERVICE`.
- **Web panels:** Fully supported.

#### macOS
- **TUN:** `utun` interfaces via `SYSPROTO_CONTROL` socket. Requires root.
- **Routing:** `route add` needs root.
- **Service exposure:** Fully supported.
- **Reverse proxy:** Can bind to privileged ports with root.
- **Web panels:** Fully supported.

#### Windows
- **TUN:** Requires WinTun driver installation. Needs Administrator for driver setup and interface creation.
- **Routing:** `route add` needs Administrator.
- **Service exposure:** Fully supported.
- **Reverse proxy:** Can bind to privileged ports with Administrator.
- **Web panels:** Fully supported.
- **Asyncio:** Uses `ProactorEventLoop` (IOCP-based) automatically on Windows.

#### Termux (Android)
- **TUN:** Not available. No `/dev/net/tun`, no kernel module. Even with root, Android's
  sandboxed network stack prevents TUN interface creation from the Termux shell.
- **Routing:** Not applicable (no TUN).
- **Service exposure:** Fully supported. Can bind `127.0.0.1` TCP/UDP listeners.
  LAN binding may be restricted by Android network policies on newer versions.
- **Reverse proxy:** Fully supported (listening on non-privileged ports).
- **Web panels:** Fully supported (localhost-bound).
- **Raw sockets:** Not available without root.
- **Process visibility:** Limited — `/proc` is restricted. `ps` only shows Termux processes.
- **Filesystem:** Must run on internal storage (EXT4/F2FS). External SD cards (FAT32/exFAT)
  break symlinks and Unix sockets.

### 10.3 Root Detection & Graceful Degradation

On startup, the client detects platform capabilities and adjusts available features:

```python
# client/platform_detection.py

@dataclass
class PlatformCapabilities:
    os_name: str              # "linux" | "darwin" | "win32" | "termux"
    has_root: bool            # UID == 0 (Linux/macOS/Termux) or admin (Windows)
    tun_available: bool       # /dev/net/tun exists and writable (Linux), utun (macOS), wintun (Windows)
    raw_sockets: bool         # Can open AF_PACKET / SOCK_RAW
    privileged_ports: bool    # Can bind to ports < 1024
    is_termux: bool           # Running in Termux environment

def detect_platform() -> PlatformCapabilities:
    # 1. Detect OS: platform.system()
    # 2. Detect Termux: check for $PREFIX == /data/data/com.termux/files/usr
    # 3. Detect root: os.geteuid() == 0 on Unix, ctypes.windll.shell32.IsUserAnAdmin() on Windows
    # 4. Detect TUN: try open /dev/net/tun (Linux), check utun (macOS), check wintun.dll (Windows)
    #    On Termux: always False
    # 5. Detect raw sockets: try socket(AF_PACKET, SOCK_RAW) (non-Termux Linux only)
    # 6. Detect privileged ports: root: True, else try bind to port 80, catch PermissionError
```

**Degradation rules:**

| Capability missing | Behavior |
|-------------------|----------|
| `tun_available == False` | TUN mode disabled. Virtual LAN limited to service exposure only. Client logs: "TUN interface not available — service exposure mode only." |
| `has_root == False` | TUN, raw sockets, privileged ports disabled. Client warns on startup about limited mode. |
| `is_termux == True` | TUN permanently disabled. LAN binding may fail — localhost-only guarantees. Extra warnings in logs. |
| `raw_sockets == False` | ARP handling skipped. Broadcast traffic only works via server relay. |
| `privileged_ports == False` | Reverse proxy and service listeners default to high ports (≥1024). Web panels use high ports by default (54001, 54002, 54010). |

### 10.4 Feature Availability Summary for Users

**All platforms get (no root needed):**
- Connect to a virtual LAN network via service exposure (port forwarding)
- Expose local TCP/UDP services to the network
- Map remote network services to local ports
- Run the reverse proxy (high ports only)
- Access all web admin panels
- NAT traversal and encrypted P2P tunnels

**Root/Admin unlocks additionally:**
- Full TUN-based virtual LAN with real IP addresses
- Ping, SSH, and any IP-based application to peers
- Gateway mode (bridge to physical LAN)
- Bind to privileged ports (80, 443)

**Termux-specific:**
- TUN mode disabled permanently (regardless of root)
- Service exposure, reverse proxy, web panels fully functional
- Localhost-bound listeners only (reliable across all Android versions)
- Recommended as a lightweight service-host or relay node

---

## 11. User Experience & Ease of Use

The system is designed for non-technical users. Every interaction follows the principle:
**"It should just work. If it can't, explain why in plain language and show me what to do."**

### 11.1 Design Principles

| Principle | What it means |
|-----------|--------------|
| **Zero config** | First launch auto-detects everything. No manual setup required. |
| **Sensible defaults** | Every setting has a smart default. Users only change things if they want to. |
| **Plain language** | No jargon. "TUN interface" becomes "virtual network adapter". "NAT traversal" becomes "automatically connecting through your router". |
| **Always visible state** | The user always knows what's happening — a status indicator is always visible. |
| **Forgiving** | Mistakes are undoable. Dangerous actions ask for confirmation. Nothing breaks permanently. |
| **Guided, not manual** | Common tasks are wizards, not checklists. The system asks questions and does the work. |

### 11.2 Terminology Map — Technical → User-Facing

| Technical term | What the user sees |
|---------------|-------------------|
| Mediation server | "Network Hub" or "Coordinator" |
| Client / node | "Your computer" or "Device" |
| Virtual LAN / TUN | "Virtual network adapter" |
| NAT traversal / UDP hole punch | "Automatically connecting through your router" |
| P2P tunnel | "Direct connection" |
| Relay fallback | "Connection via relay" (shown as slower but working) |
| Service exposure | "Share a service" or "Make accessible to network" |
| Port forwarding | "Open to the network" |
| Upstream server (proxy) | "Backend server" |
| Load balancing | "Traffic distribution" |
| SSL/TLS termination | "HTTPS security" |
| Health check | "Server monitoring" |
| Rate limiting | "Traffic protection" |
| Mesh topology | "Everyone connected to everyone" |
| Hub-and-spoke | "Everyone connects through one computer" |
| Gateway mode | "Access your home/office network remotely" |

### 11.3 First-Run Experience — Setup Wizard

Instead of requiring CLI flags, the first launch opens an interactive setup wizard
(in the terminal or web panel) that walks the user through setup with simple questions:

```
┌─────────────────────────────────────────────────┐
│          Welcome to LocalNetwork!                │
│                                                  │
│  What would you like to do?                      │
│                                                  │
│  [1] Join an existing network                    │
│      Connect to a network someone already made   │
│                                                  │
│  [2] Create a new network                        │
│      Start a network and invite others           │
│                                                  │
│  [3] Set up a network hub (for coordinators)     │
│      Run the central server that connects everyone│
│                                                  │
│  [4] Set up the reverse proxy                    │
│      Load balance traffic to your web servers    │
│                                                  │
│  Type a number and press Enter: _                │
└─────────────────────────────────────────────────┘
```

**Path 1 — Join a network:**
1. "Enter the network address your friend gave you:" → `friend-123.localnetwork`
2. "Enter the network password:" → `••••••••`
3. System auto-connects, detects platform, shows: "✅ Connected! You're now on 'My Gaming Network'. Your virtual IP is 25.1.0.3."
4. "Would you like to open the web dashboard? [Y/n]"

**Path 2 — Create a network:**
1. "What should we call your network?" → `My Gaming Network`
2. "Set a password so only people you invite can join:" → `••••••••`
3. "How should devices connect to each other?" → [Everyone to everyone / Through one computer / Access home network remotely]
4. "✅ Network created! Share this with your friends to let them join:"
   `Network: My Gaming Network`
   `Address:  your-id.localnetwork`
   `Password: (the one you set)`
5. "Would you like to open the web dashboard? [Y/n]"

**Path 3 — Set up a hub:**
1. "This computer needs to be reachable by others. Is it on a public server or your home network?"
2. If home network: "We'll try to make it accessible automatically. If that doesn't work, you may need to enable UPnP on your router or forward port 54000."
3. System starts the server, tests reachability, reports result.
4. "✅ Hub is running and reachable! Admin panel: http://localhost:54001"

### 11.4 Web Panel — One-Click Workflows

Common tasks that would normally require multiple CLI commands are reduced to
single buttons with clear labels in the web panels:

**Client panel — common tasks:**
| Button | What it does |
|--------|-------------|
| "Share a service" | Opens a dialog: pick service type (Game server / Web app / Other), enter port → done |
| "Connect to a service" | Shows available services on the network → click one → it's mapped locally |
| "Invite someone" | Generates an invite code/link they can share |
| "Test my connection" | Runs NAT type detection, checks if P2P works, shows simple result: "✅ Your connection is good" or "⚠️ Your router is strict — some connections will use relay (slower but still works)" |
| "Fix connection issues" | Guided troubleshooting: step 1 → step 2 → step 3 with simple instructions |

**Server panel — common tasks:**
| Button | What it does |
|--------|-------------|
| "View connected devices" | Table of all online devices with status dots |
| "Remove a device" | Click device → "Remove from network" → confirm |
| "Network settings" | Change name, password, topology — all with plain descriptions |

### 11.5 Error Messages — Philosophy

Every error message answers three questions: **What happened? Why? What can I do?**

**Bad (technical):**
> `ConnectionError: [Errno 111] Connection refused to 192.168.1.10:54000`

**Good (user-facing):**
> ⚠️ **Couldn't reach the network hub**
>
> Your computer can't connect to the network coordinator at `192.168.1.10`.
> This usually means:
> • The hub computer isn't running, or
> • A firewall is blocking the connection
>
> **Try this:**
> 1. Make sure the hub computer is turned on and running LocalNetwork
> 2. Check that port 54000 is open on the hub's firewall
> 3. If you're on different networks, make sure the hub's address is correct
>
> [Retry] [Change hub address] [Help me fix this]

**Error severity levels and their tone:**

| Level | Icon | Tone | Example |
|-------|------|------|---------|
| Info | ℹ️ | Neutral, informative | "3 new devices joined your network" |
| Success | ✅ | Positive, confirming | "Service shared successfully" |
| Warning | ⚠️ | Concerned but functional | "Direct connection failed — using relay (slightly slower)" |
| Error | ❌ | Clear, actionable | "Cannot connect — the password is incorrect" |
| Critical | 🚫 | Urgent, specific | "No network adapter found. Try restarting the app." |

### 11.6 Status Visibility

Users should always know the system state at a glance:

**System tray / menu bar indicator:**
- 🟢 Green dot: Everything is working. Connected to network, all tunnels healthy.
- 🟡 Yellow dot: Degraded. Some connections on relay, or one peer unreachable.
- 🔴 Red dot: Problem. Not connected to hub, or all tunnels down.
- ⚪ Gray dot: Idle. Client running but not joined to any network.

Hovering the indicator shows a tooltip: "Connected to My Gaming Network • 3 peers online • P2P: good"

**Web panel status bar (always visible at top):**
```
🟢 Connected to "My Gaming Network"  |  3 peers online  |  Virtual IP: 25.1.0.3  |  P2P: Direct ✅
```

### 11.7 Auto-Configuration

Things the system does automatically so the user doesn't have to:

| What | How |
|------|-----|
| **Find the hub** | If no hub address given, scan local network for running hubs (mDNS/broadcast discovery) |
| **Pick the best connection** | Auto-try P2P; if it fails, silently fall back to relay |
| **Choose a port** | Auto-pick an available port for service exposure; only ask if all in range are taken |
| **Handle reconnection** | If connection drops, auto-reconnect with exponential backoff — user never sees it |
| **Generate identity** | First launch: auto-generate RSA keys. User doesn't need to know this happened. |
| **Platform detection** | Auto-detect OS, root status, TUN availability. Only show features that work. |
| **Update notifications** | Check for new versions, show a subtle "Update available" in the panel |

### 11.8 Progressive Disclosure

Advanced features exist but are hidden by default. Users discover them naturally:

**Level 1 — Everyone sees:**
- Join a network, create a network
- Share a service (pick from list: Game, Web, Other)
- See who's online
- Basic status: connected/not connected

**Level 2 — Available behind "Advanced" toggle:**
- Manual port selection for services
- Tunnel details (latency, encryption info)
- Network topology choice
- NAT type diagnostics

**Level 3 — Config files and CLI for power users:**
- YAML config for reverse proxy
- Custom route and upstream definitions
- Scriptable CLI for automation
- Raw log access

### 11.9 Help System

Every page in the web panel has a `?` help icon in the top-right corner.
Clicking it opens a contextual help panel with:
1. **"What is this?"** — one-sentence plain-language explanation
2. **"Common tasks"** — 2-3 most common things people do here
3. **"Having trouble?"** — link to the troubleshooting flow for this page

Built-in troubleshooting wizard ("Fix my connection"):
1. Checks if the hub is reachable → if not, suggests checking the address
2. Checks NAT type → if symmetric, explains relay mode and that it's still functional
3. Checks TUN status → if unavailable, explains that only service sharing works
4. Generates a diagnostic report the user can share for help

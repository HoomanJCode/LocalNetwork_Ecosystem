# LocalNetwork Ecosystem

A Python-based networking toolkit with three core features:
- **Virtual LAN** — connect remote computers into a private encrypted network
- **Reverse Proxy** — high-performance HTTP/TCP load balancer and traffic manager
- **Web Admin Panels** — browser-based dashboards for server, client, and proxy

> **Status:** In development — [DESIGN.md](DESIGN.md) defines the architecture and
> [TODO.md](TODO.md) tracks the phased implementation plan.
> **Quick start:** see [USAGE.md](USAGE.md) for the one-page command reference.

---

## How It Works

1. **You start the app** — it automatically sets everything up. No configuration needed.
2. **You join or create a network** — like joining a Wi-Fi network, but works anywhere in the world.
3. **Your computer connects directly to others** — the app finds the fastest path through routers
   automatically. If a direct path isn't possible, it uses a relay (slightly slower, still encrypted).
4. **You see other people's devices** — just like they were on your home network. Share files,
   play games, access services — everything just works.

The central server only helps devices find each other. After that, all your data
flows directly between computers, fully encrypted. Even the server can't read it.

---

## What You Can Do

| You want to... | How LocalNetwork helps |
|---------------|----------------------|
| **Play games with friends** | Share your game server. Friends connect like you're on the same Wi-Fi. |
| **Access your home computer** | Connect to your home PC from anywhere — files, apps, everything. |
| **Share a web app** | Make your local dev server accessible to teammates without deploying. |
| **Run a Minecraft server** | Expose your server to friends with one click. No port forwarding, no router config. |
| **Set up a load balancer** | Distribute traffic across multiple servers. Built-in health checks. |
| **Create a private network** | Connect all your devices — computers, servers, phones (via Termux) — into one network. |

---

## Requirements

- **Python 3.10+**
- **Open UDP port** on your firewall/routers for best P2P results

### Platform Support

| Feature | Linux | macOS | Windows | Termux |
|---------|:-----:|:-----:|:-------:|:------:|
| Mediation server | ✅ | ✅ | ✅ | ✅ |
| P2P tunnels + NAT traversal | ✅ | ✅ | ✅ | ✅ |
| Service exposure (port forward) | ✅ | ✅ | ✅ | ✅ |
| Reverse proxy | ✅ | ✅ | ✅ | ✅ |
| Web admin panels | ✅ | ✅ | ✅ | ✅ |
| **Virtual LAN — TUN mode** | ✅ root | ✅ root | ✅ admin | ❌ |
| Gateway mode (LAN bridging) | ✅ root | ✅ root | ⚠️ | ❌ |

✅ = no special requirements  
✅ root = needs root/administrator  
⚠️ = partial support  
❌ = not available

**All features except TUN-mode virtual LAN work on every platform without root.**
TUN mode provides full IP-level LAN emulation (ping, SSH, any IP app) and requires
root/admin because it creates a virtual network interface.

### OS-specific notes

| OS      | TUN driver       | Setup                                         |
|---------|------------------|-----------------------------------------------|
| Linux   | `tun` (kernel)   | `modprobe tun` — built in on most distros     |
| macOS   | `utun` (kernel)  | No setup needed — `utun` is always available   |
| Windows | WinTun / tap-windows | Install [WinTun](https://www.wintun.net/) for TUN mode |
| Termux | None (TUN unavailable) | TUN mode disabled. Service exposure + reverse proxy + web panels work fully. |

---

## Installation

```bash
# Option 1: From PyPI (recommended)
pip install localnetwork-ecosystem

# Option 2: From source
git clone https://github.com/your-org/LocalNetwork_Ecosystem.git
cd LocalNetwork_Ecosystem
pip install -r requirements.txt
pip install -e .      # makes localnetwork-* commands available
```

After installation, three commands are available:

| Command | Purpose |
|---------|---------|
| `localnetwork-server` | Runs the mediation server (registry, auth, relay) |
| `localnetwork-client` | Runs the VPN client daemon (connect + keepalive) |
| `localnetwork-cli` | Management CLI for creating/joining/listing networks |

## Quick Start (2 machines, 5 minutes)

### 1. Start the mediation server (machine 1)

```bash
localnetwork-server --host 0.0.0.0 --port 54000 --log-level INFO
```

You should see:
```
mediation server listening on 0.0.0.0:54000 (max_clients=256)
```

> **Firewall:** open TCP port `54000` so clients can reach the server.

### 2. Connect client A (machine 1, same terminal or another)

```bash
# Terminal 2 — connect to the server and stay online
localnetwork-client --server localhost:54000 --log-level INFO
```

### 3. Connect client B (machine 2)

```bash
localnetwork-client --server <server-ip>:54000 --log-level INFO
```

### 4. Create a network (from client A, in a 3rd terminal)

```bash
localnetwork-cli --host localhost --port 54000 create mynet --password secret
# → Created network 'mynet' with id 7f9c2b14-...
```

### 5. Join the network (from client B)

```bash
localnetwork-cli --host <server-ip> --port 54000 join 7f9c2b14-... --password secret
# → Joined network 7f9c2b14-...
```

Both daemons now log `peer online` notifications — the two clients can
see each other through the mediation server. (Direct P2P tunnels between
peers arrive with the tunnel manager; see [Roadmap](#roadmap).)

### Other useful commands

```bash
localnetwork-cli list                 # networks you belong to
localnetwork-cli status               # connection status + platform capabilities
localnetwork-cli info <network-id>    # network details (owner, topology, members)
localnetwork-cli leave <network-id>   # leave a network
localnetwork-client --detect-platform # print platform capabilities and exit
localnetwork-server --version
```

---

## Development

### Dev runner script

The quickest way to set up a development environment and try the whole stack:

**Linux / macOS:**

```bash
./scripts/run_dev.sh setup   # venv + deps + editable install (first time)
./scripts/run_dev.sh test    # run the full test suite
./scripts/run_dev.sh server  # start the mediation server
./scripts/run_dev.sh client  # start a client daemon
./scripts/run_dev.sh demo    # launch server + 2 clients in separate terminals
./scripts/run_dev.sh cli -- create mynet --password secret
./scripts/run_dev.sh clean   # remove venv and caches
```

**Windows (PowerShell or cmd):**

```bat
scripts\run_dev.bat setup
scripts\run_dev.bat test
scripts\run_dev.bat server
scripts\run_dev.bat client
scripts\run_dev.bat demo
scripts\run_dev.bat cli -- create mynet --password secret
scripts\run_dev.bat clean
```

Run `./scripts/run_dev.sh help` (or `scripts\run_dev.bat help`) for the full
command reference inside the script.

### Running tests directly

```bash
# Unit & integration tests (no root needed)
python -m pytest tests/ -v

# A specific test file
python -m pytest tests/test_encryption.py -v

# A single test
python -m pytest tests/test_nat_traversal.py::TestPunchPeer -v
```

See [DESIGN.md](DESIGN.md) for architecture and protocol details, [TODO.md](TODO.md)
for the phased implementation plan, and [AI_COMMIT_RULES.md](AI_COMMIT_RULES.md)
for the git workflow AI agents must follow.

---

## Roadmap

The project is developed in phases ([TODO.md](TODO.md)). Everything below marked
**planned** is specified in [DESIGN.md](DESIGN.md) but not implemented yet.

### Implemented

- ✅ Mediation server: register → auth → create/join/leave/list networks
- ✅ Relay fallback (server forwards encrypted frames when P2P fails)
- ✅ Client identity (RSA-2048), control channel, heartbeats, reconnection
- ✅ UDP hole-punching engine + STUN NAT classification
- ✅ P2P tunnel manager + keepalive manager
- ✅ Platform capability detection
- ✅ Full test suite (224+ tests)

### Planned

- ⏳ TUN virtual interface (full IP-level LAN emulation — ping/SSH over the VPN)
- ⏳ Network topologies (hub-and-spoke, gateway)
- ⏳ Service exposure / port forwarding (share one port, no root needed)
- ⏳ Setup wizard & friendly CLI UX (`localnetwork` launcher, `--daemon`, `diagnose`)
- ⏳ Web admin panels (server + client dashboards)
- ⏳ Reverse proxy / load balancer

---

## Getting Started (planned UX)

> The friendly wizard described below is **planned** (Phase 10). Until then use
> the [Quick Start](#quick-start-2-machines-5-minutes) section above.

No technical knowledge needed. The setup wizard will guide you through everything.

### If someone invited you to their network

```bash
# 1. Install
pip install localnetwork-ecosystem

# 2. Start — the wizard will appear
localnetwork

# The wizard asks:
#   "What would you like to do?" → Choose "Join an existing network"
#   "Enter the network address:" → Paste what your friend sent you
#   "Enter the password:" → Type the password they gave you
#
# That's it! You're connected.
```

### If you want to create your own network

```bash
# 1. Install
pip install localnetwork-ecosystem

# 2. Start — the wizard will appear
localnetwork

# The wizard asks:
#   "What would you like to do?" → Choose "Create a new network"
#   "What should we call it?" → Type a name like "My Gaming Network"
#   "Set a password:" → Pick something you'll share with friends
#
# Done! Share the network address and password with your friends.
```

### If you want to share a game server or web app

```bash
# 1. Open your web dashboard
localnetwork dashboard

# 2. Click "Share a service"
# 3. Pick "Game server" (or "Web app" / "Other")
# 4. Enter the port number (e.g., 25565 for Minecraft)
# 5. Click "Share"
#
# Your friends will see it appear and can connect with one click.
```

### If you want to set up the reverse proxy

```bash
# 1. Start the setup wizard for the proxy
localnetwork proxy-setup

# The wizard asks:
#   "What port should the proxy listen on?" → 80 or 8080
#   "Where should traffic go?" → Enter your app's address (e.g., localhost:3000)
#   "Add another destination server?" → Yes/No (for load balancing)
#
# 2. The proxy starts automatically. Open the dashboard:
localnetwork proxy-dashboard
```

---

## CLI Reference

### Virtual LAN Server

```
localnetwork-server [OPTIONS]

Options:
  --host HOST         Bind address (default: 0.0.0.0)
  --port PORT         TCP port (default: 54000)
  --web-port PORT     Admin panel HTTP port (flag exists; panel itself is ⏳ planned)
  --max-clients N     Maximum concurrent clients (default: 256)
  --log-level LEVEL   DEBUG | INFO | WARNING | ERROR (default: INFO)
  --version           Show version
```

### Virtual LAN Client

```
localnetwork-client [OPTIONS]

Options:
  --server HOST:PORT     Mediation server address (default: localhost:54000)
  --identity-dir PATH    Key storage directory (default: ~/.localnetwork/)
  --virtual-ip IP        Request a specific virtual IP
  --web-port PORT        Admin panel HTTP port (flag exists; panel itself is ⏳ planned)
  --log-level LEVEL      DEBUG | INFO | WARNING | ERROR
  --detect-platform      Print platform capabilities and exit
  --version              Show version
```

### Virtual LAN Management CLI

```
localnetwork-cli <command> [ARGS]

Global options:
  --host HOST        Server host (default: localhost)
  --port PORT        Server port (default: 54000)

Commands (✅ implemented, ⏳ planned):
  ✅ create NAME [--password PASS] [--topology mesh|hub|gateway]
      Create a new virtual network; prints the network id to share

  ✅ join NETWORK [--password PASS]
      Join an existing network

  ✅ leave NETWORK
      Leave a network

  ✅ list
      List networks you belong to (name, id, topology, members)

  ✅ status
      Show connection status and platform capabilities

  ✅ info NETWORK
      Show details about a network (members, topology, owner)

  ✅ version
      Show version information

  ⏳ peer-endpoints PEER_ID
      Show the public endpoints of a peer (for debugging)

  ⏳ expose / unexpose / services / map / unmap
      Service exposure (port forwarding) — Phase 14
```

### Reverse Proxy ⏳

```
localnetwork-proxy [OPTIONS]   # planned — Phase 17+

Options:
  --config PATH      YAML configuration file (default: proxy-config.yml)
  --workers N        Number of worker processes (default: auto = CPU count)
  --validate-config  Parse and validate the config file, then exit
  --version          Show version
```

### Environment variables

Client and server options can also be set via environment variables or a `.env` file:

| Variable                  | Equivalent            |
|---------------------------|-----------------------|
| `LNSERVER_HOST`           | `--host`              |
| `LNSERVER_PORT`           | `--port`              |
| `LNCLIENT_SERVER`         | `--server`            |
| `LNCLIENT_IDENTITY_DIR`   | `--identity-dir`      |
| `LNCLIENT_LOG_LEVEL`      | `--log-level`         |

---

## Security

| Layer            | Mechanism                                          |
|------------------|----------------------------------------------------|
| **Identity**     | RSA-2048 key pair generated locally. Private key never transmitted. |
| **Authentication** | Server sends 256-bit nonce; client signs it with private key. |
| **Tunnel key exchange** | ECDH (X25519) ephemeral keys exchanged during hole-punch. |
| **Tunnel encryption** | AES-256-GCM — encrypts and authenticates every frame. |
| **Replay protection** | Monotonic sequence numbers on every tunnel. |
| **Network access** | bcrypt-hashed passwords; server enforces membership. |
| **Zero trust**    | Server never possesses private keys or session keys. Cannot decrypt traffic — even when relaying. |

---

## Troubleshooting

| Symptom                                | Likely cause & fix                                           |
|----------------------------------------|--------------------------------------------------------------|
| `localnetwork-*: command not found`   | Package not installed with entry points. Run `pip install -e .` |
| `ConnectionRefusedError` on client     | Server not running, or firewall blocking TCP 54000            |
| `Identity error` on CLI commands       | No identity yet — run `localnetwork-client` once to generate one |
| `WRONG_PASSWORD` on join               | Passwords are case-sensitive. Ask the network owner to confirm. |
| `NO_SHARED_NETWORK` on relay           | Both clients must join the same network before connecting     |
| Tunnels stuck in `CONNECTING`          | Both peers behind symmetric NAT — relay fallback should kick in. Check server logs. |
| High latency / low throughput          | You're on relay fallback, not direct P2P                      |

### Planned features (not yet available)

| Symptom                                | Status                                             |
|----------------------------------------|----------------------------------------------------|
| `Permission denied` on `/dev/net/tun`  | TUN interface is planned (Phase 8) — not implemented |
| Can't ping virtual IP                  | Requires TUN mode (Phase 8)                        |
| `localnetwork diagnose`                | Planned (Phase 10)                                 |
| Proxy errors (502/504, cache, SSL)     | Reverse proxy is planned (Phase 17+)               |

---

## License

MIT

---

## Project Layout

```
server/     Mediation server (registry, networks, relay, web admin)
client/     VPN client (identity, control channel, NAT traversal, tunnels, TUN)
proxy/      Reverse proxy / load balancer (master-worker, HTTP, caching, SSL)
common/     Shared protocol constants, messages, frames, and web UI assets
common/web_static/  Shared admin-panel design system (CSS/JS)
scripts/    Development runner scripts (run_dev.sh / run_dev.bat)
tests/      Unit & integration tests (pytest)
```

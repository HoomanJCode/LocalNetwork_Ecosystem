# LocalNetwork Ecosystem

A Python-based networking toolkit with three core features:
- **Virtual LAN** — connect remote computers into a private encrypted network
- **Reverse Proxy** — high-performance HTTP/TCP load balancer and traffic manager
- **Web Admin Panels** — browser-based dashboards for server, client, and proxy

> **Status:** In development — [DESIGN.md](DESIGN.md) defines the architecture and
> [TODO.md](TODO.md) tracks the phased implementation plan.

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
```

## Getting Started

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
  --web-port PORT     Admin panel HTTP port (default: 54001. Set 0 to disable)
  --max-clients N     Maximum concurrent clients (default: 256)
  --admin-user USER   Admin panel username (env: LNSERVER_ADMIN_USER)
  --admin-pass PASS   Admin panel password (env: LNSERVER_ADMIN_PASS)
  --log-level LEVEL   DEBUG | INFO | WARNING | ERROR (default: INFO)
```

### Virtual LAN Client

```
localnetwork-client [OPTIONS]

Options:
  --server HOST:PORT     Mediation server address (default: localhost:54000)
  --identity-dir PATH    Key storage directory (default: ~/.localnetwork/)
  --virtual-ip IP        Request a specific virtual IP
  --daemon               Run in background
  --pid-file PATH        PID file for daemon mode
  --log-file PATH        Log output file
  --web-port PORT        Admin panel HTTP port (default: 54002. Set 0 to disable)
  --verbose, -v          Verbose output
  --quiet, -q            Minimal output
  --version              Show version
```

### Virtual LAN Management CLI

```
localnetwork-cli <command> [ARGS]

Commands:
  create NAME [--password PASS] [--topology mesh|hub|gateway]
      Create a new virtual network

  join NETWORK [--password PASS]
      Join an existing network

  leave NETWORK
      Leave a network

  list
      List networks you belong to

  status
      Show connection status, virtual IP, peer list, tunnel states

  info NETWORK
      Show details about a network (members, topology, owner)

  peer-endpoints PEER_ID
      Show the public endpoints of a peer (for debugging)

  expose NAME --protocol tcp|udp --port PORT [--host HOST]
      Expose a local service to the network

  unexpose SERVICE_ID
      Stop exposing a service

  services
      List services available on your networks

  map SERVICE_ID [--port PORT] [--strategy same|auto|manual]
      Map a remote service to a local port

  unmap SERVICE_ID
      Stop mapping a remote service

  version
      Show version information
```

### Reverse Proxy

```
localnetwork-proxy [OPTIONS]

Options:
  --config PATH      YAML configuration file (default: proxy-config.yml)
  --workers N        Number of worker processes (default: auto = CPU count)
  --validate-config  Parse and validate the config file, then exit
  --version          Show version

Signals:
  SIGHUP             Graceful reload — re-read config, restart workers
  SIGINT / SIGTERM   Graceful shutdown — drain connections, stop workers
```

### Environment variables

All options can also be set via environment variables or a `.env` file:

| Variable                  | Equivalent            |
|---------------------------|-----------------------|
| `LNSERVER_HOST`           | `--host`              |
| `LNSERVER_PORT`           | `--port`              |
| `LNCLIENT_SERVER`         | `--server`            |
| `LNCLIENT_IDENTITY_DIR`   | `--identity-dir`      |
| `LNCLIENT_LOG_LEVEL`      | `--log-level`         |
| `LNPROXY_CONFIG`          | `--config`            |
| `LNPROXY_WORKERS`         | `--workers`           |

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
| `Permission denied` on `/dev/net/tun`  | Run the client with `sudo`, or add your user to the right group |
| Tunnels stuck in `CONNECTING`          | Both peers behind symmetric NAT — relay fallback should kick in. Check server logs. |
| Can't ping virtual IP                  | TUN interface not up. Check `localnetwork-cli status`. Run `ip addr show` to verify. |
| Server unreachable                     | Firewall blocking TCP port 54000. Open it on the server machine. |
| "Wrong password" on join               | Passwords are case-sensitive. Ask the network owner to confirm. |
| High latency / low throughput          | You're on relay fallback, not direct P2P. Check NAT types with `--diagnose-nat`. |

### Proxy-specific

| Symptom                                | Likely cause & fix                                           |
|----------------------------------------|--------------------------------------------------------------|
| `bind: address already in use`         | Another process is using the listen port. Change the port or kill the other process. |
| Upstream server stays `down`           | Backend is crashing on every request. Check backend logs.     |
| 502 Bad Gateway                        | Proxy can't reach any upstream server. Verify backends are running. |
| 504 Gateway Timeout                    | Backend taking too long to respond. Increase `proxy_read_timeout` in config. |
| Cache not working                      | Backend sending `Cache-Control: no-cache` or `private`. Check response headers. |
| SSL handshake fails                    | Certificate or key file path wrong, or key doesn't match cert. |

### Diagnose your connection

```bash
localnetwork diagnose
```

Shows whether your connection is good for direct P2P or will use relay.

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
tests/      Unit & integration tests (pytest)
```

---

## Development

See [DESIGN.md](DESIGN.md) for architecture and protocol details, [TODO.md](TODO.md)
for the phased implementation plan, and [AI_COMMIT_RULES.md](AI_COMMIT_RULES.md)
for the git workflow AI agents must follow.

### Running tests

```bash
# Unit & integration tests (no root needed)
python -m pytest tests/ -v

# End-to-end tests (requires root for TUN)
sudo python -m pytest tests/ -v --e2e

# Specific test file
python -m pytest tests/test_encryption.py -v
```

# LocalNetwork Ecosystem

A Python-based networking toolkit with three core features:
- **Virtual LAN** — connect remote computers into a private encrypted network
- **Service Exposure** — share individual TCP/UDP services without root or TUN
- **Reverse Proxy** — high-performance HTTP/TCP load balancer and traffic manager
- **Web Admin Panels** — browser-based dashboards for server, client, and proxy

> **Status:** Core implementation complete (22/23 phases). See [TODO.md](TODO.md) for details.
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
git clone https://github.com/HoomanJCode/LocalNetwork_Ecosystem.git
cd LocalNetwork_Ecosystem
pip install -r requirements.txt
pip install -e .      # makes localnetwork-* commands available
```

After installation, four commands are available:

| Command | Purpose |
|---------|---------|
| `localnetwork-server` | Runs the mediation server (registry, auth, relay) |
| `localnetwork-client` | Runs the VPN client daemon (connect, tunnels, TUN) |
| `localnetwork-cli` | Management CLI for creating/joining/listing networks |
| `localnetwork-proxy` | Reverse proxy and load balancer |

---

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

### 2. Connect client A (machine 1, another terminal)

```bash
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

Both daemons now log `peer online` notifications. For full TUN-mode (virtual LAN with ping/SSH), start clients with `--tun` and run as root.

### Enable Virtual LAN (TUN mode)

```bash
# Linux — needs root
sudo localnetwork-client --server localhost:54000 --tun --virtual-ip 25.1.0.1

# After TUN is up, peers can ping each other
ping 25.1.0.2
```

### Start the Reverse Proxy

```bash
# Create a proxy config file (proxy.yaml):
cat > proxy.yaml << 'EOF'
http: [8080]
upstreams:
  - name: app
    servers:
      - localhost:3000
locations:
  - path: /
    upstream: app
EOF

# Start the proxy
localnetwork-proxy --config proxy.yaml
```

### Other useful commands

```bash
localnetwork-cli list                 # networks you belong to
localnetwork-cli status               # connection status + platform capabilities
localnetwork-cli info <network-id>    # network details (owner, topology, members)
localnetwork-cli leave <network-id>   # leave a network
localnetwork-client --detect-platform # print platform capabilities and exit
localnetwork-client --tun             # enable TUN virtual LAN interface
localnetwork-client --daemon          # run as background daemon
localnetwork-proxy --validate-config proxy.yaml  # validate proxy config without starting
localnetwork-server --version
```

### Web Admin Panels

Once the server or client is running, open the web dashboard:

| Panel | URL | Description |
|-------|-----|-------------|
| Server Admin | `http://<server>:54001` | Monitor clients, networks, relay, config |
| Client Admin | `http://localhost:54002` | Manage networks, peers, tunnels, services |
| Proxy Admin | `http://localhost:54010` | View upstreams, cache stats, active connections |

---

## CLI Reference

### Virtual LAN Server

```
localnetwork-server [OPTIONS]

Options:
  --host HOST         Bind address (default: 0.0.0.0)
  --port PORT         TCP port (default: 54000)
  --web-port PORT     Admin panel HTTP port (default: 54001)
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
  --tun                  Enable TUN mode (virtual LAN interface, needs root)
  --no-tun               Disable TUN mode even if available
  --web-port PORT        Admin panel HTTP port (default: 54002)
  --log-level LEVEL      DEBUG | INFO | WARNING | ERROR
  --daemon               Fork to background (PID file in ~/.localnetwork/)
  --detect-platform      Print platform capabilities and exit
  --version              Show version
```

### Virtual LAN Management CLI

```
localnetwork-cli [--host HOST] [--port PORT] <command> [ARGS]

Commands:
  create NAME [--password PASS] [--topology mesh|hub|gateway] [--virtual-ip IP]
      Create a new virtual network

  join NETWORK [--password PASS]
      Join an existing network

  leave NETWORK
      Leave a network

  list
      List networks you belong to

  status
      Show connection status and platform capabilities

  info NETWORK
      Show network details (members, topology, owner)

  version
      Show version information
```

### Reverse Proxy

```
localnetwork-proxy [OPTIONS]

Options:
  --config, -c PATH    YAML configuration file (default: proxy.yaml)
  --workers, -w N      Number of worker processes (default: auto = CPU count)
  --validate-config    Parse and validate the config file, then exit
  --log-level LEVEL    DEBUG | INFO | WARNING | ERROR
  --version, -V        Show version
```

### Proxy Configuration Reference (YAML)

```yaml
# proxy.yaml — LocalNetwork Reverse Proxy configuration

workers: 0               # 0 = auto (CPU count)
worker_connections: 1024

http: [80, 8080]         # HTTP listen ports
https: [443]             # HTTPS listen ports

upstreams:
  - name: app
    algorithm: round_robin   # round_robin | least_conn | ip_hash | random
    max_failures: 3
    fail_timeout: 10
    servers:
      - host: 10.0.0.1
        port: 3000
        weight: 3            # higher = more traffic
      - host: 10.0.0.2
        port: 3000
        weight: 1
        backup: true         # only used when primaries are down
      - 10.0.0.3:3000        # short form

locations:
  - path: /api
    upstream: app
    rate_limit: 100          # requests/second (0 = unlimited)
    compress: true
  - path: /static
    root: /var/www/static    # serve static files
    cache: true
    cache_ttl: 600

ssl:
  443:
    cert: /etc/certs/server.crt
    key: /etc/certs/server.key

cache:
  path: /tmp/lnproxy-cache
  max_size: 104857600       # 100 MB

gzip:
  enabled: true
  min_length: 256
  level: 6

access_log: /var/log/lnproxy/access.log
error_log: /var/log/lnproxy/error.log
log_format: combined          # combined | json

admin:
  port: 54010
```

### Environment variables

Client and server options can also be set via environment variables or a `.env` file:

| Variable                  | Equivalent            |
|---------------------------|-----------------------|
| `LNSERVER_HOST`           | `--host`              |
| `LNSERVER_PORT`           | `--port`              |
| `LNSERVER_WEB_PORT`       | `--web-port` (server) |
| `LNSERVER_MAX_CLIENTS`    | `--max-clients`       |
| `LNSERVER_LOG_LEVEL`      | `--log-level` (server)|
| `LNSERVER_ADMIN_USER`     | Admin panel username  |
| `LNSERVER_ADMIN_PASS`     | Admin panel password  |
| `LNCLIENT_SERVER`         | `--server`            |
| `LNCLIENT_SERVER_HOST`    | `--server` (host)     |
| `LNCLIENT_SERVER_PORT`    | `--server` (port)     |
| `LNCLIENT_IDENTITY_DIR`   | `--identity-dir`      |
| `LNCLIENT_VIRTUAL_IP`     | `--virtual-ip`        |
| `LNCLIENT_WEB_PORT`       | `--web-port` (client) |
| `LNCLIENT_LOG_LEVEL`      | `--log-level` (client)|

---

## Development

### Dev runner script

**Linux / macOS:**

```bash
./scripts/run_dev.sh setup   # venv + deps + editable install (first time)
./scripts/run_dev.sh test    # run the full test suite
./scripts/run_dev.sh server  # start the mediation server
./scripts/run_dev.sh client  # start a client daemon
./scripts/run_dev.sh demo    # launch server + 2 clients in separate terminals
./scripts/run_dev.sh cli -- create mynet --password secret
./scripts/run_dev.sh proxy   # start the reverse proxy
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
scripts\run_dev.bat proxy
scripts\run_dev.bat clean
```

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

The project is developed in phases ([TODO.md](TODO.md)). 22 of 23 phases are complete.

### Implemented ✅

- ✅ Mediation server: register → auth → create/join/leave/list networks
- ✅ Relay fallback (server forwards encrypted frames when P2P fails)
- ✅ Client identity (RSA-2048), control channel, heartbeats, reconnection
- ✅ UDP hole-punching engine + STUN NAT classification
- ✅ P2P tunnel manager + AES-256-GCM encryption + keepalive manager
- ✅ Platform capability detection (Linux, macOS, Windows, Termux)
- ✅ TUN virtual interface (Linux/macOS/Windows — full IP-level LAN emulation)
- ✅ Network topologies (mesh, hub-and-spoke, gateway)
- ✅ Service exposure / port forwarding (share individual TCP/UDP services, no root)
- ✅ Setup wizard & friendly CLI UX (`--daemon`, colored output, status indicator)
- ✅ User-facing error catalog (plain language messages with suggestions)
- ✅ Server web admin panel (clients, networks, relay, config, logs, access control)
- ✅ Client web admin panel (dashboard, networks, peers, services, NAT diagnostics)
- ✅ Reverse proxy / load balancer (master-worker model, 4 LB algorithms, health checks)
- ✅ Reverse proxy: gzip compression, access logging, runtime stats
- ✅ Reverse proxy web admin panel (upstreams, cache, config, logs)
- ✅ Full test suite (unit + integration + proxy config)

### Remaining

- ⬜ Documentation polish & docstring coverage (Phase 23)

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
| `Permission denied` on `/dev/net/tun`  | TUN mode requires root. Run with `sudo` or use service exposure mode. |
| Proxy 502 Bad Gateway                  | Upstream server unreachable. Check health status in proxy admin panel. |
| Proxy returns connection refused       | Upstream port not open. Verify backend servers are running.    |

---

## License

MIT

---

## Project Layout

```
server/     Mediation server (registry, networks, relay, web admin)
client/     VPN client (identity, control channel, NAT traversal, tunnels, TUN, web admin)
proxy/      Reverse proxy / load balancer (master-worker, HTTP, caching, web admin)
common/     Shared protocol constants, messages, frames, errors, logging, web UI assets
common/web_static/  Shared admin-panel design system (CSS/JS)
scripts/    Development runner scripts (run_dev.sh / run_dev.bat)
tests/      Unit & integration tests (pytest)
docs/       Architecture (DESIGN.md) and phased implementation plan (TODO.md + todos/)
```

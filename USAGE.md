# LocalNetwork Ecosystem — Quick Reference

A one-page cheat sheet for all commands.
For the full guide see [README.md](README.md); for design details see [DESIGN.md](DESIGN.md).

---

## Commands at a glance

| Command | What it does |
|---------|--------------|
| `localnetwork-server` | Runs the mediation server (registry, auth, relay, web admin) |
| `localnetwork-client` | Runs the VPN client daemon (connect, tunnels, TUN, web admin) |
| `localnetwork-cli` | Management CLI for networks |
| `localnetwork-proxy` | Reverse proxy and load balancer |

---

## 1. Start the server

```bash
localnetwork-server --host 0.0.0.0 --port 54000 --log-level INFO
```

| Option | Default | Purpose |
|--------|---------|---------|
| `--host HOST` | `0.0.0.0` | Bind address |
| `--port PORT` | `54000` | Control-channel TCP port |
| `--web-port PORT` | `54001` | Admin panel port |
| `--max-clients N` | `256` | Max concurrent clients |
| `--log-level LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |
| `--version` | — | Show version |

Web admin panel: `http://<server>:54001` (login with `LNSERVER_ADMIN_USER`/`LNSERVER_ADMIN_PASS` env vars).

---

## 2. Start a client

```bash
localnetwork-client --server localhost:54000 --log-level INFO
```

| Option | Default | Purpose |
|--------|---------|---------|
| `--server HOST:PORT` | `localhost:54000` | Mediation server address |
| `--identity-dir PATH` | `~/.localnetwork/` | Key storage (auto-generated on first run) |
| `--virtual-ip IP` | — | Request a specific virtual IP |
| `--tun` | — | Enable TUN mode (virtual LAN, needs root) |
| `--no-tun` | — | Disable TUN mode even if available |
| `--web-port PORT` | `54002` | Admin panel port |
| `--log-level LEVEL` | `INFO` | Log verbosity |
| `--daemon` | — | Fork to background |
| `--detect-platform` | — | Print platform capabilities and exit |
| `--version` | — | Show version |

Web admin panel: `http://localhost:54002`.

---

## 3. Manage networks with `localnetwork-cli`

```bash
localnetwork-cli [--host HOST] [--port PORT] <command> [ARGS]
```

Global options: `--host` (default `localhost`), `--port` (default `54000`),
`--identity-dir` (default `~/.localnetwork/`).

### Commands

| Command | Example |
|---------|---------|
| `create NAME [--password PASS] [--topology mesh\|hub\|gateway]` | `localnetwork-cli create mynet --password secret` |
| `join NETWORK [--password PASS]` | `localnetwork-cli join <network-id> --password secret` |
| `leave NETWORK` | `localnetwork-cli leave <network-id>` |
| `list` | `localnetwork-cli list` |
| `status` | `localnetwork-cli status` |
| `info NETWORK` | `localnetwork-cli info <network-id>` |
| `version` | `localnetwork-cli version` |

`--topology` accepts `mesh` (default), `hub` → hub-and-spoke, `gateway`.

---

## 4. Reverse Proxy

```bash
localnetwork-proxy --config proxy.yaml
```

| Option | Default | Purpose |
|--------|---------|---------|
| `--config, -c PATH` | `proxy.yaml` | YAML config file |
| `--workers, -w N` | `0` (auto) | Worker processes |
| `--validate-config` | — | Parse config and exit |
| `--log-level LEVEL` | `INFO` | Log verbosity |
| `--version, -V` | — | Show version |

Web admin panel: `http://localhost:54010`.

Example minimal config:

```yaml
http: [8080]
upstreams:
  - name: backend
    servers:
      - localhost:3000
      - localhost:3001
locations:
  - path: /
    upstream: backend
```

---

## 5. Typical flow (two machines)

```bash
# Machine 1 — server
localnetwork-server --host 0.0.0.0 --port 54000

# Machine 1 — client A + create network (3rd terminal)
localnetwork-client --server localhost:54000
localnetwork-cli create mynet --password secret
# → Created network 'mynet' with id 7f9c2b14-...

# Machine 2 — client B + join
localnetwork-client --server <server-ip>:54000
localnetwork-cli join 7f9c2b14-... --password secret
```

Both daemons now log `peer online` notifications.

### TUN mode (virtual LAN with ping/SSH)

```bash
# As root on both machines
sudo localnetwork-client --server <server-ip>:54000 --tun --virtual-ip 25.1.0.1
sudo localnetwork-client --server <server-ip>:54000 --tun --virtual-ip 25.1.0.2

# Now you can ping the other machine
ping 25.1.0.2
ssh user@25.1.0.2
```

---

## 6. Development runner scripts

```bash
# Linux / macOS
./scripts/run_dev.sh setup    # first-time: venv + deps + editable install
./scripts/run_dev.sh test     # run all tests
./scripts/run_dev.sh server   # start mediation server
./scripts/run_dev.sh client   # start a client daemon
./scripts/run_dev.sh proxy    # start reverse proxy
./scripts/run_dev.sh demo     # server + 2 clients in separate terminals
./scripts/run_dev.sh cli -- create mynet --password secret
./scripts/run_dev.sh clean    # remove venv + caches
```

```bat
:: Windows (PowerShell or cmd)
scripts\run_dev.bat setup
scripts\run_dev.bat test
scripts\run_dev.bat server
scripts\run_dev.bat client
scripts\run_dev.bat proxy
scripts\run_dev.bat demo
scripts\run_dev.bat cli -- create mynet --password secret
scripts\run_dev.bat clean
```

---

## 7. Testing

```bash
python -m pytest tests/ -v                          # full suite
python -m pytest tests/test_nat_traversal.py -v     # one file
python -m pytest tests/test_nat_traversal.py::TestPunchPeer -v   # one class
```

---

## 8. Environment variables

| Variable | Equivalent | Applies to |
|----------|-----------|------------|
| `LNSERVER_HOST` | `--host` | server |
| `LNSERVER_PORT` | `--port` | server |
| `LNSERVER_WEB_PORT` | `--web-port` | server |
| `LNSERVER_MAX_CLIENTS` | `--max-clients` | server |
| `LNSERVER_LOG_LEVEL` | `--log-level` | server |
| `LNSERVER_ADMIN_USER` | Admin username | server web panel |
| `LNSERVER_ADMIN_PASS` | Admin password | server web panel |
| `LNCLIENT_SERVER` | `--server` (host:port) | client |
| `LNCLIENT_SERVER_HOST` | `--server` (host) | client |
| `LNCLIENT_SERVER_PORT` | `--server` (port) | client |
| `LNCLIENT_IDENTITY_DIR` | `--identity-dir` | client |
| `LNCLIENT_VIRTUAL_IP` | `--virtual-ip` | client |
| `LNCLIENT_WEB_PORT` | `--web-port` | client |
| `LNCLIENT_LOG_LEVEL` | `--log-level` | client |

---

## 9. Quick troubleshooting

| Symptom | Fix |
|---------|-----|
| `localnetwork-*: command not found` | `pip install -e .` |
| `ConnectionRefusedError` | Server not running, or firewall blocks TCP 54000 |
| `Identity error` on CLI | Run `localnetwork-client` once to generate an identity |
| `WRONG_PASSWORD` on join | Passwords are case-sensitive |
| `NO_SHARED_NETWORK` | Both clients must join the same network first |
| TUN `Permission denied` | TUN mode needs root — use `sudo` |
| Proxy 502 Bad Gateway | Upstream backend not reachable |

---

## 10. Where things live

```
server/     Mediation server (registry, networks, relay, web admin)
client/     VPN client (identity, control channel, NAT traversal, tunnels, TUN, web admin)
proxy/      Reverse proxy / load balancer (master-worker, HTTP, caching, web admin)
common/     Shared protocol constants, messages, frames, errors, logging, web UI assets
scripts/    Dev runner scripts (run_dev.sh / run_dev.bat)
tests/      Unit & integration tests (pytest)
docs/       Architecture (DESIGN.md) and phased implementation plan
```

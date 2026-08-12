# LocalNetwork Ecosystem — Quick Reference

A one-page cheat sheet for the currently implemented commands.
For the full guide see [README.md](README.md); for design details see [DESIGN.md](DESIGN.md).

> ✅ = implemented now &nbsp;·&nbsp; ⏳ = planned (see [README Roadmap](README.md#roadmap))

---

## Commands at a glance

| Command | What it does |
|---------|--------------|
| `localnetwork-server` | Runs the mediation server (registry, auth, relay) |
| `localnetwork-client` | Runs the VPN client daemon (connect + heartbeats) |
| `localnetwork-cli` | Management CLI for networks |

---

## 1. Start the server

```bash
localnetwork-server --host 0.0.0.0 --port 54000 --log-level INFO
```

| Option | Default | Purpose |
|--------|---------|---------|
| `--host HOST` | `0.0.0.0` | Bind address |
| `--port PORT` | `54000` | Control-channel TCP port |
| `--web-port PORT` | `54001` | Admin panel port (flag exists; panel ⏳) |
| `--max-clients N` | `256` | Max concurrent clients |
| `--log-level LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` |
| `--version` | — | Show version |

Expected output: `mediation server listening on 0.0.0.0:54000 (max_clients=256)`

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
| `--web-port PORT` | `54002` | Admin panel port (flag exists; panel ⏳) |
| `--log-level LEVEL` | `INFO` | Log verbosity |
| `--detect-platform` | — | Print platform capabilities and exit |
| `--version` | — | Show version |

Tip: run it once to generate your identity, then the `localnetwork-cli` commands will work.

---

## 3. Manage networks with `localnetwork-cli`

```bash
localnetwork-cli [--host HOST] [--port PORT] <command> [ARGS]
```

Global options: `--host` (default `localhost`), `--port` (default `54000`),
`--identity-dir` (default `~/.localnetwork/`).

### ✅ Implemented commands

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

### ⏳ Planned

`peer-endpoints`, `expose`, `unexpose`, `services`, `map`, `unmap` (service exposure, Phase 14).

---

## 4. Typical flow (two machines)

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

---

## 5. Development runner scripts

```bash
# Linux / macOS
./scripts/run_dev.sh setup    # first-time: venv + deps + editable install
./scripts/run_dev.sh test     # run all tests
./scripts/run_dev.sh server   # start mediation server
./scripts/run_dev.sh client   # start a client daemon
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
scripts\run_dev.bat demo
scripts\run_dev.bat cli -- create mynet --password secret
scripts\run_dev.bat clean
```

Run `./scripts/run_dev.sh help` or `scripts\run_dev.bat help` for the full in-script reference.

---

## 6. Testing

```bash
python -m pytest tests/ -v                          # full suite
python -m pytest tests/test_nat_traversal.py -v     # one file
python -m pytest tests/test_nat_traversal.py::TestPunchPeer -v   # one class
python -m pytest tests/test_nat_traversal.py::TestPunchPeer::test_two_local_sockets_punch_succeeds -v  # one test
```

---

## 7. Environment variables

Set in the shell or a `.env` file — same effect as the matching CLI flag.

| Variable | Equivalent | Applies to |
|----------|-----------|------------|
| `LNSERVER_HOST` | `--host` | server |
| `LNSERVER_PORT` | `--port` | server |
| `LNSERVER_WEB_PORT` | `--web-port` | server |
| `LNSERVER_MAX_CLIENTS` | `--max-clients` | server |
| `LNSERVER_LOG_LEVEL` | `--log-level` | server |
| `LNCLIENT_SERVER` | `--server` (host:port) | client |
| `LNCLIENT_SERVER_HOST` | `--server` (host) | client |
| `LNCLIENT_SERVER_PORT` | `--server` (port) | client |
| `LNCLIENT_IDENTITY_DIR` | `--identity-dir` | client |
| `LNCLIENT_VIRTUAL_IP` | `--virtual-ip` | client |
| `LNCLIENT_WEB_PORT` | `--web-port` | client |
| `LNCLIENT_LOG_LEVEL` | `--log-level` | client |

---

## 8. Quick troubleshooting

| Symptom | Fix |
|---------|-----|
| `localnetwork-*: command not found` | `pip install -e .` |
| `ConnectionRefusedError` | Server not running, or firewall blocks TCP 54000 |
| `Identity error` on CLI | Run `localnetwork-client` once to generate an identity |
| `WRONG_PASSWORD` on join | Passwords are case-sensitive |
| `NO_SHARED_NETWORK` | Both clients must join the same network first |

---

## 9. Where things live

```
server/     Mediation server (registry, networks, relay, web admin)
client/     VPN client (identity, control channel, NAT traversal, tunnels, TUN)
proxy/      Reverse proxy / load balancer (master-worker, HTTP, caching, SSL)
common/     Shared protocol constants, messages, frames, web UI assets
scripts/    Dev runner scripts (run_dev.sh / run_dev.bat)
tests/      Unit & integration tests (pytest)
```

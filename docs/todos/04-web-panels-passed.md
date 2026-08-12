# TODO — Phase 15–16: Web Admin Panels

> Part of the [LocalNetwork Ecosystem implementation plan](../TODO.md).
> Each task is a checkbox. Work through phases in order; items within a phase
> can be parallelized. Testing tasks are listed alongside the feature they test.

---

## Phase 15 — Server Web Admin Panel

- [x] 15.1 **`server/web/app.py`** — aiohttp web application setup
  - Create `aiohttp.web.Application` with routes registered
  - Start HTTP server on configurable port (default 54001) alongside the mediation server
  - Share references to `ClientRegistry`, `NetworkManager`, `RelayForwarder` for data access

- [x] 15.2 **`server/web/auth.py`** — Admin authentication
  - Admin credentials: `LNSERVER_ADMIN_USER` / `LNSERVER_ADMIN_PASS` env vars
  - On first launch with no env vars: generate random password, print to console
  - Session management with secure cookies (`aiohttp_session`)
  - Login/logout routes
  - Auth middleware that protects all panel routes

- [x] 15.3 **Server web routes:**
  - **Dashboard** (`/`): uptime, total/online clients, network count, relay stats, CPU/mem
  - **Clients** (`/clients`): table of all clients with status, join date, networks. Detail view per client
  - **Networks** (`/networks`): table of all networks with member count, owner, topology. Detail view per network
  - **Relay** (`/relay`): active relay paths, bytes relayed, queue depths
  - **Configuration** (`/config`): form to edit server settings, save to config
  - **Logs** (`/logs`): live log tail via SSE
  - **Access Control** (`/access`): ban/unban client IDs and IP ranges
  - **API endpoints:** JSON API for each page (used by JS for live updates via SSE/polling)

- [x] 15.4 **Server HTML templates** — Jinja2
  - `base.html`: shared layout (sidebar nav, header, footer, CSS/JS includes)
  - One template per page (dashboard, clients, client_detail, networks, network_detail, relay, config, logs, access)
  - Dark theme, responsive design

- [x] 15.5 **Server static assets**
  - `admin.css`: dark theme, table styles, status badges (online green/offline red), form styles, responsive
  - `dashboard.js`: SSE client, auto-refresh counters, chart for relay throughput (simple canvas or pure CSS)
  - `sse.js`: reusable SSE helper for live log tail and data updates

- [x] 15.6 **Write tests:**
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

- [x] 16.1 **`client/web/app.py`** — aiohttp web application setup
  - Create `aiohttp.web.Application` bound to `127.0.0.1` only (security)
  - Start HTTP server on configurable port (default 54002) alongside the client daemon
  - Share references to `ControlChannel`, `TunnelManager`, `NatTraversal` for data access

- [x] 16.2 **Client web routes:**
  - **Dashboard** (`/`): server connection status, virtual IP, uptime, active tunnels, bytes tx/rx, NAT type
  - **Networks** (`/networks`): list joined networks, create/join/leave actions
  - **Peers** (`/peers`): table of connected peers with state, latency, throughput. Per-peer detail view
  - **Services** (`/services`): expose local services, map remote services (see Phase 14)
  - **Configuration** (`/config`): form to edit client settings (server addr, identity dir, log level, TUN name)
  - **Logs** (`/logs`): live log tail via SSE
  - **NAT Diagnostics** (`/nat-diag`): run NAT type detection, show results, P2P compatibility matrix
  - **API endpoints:** JSON API for each page

- [x] 16.3 **Client HTML templates** — Jinja2
  - `base.html`: shared layout (sidebar nav, header, footer, CSS/JS includes)
  - One template per page (dashboard, networks, peers, peer_detail, services, config, logs, nat_diag)
  - Dark theme matching the server panel, responsive design

- [x] 16.4 **Client static assets**
  - `admin.css`: dark theme, status badges, table styles, form styles, responsive
  - `dashboard.js`: SSE client for live counters, tunnel state indicators
  - `sse.js`: reusable SSE helper

- [x] 16.5 **Write tests:**
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

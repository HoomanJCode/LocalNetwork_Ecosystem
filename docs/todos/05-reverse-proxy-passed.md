# TODO — Phase 17–22: Reverse Proxy

> Part of the [LocalNetwork Ecosystem implementation plan](../TODO.md).
> Each task is a checkbox. Work through phases in order; items within a phase
> can be parallelized. Testing tasks are listed alongside the feature they test.

---

## Phase 17 — Reverse Proxy: Core Architecture

- [x] 17.1 **`proxy/config.py`** — Configuration loader
  - `ProxyConfig` dataclass mirroring the YAML schema:
    workers, worker_connections, http blocks, upstream blocks, ssl blocks,
    cache settings, rate_limiting, access_control, compression, logging, admin port
  - `load_config(path: str) -> ProxyConfig` — parse YAML/JSON, validate
  - Sensible defaults for all optional fields

- [x] 17.2 **`proxy/master.py`** — Master process
  - `MasterProcess` class:
    - `start()` — read config, bind listen sockets, spawn N workers
    - `reload()` — handle SIGHUP: read new config, spawn new workers, gracefully kill old
    - `shutdown()` — handle SIGINT/SIGTERM: stop all workers, close sockets
    - Track worker PIDs, restart any that crash unexpectedly
  - Use `os.fork()` or `multiprocessing` for worker processes
  - `SO_REUSEPORT` on listen sockets so kernel distributes across workers

- [x] 17.3 **`proxy/worker.py`** — Worker event loop
  - `WorkerProcess` class:
    - `start(listen_sockets)` — create asyncio event loop, accept connections
    - `accept_loop()` — accept new connections, spawn `Connection` coroutines
    - Track active connection count for status reporting
    - Graceful shutdown: stop accepting, drain existing connections
  - Optional `uvloop` integration for performance

- [x] 17.4 **`proxy/main.py`** — CLI entry point
  - `localnetwork-proxy [--config PATH] [--workers N]` command
  - Signal handlers for SIGHUP (reload), SIGINT/SIGTERM (shutdown)
  - `--version` flag
  - `--validate-config` flag: parse and validate config, exit

- [x] 17.5 **Write tests:**
  - `tests/test_proxy_config.py`
    - Load minimal valid config → all defaults filled
    - Load full config → all values parsed correctly
    - Invalid YAML → clear error message
    - Missing required field → validation error
    - `--validate-config` exits 0 on valid, non-zero on invalid

---

## Phase 18 — Reverse Proxy: Connection Handling & HTTP Processing

- [x] 18.1 **`proxy/connection.py`** — HTTP connection state machine
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

- [x] 18.2 **`proxy/upstream.py`** — Upstream backend management
  - `UpstreamPool` class:
    - `get_server(algorithm)` — select backend using configured load balancer
    - `connect(server)` — async TCP connect + optional TLS handshake
    - Connection pooling: keep-alive connections reused across requests
    - `release(server, connection)` — return connection to pool or close
    - Track per-server: active connections, failure count, last failure time
  - `UpstreamServer` dataclass: host, port, weight, max_conns, backup, down, state (up/down/unavailable)

- [x] 18.3 **Header management in `connection.py`:**
  - Set `Host` header to upstream target
  - Add `X-Real-IP` with original client address
  - Append to `X-Forwarded-For`
  - Set `X-Forwarded-Proto` based on original scheme
  - Strip hop-by-hop headers: Connection, Keep-Alive, Transfer-Encoding, TE, Trailer

- [x] 18.4 **Write tests:**
  - `tests/test_proxy_integration.py` (part 1)
    - Start proxy with a single backend → HTTP request proxied correctly
    - Response from backend reaches client unmodified
    - `X-Real-IP` and `X-Forwarded-For` headers set
    - 502 returned when backend is unreachable
    - Static file served from `root` path
    - Multiple locations route to different upstreams

---

## Phase 19 — Reverse Proxy: Load Balancing & Health Checks

- [x] 19.1 **`proxy/load_balancer.py`**
  - `LoadBalancer` abstract base class with `select(servers: list) -> UpstreamServer`
  - `RoundRobinBalancer`: stateful index, weighted with `itertools.cycle` approach
  - `LeastConnBalancer`: select server with `min(active_connections / weight)`
  - `IpHashBalancer`: `hash(client_ip) % total_weight` → deterministic server
  - `RandomBalancer`: `random.choices(servers, weights=[s.weight])`
  - Respect `backup` and `down` flags

- [x] 19.2 **`proxy/health_check.py`** — Passive health checks
  - `HealthMonitor` class:
    - On upstream connection failure: increment failure counter for that server
    - After N consecutive failures within a time window → mark `unavailable`
    - After `fail_timeout` seconds → mark as retrying, send single probe request
    - On probe success → mark `available`, reset counters
    - Configurable: `max_failures`, `fail_timeout` per upstream/server
  - `check_all()` — periodic passive sweep that detects timed-out unavailable servers

- [x] 19.3 **Write tests:**
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

- [x] 20.1 **`proxy/ssl/terminator.py`**
  - `SSLContextManager`:
    - Load certificate + private key from PEM files
    - Create `ssl.SSLContext` with configured protocols and ciphers
    - SNI callback: select correct cert based on `server_name`
    - OCSP stapling support (load OCSP response file)
    - Session ticket keys for stateless resumption
  - Wrap accepted sockets with `ssl_context.wrap_socket()`
  - Async SSL handshake integrated with event loop

- [x] 20.2 **`proxy/cache/`** — Response caching
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

- [x] 20.3 **`proxy/compression.py`**
  - `GzipCompressor`:
    - `should_compress(content_type, accept_encoding) -> bool`
    - `compress(data: bytes, level: int) -> bytes` — gzip via `zlib`
    - Minimum size threshold (default 256 bytes) — don't compress tiny responses
    - Add `Content-Encoding: gzip` and adjust `Content-Length`
    - Remove `Content-Length` if chunked (when streaming)

- [x] 20.4 **`proxy/security/rate_limiter.py`**
  - `RateLimiter` class:
    - Sliding window counters per key (typically client IP)
    - `allow(key) -> bool` — check against rate + burst, decrement window
    - On exceed: return HTTP 429 with `Retry-After` header
    - Configurable per location: rate (r/s), burst, zone name
    - In-memory storage (per-worker; approximate enforcement across workers)

- [x] 20.5 **`proxy/security/access.py`**
  - `AccessControl` class:
    - `check(client_ip) -> bool` — evaluate allow/deny rules in order
    - CIDR matching for IPv4 and IPv6
    - First matching rule wins
    - On deny: return HTTP 403

- [x] 20.6 **`proxy/security/auth.py`**
  - `BasicAuth` class:
    - `check(authorization_header) -> bool`
    - Load htpasswd-style file (bcrypt hashed)
    - Return 401 with `WWW-Authenticate: Basic realm="..."` on failure
    - Configurable per location

- [x] 20.7 **Write tests:**
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

- [x] 21.1 **`proxy/stream_proxy.py`** — TCP/UDP stream proxying
  - `StreamProxy` class:
    - `handle_tcp(client_reader, client_writer, upstream_server)`:
      connect to upstream TCP, bidirectional pipe (`asyncio.gather` read+write)
    - `handle_udp(client_data, client_addr, upstream_server)`:
      forward datagram, cache upstream response for reply
    - Supports all load balancing algorithms
    - No HTTP-level processing applies

- [x] 21.2 **`proxy/logging.py`**
  - `AccessLogger`:
    - `log(request, response, upstream, duration_ms)` — write one line
    - Formats: `combined` (Apache style), `json` (structured), custom template
    - Async writes via dedicated writer coroutine + `asyncio.Queue` (never blocks workers)
    - Log rotation: daily rotation with configurable retention count
  - `ErrorLogger`:
    - Severity-filtered: `log(level, message, **context)`
    - Worker ID and timestamp auto-prepended
    - Output: stdout, stderr, or file

- [x] 21.3 **`proxy/status.py`** — Stub status endpoint
  - `StatusCollector` class:
    - Track: active connections, accepted total, handled total, total requests
    - Per-upstream server stats: state, active connections, failure count
    - `get_stats() -> dict` — return JSON-serializable stats snapshot
  - Exposed as `GET /proxy-status` on admin port

- [x] 21.4 **Write tests:**
  - Stream proxy: TCP echo server behind proxy → client sends data, gets it back
  - Stream proxy: upstream unreachable → client connection closed cleanly
  - Logging: request logged in combined format with correct fields
  - Logging: JSON format produces valid JSON
  - Status endpoint: returns valid stats with expected keys

---

## Phase 22 — Reverse Proxy: Web Admin Panel

- [x] 22.1 **`proxy/web/app.py`** — aiohttp admin panel
  - Bind to configurable port (default 54010)
  - Share references to `UpstreamPool`, `HealthMonitor`, `CacheManager`, `RateLimiter`
  - Optional basic auth for panel access

- [x] 22.2 **Proxy admin routes:**
  - **Dashboard** (`/`): uptime, active connections, requests/sec, upstream health summary
  - **Upstreams** (`/upstreams`): per-upstream table with server states, active conns, failures
  - **Cache** (`/cache`): cache size, hit/miss ratio, purge controls
  - **Configuration** (`/config`): view current config (read-only from file)
  - **Logs** (`/logs`): live access/error log tail via SSE
  - **API endpoints:** JSON API for each page

- [x] 22.3 **Proxy HTML templates** — Jinja2
  - `base.html`, `dashboard.html`, `upstream.html`, `cache.html`, `config.html`, `logs.html`
  - Dark theme consistent with server/client panels

- [x] 22.4 **Proxy static assets**
  - `admin.css`: dark theme, upstream status indicators (green up / red down / yellow degraded)
  - `dashboard.js`: SSE live counters, upstream health grid
  - `sse.js`: reusable SSE helper

- [x] 22.5 **Write tests:**
  - Dashboard returns 200 with expected metrics
  - Upstreams page shows all configured backends with states
  - Cache page shows hit/miss stats
  - Log SSE streams log lines

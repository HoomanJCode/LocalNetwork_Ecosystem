# TODO — Phase 14: Service Exposure (Port Forwarding)

> Part of the [LocalNetwork Ecosystem implementation plan](../TODO.md).
> Each task is a checkbox. Work through phases in order; items within a phase
> can be parallelized. Testing tasks are listed alongside the feature they test.

---

## Phase 14 — Service Exposure (Port Forwarding)

- [x] 14.1 **`server/network_manager.py`** — Service Registry extension
  - `ServiceRecord` dataclass: service_id, network_id, provider_id, name, protocol, local_host, local_port, created_at
  - `NetworkManager` new methods:
    - `expose_service(network_id, provider_id, name, protocol, local_host, local_port) -> service_id`
    - `unexpose_service(network_id, service_id)`
    - `list_services(network_id) -> list[ServiceRecord]`
    - `get_service(service_id) -> ServiceRecord | None`
  - On service exposed: push `SERVICE_ADDED` to all network members
  - On service removed: push `SERVICE_REMOVED` to all network members
  - On client disconnect: auto-remove all their exposed services

- [x] 14.2 **New control messages** in `common/messages.py`
  - `ExposeService` / `UnexposeService` / `ServiceList` / `ServiceAdded` / `ServiceRemoved` / `MapService` / `UnmapService`

- [x] 14.3 **New frame type** in `common/frame.py`
  - `FRAME_FORWARDED_STREAM = 0x05` — extended header includes `stream_id` (UUID, 16B) and `service_id` (UUID, 16B) in the associated data field

- [x] 14.4 **`client/service_exposure.py`** — Expose local services
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

- [x] 14.5 **`client/service_consumer.py`** — Map remote services to local ports
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

- [x] 14.6 **Integration with `tunnel_manager.py`**
  - `TunnelManager.send_forwarded_stream(tunnel, service_id, stream_id, data)` — convenience wrapper
  - `TunnelManager` dispatches incoming `FRAME_FORWARDED_STREAM` to `ServiceExposureManager.handle_incoming_stream()` or `ServiceConsumer` depending on direction
  - Frame routing: each FORWARDED_STREAM frame carries `service_id` + `stream_id` in the associated data portion of the encrypted payload

- [x] 14.7 **Write tests:**
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

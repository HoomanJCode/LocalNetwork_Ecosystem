# TODO — Phase 3–4: Mediation Server

> Part of the [LocalNetwork Ecosystem implementation plan](../TODO.md).
> Each task is a checkbox. Work through phases in order; items within a phase
> can be parallelized. Testing tasks are listed alongside the feature they test.

---

## Phase 3 — Mediation Server (Core)

- [x] 3.1 **`server/config.py`**
  - `ServerConfig` dataclass: host, port, max_clients, heartbeat_timeout
  - Load from env vars / `.env` via `python-dotenv`
  - Default values

- [x] 3.2 **`server/protocol.py`**
  - `parse_message(data: bytes) -> Message` — wraps `common.messages.deserialize`
  - `build_message(msg: Message) -> bytes` — wraps `common.messages.serialize`
  - Validation helpers: `validate_register`, `validate_create_network`, etc.

- [x] 3.3 **`server/registry.py`**
  - `ClientRecord` dataclass: client_id, public_key_pem, public_endpoint, last_heartbeat, online, networks
  - `ClientRegistry` class:
    - `register(client_id, public_key_pem)`
    - `unregister(client_id)` — mark offline
    - `get(client_id) -> ClientRecord | None`
    - `get_online() -> list[ClientRecord]`
    - `update_endpoint(client_id, addr)`
    - `heartbeat(client_id)` — bump timestamp
    - `prune_stale(timeout)` — mark timed-out clients offline

- [x] 3.4 **`server/network_manager.py`**
  - `NetworkRecord` dataclass: network_id, name, password_hash, owner_id, topology, members, hub_id, gateway_id
  - `NetworkManager` class:
    - `create(network_id, name, password, owner_id, topology)`
    - `join(network_id, client_id, password) -> bool` — verify bcrypt hash
    - `leave(network_id, client_id)`
    - `get_peers(network_id, client_id) -> list[ClientRecord]` — other online members
    - `list_for_client(client_id) -> list[NetworkRecord]`
    - `delete(network_id, requester_id)`

- [x] 3.5 **`server/auth.py`**
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

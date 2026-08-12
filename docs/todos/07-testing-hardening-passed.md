# TODO — Phase 12–13: Integration Testing & Hardening

> Part of the [LocalNetwork Ecosystem implementation plan](../TODO.md).
> Each task is a checkbox. Work through phases in order; items within a phase
> can be parallelized. Testing tasks are listed alongside the feature they test.

---

## Phase 12 — Integration & End-to-End Testing

- [ ] 12.1 **`tests/test_integration.py`**
  - **Setup:** Start a mediation server on localhost, spawn 3 client instances
    (each in its own asyncio task with separate identity and ports).
  - **Test: Full registration flow**
    - All 3 clients register and authenticate
  - **Test: Network create & join**
    - Client A creates "testnet"
    - Clients B and C join with password
    - Verify all get PEER_ONLINE notifications
  - **Test: Peer discovery**
    - Client A requests peer endpoints for B and C
    - Gets correct (IP, port) pairs
  - **Test: P2P data exchange**
    - Client A sends frame to Client B directly (mock TUN — inject raw data)
    - Client B decrypts and verifies
  - **Test: Relay fallback**
    - Simulate punch failure → data flows through relay
    - Verify encryption end-to-end (server can't read data)

- [ ] 12.2 **`tests/test_e2e.py`**
  - Requires root/admin for TUN setup (skip on CI without `--e2e` flag)
  - **Test: Ping between two virtual clients**
    1. Start mediation server
    2. Start Client A (with TUN, IP 25.1.0.1)
    3. Start Client B (with TUN, IP 25.1.0.2)
    4. Both join same network
    5. Wait for P2P tunnel establishment
    6. From host: `ping 25.1.0.2` (via A's TUN) → expect replies from B
  - **Test: TCP connection over VPN**
    - B runs `nc -l 25.1.0.2 9999`
    - A runs `echo hello | nc 25.1.0.2 9999`
    - B receives "hello"
  - **Test: Mesh broadcast**
    - 3 clients, A sends broadcast frame, B and C both receive

- [ ] 12.3 **Performance benchmarks:**
  - Measure latency: P2P vs relay vs direct LAN
  - Measure throughput: iperf3 over the VPN tunnel
  - Measure tunnel setup time (from PEER_ONLINE to CONNECTED)

---

## Phase 13 — Hardening & Edge Cases

- [ ] 13.1 **Error handling**
  - Server crash → clients reconnect and re-establish tunnels
  - Peer crash → tunnel timeout, clean up, notify server
  - Partial messages (TCP split) → buffer and reassemble
  - Invalid messages → log warning, don't crash

- [ ] 13.2 **Security hardening**
  - Rate limiting on auth attempts (prevent brute-force)
  - Max message size limit (prevent DoS)
  - Replay protection window (track recent sequence numbers, reject old)
  - Input validation on all message fields

- [ ] 13.3 **Concurrency & resource limits**
  - Max simultaneous tunnels per client
  - Socket buffer tuning
  - Memory limits on relay queues

- [ ] 13.4 **Graceful degradation**
  - If TUN interface can't be created → warn but still enable relay-only mode
  - If ECDH fails → fall back to RSA key exchange
  - If relay queue is full → backpressure to sender

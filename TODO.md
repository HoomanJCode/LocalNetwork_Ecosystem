# TODO — LocalNetwork Ecosystem Implementation Plan

> **Status:** Planning complete. Implementation not started.
>
> Each task is a checkbox. Work through phases in order; items within a phase can
> be parallelized. Testing tasks are listed alongside the feature they test.

The plan is split into focused files by subsystem. Each file keeps the global
phase numbering so cross-references stay stable.

## Phase Files

| File | Phases | Description |
|------|--------|-------------|
| [00-foundation-passed.md](docs/todos/00-foundation-passed.md) ✅ | 0–2 | Project skeleton, protocol constants/messages/frames, cryptography |
| [01-server-passed.md](docs/todos/01-server-passed.md) ✅ | 3–4 | Mediation server core + relay fallback |
| [02-client-vpn.md](docs/todos/02-client-vpn.md) | 5–9 | Client core, platform detection, NAT traversal, P2P tunnels, TUN interface, topologies |
| [03-service-exposure.md](docs/todos/03-service-exposure.md) | 14 | Service exposure (port forwarding) |
| [04-web-panels.md](docs/todos/04-web-panels.md) | 15–16 | Server & client web admin panels |
| [05-reverse-proxy.md](docs/todos/05-reverse-proxy.md) | 17–22 | Reverse proxy: core, HTTP, load balancing, SSL/cache/security, stream/log/status, admin panel |
| [06-ux-cli.md](docs/todos/06-ux-cli.md) | 10–11 | User experience & ease of use, CLI polish |
| [07-testing-hardening.md](docs/todos/07-testing-hardening.md) | 12–13 | Integration & E2E testing, hardening |
| [08-docs.md](docs/todos/08-docs.md) | 23 | Documentation & README |

## Suggested Implementation Order

1. **Foundation** — [00-foundation-passed.md](docs/todos/00-foundation-passed.md) ✅ (Phases 0–2)
2. **Mediation server** — [01-server-passed.md](docs/todos/01-server-passed.md) ✅ (Phases 3–4)
3. **Client core + VPN** — [02-client-vpn.md](docs/todos/02-client-vpn.md) (Phases 5–9)
4. **UX basics** — [06-ux-cli.md](docs/todos/06-ux-cli.md) (Phase 10)
5. **Service exposure** — [03-service-exposure.md](docs/todos/03-service-exposure.md) (Phase 14)
6. **Web panels** — [04-web-panels.md](docs/todos/04-web-panels.md) (Phases 15–16)
7. **Reverse proxy** — [05-reverse-proxy.md](docs/todos/05-reverse-proxy.md) (Phases 17–22)
8. **Integration testing & hardening** — [07-testing-hardening.md](docs/todos/07-testing-hardening.md) (Phases 12–13)
9. **Documentation** — [08-docs.md](docs/todos/08-docs.md) (Phase 23)

## Summary

| Phase | Description                        | Est. Effort | File |
|-------|------------------------------------|-------------|------|
| 0     | Skeleton & tooling                 | Small       | [00](docs/todos/00-foundation-passed.md) ✅ |
| 1     | Protocol & frame definitions       | Small       | [00](docs/todos/00-foundation-passed.md) ✅ |
| 2     | Cryptography                       | Medium      | [00](docs/todos/00-foundation-passed.md) ✅ |
| 3     | Mediation server core              | Large       | [01](docs/todos/01-server-passed.md) ✅ |
| 4     | Server relay fallback              | Medium      | [01](docs/todos/01-server-passed.md) ✅ |
| 5     | Client core                        | Large       | [02](docs/todos/02-client-vpn.md) |
| 6     | NAT traversal                      | Medium      | [02](docs/todos/02-client-vpn.md) |
| 7     | P2P tunnel manager                 | Large       | [02](docs/todos/02-client-vpn.md) |
| 8     | TUN virtual interface              | Large       | [02](docs/todos/02-client-vpn.md) |
| 9     | Network topologies                 | Medium      | [02](docs/todos/02-client-vpn.md) |
| 10    | User experience & ease of use      | Large       | [06](docs/todos/06-ux-cli.md) |
| 11    | CLI & UX polish                    | Small       | [06](docs/todos/06-ux-cli.md) |
| 12    | Integration & E2E testing          | Large       | [07](docs/todos/07-testing-hardening.md) |
| 13    | Hardening                          | Medium      | [07](docs/todos/07-testing-hardening.md) |
| 14    | Service exposure (port forwarding) | Large       | [03](docs/todos/03-service-exposure.md) |
| 15    | Server web admin panel             | Large       | [04](docs/todos/04-web-panels.md) |
| 16    | Client web admin panel             | Large       | [04](docs/todos/04-web-panels.md) |
| 17    | Reverse proxy: core architecture   | Large       | [05](docs/todos/05-reverse-proxy.md) |
| 18    | Reverse proxy: connections & HTTP  | Large       | [05](docs/todos/05-reverse-proxy.md) |
| 19    | Reverse proxy: LB & health checks  | Medium      | [05](docs/todos/05-reverse-proxy.md) |
| 20    | Reverse proxy: SSL/cache/compress  | Large       | [05](docs/todos/05-reverse-proxy.md) |
| 21    | Reverse proxy: stream/log/status   | Medium      | [05](docs/todos/05-reverse-proxy.md) |
| 22    | Reverse proxy: web admin panel     | Medium      | [05](docs/todos/05-reverse-proxy.md) |
| 23    | Documentation                      | Small       | [08](docs/todos/08-docs.md) |

**Total estimated phases:** 23  
**MVP scope (demo-worthy):** Phases 0–8 + 11 (basic mesh, two-client E2E)

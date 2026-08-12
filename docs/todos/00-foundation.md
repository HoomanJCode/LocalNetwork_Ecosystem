# TODO — Phase 0–2: Foundation

> Part of the [LocalNetwork Ecosystem implementation plan](../TODO.md).
> Each task is a checkbox. Work through phases in order; items within a phase
> can be parallelized. Testing tasks are listed alongside the feature they test.

---

## Phase 0 — Project Skeleton & Tooling

- [x] 0.1 Create directory structure (`server/`, `client/`, `proxy/`, `common/`, `common/web_static/`, `tests/`)
- [x] 0.2 Create `requirements.txt` with `cryptography`, `bcrypt`, `python-dotenv`, `pyyaml`, `aiohttp`, `jinja2`
- [x] 0.3 Create `README.md` with project overview, quickstart, and CLI usage
- [ ] 0.4 Create `common/__init__.py`, `server/__init__.py`, `client/__init__.py`, `proxy/__init__.py`, `tests/__init__.py`
- [ ] 0.5 Create shared UI foundation — `common/web_static/css/variables.css` with all design tokens (colors, typography, spacing, radius, shadows), `reset.css`, `layout.css` shell template, `components.css` (cards, tables, badges, forms, buttons, modals, toasts), `utilities.css`
- [ ] 0.6 Create shared JS — `common/web_static/js/sse.js` (EventSource helper), `dashboard.js` (counters, sparklines), `tables.js` (sortable/filterable), `ui.js` (toasts, modals, sidebar)
- [ ] 0.7 Create `tests/conftest.py` — shared fixtures (unused port finder, temp dirs, event loop)
- [ ] 0.8 Set up `pytest` as test runner; verify `python -m pytest tests/` runs (0 tests)

---

## Phase 1 — Common: Protocol Constants, Messages & Frame Definitions

- [ ] 1.1 **`common/constants.py`**
  - `SERVER_DEFAULT_HOST = "0.0.0.0"`
  - `SERVER_DEFAULT_PORT = 54000`
  - `HEARTBEAT_INTERVAL = 30` (seconds)
  - `HOLE_PUNCH_TIMEOUT = 5` (seconds)
  - `VIRTUAL_MTU = 1400`
  - `VIRTUAL_SUBNET = "25.0.0.0/8"`
  - `FRAME_VERSION = 0x01`
  - Frame type constants: `FRAME_DATA = 0x01`, `FRAME_PUNCH = 0x02`, `FRAME_KEEPALIVE = 0x03`, `FRAME_CLOSE = 0x04`, `FRAME_FORWARDED_STREAM = 0x05`
  - Control message type strings

- [ ] 1.2 **`common/messages.py`** — dataclasses for control-channel messages
  - `Message` base dataclass with `type: str` and `payload: dict`
  - `serialize(msg) -> bytes` — JSON encode + 4-byte BE length prefix
  - `deserialize(data: bytes) -> Message` — read length prefix, JSON decode
  - All message type dataclasses: `RegisterMessage`, `AuthChallenge`, `AuthResponse`, `CreateNetwork`, `JoinNetwork`, `PeerOnline`, `PeerOffline`, `PeerEndpoints`, `Heartbeat`, etc.
  - Service messages: `ExposeService`, `UnexposeService`, `ServiceList`, `ServiceAdded`, `ServiceRemoved`, `MapService`, `UnmapService`

- [ ] 1.3 **`common/frame.py`** — data-plane frame struct
  - `FrameHeader` dataclass: version, type, payload_length, seq_num
  - `pack_frame(header, encrypted_payload, auth_tag) -> bytes`
  - `unpack_frame(data: bytes) -> tuple[FrameHeader, bytes, bytes]` (header, ciphertext, tag)
  - `FRAME_HEADER_SIZE = 8`
  - `GCM_TAG_SIZE = 16`

- [ ] 1.4 **Write tests:**
  - `tests/test_protocol.py` — serialize/deserialize round-trip for all message types
  - `tests/test_packet.py` — pack/unpack round-trip, invalid frame rejection, boundary cases

---

## Phase 2 — Cryptography Foundation

- [ ] 2.1 **`client/identity.py`**
  - `generate_identity() -> tuple[RSAPrivateKey, RSAPublicKey]` — RSA-2048
  - `save_identity(private_key, public_key, path="~/.localnetwork/")` — PEM format, `0600` perms
  - `load_identity(path="~/.localnetwork/") -> tuple[RSAPrivateKey, RSAPublicKey]`
  - `load_public_key(path) -> RSAPublicKey`
  - `sign_challenge(private_key, challenge: bytes) -> bytes` — SHA-256 + RSA sign
  - `verify_challenge(public_key, challenge: bytes, signature: bytes) -> bool`

- [ ] 2.2 **`client/encryption.py`**
  - `generate_ecdh_keypair() -> ec.EllipticCurvePrivateKey` — X25519 or P-256
  - `derive_session_key(our_private, peer_public) -> bytes` — ECDH + HKDF-SHA256 → 32-byte key
  - `CipherContext` class:
    - `__init__(session_key: bytes)`
    - `encrypt(plaintext: bytes, associated_data: bytes) -> tuple[bytes, bytes]` → (ciphertext, tag)
    - `decrypt(ciphertext: bytes, tag: bytes, associated_data: bytes) -> bytes` or raises
  - Uses AES-256-GCM with 12-byte random IV (prepended to ciphertext)

- [ ] 2.3 **Write tests:**
  - `tests/test_identity.py`
    - Generate → save → load round-trip
    - Sign → verify round-trip
    - Tampered signature rejected
    - Wrong public key fails verification
  - `tests/test_encryption.py`
    - Encrypt → decrypt round-trip
    - Wrong key fails decrypt
    - Tampered ciphertext fails (GCM auth)
    - Tampered associated data fails
    - ECDH key agreement: both sides derive same session key
    - Different key pairs produce different session keys

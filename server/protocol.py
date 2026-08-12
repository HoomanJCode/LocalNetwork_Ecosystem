"""Server-side protocol helpers.

These wrap :mod:`common.messages` with field-level validation so the
connection handler can trust message payloads after ``validate_*`` passes.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

from common import constants
from common.messages import Message, deserialize, serialize

# ---------------------------------------------------------------------------
# Wire wrappers
# ---------------------------------------------------------------------------
def parse_message(data: bytes) -> Message:
    """Deserialize a length-prefixed message (wraps common.messages.deserialize)."""
    return deserialize(data)


def build_message(msg: Message) -> bytes:
    """Serialize a message to length-prefixed bytes (wraps serialize)."""
    return serialize(msg)


# ---------------------------------------------------------------------------
# Field validators
# ---------------------------------------------------------------------------
_ID_RE = re.compile(r"^[A-Za-z0-9\-_]{8,64}$")
_NAME_RE = re.compile(r"^.{1,64}$")
_PEM_RE = re.compile(r"^-----BEGIN (RSA )?PUBLIC KEY-----")


class ProtocolError(ValueError):
    """Raised when a message fails validation."""


def _require(payload: dict, key: str, label: str) -> object:
    value = payload.get(key)
    if value in (None, ""):
        raise ProtocolError(f"{label} is required")
    return value


def validate_register(payload: dict) -> Tuple[str, str]:
    """Validate a REGISTER payload; returns (client_id, public_key_pem)."""
    client_id = _require(payload, "client_id", "client_id")
    public_key = _require(payload, "public_key", "public_key")
    if not _ID_RE.match(str(client_id)):
        raise ProtocolError("client_id must be 8-64 chars of [A-Za-z0-9_-]")
    if not _PEM_RE.match(str(public_key).strip()):
        raise ProtocolError("public_key must be a PEM-encoded public key")
    return str(client_id), str(public_key)


def validate_auth_response(payload: dict) -> Tuple[str, bytes]:
    """Validate an AUTH_RESPONSE payload; returns (challenge_hex, signature_hex)."""
    challenge = _require(payload, "challenge", "challenge")
    signature = _require(payload, "signature", "signature")
    return str(challenge), str(signature)


def validate_create_network(payload: dict) -> Tuple[str, str, str]:
    """Validate CREATE_NETWORK; returns (name, password, topology)."""
    name = _require(payload, "name", "name")
    password = _require(payload, "password", "password")
    topology = str(payload.get("topology") or constants.DEFAULT_TOPOLOGY)
    if not _NAME_RE.match(str(name)):
        raise ProtocolError("name must be 1-64 characters")
    if topology not in constants.SUPPORTED_TOPOLOGIES:
        raise ProtocolError(
            f"unsupported topology {topology!r}; expected one of "
            f"{', '.join(constants.SUPPORTED_TOPOLOGIES)}"
        )
    return str(name), str(password), topology


def validate_join_network(payload: dict) -> Tuple[str, str]:
    """Validate JOIN_NETWORK; returns (network_id, password)."""
    network_id = _require(payload, "network_id", "network_id")
    password = _require(payload, "password", "password")
    return str(network_id), str(password)


def validate_network_id(payload: dict) -> str:
    """Validate a payload carrying only a network_id."""
    network_id = _require(payload, "network_id", "network_id")
    return str(network_id)


def validate_peer_id(payload: dict) -> str:
    """Validate a payload carrying only a peer_id."""
    peer_id = _require(payload, "peer_id", "peer_id")
    return str(peer_id)


def validate_expose_service(payload: dict) -> Tuple[str, str, str, int]:
    """Validate EXPOSE_SERVICE; returns (name, protocol, local_host, local_port)."""
    name = _require(payload, "name", "name")
    protocol = str(payload.get("protocol") or "tcp").lower()
    local_host = str(payload.get("local_host") or "127.0.0.1")
    local_port = payload.get("local_port")
    if protocol not in {"tcp", "udp"}:
        raise ProtocolError(f"protocol must be tcp or udp, got {protocol!r}")
    if not isinstance(local_port, int) or not (0 < local_port < 65536):
        raise ProtocolError(f"invalid local_port: {local_port!r}")
    return str(name), protocol, local_host, local_port


def validate_service_id(payload: dict) -> str:
    """Validate a payload carrying only a service_id."""
    service_id = _require(payload, "service_id", "service_id")
    return str(service_id)


__all__ = [
    "ProtocolError",
    "parse_message",
    "build_message",
    "validate_register",
    "validate_auth_response",
    "validate_create_network",
    "validate_join_network",
    "validate_network_id",
    "validate_peer_id",
    "validate_expose_service",
    "validate_service_id",
]

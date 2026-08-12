"""Control-channel messages for the LocalNetwork Ecosystem.

Wire format: 4-byte big-endian length prefix followed by the UTF-8 JSON body.

.. code-block:: text

    +----------------+------------------------------+
    | length (4B BE) | JSON payload (UTF-8)          |
    +----------------+------------------------------+

Every message is a :class:`Message` with a ``type`` string and a ``payload``
dict. Named dataclasses (``RegisterMessage`` etc.) provide typed construction
helpers; serialization always flattens to the generic ``Message`` shape so
unknown future message types still round-trip.
"""

from __future__ import annotations

import json
import struct
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any, Optional, Union

from . import constants

LENGTH_PREFIX = struct.Struct("!I")


# ---------------------------------------------------------------------------
# Base message
# ---------------------------------------------------------------------------
@dataclass
class Message:
    """A generic control-channel message.

    Attributes:
        type: Message type string (see :mod:`common.constants`).
        payload: Arbitrary JSON-serializable payload dict.
    """

    type: str
    payload: dict = field(default_factory=dict)


class UnknownMessageTypeError(ValueError):
    """Raised when deserializing a message with an unknown ``type`` string."""


class MessageTooLargeError(ValueError):
    """Raised when a message exceeds the maximum allowed size."""


def _payload_from_dataclass(obj: Any) -> dict:
    """Convert a dataclass instance to a plain dict (skipping None fields)."""
    if is_dataclass(obj) and not isinstance(obj, type):
        out = {}
        for f in fields(obj):
            value = getattr(obj, f.name)
            if value is not None:
                out[f.name] = value
        return out
    if isinstance(obj, dict):
        return {k: v for k, v in obj.items() if v is not None}
    return {}


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------
def serialize(msg: Union[Message, Any]) -> bytes:
    """Serialize a message to length-prefixed JSON bytes.

    Accepts a :class:`Message` or any dataclass with a ``type`` attribute.
    """
    if isinstance(msg, Message):
        body = {constants.KEY_TYPE: msg.type, constants.KEY_PAYLOAD: msg.payload}
    elif is_dataclass(msg) and not isinstance(msg, type):
        d = _payload_from_dataclass(msg)
        body = {constants.KEY_TYPE: d.pop("type"), constants.KEY_PAYLOAD: d}
    else:
        raise TypeError(f"cannot serialize {type(msg)!r} as a Message")

    data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(data) > constants.MAX_MESSAGE_SIZE:
        raise MessageTooLargeError(
            f"message payload of {len(data)} bytes exceeds MAX_MESSAGE_SIZE"
        )
    return LENGTH_PREFIX.pack(len(data)) + data


def deserialize(data: bytes) -> Message:
    """Deserialize length-prefixed bytes into a :class:`Message`.

    Raises:
        UnknownMessageTypeError: If the ``type`` field is not a known constant.
        MessageTooLargeError: If the declared length exceeds the maximum.
    """
    if len(data) < LENGTH_PREFIX.size:
        raise ValueError("message too short to contain a length prefix")

    length = LENGTH_PREFIX.unpack_from(data)[0]
    if length > constants.MAX_MESSAGE_SIZE:
        raise MessageTooLargeError(
            f"declared message length {length} exceeds MAX_MESSAGE_SIZE"
        )
    if len(data) < LENGTH_PREFIX.size + length:
        raise ValueError(
            f"incomplete message: declared {length} bytes, got "
            f"{len(data) - LENGTH_PREFIX.size}"
        )

    body = json.loads(data[LENGTH_PREFIX.size : LENGTH_PREFIX.size + length].decode("utf-8"))
    type_str = body.get(constants.KEY_TYPE)
    payload = body.get(constants.KEY_PAYLOAD, {})
    if not isinstance(type_str, str):
        raise ValueError("message missing a valid 'type' field")
    if not hasattr(constants, f"MSG_{type_str}"):
        # Unknown types are tolerated (forward compatibility) but surfaced.
        pass
    return Message(type=type_str, payload=payload if isinstance(payload, dict) else {})


def msg_size(serialized: bytes) -> int:
    """Return the JSON body length declared in a serialized message."""
    if len(serialized) < LENGTH_PREFIX.size:
        raise ValueError("buffer too short for a length prefix")
    return LENGTH_PREFIX.unpack_from(serialized)[0]


def parse_stream(data: bytes) -> tuple[Optional[Message], bytes]:
    """Try to parse exactly one message from a (possibly partial) stream buffer.

    Returns ``(message, remaining)`` where ``remaining`` is the unconsumed tail.
    If a full message is not yet available, returns ``(None, data)``.
    """
    if len(data) < LENGTH_PREFIX.size:
        return None, data
    length = LENGTH_PREFIX.unpack_from(data)[0]
    if length > constants.MAX_MESSAGE_SIZE:
        raise MessageTooLargeError(f"declared message length {length} too large")
    total = LENGTH_PREFIX.size + length
    if len(data) < total:
        return None, data
    msg = deserialize(data[:total])
    return msg, data[total:]


# ---------------------------------------------------------------------------
# Named message dataclasses (typed construction helpers)
# ---------------------------------------------------------------------------
@dataclass
class RegisterMessage:
    type: str = constants.MSG_REGISTER
    client_id: str = ""
    public_key: str = ""
    version: str = ""


@dataclass
class AuthChallenge:
    type: str = constants.MSG_AUTH_CHALLENGE
    challenge: str = ""  # hex-encoded nonce
    client_id: str = ""


@dataclass
class AuthResponse:
    type: str = constants.MSG_AUTH_RESPONSE
    signature: str = ""  # hex-encoded RSA signature
    challenge: str = ""


@dataclass
class AuthResult:
    type: str = constants.MSG_AUTH_OK  # or AUTH_FAIL
    ok: bool = True
    message: str = ""
    virtual_ip: Optional[str] = None


@dataclass
class CreateNetwork:
    type: str = constants.MSG_CREATE_NETWORK
    name: str = ""
    password: str = ""
    topology: str = constants.DEFAULT_TOPOLOGY


@dataclass
class NetworkCreated:
    type: str = constants.MSG_NETWORK_CREATED
    network_id: str = ""
    name: str = ""
    owner_id: str = ""
    topology: str = ""


@dataclass
class JoinNetwork:
    type: str = constants.MSG_JOIN_NETWORK
    network_id: str = ""
    password: str = ""


@dataclass
class NetworkJoined:
    type: str = constants.MSG_NETWORK_JOINED
    network_id: str = ""
    name: str = ""
    virtual_ip: Optional[str] = None


@dataclass
class LeaveNetwork:
    type: str = constants.MSG_LEAVE_NETWORK
    network_id: str = ""


@dataclass
class NetworkLeft:
    type: str = constants.MSG_NETWORK_LEFT
    network_id: str = ""


@dataclass
class ListNetworks:
    type: str = constants.MSG_LIST_NETWORKS


@dataclass
class NetworkInfo:
    """One network entry inside a NETWORK_LIST payload."""

    network_id: str
    name: str
    owner_id: str
    topology: str
    member_count: int = 0


@dataclass
class NetworkList:
    type: str = constants.MSG_NETWORK_LIST
    networks: list = field(default_factory=list)  # list of NetworkInfo dicts


@dataclass
class NetworkPeers:
    type: str = constants.MSG_NETWORK_PEERS
    network_id: str = ""
    peers: list = field(default_factory=list)  # list of {client_id, public_endpoint}


@dataclass
class PeerOnline:
    type: str = constants.MSG_PEER_ONLINE
    network_id: str = ""
    peer_id: str = ""
    peer_ip: Optional[str] = None


@dataclass
class PeerOffline:
    type: str = constants.MSG_PEER_OFFLINE
    network_id: str = ""
    peer_id: str = ""


@dataclass
class RequestPeerConn:
    type: str = constants.MSG_REQUEST_PEER_CONN
    peer_id: str = ""


@dataclass
class PeerEndpoints:
    type: str = constants.MSG_PEER_ENDPOINTS
    peer_id: str = ""
    endpoints: list = field(default_factory=list)  # list of [host, port]


@dataclass
class RelayRequest:
    type: str = constants.MSG_RELAY_REQUEST
    peer_id: str = ""


@dataclass
class RelayGranted:
    type: str = constants.MSG_RELAY_GRANTED
    peer_id: str = ""
    path_id: str = ""


@dataclass
class RelayFrame:
    """Wraps a raw data frame travelling over the control channel."""

    type: str = constants.MSG_RELAY_FRAME
    src_id: str = ""
    dst_id: str = ""
    frame_b64: str = ""  # base64-encoded data frame


@dataclass
class Heartbeat:
    type: str = constants.MSG_HEARTBEAT
    ts: float = 0.0


@dataclass
class HeartbeatAck:
    type: str = constants.MSG_HEARTBEAT_ACK
    ts: float = 0.0


@dataclass
class ErrorMessage:
    type: str = constants.MSG_ERROR
    code: str = ""
    message: str = ""


# ---- Service exposure (Phase 14) -------------------------------------------
@dataclass
class ExposeService:
    type: str = constants.MSG_EXPOSE_SERVICE
    name: str = ""
    protocol: str = "tcp"
    local_host: str = "127.0.0.1"
    local_port: int = 0


@dataclass
class ServiceExposed:
    type: str = constants.MSG_SERVICE_EXPOSED
    service_id: str = ""
    name: str = ""
    protocol: str = ""
    local_port: int = 0


@dataclass
class UnexposeService:
    type: str = constants.MSG_UNEXPOSE_SERVICE
    service_id: str = ""


@dataclass
class ServiceUnexposed:
    type: str = constants.MSG_SERVICE_UNEXPOSED
    service_id: str = ""


@dataclass
class ServiceList:
    type: str = constants.MSG_SERVICE_LIST
    services: list = field(default_factory=list)


@dataclass
class ServiceAdded:
    type: str = constants.MSG_SERVICE_ADDED
    service: dict = field(default_factory=dict)


@dataclass
class ServiceRemoved:
    type: str = constants.MSG_SERVICE_REMOVED
    service_id: str = ""


@dataclass
class MapService:
    type: str = constants.MSG_MAP_SERVICE
    service_id: str = ""
    local_port: Optional[int] = None
    strategy: str = "auto"


@dataclass
class ServiceMapped:
    type: str = constants.MSG_SERVICE_MAPPED
    service_id: str = ""
    local_port: int = 0


@dataclass
class UnmapService:
    type: str = constants.MSG_UNMAP_SERVICE
    service_id: str = ""


@dataclass
class ServiceUnmapped:
    type: str = constants.MSG_SERVICE_UNMAPPED
    service_id: str = ""


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------
def make_message(cls, **kwargs) -> Message:
    """Construct a generic :class:`Message` from a typed message dataclass."""
    if "type" not in kwargs and hasattr(cls, "type"):
        kwargs.setdefault("type", getattr(cls, "type"))
    instance = cls(**kwargs)
    return Message(type=instance.type, payload=_payload_from_dataclass(instance))


def message_to_dataclass(msg: Message, mapping: dict) -> Optional[Any]:
    """Convert a generic Message to a typed dataclass if its type is mapped."""
    cls = mapping.get(msg.type)
    if cls is None:
        return None
    # Filter payload keys to declared dataclass fields
    allowed = {f.name for f in fields(cls)}
    kwargs = {k: v for k, v in msg.payload.items() if k in allowed}
    try:
        return cls(**kwargs)
    except TypeError:
        return None


# Mapping of message type string -> typed dataclass
MESSAGE_TYPES: dict[str, Any] = {
    constants.MSG_REGISTER: RegisterMessage,
    constants.MSG_AUTH_CHALLENGE: AuthChallenge,
    constants.MSG_AUTH_RESPONSE: AuthResponse,
    constants.MSG_AUTH_OK: AuthResult,
    constants.MSG_AUTH_FAIL: AuthResult,
    constants.MSG_CREATE_NETWORK: CreateNetwork,
    constants.MSG_NETWORK_CREATED: NetworkCreated,
    constants.MSG_JOIN_NETWORK: JoinNetwork,
    constants.MSG_NETWORK_JOINED: NetworkJoined,
    constants.MSG_LEAVE_NETWORK: LeaveNetwork,
    constants.MSG_NETWORK_LEFT: NetworkLeft,
    constants.MSG_LIST_NETWORKS: ListNetworks,
    constants.MSG_NETWORK_LIST: NetworkList,
    constants.MSG_NETWORK_PEERS: NetworkPeers,
    constants.MSG_PEER_ONLINE: PeerOnline,
    constants.MSG_PEER_OFFLINE: PeerOffline,
    constants.MSG_REQUEST_PEER_CONN: RequestPeerConn,
    constants.MSG_PEER_ENDPOINTS: PeerEndpoints,
    constants.MSG_RELAY_REQUEST: RelayRequest,
    constants.MSG_RELAY_GRANTED: RelayGranted,
    constants.MSG_RELAY_FRAME: RelayFrame,
    constants.MSG_HEARTBEAT: Heartbeat,
    constants.MSG_HEARTBEAT_ACK: HeartbeatAck,
    constants.MSG_ERROR: ErrorMessage,
    constants.MSG_EXPOSE_SERVICE: ExposeService,
    constants.MSG_SERVICE_EXPOSED: ServiceExposed,
    constants.MSG_UNEXPOSE_SERVICE: UnexposeService,
    constants.MSG_SERVICE_UNEXPOSED: ServiceUnexposed,
    constants.MSG_SERVICE_LIST: ServiceList,
    constants.MSG_SERVICE_ADDED: ServiceAdded,
    constants.MSG_SERVICE_REMOVED: ServiceRemoved,
    constants.MSG_MAP_SERVICE: MapService,
    constants.MSG_SERVICE_MAPPED: ServiceMapped,
    constants.MSG_UNMAP_SERVICE: UnmapService,
    constants.MSG_SERVICE_UNMAPPED: ServiceUnmapped,
}

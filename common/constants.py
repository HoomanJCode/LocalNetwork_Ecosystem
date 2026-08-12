"""Shared protocol constants for the LocalNetwork Ecosystem.

All timing values are in seconds. Frame types are single-byte identifiers used
in the data-plane frame header; control message types are strings used in the
JSON control channel.
"""

# ---- Server defaults --------------------------------------------------------
SERVER_DEFAULT_HOST = "0.0.0.0"
SERVER_DEFAULT_PORT = 54000
SERVER_DEFAULT_WEB_PORT = 54001
CLIENT_DEFAULT_WEB_PORT = 54002
PROXY_DEFAULT_WEB_PORT = 54010
MAX_MESSAGE_SIZE = 1024 * 1024  # 1 MiB upper bound for control messages

# ---- Timing -----------------------------------------------------------------
HEARTBEAT_INTERVAL = 30          # seconds between client heartbeats
HEARTBEAT_TIMEOUT = 90           # server marks client offline after this silence
HOLE_PUNCH_TIMEOUT = 5           # seconds to wait for a PUNCH reply
KEEPALIVE_INTERVAL = 10          # tunnel keep-alive frames
TUNNEL_STALE_TIMEOUT = 60        # close tunnels silent for this long
AUTH_CHALLENGE_TTL = 60          # pending auth challenge lifetime (server side)
AUTH_MAX_ATTEMPTS = 5            # failed auth attempts before disconnect
RECONNECT_BASE_DELAY = 1.0       # client reconnection backoff start
RECONNECT_MAX_DELAY = 60.0       # client reconnection backoff ceiling

# ---- Virtual network --------------------------------------------------------
VIRTUAL_MTU = 1400               # leaves headroom for GCM tag + frame header
VIRTUAL_SUBNET = "25.0.0.0/8"
VIRTUAL_NETMASK = "255.0.0.0"
DEFAULT_TOPOLOGY = "mesh"

# ---- Data-plane frame layout ------------------------------------------------
# 0                   1                   2                   3
# 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
# +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
# | Version(1B)  |  Type(1B)    |         Payload Length (2B)      |
# +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
# |                        Sequence Number (4B)                    |
# +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
FRAME_VERSION = 0x01
FRAME_HEADER_SIZE = 8
GCM_TAG_SIZE = 16
MAX_FRAME_PAYLOAD = 65535  # 2-byte length field

# Frame type constants
FRAME_DATA = 0x01             # Encapsulated IP packet
FRAME_PUNCH = 0x02            # UDP hole-punching probe
FRAME_PUNCH_ACK = 0x06        # Hole-punch acknowledgement
FRAME_KEEPALIVE = 0x03        # Connection keep-alive
FRAME_CLOSE = 0x04            # Graceful tunnel close
FRAME_FORWARDED_STREAM = 0x05 # TCP/UDP stream data for a forwarded service

# ---- Control message types (JSON, length-prefixed over TCP) ----------------
MSG_REGISTER = "REGISTER"
MSG_AUTH_CHALLENGE = "AUTH_CHALLENGE"
MSG_AUTH_RESPONSE = "AUTH_RESPONSE"
MSG_AUTH_OK = "AUTH_OK"
MSG_AUTH_FAIL = "AUTH_FAIL"

MSG_CREATE_NETWORK = "CREATE_NETWORK"
MSG_NETWORK_CREATED = "NETWORK_CREATED"
MSG_JOIN_NETWORK = "JOIN_NETWORK"
MSG_NETWORK_JOINED = "NETWORK_JOINED"
MSG_LEAVE_NETWORK = "LEAVE_NETWORK"
MSG_NETWORK_LEFT = "NETWORK_LEFT"
MSG_LIST_NETWORKS = "LIST_NETWORKS"
MSG_NETWORK_LIST = "NETWORK_LIST"

MSG_NETWORK_PEERS = "NETWORK_PEERS"
MSG_PEER_ONLINE = "PEER_ONLINE"
MSG_PEER_OFFLINE = "PEER_OFFLINE"
MSG_REQUEST_PEER_CONN = "REQUEST_PEER_CONN"
MSG_PEER_ENDPOINTS = "PEER_ENDPOINTS"

MSG_RELAY_REQUEST = "RELAY_REQUEST"
MSG_RELAY_GRANTED = "RELAY_GRANTED"
MSG_RELAY_FRAME = "RELAY_FRAME"     # wraps a data frame on the control channel
MSG_RELAY_CLOSED = "RELAY_CLOSED"

MSG_HEARTBEAT = "HEARTBEAT"
MSG_HEARTBEAT_ACK = "HEARTBEAT_ACK"
MSG_ERROR = "ERROR"

# ---- Service exposure (Phase 14) --------------------------------------------
MSG_EXPOSE_SERVICE = "EXPOSE_SERVICE"
MSG_SERVICE_EXPOSED = "SERVICE_EXPOSED"
MSG_UNEXPOSE_SERVICE = "UNEXPOSE_SERVICE"
MSG_SERVICE_UNEXPOSED = "SERVICE_UNEXPOSED"
MSG_SERVICE_LIST = "SERVICE_LIST"
MSG_SERVICE_ADDED = "SERVICE_ADDED"
MSG_SERVICE_REMOVED = "SERVICE_REMOVED"
MSG_MAP_SERVICE = "MAP_SERVICE"
MSG_SERVICE_MAPPED = "SERVICE_MAPPED"
MSG_UNMAP_SERVICE = "UNMAP_SERVICE"
MSG_SERVICE_UNMAPPED = "SERVICE_UNMAPPED"

# ---- Topologies --------------------------------------------------------------
TOPOLOGY_MESH = "mesh"
TOPOLOGY_HUB_AND_SPOKE = "hub_and_spoke"
TOPOLOGY_GATEWAY = "gateway"
SUPPORTED_TOPOLOGIES = (TOPOLOGY_MESH, TOPOLOGY_HUB_AND_SPOKE, TOPOLOGY_GATEWAY)

# ---- Message key names (kept in sync with common.messages) -------------------
KEY_TYPE = "type"
KEY_PAYLOAD = "payload"

__all__ = [
    "SERVER_DEFAULT_HOST",
    "SERVER_DEFAULT_PORT",
    "SERVER_DEFAULT_WEB_PORT",
    "CLIENT_DEFAULT_WEB_PORT",
    "PROXY_DEFAULT_WEB_PORT",
    "MAX_MESSAGE_SIZE",
    "HEARTBEAT_INTERVAL",
    "HEARTBEAT_TIMEOUT",
    "HOLE_PUNCH_TIMEOUT",
    "KEEPALIVE_INTERVAL",
    "TUNNEL_STALE_TIMEOUT",
    "AUTH_CHALLENGE_TTL",
    "AUTH_MAX_ATTEMPTS",
    "RECONNECT_BASE_DELAY",
    "RECONNECT_MAX_DELAY",
    "VIRTUAL_MTU",
    "VIRTUAL_SUBNET",
    "VIRTUAL_NETMASK",
    "DEFAULT_TOPOLOGY",
    "FRAME_VERSION",
    "FRAME_HEADER_SIZE",
    "GCM_TAG_SIZE",
    "MAX_FRAME_PAYLOAD",
    "FRAME_DATA",
    "FRAME_PUNCH",
    "FRAME_PUNCH_ACK",
    "FRAME_KEEPALIVE",
    "FRAME_CLOSE",
    "FRAME_FORWARDED_STREAM",
    "MSG_REGISTER",
    "MSG_AUTH_CHALLENGE",
    "MSG_AUTH_RESPONSE",
    "MSG_AUTH_OK",
    "MSG_AUTH_FAIL",
    "MSG_CREATE_NETWORK",
    "MSG_NETWORK_CREATED",
    "MSG_JOIN_NETWORK",
    "MSG_NETWORK_JOINED",
    "MSG_LEAVE_NETWORK",
    "MSG_NETWORK_LEFT",
    "MSG_LIST_NETWORKS",
    "MSG_NETWORK_LIST",
    "MSG_NETWORK_PEERS",
    "MSG_PEER_ONLINE",
    "MSG_PEER_OFFLINE",
    "MSG_REQUEST_PEER_CONN",
    "MSG_PEER_ENDPOINTS",
    "MSG_RELAY_REQUEST",
    "MSG_RELAY_GRANTED",
    "MSG_RELAY_FRAME",
    "MSG_RELAY_CLOSED",
    "MSG_HEARTBEAT",
    "MSG_HEARTBEAT_ACK",
    "MSG_ERROR",
    "MSG_EXPOSE_SERVICE",
    "MSG_SERVICE_EXPOSED",
    "MSG_UNEXPOSE_SERVICE",
    "MSG_SERVICE_UNEXPOSED",
    "MSG_SERVICE_LIST",
    "MSG_SERVICE_ADDED",
    "MSG_SERVICE_REMOVED",
    "MSG_MAP_SERVICE",
    "MSG_SERVICE_MAPPED",
    "MSG_UNMAP_SERVICE",
    "MSG_SERVICE_UNMAPPED",
    "TOPOLOGY_MESH",
    "TOPOLOGY_HUB_AND_SPOKE",
    "TOPOLOGY_GATEWAY",
    "SUPPORTED_TOPOLOGIES",
]

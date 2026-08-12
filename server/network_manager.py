"""Network membership management.

Passwords are stored as bcrypt hashes; the plaintext password is never kept.
The manager also records topology metadata (mesh, hub-and-spoke, gateway) which
the connection handler uses to decide which endpoints to share.
"""

from __future__ import annotations

import random
import string
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import bcrypt

from common.constants import DEFAULT_TOPOLOGY, SUPPORTED_TOPOLOGIES
from server.registry import ClientRecord, ClientRegistry


@dataclass
class NetworkRecord:
    """Everything the server knows about one virtual network."""

    network_id: str
    name: str
    password_hash: bytes
    owner_id: str
    topology: str = DEFAULT_TOPOLOGY
    members: Set[str] = field(default_factory=set)
    hub_id: Optional[str] = None
    gateway_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self, member_count: Optional[int] = None) -> dict:
        return {
            "network_id": self.network_id,
            "name": self.name,
            "owner_id": self.owner_id,
            "topology": self.topology,
            "member_count": (
                len(self.members) if member_count is None else member_count
            ),
            "created_at": self.created_at,
        }


@dataclass
class ServiceRecord:
    """One service exposed on a network."""

    service_id: str
    network_id: str
    provider_id: str
    name: str
    protocol: str  # "tcp" or "udp"
    local_host: str
    local_port: int
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "service_id": self.service_id,
            "network_id": self.network_id,
            "provider_id": self.provider_id,
            "name": self.name,
            "protocol": self.protocol,
            "local_host": self.local_host,
            "local_port": self.local_port,
            "created_at": self.created_at,
        }


class NetworkManager:
    """Creates, joins, and deletes virtual networks."""

    _INVITE_CODE_LENGTH = 8
    _INVITE_CODE_ALPHABET = string.ascii_lowercase + string.digits

    def __init__(self, registry: ClientRegistry) -> None:
        self._registry = registry
        self._networks: Dict[str, NetworkRecord] = {}
        self._banned_networks: Set[str] = set()
        self._services: Dict[str, Dict[str, ServiceRecord]] = {}  # network_id → {service_id → record}
        self._invite_codes: Dict[str, str] = {}  # code → network_id
        self._network_invite_codes: Dict[str, str] = {}  # network_id → code

    # ---- creation / lookup --------------------------------------------------
    def create(
        self,
        name: str,
        password: str,
        owner_id: str,
        topology: str = DEFAULT_TOPOLOGY,
        network_id: Optional[str] = None,
    ) -> NetworkRecord:
        """Create a network owned by ``owner_id`` and add the owner as a member."""
        if topology not in SUPPORTED_TOPOLOGIES:
            raise ValueError(f"unsupported topology: {topology}")
        record = NetworkRecord(
            network_id=network_id or str(uuid.uuid4()),
            name=name,
            password_hash=bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()),
            owner_id=owner_id,
            topology=topology,
        )
        if topology == "hub_and_spoke":
            record.hub_id = owner_id
        if topology == "gateway":
            record.gateway_id = owner_id
        record.members.add(owner_id)
        self._networks[record.network_id] = record
        self._registry.add_network(owner_id, record.network_id)
        return record

    def get(self, network_id: str) -> Optional[NetworkRecord]:
        return self._networks.get(network_id)

    def list_all(self) -> List[NetworkRecord]:
        return list(self._networks.values())

    def __len__(self) -> int:
        return len(self._networks)

    def is_banned(self, network_id: str) -> bool:
        return network_id in self._banned_networks

    # ---- membership ----------------------------------------------------------
    def join(self, network_id: str, client_id: str, password: str) -> bool:
        """Join a network; verifies the bcrypt password hash.

        Returns:
            True on success, False on wrong password / missing network.
        """
        record = self._networks.get(network_id)
        if record is None or self.is_banned(network_id):
            return False
        try:
            if not bcrypt.checkpw(password.encode("utf-8"), record.password_hash):
                return False
        except ValueError:
            return False
        record.members.add(client_id)
        self._registry.add_network(client_id, network_id)
        return True

    def leave(self, network_id: str, client_id: str) -> bool:
        """Remove a client from a network. Returns False if not a member."""
        record = self._networks.get(network_id)
        if record is None:
            return False
        if client_id not in record.members:
            return False
        record.members.discard(client_id)
        self._registry.remove_network(client_id, network_id)
        if record.hub_id == client_id:
            record.hub_id = None
        if record.gateway_id == client_id:
            record.gateway_id = None
        return True

    def is_member(self, network_id: str, client_id: str) -> bool:
        record = self._networks.get(network_id)
        return bool(record and client_id in record.members)

    def members(self, network_id: str) -> Set[str]:
        record = self._networks.get(network_id)
        return set(record.members) if record else set()

    def get_peers(
        self, network_id: str, client_id: str
    ) -> List[ClientRecord]:
        """Other **online** members of a network, excluding the requester."""
        record = self._networks.get(network_id)
        if record is None:
            return []
        peers = []
        for member_id in record.members:
            if member_id == client_id:
                continue
            member = self._registry.get(member_id)
            if member is not None and member.online:
                peers.append(member)
        return peers

    def list_for_client(self, client_id: str) -> List[NetworkRecord]:
        """Networks a client currently belongs to (active membership only).

        Ownership alone does not count — an owner who left the network no
        longer sees it in their list.
        """
        return [
            r for r in self._networks.values() if client_id in r.members
        ]

    # ---- deletion --------------------------------------------------------------
    def delete(self, network_id: str, requester_id: str) -> bool:
        """Delete a network; only the owner may delete it.

        Returns:
            True on success, False if missing or not the owner.
        """
        record = self._networks.get(network_id)
        if record is None or record.owner_id != requester_id:
            return False
        for member_id in record.members:
            self._registry.remove_network(member_id, network_id)
        # Clean up invite code
        code = self._network_invite_codes.pop(network_id, None)
        if code:
            self._invite_codes.pop(code, None)
        del self._networks[network_id]
        return True

    def purge_client(self, client_id: str) -> List[str]:
        """Remove a client from every network (on disconnect/ban).

        Networks left empty by their last member are deleted.

        Returns:
            network_ids the client was removed from.
        """
        removed: List[str] = []
        for network_id, record in list(self._networks.items()):
            if client_id in record.members:
                record.members.discard(client_id)
                self._registry.remove_network(client_id, network_id)
                removed.append(network_id)
                if record.hub_id == client_id:
                    record.hub_id = None
                if record.gateway_id == client_id:
                    record.gateway_id = None
                if not record.members:
                    del self._networks[network_id]
        # Also remove all services exposed by this client
        for network_id, svc_map in self._services.items():
            to_remove = [
                sid for sid, svc in svc_map.items()
                if svc.provider_id == client_id
            ]
            for sid in to_remove:
                del svc_map[sid]
        return removed

    # ---- service registry ----------------------------------------------------
    def expose_service(
        self,
        network_id: str,
        provider_id: str,
        name: str,
        protocol: str,
        local_host: str,
        local_port: int,
    ) -> str:
        """Register a service on a network. Returns the service_id."""
        record = self._networks.get(network_id)
        if record is None or provider_id not in record.members:
            raise ValueError("client not a member of this network")
        service_id = str(uuid.uuid4())
        svc = ServiceRecord(
            service_id=service_id,
            network_id=network_id,
            provider_id=provider_id,
            name=name,
            protocol=protocol,
            local_host=local_host,
            local_port=local_port,
        )
        self._services.setdefault(network_id, {})[service_id] = svc
        return service_id

    def unexpose_service(self, network_id: str, service_id: str) -> bool:
        """Remove a service registration. Returns True if removed."""
        svc_map = self._services.get(network_id, {})
        if service_id in svc_map:
            del svc_map[service_id]
            return True
        return False

    def list_services(self, network_id: str) -> List[ServiceRecord]:
        """List all services exposed on a network."""
        return list(self._services.get(network_id, {}).values())

    def get_service(self, service_id: str) -> Optional[ServiceRecord]:
        """Look up a service by its ID."""
        for svc_map in self._services.values():
            if service_id in svc_map:
                return svc_map[service_id]
        return None

    def unexpose_service_owner(self, service_id: str, client_id: str) -> Optional[str]:
        """Remove a service if owned by client_id. Returns the network_id or None."""
        for network_id, svc_map in self._services.items():
            svc = svc_map.get(service_id)
            if svc is not None and svc.provider_id == client_id:
                del svc_map[service_id]
                return network_id
        return None

    def purge_client_services(self, client_id: str) -> List[str]:
        """Remove all services exposed by a client. Returns removed service_ids."""
        removed: List[str] = []
        for svc_map in self._services.values():
            to_remove = [
                sid for sid, svc in svc_map.items()
                if svc.provider_id == client_id
            ]
            for sid in to_remove:
                del svc_map[sid]
                removed.append(sid)
        return removed

    # ---- invite codes --------------------------------------------------------
    def generate_invite_code(self, network_id: str) -> str:
        """Generate a short human-friendly invite code for a network.

        Creates codes like ``a3x9k2bc`` — 8 lowercase alphanumeric chars.
        Regenerates if collision; retries up to 10 times.
        """
        record = self._networks.get(network_id)
        if record is None:
            raise ValueError("network not found")

        # If the network already has a code, return it
        if network_id in self._network_invite_codes:
            return self._network_invite_codes[network_id]

        for _ in range(10):
            code = "".join(
                random.choices(self._INVITE_CODE_ALPHABET, k=self._INVITE_CODE_LENGTH)
            )
            if code not in self._invite_codes:
                self._invite_codes[code] = network_id
                self._network_invite_codes[network_id] = code
                return code

        # Fallback: use a prefix from the network name + random suffix
        prefix = record.name[:4].lower()
        code = f"{prefix}-{uuid.uuid4().hex[:6]}"
        self._invite_codes[code] = network_id
        self._network_invite_codes[network_id] = code
        return code

    def join_by_invite_code(
        self, code: str, client_id: str, password: str
    ) -> Optional[NetworkRecord]:
        """Join a network using its short invite code instead of network_id.

        Returns:
            The NetworkRecord on success, None on wrong code/password.
        """
        network_id = self._invite_codes.get(code)
        if network_id is None:
            return None
        record = self._networks.get(network_id)
        if record is None or self.is_banned(network_id):
            return None
        try:
            if not bcrypt.checkpw(password.encode("utf-8"), record.password_hash):
                return None
        except ValueError:
            return None
        record.members.add(client_id)
        self._registry.add_network(client_id, network_id)
        return record

    def get_invite_code(self, network_id: str) -> Optional[str]:
        """Get the invite code for a network, generating one if needed."""
        if network_id not in self._networks:
            return None
        return self.generate_invite_code(network_id)

    # ---- endpoints for hole punching -------------------------------------------
    def shared_endpoints_for(
        self, network_id: str, requester_id: str, target_id: str
    ) -> Optional[List[Tuple[str, int]]]:
        """Endpoints the server may share about ``target_id``.

        In hub-and-spoke mode, spokes only learn the hub's endpoint; in mesh
        mode everyone learns everyone else's endpoint.
        """
        record = self._networks.get(network_id)
        if record is None:
            return None
        if requester_id == target_id:
            return None
        if (
            requester_id not in record.members
            or target_id not in record.members
        ):
            return None
        target = self._registry.get(target_id)
        if target is None or not target.online or not target.public_endpoint:
            return None

        if record.topology == "hub_and_spoke":
            # Only the hub's endpoint is shared; spokes punch to the hub only.
            if record.hub_id != target_id:
                return None
        endpoints = [tuple(target.public_endpoint)]  # type: ignore[list-item]
        return endpoints  # type: ignore[return-value]


__all__ = ["NetworkRecord", "ServiceRecord", "NetworkManager"]

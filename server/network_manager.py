"""Network membership management.

Passwords are stored as bcrypt hashes; the plaintext password is never kept.
The manager also records topology metadata (mesh, hub-and-spoke, gateway) which
the connection handler uses to decide which endpoints to share.
"""

from __future__ import annotations

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


class NetworkManager:
    """Creates, joins, and deletes virtual networks."""

    def __init__(self, registry: ClientRegistry) -> None:
        self._registry = registry
        self._networks: Dict[str, NetworkRecord] = {}
        self._banned_networks: Set[str] = set()

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
        return removed

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


__all__ = ["NetworkRecord", "NetworkManager"]

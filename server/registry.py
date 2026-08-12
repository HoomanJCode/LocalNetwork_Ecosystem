"""In-memory registry of connected clients.

Thread-safety note: the mediation server is single-threaded asyncio, so the
registry does not need locks. If it is ever accessed from worker threads,
guard calls with the server's loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple


@dataclass
class ClientRecord:
    """Everything the server knows about one registered client."""

    client_id: str
    public_key_pem: str
    public_endpoint: Optional[Tuple[str, int]] = None
    last_heartbeat: float = field(default_factory=time.time)
    online: bool = False
    networks: Set[str] = field(default_factory=set)
    joined_at: float = field(default_factory=time.time)
    banned: bool = False
    client_ip: Optional[str] = None


class ClientRegistry:
    """Tracks registered clients, their endpoints, and online status."""

    def __init__(self) -> None:
        self._clients: Dict[str, ClientRecord] = {}

    # ---- basic access ------------------------------------------------------
    def register(self, client_id: str, public_key_pem: str) -> ClientRecord:
        """Register (or re-register) a client. Returns the record."""
        record = self._clients.get(client_id)
        if record is None:
            record = ClientRecord(client_id=client_id, public_key_pem=public_key_pem)
            self._clients[client_id] = record
        record.public_key_pem = public_key_pem
        return record

    def unregister(self, client_id: str) -> None:
        """Mark a client offline but keep its record for reconnects."""
        record = self._clients.get(client_id)
        if record is not None:
            record.online = False
            record.public_endpoint = None

    def forget(self, client_id: str) -> None:
        """Fully remove a client record (e.g. on ban)."""
        self._clients.pop(client_id, None)

    def get(self, client_id: str) -> Optional[ClientRecord]:
        return self._clients.get(client_id)

    def get_online(self) -> List[ClientRecord]:
        return [r for r in self._clients.values() if r.online]

    def get_all(self) -> List[ClientRecord]:
        return list(self._clients.values())

    def __len__(self) -> int:
        return len(self._clients)

    @property
    def online_count(self) -> int:
        return sum(1 for r in self._clients.values() if r.online)

    def has(self, client_id: str) -> bool:
        return client_id in self._clients

    def is_banned(self, client_id: str) -> bool:
        record = self._clients.get(client_id)
        return bool(record and record.banned)

    # ---- endpoint / status updates ----------------------------------------
    def set_online(self, client_id: str) -> bool:
        """Mark a client online. Returns False if unknown or banned."""
        record = self._clients.get(client_id)
        if record is None or record.banned:
            return False
        record.online = True
        record.last_heartbeat = time.time()
        return True

    def update_endpoint(self, client_id: str, addr: Tuple[str, int]) -> None:
        record = self._clients.get(client_id)
        if record is not None:
            record.public_endpoint = addr
            record.client_ip = addr[0]

    def heartbeat(self, client_id: str) -> bool:
        """Bump the heartbeat timestamp. Returns False if the client is unknown."""
        record = self._clients.get(client_id)
        if record is None:
            return False
        record.last_heartbeat = time.time()
        record.online = True
        return True

    def prune_stale(self, timeout: float) -> List[str]:
        """Mark clients offline when their heartbeat is older than ``timeout``.

        Returns:
            The list of client_ids that were marked offline.
        """
        cutoff = time.time() - timeout
        stale: List[str] = []
        for client_id, record in self._clients.items():
            if record.online and record.last_heartbeat < cutoff:
                record.online = False
                stale.append(client_id)
        return stale

    # ---- network membership (managed by NetworkManager) --------------------
    def add_network(self, client_id: str, network_id: str) -> None:
        record = self._clients.get(client_id)
        if record is not None:
            record.networks.add(network_id)

    def remove_network(self, client_id: str, network_id: str) -> None:
        record = self._clients.get(client_id)
        if record is not None:
            record.networks.discard(network_id)

    def networks_for(self, client_id: str) -> Set[str]:
        record = self._clients.get(client_id)
        return set(record.networks) if record else set()

    def members_in_networks(self, network_ids: Iterable[str]) -> List[ClientRecord]:
        """All online clients that belong to any of the given networks."""
        wanted = set(network_ids)
        return [
            r
            for r in self._clients.values()
            if r.online and r.networks.intersection(wanted)
        ]


__all__ = ["ClientRecord", "ClientRegistry"]

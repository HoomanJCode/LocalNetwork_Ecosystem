"""Network topology managers.

Implements the three supported topologies (DESIGN.md §4.8):

* :class:`MeshTopology` — default: every peer opens direct tunnels to ALL peers.
* :class:`HubAndSpokeTopology` — one hub; spokes only connect to the hub.
* :class:`GatewayTopology` — gateway bridges virtual network to physical LAN.

Each topology manager is a strategy object that the client daemon calls on
peer online/offline events to decide which tunnels to create or tear down.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Set

from common.constants import (
    TOPOLOGY_GATEWAY,
    TOPOLOGY_HUB_AND_SPOKE,
    TOPOLOGY_MESH,
)

log = logging.getLogger("localnetwork.client.topology")


# =============================================================================
# Abstract base
# =============================================================================
class TopologyManager(ABC):
    """Strategy object that decides tunnel creation/teardown per peer event."""

    def __init__(self, topology: str) -> None:
        self.topology = topology

    @abstractmethod
    async def on_peer_online(
        self,
        peer_id: str,
        peer_ip: str,
        network_id: str,
    ) -> bool:
        """Called when a peer comes online.

        Returns:
            True if a tunnel should be created to this peer.
        """

    @abstractmethod
    async def on_peer_offline(self, peer_id: str, network_id: str) -> bool:
        """Called when a peer goes offline.

        Returns:
            True if the tunnel to this peer should be closed.
        """

    @abstractmethod
    def get_peers_to_connect(self) -> Set[str]:
        """Return the set of peer IDs that should have active tunnels."""

    @property
    @abstractmethod
    def is_hub(self) -> bool:
        """Whether this client acts as the hub (hub-and-spoke only)."""

    @property
    @abstractmethod
    def is_gateway(self) -> bool:
        """Whether this client acts as the gateway (gateway mode only)."""


# =============================================================================
# Mesh topology
# =============================================================================
class MeshTopology(TopologyManager):
    """Full mesh: every peer connects to every other peer."""

    def __init__(self) -> None:
        super().__init__(TOPOLOGY_MESH)
        self._peers: Dict[str, str] = {}  # peer_id → peer_ip

    async def on_peer_online(
        self, peer_id: str, peer_ip: str, network_id: str
    ) -> bool:
        self._peers[peer_id] = peer_ip
        log.debug("mesh: peer %s (%s) online — will create tunnel", peer_id, peer_ip)
        return True  # Always connect

    async def on_peer_offline(self, peer_id: str, network_id: str) -> bool:
        self._peers.pop(peer_id, None)
        log.debug("mesh: peer %s offline — will close tunnel", peer_id)
        return True  # Always close

    def get_peers_to_connect(self) -> Set[str]:
        return set(self._peers.keys())

    @property
    def is_hub(self) -> bool:
        return False

    @property
    def is_gateway(self) -> bool:
        return False


# =============================================================================
# Hub-and-Spoke topology
# =============================================================================
class HubAndSpokeTopology(TopologyManager):
    """Hub-and-spoke: spokes only connect to the designated hub."""

    def __init__(self, hub_client_id: str, is_hub: bool = False) -> None:
        super().__init__(TOPOLOGY_HUB_AND_SPOKE)
        self._hub_id = hub_client_id
        self._is_hub = is_hub
        self._spokes: Dict[str, str] = {}  # spoke_id → spoke_ip

    async def on_peer_online(
        self, peer_id: str, peer_ip: str, network_id: str
    ) -> bool:
        if self._is_hub:
            # Hub accepts all spokes
            self._spokes[peer_id] = peer_ip
            log.debug("hub: spoke %s (%s) online", peer_id, peer_ip)
            return True
        else:
            # Spoke only connects to the hub
            if peer_id == self._hub_id:
                log.debug("spoke: hub %s online — will create tunnel", peer_id)
                return True
            log.debug("spoke: ignoring non-hub peer %s", peer_id)
            return False

    async def on_peer_offline(self, peer_id: str, network_id: str) -> bool:
        if self._is_hub:
            self._spokes.pop(peer_id, None)
            return True
        else:
            return peer_id == self._hub_id

    def get_peers_to_connect(self) -> Set[str]:
        if self._is_hub:
            return set(self._spokes.keys())
        return {self._hub_id}

    @property
    def is_hub(self) -> bool:
        return self._is_hub

    @property
    def is_gateway(self) -> bool:
        return False

    def get_spoke_ip(self, spoke_id: str) -> Optional[str]:
        """Get the virtual IP of a spoke (hub side)."""
        return self._spokes.get(spoke_id)


# =============================================================================
# Gateway topology
# =============================================================================
class GatewayTopology(TopologyManager):
    """Gateway mode: one client bridges the virtual network to its physical LAN."""

    def __init__(self, gateway_client_id: str, is_gateway: bool = False) -> None:
        super().__init__(TOPOLOGY_GATEWAY)
        self._gateway_id = gateway_client_id
        self._is_gateway = is_gateway
        self._peers: Dict[str, str] = {}  # peer_id → peer_ip

        # ARP proxy table: virtual IP → MAC address
        self._arp_table: Dict[str, str] = {}

    async def on_peer_online(
        self, peer_id: str, peer_ip: str, network_id: str
    ) -> bool:
        self._peers[peer_id] = peer_ip
        if self._is_gateway:
            log.debug("gateway: peer %s (%s) online", peer_id, peer_ip)
            # Gateway connects to all peers
            return True
        else:
            # Remote clients only connect to the gateway
            if peer_id == self._gateway_id:
                log.debug("remote: gateway %s online", peer_id)
                return True
            return False

    async def on_peer_offline(self, peer_id: str, network_id: str) -> bool:
        self._peers.pop(peer_id, None)
        self._arp_table.pop(peer_id, None)
        return True

    def get_peers_to_connect(self) -> Set[str]:
        if self._is_gateway:
            return set(self._peers.keys())
        return {self._gateway_id}

    @property
    def is_hub(self) -> bool:
        return False

    @property
    def is_gateway(self) -> bool:
        return self._is_gateway

    def set_arp_entry(self, ip: str, mac: str) -> None:
        """Register an ARP table entry (for proxy ARP on the gateway)."""
        self._arp_table[ip] = mac

    def get_arp_entry(self, ip: str) -> Optional[str]:
        """Look up a MAC address for a virtual IP."""
        return self._arp_table.get(ip)

    def get_peer_ip(self, peer_id: str) -> Optional[str]:
        return self._peers.get(peer_id)


# =============================================================================
# Factory
# =============================================================================
def create_topology_manager(
    topology: str,
    hub_client_id: str = "",
    gateway_client_id: str = "",
    is_hub: bool = False,
    is_gateway: bool = False,
) -> TopologyManager:
    """Create the appropriate topology manager for a network.

    Args:
        topology: One of ``mesh``, ``hub_and_spoke``, ``gateway``.
        hub_client_id: The client ID of the hub (hub-and-spoke).
        gateway_client_id: The client ID of the gateway (gateway mode).
        is_hub: Whether this client is the hub.
        is_gateway: Whether this client is the gateway.

    Returns:
        A :class:`TopologyManager` instance.

    Raises:
        ValueError: For unknown topologies.
    """
    if topology == TOPOLOGY_MESH:
        return MeshTopology()
    if topology == TOPOLOGY_HUB_AND_SPOKE:
        return HubAndSpokeTopology(hub_client_id, is_hub)
    if topology == TOPOLOGY_GATEWAY:
        return GatewayTopology(gateway_client_id, is_gateway)
    raise ValueError(f"unknown topology: {topology}")


__all__ = [
    "TopologyManager",
    "MeshTopology",
    "HubAndSpokeTopology",
    "GatewayTopology",
    "create_topology_manager",
]

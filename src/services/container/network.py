"""Docker network management for WAN-only container access.

This module provides functionality to create and manage a Docker network that
allows execution containers to access the public internet while blocking:
- Private IP ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
- Link-local addresses (169.254.0.0/16) - includes cloud metadata services
- Loopback addresses (127.0.0.0/8)
- Docker host gateway
- Inter-container communication
"""

import asyncio
import subprocess
from typing import List, Optional

import structlog
from docker import DockerClient
from docker.errors import APIError, NotFound
from docker.models.networks import Network

from ...config import settings

logger = structlog.get_logger(__name__)

# IP ranges to block (private networks + special ranges)
BLOCKED_IP_RANGES: List[str] = [
    "10.0.0.0/8",  # Class A private
    "172.16.0.0/12",  # Class B private (includes Docker default bridge)
    "192.168.0.0/16",  # Class C private
    "169.254.0.0/16",  # Link-local (includes cloud metadata 169.254.169.254)
    "127.0.0.0/8",  # Loopback
    "224.0.0.0/4",  # Multicast
    "240.0.0.0/4",  # Reserved
]

# WAN network subnet (separate from main code-interpreter-network 172.20.0.0/16)
WAN_NETWORK_SUBNET = "172.30.0.0/16"
WAN_NETWORK_GATEWAY = "172.30.0.1"

# iptables chain name for our rules
IPTABLES_CHAIN_NAME = "CODE_INTERP_WAN"


class WANNetworkManager:
    """Manages the WAN-only Docker network for execution containers.

    This class handles:
    - Creating/getting the Docker bridge network with ICC disabled
    - Applying iptables rules to block private IP ranges
    - Cleaning up iptables rules on shutdown
    """

    def __init__(self, docker_client: DockerClient):
        """Initialize the WAN network manager.

        Args:
            docker_client: Docker client instance
        """
        self._client = docker_client
        self._network: Optional[Network] = None
        self._initialized = False
        self._bridge_name: Optional[str] = None

    @property
    def network_name(self) -> str:
        """Get the WAN network name from settings."""
        return settings.wan_network_name

    @property
    def dns_servers(self) -> List[str]:
        """Get DNS servers from settings."""
        return settings.wan_dns_servers

    async def initialize(self) -> bool:
        """Initialize the WAN network with iptables rules.

        Returns:
            True if network is ready, False otherwise
        """
        if self._initialized:
            return True

        try:
            # Get or create the Docker network
            self._network = await self._get_or_create_network()

            if self._network:
                # Get the bridge interface name
                self._bridge_name = await self._get_bridge_name()

                if self._bridge_name:
                    # Apply iptables rules to block private IPs
                    await self._apply_iptables_rules()

                self._initialized = True
                logger.info(
                    "WAN network initialized",
                    network_name=self.network_name,
                    network_id=self._network.id[:12] if self._network.id else "unknown",
                    bridge_name=self._bridge_name,
                )
                return True

            return False

        except Exception as e:
            logger.error("Failed to initialize WAN network", error=str(e))
            return False

    async def _get_or_create_network(self) -> Optional[Network]:
        """Get existing network or create new one.

        Returns:
            Docker Network object or None if creation failed
        """
        loop = asyncio.get_event_loop()

        # Try to get existing network
        try:
            networks = await loop.run_in_executor(
                None, lambda: self._client.networks.list(names=[self.network_name])
            )
            if networks:
                logger.info(
                    "Found existing WAN network",
                    network_name=self.network_name,
                    network_id=networks[0].id[:12],
                )
                return networks[0]
        except Exception as e:
            logger.warning("Error checking for existing network", error=str(e))

        # Create new network with specific subnet
        logger.info("Creating WAN network", network_name=self.network_name)

        ipam_config = {
            "Driver": "default",
            "Config": [{"Subnet": WAN_NETWORK_SUBNET, "Gateway": WAN_NETWORK_GATEWAY}],
        }

        try:
            network = await loop.run_in_executor(
                None,
                lambda: self._client.networks.create(
                    name=self.network_name,
                    driver="bridge",
                    ipam=ipam_config,
                    options={
                        # Enable masquerading for outbound internet access
                        "com.docker.network.bridge.enable_ip_masquerade": "true",
                        # Disable inter-container communication
                        "com.docker.network.bridge.enable_icc": "false",
                    },
                    labels={
                        "com.code-interpreter.managed": "true",
                        "com.code-interpreter.type": "wan-access",
                    },
                ),
            )
            logger.info(
                "Created WAN network",
                network_name=self.network_name,
                network_id=network.id[:12],
                subnet=WAN_NETWORK_SUBNET,
            )
            return network
        except APIError as e:
            logger.error("Failed to create WAN network", error=str(e))
            raise

    async def _get_bridge_name(self) -> Optional[str]:
        """Get the Linux bridge interface name for the network.

        Returns:
            Bridge interface name (e.g., 'br-abc123def456') or None
        """
        if not self._network:
            return None

        try:
            loop = asyncio.get_event_loop()
            # Reload network to get fresh info
            await loop.run_in_executor(None, self._network.reload)

            # Get network ID prefix (first 12 chars)
            network_id = self._network.id[:12]
            bridge_name = f"br-{network_id}"

            logger.debug(
                "Determined bridge name",
                network_id=network_id,
                bridge_name=bridge_name,
            )
            return bridge_name
        except Exception as e:
            logger.warning("Could not determine bridge name", error=str(e))
            return None

    async def _apply_iptables_rules(self) -> None:
        """Apply iptables rules to block private IP ranges.

        This creates rules that:
        1. Allow established connections
        2. Allow DNS (UDP/TCP 53) to public DNS servers
        3. Block all traffic to private IP ranges
        4. Block access to Docker host gateway
        5. Allow all other outbound traffic to public IPs
        """
        if not self._bridge_name:
            logger.warning("No bridge name available, skipping iptables rules")
            return

        rules: List[str] = []

        # Create custom chain if it doesn't exist (ignore error if exists)
        rules.append(f"iptables -N {IPTABLES_CHAIN_NAME} 2>/dev/null || true")

        # Flush existing rules in our chain
        rules.append(f"iptables -F {IPTABLES_CHAIN_NAME}")

        # Allow established/related connections (critical for return traffic)
        rules.append(
            f"iptables -A {IPTABLES_CHAIN_NAME} -m state --state ESTABLISHED,RELATED -j ACCEPT"
        )

        # Allow DNS to public DNS servers
        for dns in self.dns_servers:
            rules.append(
                f"iptables -A {IPTABLES_CHAIN_NAME} -p udp -d {dns} --dport 53 -j ACCEPT"
            )
            rules.append(
                f"iptables -A {IPTABLES_CHAIN_NAME} -p tcp -d {dns} --dport 53 -j ACCEPT"
            )

        # Block all private IP ranges
        for ip_range in BLOCKED_IP_RANGES:
            rules.append(f"iptables -A {IPTABLES_CHAIN_NAME} -d {ip_range} -j DROP")

        # Block Docker host gateway explicitly
        rules.append(
            f"iptables -A {IPTABLES_CHAIN_NAME} -d {WAN_NETWORK_GATEWAY} -j DROP"
        )

        # Allow all other traffic (public internet)
        rules.append(f"iptables -A {IPTABLES_CHAIN_NAME} -j ACCEPT")

        # Remove any existing rule in FORWARD chain (ignore error if not exists)
        rules.append(
            f"iptables -D FORWARD -i {self._bridge_name} -j {IPTABLES_CHAIN_NAME} 2>/dev/null || true"
        )

        # Insert our chain at the beginning of FORWARD chain
        rules.append(
            f"iptables -I FORWARD 1 -i {self._bridge_name} -j {IPTABLES_CHAIN_NAME}"
        )

        # Execute rules
        loop = asyncio.get_event_loop()
        failed_rules = []

        for rule in rules:
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda r=rule: subprocess.run(  # nosec B602 - iptables rules built from constants
                        r, shell=True, check=False, capture_output=True, text=True
                    ),
                )
                if result.returncode != 0 and "already exists" not in result.stderr:
                    # Only log as warning if it's not an expected "already exists" error
                    if result.stderr.strip():
                        failed_rules.append((rule, result.stderr.strip()))
            except Exception as e:
                failed_rules.append((rule, str(e)))

        if failed_rules:
            logger.warning(
                "Some iptables rules failed",
                failed_count=len(failed_rules),
                failed_rules=failed_rules[:3],  # Log first 3 failures
            )

        logger.info(
            "Applied iptables rules for WAN network",
            chain_name=IPTABLES_CHAIN_NAME,
            bridge_name=self._bridge_name,
            blocked_ranges=len(BLOCKED_IP_RANGES),
            dns_servers=self.dns_servers,
        )

    async def cleanup(self) -> None:
        """Clean up iptables rules.

        Called on application shutdown to remove the iptables rules.
        The Docker network itself is left intact for reuse.
        """
        if not self._bridge_name:
            return

        logger.info("Cleaning up WAN network iptables rules")

        rules = [
            # Remove from FORWARD chain
            f"iptables -D FORWARD -i {self._bridge_name} -j {IPTABLES_CHAIN_NAME} 2>/dev/null || true",
            # Flush our chain
            f"iptables -F {IPTABLES_CHAIN_NAME} 2>/dev/null || true",
            # Delete our chain
            f"iptables -X {IPTABLES_CHAIN_NAME} 2>/dev/null || true",
        ]

        loop = asyncio.get_event_loop()
        for rule in rules:
            try:
                await loop.run_in_executor(
                    None,
                    lambda r=rule: subprocess.run(
                        r, shell=True, check=False
                    ),  # nosec B602
                )
            except Exception:
                pass  # Ignore cleanup errors

        logger.info("Cleaned up WAN network iptables rules")

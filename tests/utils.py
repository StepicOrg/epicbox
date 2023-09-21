from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docker import DockerClient


def is_docker_swarm(client: DockerClient) -> bool:
    """Check if the client is connected to a Docker Swarm cluster."""
    docker_version = client.version()["Version"]
    return docker_version.startswith("swarm")


def get_swarm_nodes(client: DockerClient) -> list[str]:
    system_status = client.info()["SystemStatus"]
    if not system_status:
        return []
    return [node[0].strip() for node in system_status[4::9]]

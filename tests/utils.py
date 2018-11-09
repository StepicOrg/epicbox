def is_docker_swarm(client):
    """Check if the client is connected to a Docker Swarm cluster."""
    docker_version = client.version()['Version']
    return docker_version.startswith('swarm')


def get_swarm_nodes(client):
    system_status = client.info()['SystemStatus']
    if not system_status:
        return []
    return list(map(lambda node: node[0].strip(), system_status[4::9]))

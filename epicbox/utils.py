import signal

import docker

from . import config


def get_docker_client(base_url=None):
    return docker.Client(base_url=base_url or config.DOCKER_URL)


def is_docker_swarm(client):
    """Check if the client connected to a Docker Swarm cluster."""
    docker_version = client.version()['Version']
    return docker_version.startswith('swarm')


def get_swarm_nodes(client):
    system_status = client.info()['SystemStatus']
    if not system_status:
        return []
    return list(map(lambda node: node[0].strip(), system_status[4::9]))


def filter_filenames(files):
    return [file['name'] for file in files if 'name' in file]


def merge_limits_defaults(limits):
    if not limits:
        return config.DEFAULT_LIMITS
    is_realtime_specified = 'realtime' in limits
    for limit_name, default_value in config.DEFAULT_LIMITS.items():
        if limit_name not in limits:
            limits[limit_name] = default_value
    if not is_realtime_specified:
        limits['realtime'] = limits['cputime'] * config.CPU_TO_REAL_TIME_FACTOR
    return limits


def truncate_result(result):
    MAX_OUTPUT_LENGTH = 100
    truncated = {}
    for k, v in result.items():
        if k in ['stdout', 'stderr']:
            if len(v) > MAX_OUTPUT_LENGTH:
                v = v[:MAX_OUTPUT_LENGTH] + b' *** truncated ***'
        truncated[k] = v
    return truncated


def is_killed_by_sigkill_or_sigxcpu(status):
    return status - 128 in [signal.SIGKILL, signal.SIGXCPU]

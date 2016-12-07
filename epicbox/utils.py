import signal
import struct

import docker

from docker import constants as docker_consts

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


def demultiplex_docker_buffer(response):
    """An improved version of _multiplexed_buffer_helper from docker-py."""
    buf = response.content
    buf_length = len(buf)
    chunks = []
    walker = 0
    while True:
        if buf_length - walker < 8:
            break
        header = buf[walker:walker + docker_consts.STREAM_HEADER_SIZE_BYTES]
        _, length = struct.unpack_from('>BxxxL', header)
        start = walker + docker_consts.STREAM_HEADER_SIZE_BYTES
        end = start + length
        walker = end
        chunks.append(buf[start:end])
    return b''.join(chunks)


def docker_logs(container, stdout=False, stderr=False):
    docker_client = get_docker_client()
    params = {
        'stdout': stdout and 1 or 0,
        'stderr': stderr and 1 or 0,
    }
    url = docker_client._url("/containers/{0}/logs", container['Id'])
    res = docker_client._get(url, params=params, stream=False)
    return demultiplex_docker_buffer(res)


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

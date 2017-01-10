import signal
import struct

import docker

from docker import constants as docker_consts
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

from . import config


_DOCKER_CLIENTS = {}


def get_docker_client(base_url=None, retry_read=config.DOCKER_MAX_READ_RETRIES,
                      retry_status_forcelist=(500,)):
    client_key = (retry_read, retry_status_forcelist)
    if client_key not in _DOCKER_CLIENTS:
        client = docker.Client(base_url=base_url or config.DOCKER_URL,
                               timeout=config.DOCKER_TIMEOUT)
        retries = Retry(total=config.DOCKER_MAX_RETRIES,
                        connect=0,
                        read=retry_read,
                        method_whitelist=False,
                        status_forcelist=retry_status_forcelist,
                        backoff_factor=config.DOCKER_BACKOFF_FACTOR,
                        raise_on_status=False)
        http_adapter = HTTPAdapter(max_retries=retries)
        client.mount('http://', http_adapter)
        _DOCKER_CLIENTS[client_key] = client
    return _DOCKER_CLIENTS[client_key]


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
    if isinstance(container, dict):
        container = container.get('Id')
    params = {
        'stdout': stdout and 1 or 0,
        'stderr': stderr and 1 or 0,
    }
    url = docker_client._url("/containers/{0}/logs", container)
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

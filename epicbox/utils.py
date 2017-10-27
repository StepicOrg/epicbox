import errno
import os
import select
import signal
import socket
import struct
import time

import docker
import structlog
from docker import constants as docker_consts
from docker.utils import Ulimit
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

from . import config

logger = structlog.get_logger()

_DOCKER_CLIENTS = {}

#: Recoverable IO/OS Errors.
ERRNO_RECOVERABLE = (errno.EINTR, errno.EDEADLK, errno.EWOULDBLOCK)


def get_docker_client(base_url=None, retry_read=config.DOCKER_MAX_READ_RETRIES,
                      retry_status_forcelist=(500,)):
    client_key = (retry_read, retry_status_forcelist)
    if client_key not in _DOCKER_CLIENTS:
        client = docker.Client(base_url=base_url or config.DOCKER_URL,
                               timeout=config.DOCKER_TIMEOUT)
        retries = Retry(total=config.DOCKER_MAX_TOTAL_RETRIES,
                        connect=config.DOCKER_MAX_CONNECT_RETRIES,
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


def demultiplex_docker_stream(data):
    """
    Demultiplex the raw docker stream into separate stdout and stderr streams.

    Docker multiplexes streams together when there is no PTY attached, by
    sending an 8-byte header, followed by a chunk of data.

    The first 4 bytes of the header denote the stream from which the data came
    (i.e. 0x01 = stdout, 0x02 = stderr). Only the first byte of these initial 4
    bytes is used.

    The next 4 bytes indicate the length of the following chunk of data as an
    integer in big endian format. This much data must be consumed before the
    next 8-byte header is read.

    Docs: https://docs.docker.com/engine/api/v1.24/#attach-to-a-container

    :param bytes data: A raw stream data.
    :return: A tuple `(stdout, stderr)` of bytes objects.
    """
    data_length = len(data)
    stdout_chunks = []
    stderr_chunks = []
    walker = 0
    while data_length - walker >= 8:
        header = data[walker:walker + docker_consts.STREAM_HEADER_SIZE_BYTES]
        stream_type, length = struct.unpack_from('>BxxxL', header)
        start = walker + docker_consts.STREAM_HEADER_SIZE_BYTES
        end = start + length
        walker = end
        if stream_type == 1:
            stdout_chunks.append(data[start:end])
        elif stream_type == 2:
            stderr_chunks.append(data[start:end])
    return b''.join(stdout_chunks), b''.join(stderr_chunks)


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


def _socket_read(sock, n=4096):
    """
    Read at most `n` bytes of data from the `sock` socket.

    :return: A bytes object or `None` at end of stream.
    """
    try:
        data = os.read(sock.fileno(), n)
    except EnvironmentError as e:
        print("## exc:", e)
        if e.errno in ERRNO_RECOVERABLE:
            return b''
        raise e
    if data:
        return data


def _socket_write(sock, data):
    """
    Write as much data from the `data` buffer to the `sock` socket as possible.

    :return: The number of bytes sent.
    """
    try:
        return os.write(sock.fileno(), data)
    except EnvironmentError as e:
        if e.errno in ERRNO_RECOVERABLE:
            return 0
        raise e


def docker_communicate(container, stdin=None, start_container=True,
                       timeout=None):
    """
    Interact with the container: Start it if required. Send data to stdin.
    Read data from stdout and stderr, until end-of-file is reached. Wait for
    the container to terminate.

    :param container: A container to interact with.
    :param bytes stdin: The data to be sent to the standard input of the
                        container, or `None`, if no data should be sent.
    :param bool start_container: Whether to start the container after
                                 attaching to it.
    :param int timeout: Time in seconds to wait for the container to terminate.

    :return: A tuple `(stdout, stderr)` of bytes objects.

    :raise TimeoutError: If the container does not terminate after `timeout`
                         seconds. The container is not killed automatically.
    :raise RequestException, DockerException, OSError: If an error occurred
        with the underlying docker system.
    """
    # Retry on 'No such container' since it may happen when the attach/start
    # is called immediately after the container is created.
    docker_client = get_docker_client(retry_status_forcelist=(404, 500))
    log = logger.bind(container=container)
    params = {
        # Attach to stdin only if there is something to send to it
        'stdin': 1 if stdin else 0,
        'stdout': 1,
        'stderr': 1,
        'stream': 1,
        'logs': 0,
    }
    sock = docker_client.attach_socket(container, params=params)
    os.set_blocking(sock.fileno(), False)  # Make socket non-blocking
    log.info("Attached to the container", params=params, fd=sock.fileno(),
             timeout=timeout)
    if start_container:
        docker_client.start(container)
        log.info("Container started")

    stream_data = b''
    start_time = time.time()
    while timeout is None or time.time() - start_time < timeout:
        read_ready, write_ready, _ = select.select([sock], [sock], [], 1)
        is_io_active = False
        if read_ready:
            is_io_active = True
            data = _socket_read(sock)
            if data is None:
                log.debug("Container output reached EOF. Closing the socket")
                sock.close()
                break
            stream_data += data
        if write_ready and stdin:
            is_io_active = True
            written = _socket_write(sock, stdin)
            stdin = stdin[written:]
            if not stdin:
                log.debug("All input data has been sent. Shut down the write "
                          "half of the socket.")
                sock._sock.shutdown(socket.SHUT_WR)
        if not is_io_active:
            # Save CPU time
            time.sleep(0.05)
    else:
        sock.close()
        raise TimeoutError("Container doesn't terminate after timeout seconds")

    return demultiplex_docker_stream(stream_data)


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


def create_ulimits(limits):
    ulimits = []
    if limits['cputime']:
        cpu = limits['cputime']
        ulimits.append(Ulimit(name='cpu', soft=cpu, hard=cpu))
    if 'file_size' in limits:
        fsize = limits['file_size']
        ulimits.append(Ulimit(name='fsize', soft=fsize, hard=fsize))
    return ulimits or None


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

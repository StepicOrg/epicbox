import io
import tarfile
import time
import uuid

import dateutil.parser
import structlog

from contextlib import contextmanager
from functools import partial

from docker.errors import APIError, DockerException, NotFound
from requests.exceptions import ReadTimeout, RequestException
from requests.packages.urllib3.exceptions import \
    MaxRetryError, ReadTimeoutError

from . import config, exceptions, utils


__all__ = ['run', 'working_directory']

logger = structlog.get_logger()


def run(profile_name, command=None, files=None, stdin=None, limits=None,
        workdir=None):
    """Run a new sandbox container.

    :raises DockerError: if an error occurred with the underlying
                         docker system

    """
    if profile_name not in config.PROFILES:
        # TODO: treat name as docker image
        raise ValueError("Profile not found: {0}".format(profile_name))
    if workdir is not None and not isinstance(workdir, _WorkingDirectory):
        raise ValueError("Invalid `workdir`, it should be created using "
                         "`working_directory` context manager")
    profile = config.PROFILES[profile_name]
    command = command or profile.command or 'true'
    if stdin:
        if not isinstance(stdin, (bytes, str)):
            raise TypeError("stdin should be 'bytes' or 'str'")
        stdin_content = stdin if isinstance(stdin, bytes) else stdin.encode()
        stdin_filename = '_sandbox_stdin'
        files = files or []
        files.append({'name': stdin_filename, 'content': stdin_content})
        # TODO: write to stdin using attach API
        command = '< {0} {1}'.format(stdin_filename, command)
    command_list = ['/bin/sh', '-c', command]
    limits = utils.merge_limits_defaults(limits)

    start_sandbox = partial(
        _start_sandbox, profile.docker_image, command_list, limits,
        files=files, workdir=workdir, user=profile.user,
        read_only=profile.read_only, network_disabled=profile.network_disabled)
    if files and not workdir:
        with working_directory() as workdir:
            return start_sandbox(workdir=workdir)
    return start_sandbox()


class _WorkingDirectory(object):
    """Represent a Docker volume used as a working directory.

    Not intended to be instantiated by yourself.

    """
    def __init__(self, volume, node=None):
        self.volume = volume
        self.node = node

    def __repr__(self):
        return "WorkingDirectory(volume={!r}, node={!r})".format(self.volume,
                                                                 self.node)


@contextmanager
def working_directory():
    docker_client = utils.get_docker_client()
    volume_name = 'epicbox-' + str(uuid.uuid4())
    log = logger.bind(volume=volume_name)
    log.info("Creating new docker volume for working directory")
    try:
        docker_client.create_volume(volume_name)
    except (RequestException, DockerException) as e:
        log.exception("Failed to create a docker volume")
        raise exceptions.DockerError(str(e))
    log.info("New docker volume is created")

    yield _WorkingDirectory(volume=volume_name, node=None)

    log.info("Removing the docker volume")
    try:
        docker_client.remove_volume(volume_name)
    except NotFound:
        log.warning("Failed to remove the docker volume, it doesn't exist")
    except (RequestException, DockerException) as e:
        log.exception("Failed to remove the docker volume")
    else:
        log.info("Docker volume removed")


def _write_files(container, files):
    """Write files to the working directory in the given container."""
    # Retry on 'No such container' since it may happen when the function
    # is called immediately after the container was created
    docker_client = utils.get_docker_client(retry_status_forcelist=(404,))
    log = logger.bind(files=utils.filter_filenames(files), container=container)
    log.info("Writing files to the working directory in container")
    mtime = int(time.time())
    files_written = []
    tarball_fileobj = io.BytesIO()
    with tarfile.open(fileobj=tarball_fileobj, mode='w') as tarball:
        for file in files:
            if not file.get('name') or not isinstance(file['name'], str):
                continue
            content = file.get('content', b'')
            file_info = tarfile.TarInfo(name=file['name'])
            file_info.size = len(content)
            file_info.mtime = mtime
            tarball.addfile(file_info, fileobj=io.BytesIO(content))
            files_written.append(file['name'])
    try:
        docker_client.put_archive(container, config.DOCKER_WORKDIR,
                                  tarball_fileobj.getvalue())
    except (RequestException, DockerException) as e:
        log.exception("Failed to extract an archive of files to the working "
                      "directory in container")
        raise exceptions.DockerError(str(e))
    log.info("Successfully written files to the working directory",
             files_written=files_written)


def _get_container_output(container):
    try:
        stdout = utils.docker_logs(container, stdout=True, stderr=False)
        stderr = utils.docker_logs(container, stdout=False, stderr=True)
    except (RequestException, DockerException):
        logger.exception("Failed to get stdout/stderr of the container",
                         container=container)
        return b'', b''
    return stdout, stderr


def _inspect_container_state(container):
    docker_client = utils.get_docker_client()
    try:
        container_info = docker_client.inspect_container(container)
    except (RequestException, DockerException) as e:
        logger.exception("Failed to inspect the container",
                         container=container)
        raise exceptions.DockerError(str(e))
    started_at = dateutil.parser.parse(container_info['State']['StartedAt'])
    finished_at = dateutil.parser.parse(container_info['State']['FinishedAt'])
    duration = finished_at - started_at
    duration_seconds = duration.total_seconds()
    if duration_seconds < 0:
        duration_seconds = -1
    return {
        'duration': duration_seconds,
        'oom_killed': container_info['State'].get('OOMKilled', False),
    }


def _inspect_container_node(container):
    docker_client = utils.get_docker_client()
    try:
        container_info = docker_client.inspect_container(container)
    except (RequestException, DockerException) as e:
        logger.exception("Failed to inspect the container",
                         container=container)
        raise exceptions.DockerError(str(e))
    if 'Node' not in container_info:
        # Remote Docker side is not a Docker Swarm cluster
        return None
    return container_info['Node']['Name']


def _start_sandbox(image, command, limits, files=None, workdir=None, user=None,
                   read_only=False, network_disabled=True):
    # TODO: clean up a sandbox in case of errors (fallback/periodic task)
    sandbox_id = str(uuid.uuid4())
    name = 'epicbox-' + sandbox_id
    mem_limit = str(limits['memory']) + 'm'

    binds = {
        workdir.volume: {
            'bind': config.DOCKER_WORKDIR,
            'ro': False,
        }
    } if workdir else None
    ulimits = utils.create_ulimits(limits)
    docker_client = utils.get_docker_client()
    host_config = docker_client.create_host_config(binds=binds,
                                                   read_only=read_only,
                                                   mem_limit=mem_limit,
                                                   memswap_limit=mem_limit,
                                                   ulimits=ulimits)
    environment = None
    if workdir and workdir.node:
        # Add constraint to run a container on the Swarm node that
        # ran the first container with this working directory.
        environment = ['constraint:node==' + workdir.node]
    log = logger.bind(sandbox_id=sandbox_id)
    log.info("Starting new sandbox", name=name, image=image, command=command,
             limits=limits, workdir=workdir, user=user,
             read_only=read_only, network_disabled=network_disabled)
    try:
        c = docker_client.create_container(image,
                                           command=command,
                                           user=user,
                                           environment=environment,
                                           network_disabled=network_disabled,
                                           name=name,
                                           working_dir=config.DOCKER_WORKDIR,
                                           host_config=host_config)
    except (RequestException, DockerException) as e:
        c = None
        if "Container created" in str(e):
            # Workaround for Docker Swarm bug:
            # https://github.com/docker/swarm/pull/2190.
            # API can raise an exception: 500 Server Error: Internal Server
            # Error Container created but refresh didn't report it back.
            # We can skip the exception and use the created container.
            log.warning("Docker Swarm error caught while creating a container",
                        exc=e)
            c = name
        elif isinstance(e, APIError) and e.response.status_code == 409:
            log.info("The container with the given name is already created",
                     name=name)
            c = name
        if not c:
            log.exception("Failed to create a sandbox container")
            raise exceptions.DockerError(str(e))
    log = log.bind(container=c)
    log.info("Sandbox container created")
    if workdir and not workdir.node:
        node_name = _inspect_container_node(c)
        if node_name:
            # Assign a Swarm node name to the working directory to run
            # subsequent containers on this same node.
            workdir.node = node_name
            log.info("Assigned Swarm node to the working directory",
                     workdir=workdir)
    if files:
        _write_files(c, files)
    try:
        docker_client.start(c)
    except (RequestException, DockerException) as e:
        log.exception("Failed to start the sandbox container")
        raise exceptions.DockerError(str(e))
    log.info("Sandbox started")

    log.info("Waiting until the sandbox container exits")
    docker_wait_client = utils.get_docker_client(retry_read=0)
    timeout = False
    exit_code = None
    try:
        exit_code = docker_wait_client.wait(c, timeout=limits['realtime'])
        log.info("Sandbox container exited", exit_code=exit_code)
    except ReadTimeout:
        timeout = True
    except (RequestException, DockerException) as e:
        if isinstance(e, RequestException):
            wrapped_exc = e.args[0]
            if (isinstance(wrapped_exc, MaxRetryError) and
                    isinstance(wrapped_exc.reason, ReadTimeoutError)):
                timeout = True
        if not timeout:
            log.exception("Sandbox runtime error")
            raise exceptions.DockerError(str(e))
    if timeout:
        log.info("Sandbox realtime limit exceeded",
                 realtime=limits['realtime'])

    result = {
        'exit_code': exit_code,
        'stdout': b'',
        'stderr': b'',
        'duration': None,
        'timeout': timeout,
        'oom_killed': False,
    }
    if exit_code is not None:
        result['stdout'], result['stderr'] = _get_container_output(c)
        state = _inspect_container_state(c)
        result.update(state)
        if (utils.is_killed_by_sigkill_or_sigxcpu(exit_code) and
                not state['oom_killed']):
            # SIGKILL/SIGXCPU is sent but not by out of memory killer
            result['timeout'] = True
    log.info("Sandbox run result", result=utils.truncate_result(result))

    log.info("Cleaning up the sandbox")
    _cleanup_sandbox(c)
    log.info("Sandbox cleaned up")
    return result


def _cleanup_sandbox(container):
    docker_client = utils.get_docker_client()
    try:
        docker_client.remove_container(container, v=True, force=True)
    except (RequestException, DockerException):
        # TODO: handle 500 Driver aufs failed to remove root filesystem
        logger.exception("Failed to remove the sandbox container",
                         container=container)

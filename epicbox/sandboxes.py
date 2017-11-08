import io
import tarfile
import time
import uuid
from contextlib import contextmanager
from functools import partial

import dateutil.parser
import structlog
from docker.errors import APIError, DockerException, NotFound
from requests.exceptions import RequestException

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
            raise TypeError("'stdin' must be bytes or str")
        if isinstance(stdin, str):
            stdin = stdin.encode()
    command_list = ['/bin/sh', '-c', command]
    limits = utils.merge_limits_defaults(limits)

    start_sandbox = partial(
        _start_sandbox, profile.docker_image, command_list, limits,
        files=files, stdin=stdin, workdir=workdir, user=profile.user,
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
    # is called immediately after the container is created.
    # Retry on 500 Server Error when untar cannot allocate memory.
    docker_client = utils.get_docker_client(retry_status_forcelist=(404, 500))
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
        'exit_code': container_info['State']['ExitCode'],
        'duration': duration_seconds,
        'oom_killed': container_info['State'].get('OOMKilled', False),
    }


def _inspect_container_node(container):
    # 404 No such container may be returned when TimeoutError occurs
    # on container creation.
    docker_client = utils.get_docker_client(retry_status_forcelist=(404, 500))
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


def _start_sandbox(image, command, limits, files=None, stdin=None,
                   workdir=None, user=None, read_only=False,
                   network_disabled=True):
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
                                                   ulimits=ulimits,
                                                   log_config={'type': 'none'})
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
                                           stdin_open=bool(stdin),
                                           environment=environment,
                                           network_disabled=network_disabled,
                                           name=name,
                                           working_dir=config.DOCKER_WORKDIR,
                                           host_config=host_config)
    except (RequestException, DockerException) as e:
        if isinstance(e, APIError) and e.response.status_code == 409:
            log.info("The container with the given name is already created",
                     name=name)
            c = name
        else:
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

    result = {
        'exit_code': None,
        'stdout': b'',
        'stderr': b'',
        'duration': None,
        'timeout': False,
        'oom_killed': False,
    }
    try:
        stdout, stderr = utils.docker_communicate(c, stdin=stdin,
                                                  timeout=limits['realtime'])
    except TimeoutError:
        log.info("Sandbox realtime limit exceeded",
                 realtime=limits['realtime'])
        result['timeout'] = True
    except (RequestException, DockerException, OSError) as e:
        log.exception("Sandbox runtime error")
        raise exceptions.DockerError(str(e))
    else:
        log.info("Sandbox container exited")
        state = _inspect_container_state(c)
        result.update(stdout=stdout, stderr=stderr, **state)
        if (utils.is_killed_by_sigkill_or_sigxcpu(state['exit_code']) and
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

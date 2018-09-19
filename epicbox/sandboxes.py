import io
import tarfile
import time
import uuid
from contextlib import contextmanager

import structlog
from docker.errors import APIError, DockerException, NotFound
from requests.exceptions import RequestException

from . import config, exceptions, utils

__all__ = ['create', 'start', 'destroy', 'run', 'working_directory']

logger = structlog.get_logger()

_SANDBOX_NAME_PREFIX = 'epicbox-'


class _SandboxContext(dict):
    """A context manager wrapper for a sandbox container that destroys
    it upon completion of the block."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        destroy(self)


def create(profile_name, command=None, files=None, limits=None, workdir=None):
    """Create a new sandbox container without starting it.

    :param str profile_name: One of configured profile names.
    :param str command: A command with args to run in the sandbox container.
    :param list files: A list of `{'name': 'filename', 'content': b'data'}`
        dicts which define files to be written to the working directory
        of the sandbox.
    :param dict limits: Specify time and memory limits for the sandboxed
        process.  It overrides the default limits from `config.DEFAULT_LIMITS`.
    :param workdir: A working directory created using `working_directory`
                    context manager.
    :return dict: A sandbox object.

    :raises DockerError: If an error occurred with the underlying
                         docker system.
    """
    if profile_name not in config.PROFILES:
        raise ValueError("Profile not found: {0}".format(profile_name))
    if workdir is not None and not isinstance(workdir, _WorkingDirectory):
        raise ValueError("Invalid 'workdir', it should be created using "
                         "'working_directory' context manager")
    profile = config.PROFILES[profile_name]
    command = command or profile.command or 'true'
    command_list = ['/bin/sh', '-c', command]
    limits = utils.merge_limits_defaults(limits)
    c = _create_sandbox_container(profile.docker_image, command_list, limits,
                                  workdir=workdir, user=profile.user,
                                  read_only=profile.read_only,
                                  network_disabled=profile.network_disabled)
    if workdir and not workdir.node:
        node_name = utils.inspect_container_node(c)
        if node_name:
            # Assign a Swarm node name to the working directory to run
            # subsequent containers on this same node.
            workdir.node = node_name
            logger.info("Assigned Swarm node to the working directory",
                        workdir=workdir)
    if files:
        _write_files(c, files)
    sandbox = _SandboxContext(c)
    # Store the realtime limit in the sandbox structure to access it later
    # in `start` function.
    sandbox['RealtimeLimit'] = limits['realtime']
    logger.info("Sandbox prepared and ready to start", sandbox=sandbox)
    return sandbox


def _create_sandbox_container(image, command, limits, workdir=None, user=None,
                              read_only=False, network_disabled=True):
    sandbox_id = str(uuid.uuid4())
    name = _SANDBOX_NAME_PREFIX + sandbox_id
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
    log.info("Creating a new sandbox container", name=name, image=image,
             command=command, limits=limits, workdir=workdir, user=user,
             read_only=read_only, network_disabled=network_disabled)
    try:
        c = docker_client.create_container(image,
                                           command=command,
                                           user=user,
                                           stdin_open=True,
                                           environment=environment,
                                           network_disabled=network_disabled,
                                           name=name,
                                           working_dir=config.DOCKER_WORKDIR,
                                           host_config=host_config)
    except (RequestException, DockerException) as e:
        if isinstance(e, APIError) and e.response.status_code == 409:
            # This may happen because of retries, it's a recoverable error
            log.info("The container with the given name is already created",
                     name=name)
            c = {'Id': name}
        else:
            log.exception("Failed to create a sandbox container")
            raise exceptions.DockerError(str(e))
    log.info("Sandbox container created", container=c)
    return c


def start(sandbox, stdin=None):
    """Start a created sandbox container and wait for it to terminate.

    :param sandbox: A sandbox to start.
    :param bytes stdin: The data to be sent to the standard input of the
                        sandbox, or `None`, if no data should be sent.

    :return dict: A result structure containing the exit code of the sandbox,
        its stdout and stderr output, duration of execution, etc.

    :raises DockerError: If an error occurred with the underlying
                         docker system.
    """
    if stdin:
        if not isinstance(stdin, (bytes, str)):
            raise TypeError("'stdin' must be bytes or str")
        if isinstance(stdin, str):
            stdin = stdin.encode()
    realtime_limit = sandbox.get('RealtimeLimit')
    log = logger.bind(sandbox=sandbox)
    log.info("Starting the sandbox container", stdin_size=len(stdin or ''))
    result = {
        'exit_code': None,
        'stdout': b'',
        'stderr': b'',
        'duration': None,
        'timeout': False,
        'oom_killed': False,
    }
    try:
        stdout, stderr = utils.docker_communicate(sandbox, stdin=stdin,
                                                  timeout=realtime_limit)
    except TimeoutError:
        log.info("Sandbox realtime limit exceeded", realtime=realtime_limit)
        result['timeout'] = True
    except (RequestException, DockerException, OSError) as e:
        log.exception("Sandbox runtime error")
        raise exceptions.DockerError(str(e))
    else:
        log.info("Sandbox container exited")
        state = utils.inspect_container_state(sandbox)
        result.update(stdout=stdout, stderr=stderr, **state)
        if (utils.is_killed_by_sigkill_or_sigxcpu(state['exit_code']) and
                not state['oom_killed']):
            # SIGKILL/SIGXCPU is sent but not by out of memory killer
            result['timeout'] = True
    log.info("Sandbox run result", result=utils.truncate_result(result))
    return result


def destroy(sandbox):
    """Destroy a sandbox container.

    Kill a running sandbox before removal.  Remove the volumes auto-created
    and associated with the sandbox container.

    :param sandbox: A sandbox to destroy.
    """
    docker_client = utils.get_docker_client()
    try:
        docker_client.remove_container(sandbox, v=True, force=True)
    except (RequestException, DockerException):
        logger.exception("Failed to destroy the sandbox container",
                         sandbox=sandbox)
    else:
        logger.info("Sandbox container destroyed", sandbox=sandbox)


def run(profile_name, command=None, files=None, stdin=None, limits=None,
        workdir=None):
    """Run a command in a new sandbox container and wait for it to finish
    running.  Destroy the sandbox when it has finished running.

    The arguments to this function is a combination of arguments passed
    to `create` and `start` functions.

    :return dict: Same as for `start`.

    :raises DockerError: If an error occurred with the underlying
                         docker system.
    """
    with create(profile_name, command=command, files=files, limits=limits,
                workdir=workdir) as sandbox:
        return start(sandbox, stdin=stdin)


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
    try:
        yield _WorkingDirectory(volume=volume_name, node=None)
    finally:  # Ensure that volume cleanup takes place
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

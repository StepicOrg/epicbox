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


class Sandbox:
    """Represent a sandbox Docker container.

    It can be used as a context manager to destroy the container upon
    completion of the block.
    """

    def __init__(self, id_, container, realtime_limit=None):
        self.id_ = id_
        self.container = container
        self.realtime_limit = realtime_limit

    def __enter__(self):
        return self

    def __exit__(self, *args):
        destroy(self)

    def __repr__(self):
        return "<Sandbox: {} сontainer={}>".format(self.id_,
                                                   self.container.short_id)


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
    :return Sandbox: A :class:`Sandbox` object.

    :raises DockerError: If an error occurred with the underlying
                         docker system.
    """
    if profile_name not in config.PROFILES:
        raise ValueError("Profile not found: {0}".format(profile_name))
    if workdir is not None and not isinstance(workdir, _WorkingDirectory):
        raise ValueError("Invalid 'workdir', it should be created using "
                         "'working_directory' context manager")
    sandbox_id = str(uuid.uuid4())
    profile = config.PROFILES[profile_name]
    command = command or profile.command or 'true'
    command_list = ['/bin/sh', '-c', command]
    limits = utils.merge_limits_defaults(limits)
    c = _create_sandbox_container(sandbox_id, profile.docker_image,
                                  command_list, limits,
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
    sandbox = Sandbox(sandbox_id, c, realtime_limit=limits['realtime'])
    logger.info("Sandbox created and ready to start", sandbox=sandbox)
    return sandbox


def _create_sandbox_container(sandbox_id, image, command, limits, workdir=None,
                              user=None, read_only=False,
                              network_disabled=True):
    name = _SANDBOX_NAME_PREFIX + sandbox_id
    mem_limit = str(limits['memory']) + 'm'
    volumes = {
        workdir.volume: {
            'bind': config.DOCKER_WORKDIR,
            'mode': 'rw',
        }
    } if workdir else None
    ulimits = utils.create_ulimits(limits)
    environment = None
    if workdir and workdir.node:
        # Add constraint to run a container on the Swarm node that
        # ran the first container with this working directory.
        environment = ['constraint:node==' + workdir.node]

    docker_client = utils.get_docker_client()
    log = logger.bind(sandbox_id=sandbox_id)
    log.info("Creating a new sandbox container", name=name, image=image,
             command=command, limits=limits, workdir=workdir, user=user,
             read_only=read_only, network_disabled=network_disabled)
    try:
        c = docker_client.containers.create(image,
                                            command=command,
                                            user=user,
                                            stdin_open=True,
                                            environment=environment,
                                            network_disabled=network_disabled,
                                            name=name,
                                            working_dir=config.DOCKER_WORKDIR,
                                            volumes=volumes,
                                            read_only=read_only,
                                            mem_limit=mem_limit,
                                            # Prevent from using any swap
                                            memswap_limit=mem_limit,
                                            ulimits=ulimits,
                                            # limit pid
                                            pids_limit=limits["processes"],
                                            # Disable the logging driver
                                            log_config={'type': 'none'})
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

    :param Sandbox sandbox: A sandbox to start.
    :param bytes or str stdin: The data to be sent to the standard input of the
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
    log = logger.bind(sandbox=sandbox)
    log.info("Starting the sandbox", stdin_size=len(stdin or ''))
    result = {
        'exit_code': None,
        'stdout': b'',
        'stderr': b'',
        'duration': None,
        'timeout': False,
        'oom_killed': False,
    }
    try:
        stdout, stderr = utils.docker_communicate(
            sandbox.container, stdin=stdin, timeout=sandbox.realtime_limit)
    except TimeoutError:
        log.info("Sandbox realtime limit exceeded",
                 limit=sandbox.realtime_limit)
        result['timeout'] = True
    except (RequestException, DockerException, OSError) as e:
        log.exception("Sandbox runtime error")
        raise exceptions.DockerError(str(e))
    else:
        log.info("Sandbox container exited")
        state = utils.inspect_exited_container_state(sandbox.container)
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

    :param Sandbox sandbox: A sandbox to destroy.
    """
    try:
        sandbox.container.remove(v=True, force=True)
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

    :return dict: The same as for `start`.

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
        if self.node:
            return "<WorkingDirectory: {} node={}>".format(self.volume,
                                                           self.node)
        return "<WorkingDirectory: {}>".format(self.volume)


@contextmanager
def working_directory():
    docker_client = utils.get_docker_client()
    volume_name = 'epicbox-' + str(uuid.uuid4())
    log = logger.bind(volume=volume_name)
    log.info("Creating new docker volume for working directory")
    try:
        volume = docker_client.volumes.create(volume_name)
    except (RequestException, DockerException) as e:
        log.exception("Failed to create a docker volume")
        raise exceptions.DockerError(str(e))
    log.info("New docker volume is created")
    try:
        yield _WorkingDirectory(volume=volume_name, node=None)
    finally:  # Ensure that volume cleanup takes place
        log.info("Removing the docker volume")
        try:
            volume.remove()
        except NotFound:
            log.warning("Failed to remove the docker volume, it doesn't exist")
        except (RequestException, DockerException):
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
        docker_client.api.put_archive(container.id, config.DOCKER_WORKDIR,
                                      tarball_fileobj.getvalue())
    except (RequestException, DockerException) as e:
        log.exception("Failed to extract an archive of files to the working "
                      "directory in container")
        raise exceptions.DockerError(str(e))
    log.info("Successfully written files to the working directory",
             files_written=files_written)

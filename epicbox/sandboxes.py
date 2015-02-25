import os
import requests.exceptions
import subprocess
import tempfile
import uuid

import dateutil.parser
import structlog

from contextlib import contextmanager
from functools import partial

from docker.errors import APIError, DockerException

from . import config, utils


__all__ = ['run', 'working_directory']

logger = structlog.get_logger()


def run(profile_name, command=None, files=[], stdin=None, limits=None,
        workdir=None):
    if profile_name not in config.PROFILES:
        raise ValueError("Profile not found: {0}".format(profile_name))
    profile = config.PROFILES[profile_name]
    if stdin:
        if not isinstance(stdin, (bytes, str)):
            raise TypeError("stdin should be 'bytes' or 'str'")
        stdin_content = stdin if isinstance(stdin, bytes) else stdin.encode()
        if not files:
            files = []
        stdin_filename = '_sandbox_stdin'
        files.append({'name': stdin_filename, 'content': stdin_content})
        command = '< {0} {1}'.format(stdin_filename, command)
    limits = utils.merge_limits_defaults(limits)
    if limits['cputime']:
        command = 'ulimit -t {0}; {1}'.format(limits['cputime'], command)
    if limits['numprocs']:
        command = 'ulimit -u {0}; {1}'.format(limits['numprocs'], command)
    command_list = ['/bin/sh', '-c', command]

    start_sandbox = partial(_start_sandbox, profile.docker_image, command_list,
                            files=files, limits=limits, user=profile.user)
    if files:
        if workdir:
            _write_files(files, workdir)
            return start_sandbox(workdir=workdir)
        with working_directory() as workdir:
            _write_files(files, workdir)
            return start_sandbox(workdir=workdir)
    return start_sandbox()


@contextmanager
def working_directory():
    with tempfile.TemporaryDirectory(prefix='sandbox-',
                                     dir=config.BASE_WORKDIR) as sandbox_dir:
        log = logger.bind(workdir=sandbox_dir)
        log.info("New working directory is created")
        os.chmod(sandbox_dir, 0o777)
        if config.SELINUX_ENFORCED:
            p = subprocess.Popen(
                ['chcon', '-t', 'svirt_sandbox_file_t', sandbox_dir],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if p.wait():
                log.error("Failed to change the SELinux security context of "
                          "the working directory", error=p.stdout.read().decode())
        yield sandbox_dir


def _write_files(files, workdir):
    log = logger.bind(files=utils.filter_filenames(files), workdir=workdir)
    log.info("Writing files to the working directory")

    files_written = []
    for file in files:
        filename = file.get('name')
        if not filename or not isinstance(filename, str):
            continue
        filepath = os.path.join(workdir, filename)
        content = file.get('content', b'')
        with open(filepath, 'wb') as fd:
            fd.write(content)
        files_written.append(filename)

    log.info("Successfully written files to the working directory",
             files_written=files_written)


def _get_container_output(container):
    docker_client = utils.get_docker_client()
    try:
        # TODO: handle very long output, currently it blocks the process
        stdout = docker_client.logs(
            container, stdout=True, stderr=False, stream=False)
        stderr = docker_client.logs(
            container, stdout=False, stderr=True, stream=False)
    except (IOError, DockerException):
        logger.exception("Failed to get stdout/stderr of the container",
                         container=container)
        return b'', b''
    return stdout, stderr


def _inspect_container_state(container):
    docker_client = utils.get_docker_client()
    try:
        container_info = docker_client.inspect_container(container)
    except (IOError, DockerException):
        logger.exception("Failed to inspect the container",
                         container=container)
        return -1
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


def _start_sandbox(image, command, files=[], limits=None, workdir=None,
                   user=None):
    # TODO: clean up a sandbox in case of errors (fallback/periodic task)
    sandbox_id = str(uuid.uuid4())
    name = 'sandbox-' + sandbox_id
    mem_limit = str(limits['memory']) + 'm'

    log = logger.bind(sandbox_id=sandbox_id)
    log.info("Starting new sandbox", image=image, command=command,
             files=utils.filter_filenames(files), limits=limits,
             workdir=workdir, name=name, user=user)
    docker_client = utils.get_docker_client()
    try:
        c = docker_client.create_container(image,
                                           command=command,
                                           user=user,
                                           mem_limit=mem_limit,
                                           name=name,
                                           working_dir='/sandbox')
    except (IOError, APIError, DockerException):
        log.exception("Failed to create a sandbox container")
        raise
    log = log.bind(container=c)
    log.info("Sandbox container created")

    binds = {
        workdir: {
            'bind': '/sandbox',
            'ro': False,
        }
    } if workdir else None
    try:
        docker_client.start(c, binds=binds)
    except (IOError, APIError, DockerException):
        log.exception("Failed to start the sandbox container")
        raise
    log.info("Sandbox started")

    log.info("Waiting until the sandbox container exits")
    timeout = False
    exit_code = None
    try:
        exit_code = docker_client.wait(c, timeout=limits['realtime'])
        log.info("Sandbox container exited", exit_code=exit_code)
    except requests.exceptions.ReadTimeout:
        log.info("Sandbox realtime limit exceeded",
                 realtime=limits['realtime'])
        timeout = True
    except:
        log.exception("Sandbox runtime error")
        raise

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
        if exit_code == 137 and not state['oom_killed']:
            # SIGKILL is sent but not by out of memory killer
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
    except (IOError, APIError, DockerException):
        # TODO: handle 500 Driver aufs failed to remove root filesystem
        logger.exception("Failed to remove the sandbox container",
                         container=container)

import time
import uuid

import docker.errors
import pytest

from unittest.mock import ANY

from epicbox import config, utils
from epicbox.exceptions import DockerError
from epicbox.sandboxes import run, working_directory, \
                              _start_sandbox, _write_files


def test_run_python(profile):
    command = 'python3 -c \'import sys; ' \
              'print("stdout data"); print("stderr data", file=sys.stderr)\''
    result = run(profile.name, command)

    expected_result = {
        'exit_code': 0,
        'stdout': b'stdout data\n',
        'stderr': b'stderr data\n',
        'duration': ANY,
        'timeout': False,
        'oom_killed': False,
    }
    assert result == expected_result
    assert result['duration'] > 0


def test_run_unknown_profile():
    with pytest.raises(ValueError):
        run('unknown', 'true')


def test_run_invalid_workdir(profile):
    with pytest.raises(ValueError) as excinfo:
        run(profile.name, 'true', workdir='dir')

    assert "working_directory" in str(excinfo.value)


def test_run_non_zero_exit(profile):
    result = run(profile.name, 'false')

    assert result['exit_code'] == 1


def test_run_profile_command(profile):
    result = run(profile.name)

    assert result['exit_code'] == 0
    assert result['stdout'] == b'profile stdout\n'


def test_run_real_timeout(profile):
    result = run(profile.name, 'sleep 100', limits={'realtime': 1})

    assert result['timeout'] is True
    assert result['exit_code'] is None


def test_run_cpu_timeout(profile):
    start_time = time.time()

    result = run(profile.name, 'cat /dev/urandom > /dev/null',
                 limits={'cputime': 1, 'realtime': 10})

    assert result['timeout'] is True
    assert result['exit_code'] is not None
    realtime_duration = time.time() - start_time
    assert realtime_duration < 10


def test_run_memory_limit(profile):
    result = run(profile.name, 'python3 -c "[1] * 10 ** 8"',
                 limits={'cputime': 10, 'memory': 8})

    assert result['oom_killed'] is True
    assert result['timeout'] is False
    assert result['exit_code'] not in [None, 0]


def test_run_file_size_limit(profile):
    limits = {'file_size': 10}

    result = run(profile.name, 'echo -n "0123456789" > file', limits=limits)

    assert result['exit_code'] == 0

    result = run(profile.name, 'echo -n "01234567890" > file', limits=limits)

    assert result['exit_code'] == 1
    assert b'write error: File too large' in result['stderr']


def test_run_read_only_file_system(profile_read_only):
    result = run(profile_read_only.name, 'touch /tmp/file')

    assert result['exit_code'] not in [None, 0]
    assert b'Read-only file system' in result['stderr']


@pytest.mark.skipif('True')
def test_run_fork_limit(profile):
    result = run(profile.name, 'ls &', limits={'cputime': 30})

    assert result['exit_code']
    assert b'fork: retry: No child processes' in result['stderr']


def test_run_network_disabled(profile):
    result = run(profile.name, 'curl -I https://google.com')

    assert result['exit_code']
    assert b'Could not resolve host' in result['stderr']


def test_run_network_enabled(profile):
    profile.network_disabled = False

    result = run(profile.name, 'curl -I https://google.com')

    assert result['exit_code'] == 0
    assert b'302 Found' in result['stdout']


def test_run_upload_files(profile):
    files = [
        {'name': 'main.py', 'content': b'print(open("file.txt").read())'},
        {'name': 'file.txt', 'content': b'Data in file.txt'},
    ]

    result = run(profile.name, 'python3 main.py', files=files)

    assert result['exit_code'] == 0
    assert result['stdout'] == b'Data in file.txt\n'


def test_run_read_stdin(profile):
    result = run(profile.name, 'cat', stdin=b'binary data\n')

    assert result['exit_code'] == 0
    assert result['stdout'] == b'binary data\n'

    result = run(profile.name, 'cat', stdin='utf8 данные\n')

    assert result['exit_code'] == 0
    assert result['stdout'] == 'utf8 данные\n'.encode()


def test_run_reuse_workdir(profile, docker_client):
    with working_directory() as workdir:
        assert workdir.node is None

        run(profile.name, 'true',
            files=[{'name': 'file', 'content': b'first run data\n'}],
            workdir=workdir)

        if utils.is_docker_swarm(docker_client):
            assert workdir.node

        result = run(profile.name, 'cat file', workdir=workdir)

        assert result['exit_code'] == 0
        assert result['stdout'] == b'first run data\n'


def test_start_sandbox_apierror_no_such_image():
    with pytest.raises(DockerError) as excinfo:
        _start_sandbox('unknown_image', 'true', {'cputime': 1, 'memory': 64},
                       '/tmp')

    error = str(excinfo.value)
    # Error message depends on whether Docker Swarm is used
    assert any(("No such image" in error, "not found" in error))


def test_working_directory(docker_client):
    with working_directory() as workdir:
        assert workdir.volume.startswith('epicbox-')
        node_volume = workdir.volume
        if utils.is_docker_swarm(docker_client):
            node_name = utils.get_swarm_nodes(docker_client)[0]
            node_volume = node_name + '/' + workdir.volume
        volume = docker_client.inspect_volume(node_volume)
        assert volume['Name'] == workdir.volume

    with pytest.raises(docker.errors.NotFound):
        docker_client.inspect_volume(node_volume)


def test_write_files(docker_client, docker_image):
    command = ('/bin/bash -c '
               '"stat -c %a /sandbox && ls -1 /sandbox && cat /sandbox/*"')
    name = 'epicbox-test-' + str(uuid.uuid4())
    working_dir = config.DOCKER_WORKDIR
    container = docker_client.create_container(docker_image,
                                               command=command,
                                               name=name,
                                               working_dir=working_dir)
    files = [
        {'name': 'main.py', 'content': b'main.py content'},
        {'name': 'file.txt', 'content': b'file.txt content'},
    ]

    try:
        _write_files(container, files)

        docker_client.start(container)
        docker_client.wait(container, timeout=5)
        stdout = docker_client.logs(
            container, stdout=True, stderr=False, stream=False)
        assert stdout == (b'755\n'
                          b'file.txt\n'
                          b'main.py\n'
                          b'file.txt contentmain.py content')
    finally:
        docker_client.remove_container(container, v=True, force=True)

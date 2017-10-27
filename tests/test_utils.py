import uuid

import pytest

from epicbox.utils import docker_communicate, docker_logs


def test_docker_logs(docker_client, docker_image):
    command = '/bin/sh -c "echo 42"'
    name = 'epicbox-test-' + str(uuid.uuid4())
    container = docker_client.create_container(docker_image,
                                               command=command,
                                               name=name)
    try:
        docker_client.start(container)
        docker_client.wait(container, timeout=5)

        stdout = docker_logs(container, stdout=True)

        assert stdout == b'42\n'

        # By container name
        stdout = docker_logs(name, stdout=True)

        assert stdout == b'42\n'
    finally:
        docker_client.remove_container(container, v=True, force=True)


def test_docker_communicate_empty_input_empty_output(test_utils,
                                                     docker_client):
    container = test_utils.create_test_container(command='true')

    stdout, stderr = docker_communicate(container)

    assert stdout == b''
    assert stderr == b''


def test_docker_communicate_only_output(test_utils, docker_client):
    container = test_utils.create_test_container(command='echo 42')

    stdout, stderr = docker_communicate(container)

    assert stdout == b'42\n'
    assert stderr == b''


def test_docker_communicate_split_output_streams(test_utils, docker_client):
    container = test_utils.create_test_container(
        command='/bin/sh -c "cat && echo error >&2"')

    stdout, stderr = docker_communicate(container, stdin=b'42\n')

    assert stdout == b'42\n'
    assert stderr == b'error\n'


def test_docker_communicate_copy_input_to_output(test_utils, docker_client):
    stdin_options = [
        b'\n\n\r\n',
        b'Hello!',
        b'Hello!\n',
        b'0123456789' * 100000,
    ]
    for stdin in stdin_options:
        container = test_utils.create_test_container(command='cat')

        stdout, stderr = docker_communicate(container, stdin=stdin)

        assert stdout == stdin
        assert stderr == b''


def test_docker_communicate_timeout_reached(test_utils, docker_client):
    container = test_utils.create_test_container(
        command='/bin/sh -c "echo 42 && sleep 30"')

    with pytest.raises(TimeoutError):
        docker_communicate(container, timeout=1)

    container_info = docker_client.inspect_container(container)
    assert container_info['State']['Running']

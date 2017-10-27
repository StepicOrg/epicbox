import pytest

from epicbox.utils import docker_communicate


def test_docker_communicate_empty_input_empty_output(test_utils):
    container = test_utils.create_test_container(command='true')

    stdout, stderr = docker_communicate(container)

    assert stdout == b''
    assert stderr == b''


def test_docker_communicate_only_output(test_utils):
    container = test_utils.create_test_container(command='echo 42')

    stdout, stderr = docker_communicate(container)

    assert stdout == b'42\n'
    assert stderr == b''


def test_docker_communicate_split_output_streams(test_utils):
    container = test_utils.create_test_container(
        command='/bin/sh -c "cat && echo error >&2"')

    stdout, stderr = docker_communicate(container, stdin=b'42\n')

    assert stdout == b'42\n'
    assert stderr == b'error\n'


def test_docker_communicate_copy_input_to_output(test_utils):
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


def test_docker_communicate_failed_command(test_utils):
    container = test_utils.create_test_container(command='sleep')

    stdout, stderr = docker_communicate(container)

    assert stdout == b''
    assert b'missing operand' in stderr


def test_docker_communicate_timeout_reached(test_utils, docker_client):
    container = test_utils.create_test_container(
        command='/bin/sh -c "echo 42 && sleep 30"')

    with pytest.raises(TimeoutError):
        docker_communicate(container, timeout=1)

    container_info = docker_client.inspect_container(container)
    assert container_info['State']['Running']

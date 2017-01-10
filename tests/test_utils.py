import uuid

from epicbox.utils import docker_logs


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

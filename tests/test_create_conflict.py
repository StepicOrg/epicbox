import unittest
from unittest.mock import MagicMock, patch

from docker.errors import APIError, NotFound
from requests import Response
from requests.exceptions import ConnectionError

from epicbox import config, exceptions, sandboxes


class CreateConflictTests(unittest.TestCase):
    def setUp(self):
        prefix = patch.object(sandboxes, "_SANDBOX_NAME_PREFIX", "epicbox-")
        prefix.start()
        self.addCleanup(prefix.stop)
        self.client = MagicMock()
        self.container = self.client.containers.get.return_value
        response = Response()
        response.status_code = 409
        self.client.containers.create.side_effect = APIError(
            'Name conflict', response=response)
        patcher = patch.object(sandboxes.utils, 'get_docker_client',
                               return_value=self.client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def create(self):
        return sandboxes._create_sandbox_container(
            'test-conflict', 'image', ['true'], dict(config.DEFAULT_LIMITS))

    def test_conflict_returns_existing_container(self):
        result = self.create()
        self.assertIs(result, self.container)
        self.client.containers.get.assert_called_once_with(
            'epicbox-test-conflict')
        result.remove(v=True, force=True)
        self.container.remove.assert_called_once_with(v=True, force=True)

    def test_missing_conflicting_container_raises_docker_error(self):
        self.client.containers.get.side_effect = NotFound('Container vanished')
        with self.assertRaises(exceptions.DockerError):
            self.create()

    def test_failed_conflict_lookup_raises_docker_error(self):
        self.client.containers.get.side_effect = ConnectionError('Disconnected')
        with self.assertRaises(exceptions.DockerError):
            self.create()

    def test_other_api_error_does_not_attempt_lookup(self):
        response = Response()
        response.status_code = 500
        self.client.containers.create.side_effect = APIError(
            'Server error', response=response)
        with self.assertRaises(exceptions.DockerError):
            self.create()
        self.assertFalse(self.client.containers.get.called)


def test_conflict_recovers_real_container(docker_client, docker_image):
    import uuid
    sandbox_id = str(uuid.uuid4())
    limits = dict(config.DEFAULT_LIMITS)
    first = sandboxes._create_sandbox_container(
        sandbox_id, docker_image, ['true'], limits)
    try:
        recovered = sandboxes._create_sandbox_container(
            sandbox_id, docker_image, ['true'], limits)
        assert recovered.id == first.id
        assert recovered.attrs['Name'] == first.attrs['Name']
        recovered.start()
        assert recovered.wait()['StatusCode'] == 0
    finally:
        first.remove(v=True, force=True)

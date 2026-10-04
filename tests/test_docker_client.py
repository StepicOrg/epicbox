import unittest
from unittest.mock import MagicMock, patch

from epicbox import config, utils


class DockerClientCacheTests(unittest.TestCase):
    def setUp(self):
        patches = [
            patch.object(utils, '_DOCKER_CLIENTS', {}),
            patch.object(config, 'DOCKER_URL', None),
            patch.object(config, 'IS_CONFIGURED', False),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        client_patch = patch.object(utils.docker, 'DockerClient',
                                   side_effect=lambda **kwargs: MagicMock())
        self.create_client = client_patch.start()
        self.addCleanup(client_patch.stop)

    def test_different_endpoints_create_different_clients(self):
        first = utils.get_docker_client(base_url='tcp://first:2375')
        second = utils.get_docker_client(base_url='tcp://second:2375')

        self.assertIsNot(first, second)
        self.assertEqual(self.create_client.call_count, 2)
        self.assertEqual(self.create_client.call_args_list[0][1]['base_url'],
                         'tcp://first:2375')
        self.assertEqual(self.create_client.call_args_list[1][1]['base_url'],
                         'tcp://second:2375')

    def test_same_endpoint_reuses_client(self):
        first = utils.get_docker_client(base_url='tcp://first:2375')
        second = utils.get_docker_client(base_url='tcp://first:2375')

        self.assertIs(first, second)
        self.assertEqual(self.create_client.call_count, 1)

    def test_reconfigure_endpoint_creates_new_client(self):
        config.configure(docker_url='tcp://first:2375')
        first = utils.get_docker_client()
        config.configure(docker_url='tcp://second:2375')
        second = utils.get_docker_client()

        self.assertIsNot(first, second)
        self.assertEqual(self.create_client.call_count, 2)
        self.assertEqual(self.create_client.call_args[1]['base_url'],
                         'tcp://second:2375')

    def test_explicit_and_configured_endpoint_share_client(self):
        config.configure(docker_url='tcp://first:2375')
        first = utils.get_docker_client()
        second = utils.get_docker_client(base_url='tcp://first:2375')

        self.assertIs(first, second)
        self.assertEqual(self.create_client.call_count, 1)

    def test_different_retry_settings_create_different_clients(self):
        first = utils.get_docker_client(base_url='tcp://first:2375')
        second = utils.get_docker_client(base_url='tcp://first:2375',
                                        retry_read=0)
        third = utils.get_docker_client(base_url='tcp://first:2375',
                                       retry_status_forcelist=(404, 500))

        self.assertIsNot(first, second)
        self.assertIsNot(first, third)
        self.assertIsNot(second, third)
        self.assertEqual(self.create_client.call_count, 3)

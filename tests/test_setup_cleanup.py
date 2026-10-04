import unittest
from unittest.mock import MagicMock, patch

from docker.errors import DockerException

from epicbox import config, exceptions, sandboxes


class SetupCleanupTests(unittest.TestCase):
    def setUp(self):
        self.container = MagicMock()
        patcher = patch.object(sandboxes, '_create_sandbox_container',
                               return_value=self.container)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(sandboxes.utils, 'get_docker_client',
                               return_value=MagicMock())
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.dict(config.PROFILES,
                             {'test': config.Profile('test', 'image')})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_archive_upload_failure_removes_container(self):
        error = exceptions.DockerError('Archive upload failed')
        with patch.object(sandboxes, '_write_files', side_effect=error):
            with self.assertRaises(exceptions.DockerError) as raised:
                sandboxes.create('test', files=[{'name': 'input'}])
        self.assertIs(raised.exception, error)
        self.container.remove.assert_called_once_with(v=True, force=True)

    def test_invalid_file_content_removes_container(self):
        with self.assertRaises(TypeError):
            sandboxes.create('test', files=[{'name': 'input', 'content': 'text'}])
        self.container.remove.assert_called_once_with(v=True, force=True)

    def test_node_inspection_failure_removes_container(self):
        error = exceptions.DockerError('Node inspection failed')
        workdir = sandboxes._WorkingDirectory('volume')
        with patch.object(sandboxes.utils, 'inspect_container_node',
                          side_effect=error):
            with self.assertRaises(exceptions.DockerError) as raised:
                sandboxes.create('test', workdir=workdir)
        self.assertIs(raised.exception, error)
        self.container.remove.assert_called_once_with(v=True, force=True)

    def test_cleanup_failure_preserves_original_error(self):
        error = exceptions.DockerError('Archive upload failed')
        self.container.remove.side_effect = DockerException('Remove failed')
        with patch.object(sandboxes, '_write_files', side_effect=error):
            with self.assertRaises(exceptions.DockerError) as raised:
                sandboxes.create('test', files=[{'name': 'input'}])
        self.assertIs(raised.exception, error)
        self.container.remove.assert_called_once_with(v=True, force=True)

    def test_success_keeps_container_until_destroy(self):
        sandbox = sandboxes.create('test')
        self.assertIs(sandbox.container, self.container)
        self.assertFalse(self.container.remove.called)
        sandboxes.destroy(sandbox)
        self.container.remove.assert_called_once_with(v=True, force=True)


def test_invalid_file_content_leaves_no_real_container(docker_client, profile):
    import pytest
    before = {c.id for c in docker_client.containers.list(all=True)}
    with pytest.raises(TypeError):
        sandboxes.create(profile.name,
                         files=[{'name': 'input', 'content': 'text'}])
    after = {c.id for c in docker_client.containers.list(all=True)}
    assert after == before


def test_failed_archive_upload_leaves_no_real_container(docker_client, profile):
    import pytest
    from requests.exceptions import ConnectionError
    before = {c.id for c in docker_client.containers.list(all=True)}
    # The upload client is cached with retry settings distinct from creation.
    client = sandboxes.utils.get_docker_client(
        retry_status_forcelist=(404, 500))
    with patch.object(client.api, 'put_archive',
                      side_effect=ConnectionError('Upload failed')):
        with pytest.raises(exceptions.DockerError):
            sandboxes.create(profile.name,
                             files=[{'name': 'input', 'content': b'data'}])
    after = {c.id for c in docker_client.containers.list(all=True)}
    assert after == before


def test_failed_node_inspection_leaves_no_real_container(docker_client, profile):
    import pytest
    before = {c.id for c in docker_client.containers.list(all=True)}
    with sandboxes.working_directory() as workdir:
        with patch.object(sandboxes.utils, 'inspect_container_node',
                          side_effect=exceptions.DockerError('Inspect failed')):
            with pytest.raises(exceptions.DockerError):
                sandboxes.create(profile.name, workdir=workdir)
    after = {c.id for c in docker_client.containers.list(all=True)}
    assert after == before

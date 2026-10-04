import struct
import unittest
from unittest.mock import MagicMock, patch

from epicbox import utils


class OutputBufferTests(unittest.TestCase):
    def communicate(self, chunks):
        sock = MagicMock()
        client = MagicMock()
        client.api.attach_socket.return_value = sock
        with patch.object(utils, 'get_docker_client', return_value=client), \
                patch.object(utils.select, 'select',
                             return_value=([sock], [], [])), \
                patch.object(utils, '_socket_read', side_effect=chunks + [None]):
            result = utils.docker_communicate(MagicMock())
        sock.close.assert_called_once_with()
        self.assertIsInstance(result[0], bytes)
        self.assertIsInstance(result[1], bytes)
        return result

    def test_fragmented_interleaved_output(self):
        frames = [(1, b'out'), (2, b'error'), (1, b'put')]
        raw = b''.join(struct.pack('>BxxxL', stream, len(data)) + data
                       for stream, data in frames)
        self.assertEqual(self.communicate([raw[i:i + 3]
                                          for i in range(0, len(raw), 3)]),
                         (b'output', b'error'))

    def test_large_output_is_complete(self):
        stdout = b'x' * (4 * 1024 * 1024)
        stderr = b'y' * 8192
        raw = (struct.pack('>BxxxL', 1, len(stdout)) + stdout +
               struct.pack('>BxxxL', 2, len(stderr)) + stderr)
        chunks = [raw[i:i + 4096] for i in range(0, len(raw), 4096)]
        self.assertEqual(self.communicate(chunks), (stdout, stderr))

    def test_empty_output(self):
        self.assertEqual(self.communicate([]), (b'', b''))


def test_large_stdout_and_stderr_from_real_container(profile):
    from epicbox import sandboxes
    command = ("python3 -c 'import sys; "
               'sys.stdout.buffer.write(b"x" * (4 * 1024 * 1024)); '
               'sys.stderr.buffer.write(b"y" * 8192)\'')
    result = sandboxes.run(profile.name, command=command,
                          limits={'memory': 256, 'cputime': 10, 'realtime': 30})
    assert result['exit_code'] == 0
    assert result['stdout'] == b'x' * (4 * 1024 * 1024)
    assert result['stderr'] == b'y' * 8192
    assert isinstance(result['stdout'], bytes)
    assert isinstance(result['stderr'], bytes)

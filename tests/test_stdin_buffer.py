import socket
import unittest
from unittest.mock import MagicMock, patch

from epicbox import utils


class StdinBufferTests(unittest.TestCase):
    def test_partial_writes_reuse_original_input_buffer(self):
        stdin = b'abcde'
        sock = MagicMock()
        client = MagicMock()
        client.api.attach_socket.return_value = sock
        counts = iter([0, 2, 3])
        pending = []

        def write(sock, data):
            self.assertIsInstance(data, memoryview)
            self.assertIs(data.obj, stdin)
            pending.append(bytes(data))
            return next(counts)

        with patch.object(utils, 'get_docker_client', return_value=client), \
                patch.object(utils.select, 'select', side_effect=[
                    ([], [sock], []), ([], [sock], []),
                    ([], [sock], []), ([sock], [], [])]), \
                patch.object(utils, '_socket_write', side_effect=write), \
                patch.object(utils, '_socket_read', return_value=None):
            result = utils.docker_communicate(MagicMock(), stdin=stdin)
        self.assertEqual(result, (b'', b''))
        self.assertEqual(pending, [b'abcde', b'abcde', b'cde'])
        sock._sock.shutdown.assert_called_once_with(socket.SHUT_WR)

    def test_socket_write_accepts_input_view(self):
        sender, receiver = socket.socketpair()
        try:
            data = memoryview(b'prefix payload')[7:]
            self.assertEqual(utils._socket_write(sender, data), len(data))
            self.assertEqual(receiver.recv(1024), b'payload')
        finally:
            sender.close()
            receiver.close()

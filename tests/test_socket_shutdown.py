import errno
import socket
import struct
import unittest
from unittest.mock import MagicMock, patch

from docker.errors import DockerException

from epicbox import utils


class DockerCommunicateShutdownTests(unittest.TestCase):
    def setUp(self):
        self.container = MagicMock()
        self.sock = MagicMock()
        self.client = MagicMock()
        self.client.api.attach_socket.return_value = self.sock
        frame = (struct.pack('>BxxxL', 1, 4) + b'out\n' +
                 struct.pack('>BxxxL', 2, 4) + b'err\n')
        patches = {
            'get_docker_client': {'return_value': self.client},
            '_socket_read': {'side_effect': [frame, None]},
            '_socket_write': {'side_effect': lambda sock, data: len(data)},
        }
        for name, kwargs in patches.items():
            patcher = patch.object(utils, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        select_patch = patch.object(utils.select, 'select',
                                    return_value=([self.sock], [self.sock], []))
        select_patch.start()
        self.addCleanup(select_patch.stop)

    def test_disconnected_shutdown_after_input_preserves_output(self):
        self.sock._sock.shutdown.side_effect = OSError(errno.ENOTCONN,
                                                       'Not connected')

        result = utils.docker_communicate(self.container, stdin=b'input')

        self.assertEqual(result, (b'out\n', b'err\n'))
        self.sock._sock.shutdown.assert_called_once_with(socket.SHUT_WR)
        self.sock.close.assert_called_once_with()

    def test_disconnected_shutdown_without_input_preserves_output(self):
        self.sock._sock.shutdown.side_effect = OSError(errno.ENOTCONN,
                                                       'Not connected')

        result = utils.docker_communicate(self.container)

        self.assertEqual(result, (b'out\n', b'err\n'))
        self.sock.close.assert_called_once_with()

    def test_unexpected_shutdown_error_is_raised_and_socket_closed(self):
        error = OSError(errno.EBADF, 'Bad file descriptor')
        self.sock._sock.shutdown.side_effect = error

        with self.assertRaises(OSError) as raised:
            utils.docker_communicate(self.container, stdin=b'input')

        self.assertIs(raised.exception, error)
        self.sock.close.assert_called_once_with()

    def test_start_error_closes_socket(self):
        self.container.start.side_effect = DockerException('Start failed')

        with self.assertRaises(DockerException):
            utils.docker_communicate(self.container)

        self.sock.close.assert_called_once_with()

    def test_read_error_closes_socket(self):
        with patch.object(utils, '_socket_read',
                          side_effect=OSError(errno.EIO, 'Read failed')):
            with self.assertRaises(OSError):
                utils.docker_communicate(self.container)

        self.sock.close.assert_called_once_with()

    def test_socket_setup_error_closes_socket(self):
        self.sock._sock.setblocking.side_effect = OSError(errno.EBADF,
                                                          'Bad descriptor')

        with self.assertRaises(OSError):
            utils.docker_communicate(self.container)

        self.sock.close.assert_called_once_with()

    def test_timeout_closes_socket_once(self):
        with self.assertRaises(TimeoutError):
            utils.docker_communicate(self.container, timeout=0)

        self.sock.close.assert_called_once_with()

    def test_success_closes_socket_once(self):
        result = utils.docker_communicate(self.container, stdin=b'input')

        self.assertEqual(result, (b'out\n', b'err\n'))
        self.sock.close.assert_called_once_with()

    def test_close_error_preserves_start_error(self):
        error = DockerException('Start failed')
        self.container.start.side_effect = error
        self.sock.close.side_effect = OSError(errno.EIO, 'Close failed')

        with self.assertRaises(DockerException) as raised:
            utils.docker_communicate(self.container)

        self.assertIs(raised.exception, error)
        self.sock.close.assert_called_once_with()


class DisconnectedSocketTests(unittest.TestCase):
    def test_closed_peer_preserves_buffered_docker_output(self):
        connection, peer = socket.socketpair()
        self.addCleanup(connection.close)
        self.addCleanup(peer.close)
        stream = socket.SocketIO(connection, 'rw')
        self.addCleanup(stream.close)
        frame = (struct.pack('>BxxxL', 1, 4) + b'out\n' +
                 struct.pack('>BxxxL', 2, 4) + b'err\n')
        peer.sendall(frame)
        peer.close()
        client = MagicMock()
        client.api.attach_socket.return_value = stream

        with patch.object(utils, 'get_docker_client', return_value=client):
            result = utils.docker_communicate(MagicMock(),
                                              start_container=False,
                                              timeout=1)

        self.assertEqual(result, (b'out\n', b'err\n'))
        self.assertTrue(stream.closed)

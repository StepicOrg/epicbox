import http.client
import socket
import struct
import unittest
from unittest.mock import MagicMock, patch

import requests
import urllib3

from epicbox import utils


class DockerResponseCleanupTests(unittest.TestCase):
    def setUp(self):
        connection, self.peer = socket.socketpair()
        self.addCleanup(connection.close)
        self.addCleanup(self.peer.close)
        self.http_response = http.client.HTTPResponse(connection)
        self.sock = self.http_response.fp.raw
        self.response = requests.Response()
        self.response.raw = urllib3.response.HTTPResponse(
            body=self.http_response, original_response=self.http_response,
            preload_content=False)
        self.sock._response = self.response
        self.addCleanup(self.cleanup_response)
        client = MagicMock()
        client.api.attach_socket.return_value = self.sock
        frame = struct.pack('>BxxxL', 1, 4) + b'out\n'
        patches = {
            'get_docker_client': {'return_value': client},
            '_socket_read': {'side_effect': [frame, None]},
        }
        for name, kwargs in patches.items():
            patcher = patch.object(utils, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        select_patch = patch.object(utils.select, 'select',
                                    return_value=([self.sock], [], []))
        select_patch.start()
        self.addCleanup(select_patch.stop)

    def cleanup_response(self):
        # Repair the baseline's already-closed buffer before final cleanup.
        if self.http_response.fp and self.http_response.fp.closed:
            self.http_response._close_conn()
        self.response.close()

    def test_success_closes_http_response_before_socket(self):
        result = utils.docker_communicate(MagicMock())

        self.assertEqual(result, (b'out\n', b''))
        self.assertTrue(self.sock.closed)
        self.assertTrue(self.http_response.closed)
        self.response.close()

    def test_timeout_closes_http_response_before_socket(self):
        with self.assertRaises(TimeoutError):
            utils.docker_communicate(MagicMock(), timeout=0)

        self.assertTrue(self.sock.closed)
        self.assertTrue(self.http_response.closed)
        self.response.close()

    def test_response_close_error_still_closes_socket(self):
        error = RuntimeError('Response close failed')
        with patch.object(self.response, 'close', side_effect=error):
            with self.assertRaises(RuntimeError) as raised:
                utils.docker_communicate(MagicMock())

        self.assertIs(raised.exception, error)
        self.assertTrue(self.sock.closed)

    def test_socket_without_retained_response_still_closes(self):
        del self.sock._response

        result = utils.docker_communicate(MagicMock())

        self.assertEqual(result, (b'out\n', b''))
        self.assertTrue(self.sock.closed)

    def test_start_error_closes_response(self):
        error = RuntimeError('Start failed')
        container = MagicMock()
        container.start.side_effect = error

        with self.assertRaises(RuntimeError) as raised:
            utils.docker_communicate(container)

        self.assertIs(raised.exception, error)
        self.assertTrue(self.http_response.closed)
        self.assertTrue(self.sock.closed)

    def test_read_error_closes_response(self):
        error = OSError('Read failed')
        with patch.object(utils, '_socket_read', side_effect=error):
            with self.assertRaises(OSError) as raised:
                utils.docker_communicate(MagicMock())

        self.assertIs(raised.exception, error)
        self.assertTrue(self.http_response.closed)
        self.assertTrue(self.sock.closed)

    def test_select_error_closes_response(self):
        error = OSError('Select failed')
        with patch.object(utils.select, 'select', side_effect=error):
            with self.assertRaises(OSError) as raised:
                utils.docker_communicate(MagicMock())

        self.assertIs(raised.exception, error)
        self.assertTrue(self.http_response.closed)
        self.assertTrue(self.sock.closed)

    def test_cleanup_error_preserves_start_error(self):
        error = RuntimeError('Start failed')
        container = MagicMock()
        container.start.side_effect = error
        with patch.object(self.response, 'close',
                          side_effect=ValueError('Response close failed')):
            with self.assertRaises(RuntimeError) as raised:
                utils.docker_communicate(container)

        self.assertIs(raised.exception, error)
        self.assertTrue(self.sock.closed)

    def test_socket_setup_error_closes_response(self):
        error = OSError('Setup failed')
        with patch.object(socket.socket, 'setblocking', side_effect=error):
            with self.assertRaises(OSError) as raised:
                utils.docker_communicate(MagicMock())
        self.assertIs(raised.exception, error)
        self.assertTrue(self.http_response.closed)
        self.assertTrue(self.sock.closed)

    def test_write_error_closes_response(self):
        error = OSError('Write failed')
        with patch.object(utils.select, 'select',
                          return_value=([self.sock], [self.sock], [])), \
                patch.object(utils, '_socket_write', side_effect=error):
            with self.assertRaises(OSError) as raised:
                utils.docker_communicate(MagicMock(), stdin=b'input')
        self.assertIs(raised.exception, error)
        self.assertTrue(self.http_response.closed)
        self.assertTrue(self.sock.closed)

    def test_broken_pipe_preserves_output_and_closes_response(self):
        with patch.object(utils.select, 'select',
                          return_value=([self.sock], [self.sock], [])), \
                patch.object(utils, '_socket_write', side_effect=BrokenPipeError):
            result = utils.docker_communicate(MagicMock(), stdin=b'input')
        self.assertEqual(result, (b'out\n', b''))
        self.assertTrue(self.http_response.closed)
        self.assertTrue(self.sock.closed)

    def test_connection_reset_preserves_output_and_closes_response(self):
        frame = struct.pack('>BxxxL', 1, 4) + b'out\n'
        with patch.object(utils, '_socket_read',
                          side_effect=[frame, ConnectionResetError]):
            result = utils.docker_communicate(MagicMock())
        self.assertEqual(result, (b'out\n', b''))
        self.assertTrue(self.http_response.closed)
        self.assertTrue(self.sock.closed)

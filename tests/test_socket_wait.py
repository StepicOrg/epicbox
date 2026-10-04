import unittest
from unittest.mock import MagicMock, patch

import pytest

from epicbox import sandboxes
from epicbox import utils


class SocketWaitTests(unittest.TestCase):
    def test_closed_stdin_waits_for_output_without_sleeping(self):
        sock = MagicMock()
        client = MagicMock()
        client.api.attach_socket.return_value = sock

        with patch.object(utils, 'get_docker_client', return_value=client), \
                patch.object(utils.select, 'select', side_effect=[
                    ([], [], []), ([sock], [], [])]) as wait, \
                patch.object(utils, '_socket_read', return_value=None), \
                patch.object(utils.time, 'sleep') as sleep:
            self.assertEqual(utils.docker_communicate(MagicMock()), (b'', b''))
        for call in wait.call_args_list:
            self.assertEqual(call[0][0], [sock])
            self.assertEqual(call[0][1], [])
        self.assertFalse(sleep.called)

    def test_write_interest_ends_when_input_is_sent(self):
        sock = MagicMock()
        client = MagicMock()
        client.api.attach_socket.return_value = sock
        with patch.object(utils, 'get_docker_client', return_value=client), \
                patch.object(utils.select, 'select', side_effect=[
                    ([], [sock], []), ([], [sock], []), ([sock], [], [])]) as wait, \
                patch.object(utils, '_socket_write', side_effect=[1, 1]), \
                patch.object(utils, '_socket_read', return_value=None):
            utils.docker_communicate(MagicMock(), stdin=b'ab')
        self.assertEqual([call[0][1] for call in wait.call_args_list],
                         [[sock], [sock], []])
        sock._sock.shutdown.assert_called_once_with(utils.socket.SHUT_WR)

    def test_select_wait_is_bounded_by_remaining_timeout(self):
        sock = MagicMock()
        client = MagicMock()
        client.api.attach_socket.return_value = sock
        with patch.object(utils, 'get_docker_client', return_value=client), \
                patch.object(utils.time, 'monotonic',
                             side_effect=[0, 0.75, 0.75, 1]), \
                patch.object(utils.select, 'select',
                             return_value=([], [], [])) as wait, \
                patch.object(utils.time, 'sleep') as sleep:
            with self.assertRaises(TimeoutError):
                utils.docker_communicate(MagicMock(), timeout=1)
        self.assertEqual(wait.call_args[0][3], 0.25)
        self.assertFalse(sleep.called)
        sock.close.assert_called_once_with()

    def test_missing_timeout_uses_finite_realtime_default(self):
        sock = MagicMock()
        client = MagicMock()
        client.api.attach_socket.return_value = sock
        with patch.object(utils, 'get_docker_client', return_value=client), \
                patch.dict(utils.config.DEFAULT_LIMITS, realtime=2), \
                patch.object(utils.time, 'monotonic',
                             side_effect=[0, 0, 0, 2]), \
                patch.object(utils.select, 'select',
                             side_effect=[([], [], []),
                                          AssertionError('Unbounded wait')]) as wait:
            with self.assertRaises(TimeoutError):
                utils.docker_communicate(MagicMock(), timeout=None)
        self.assertEqual(wait.call_count, 1)
        sock.close.assert_called_once_with()

    def test_continuous_output_cannot_extend_timeout(self):
        sock = MagicMock()
        client = MagicMock()
        client.api.attach_socket.return_value = sock
        with patch.object(utils, 'get_docker_client', return_value=client), \
                patch.object(utils.time, 'monotonic',
                             side_effect=[0, 0, 0, 1]), \
                patch.object(utils.select, 'select',
                             return_value=([sock], [], [])), \
                patch.object(utils, '_socket_read', return_value=b'output'):
            with self.assertRaises(TimeoutError):
                utils.docker_communicate(MagicMock(), timeout=1)
        sock.close.assert_called_once_with()

    def test_invalid_timeout_is_rejected_before_attaching(self):
        for timeout in [-1, float('inf'), float('-inf'), float('nan'), '5']:
            with patch.object(utils, 'get_docker_client') as create_client:
                with self.assertRaises(ValueError):
                    utils.docker_communicate(MagicMock(), timeout=timeout)
                self.assertFalse(create_client.called)

    def test_invalid_default_timeout_is_rejected_before_attaching(self):
        with patch.dict(utils.config.DEFAULT_LIMITS, realtime=None), \
                patch.object(utils, 'get_docker_client',
                             side_effect=AssertionError('Invalid timeout accepted')) as create_client:
            with self.assertRaises(ValueError):
                utils.docker_communicate(MagicMock())
        self.assertFalse(create_client.called)

    def test_start_delay_consumes_realtime_budget(self):
        sock = MagicMock()
        client = MagicMock()
        client.api.attach_socket.return_value = sock
        clock = [0]
        container = MagicMock()

        def start():
            clock[0] += 2

        container.start.side_effect = start
        with patch.object(utils, 'get_docker_client', return_value=client), \
                patch.object(utils.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(utils.select, 'select',
                             return_value=([sock], [], [])), \
                patch.object(utils, '_socket_read', return_value=None):
            with self.assertRaises(TimeoutError):
                utils.docker_communicate(container, timeout=1)
        sock.close.assert_called_once_with()

    def test_late_eof_cannot_report_success(self):
        sock = MagicMock()
        client = MagicMock()
        client.api.attach_socket.return_value = sock
        clock = [0]

        def read(sock):
            clock[0] = 2
            return None

        with patch.object(utils, 'get_docker_client', return_value=client), \
                patch.object(utils.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(utils.select, 'select',
                             return_value=([sock], [], [])), \
                patch.object(utils, '_socket_read', side_effect=read):
            with self.assertRaises(TimeoutError):
                utils.docker_communicate(MagicMock(), timeout=1)
        sock.close.assert_called_once_with()


@pytest.mark.parametrize('command', [
    'sleep 30',
    'python3 -c "import os\nwhile True: os.write(1, b\'x\' * 4096)"',
])
def test_realtime_none_is_bounded_in_real_container(profile, config, command):
    limits = dict(utils.config.DEFAULT_LIMITS)
    limits['realtime'] = 0.2
    config.DEFAULT_LIMITS = limits
    result = sandboxes.run(profile.name, command=command,
                           limits={'cputime': None, 'realtime': None})
    assert result['timeout'] is True
    assert result['exit_code'] is None

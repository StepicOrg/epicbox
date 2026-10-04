import unittest
from unittest.mock import patch

from epicbox import config, utils


class LimitsCopyTests(unittest.TestCase):
    def test_partial_limits_do_not_mutate_input(self):
        limits = {'cputime': 2, 'file_size': 1024}
        result = utils.merge_limits_defaults(limits)
        self.assertEqual(limits, {'cputime': 2, 'file_size': 1024})
        self.assertEqual(result['file_size'], 1024)
        self.assertEqual(result['realtime'], 2 * config.CPU_TO_REAL_TIME_FACTOR)

    def test_reused_limits_recalculate_realtime(self):
        limits = {'cputime': 1}
        first = utils.merge_limits_defaults(limits)
        limits['cputime'] = 2
        second = utils.merge_limits_defaults(limits)
        self.assertEqual(first['cputime'], 1)
        self.assertEqual(second['realtime'], 2 * config.CPU_TO_REAL_TIME_FACTOR)

    def test_empty_limits_return_independent_defaults(self):
        for limits in (None, {}):
            result = utils.merge_limits_defaults(limits)
            self.assertEqual(result, config.DEFAULT_LIMITS)
            self.assertIsNot(result, config.DEFAULT_LIMITS)
            result['memory'] = 12345
            self.assertNotEqual(config.DEFAULT_LIMITS['memory'], 12345)

    def test_explicit_realtime_is_preserved(self):
        limits = {'cputime': 2, 'realtime': 3}
        result = utils.merge_limits_defaults(limits)
        self.assertEqual(result['realtime'], 3)
        self.assertEqual(limits, {'cputime': 2, 'realtime': 3})

    def test_empty_limits_preserve_configured_realtime_default(self):
        with patch.dict(config.DEFAULT_LIMITS, {'cputime': 1, 'realtime': 100}):
            self.assertEqual(utils.merge_limits_defaults({})['realtime'], 100)


    def test_unlimited_cputime_preserves_default_realtime(self):
        limits = {'cputime': None}
        result = utils.merge_limits_defaults(limits)
        self.assertEqual(result['realtime'], config.DEFAULT_LIMITS['realtime'])
        self.assertEqual(limits, {'cputime': None})
        self.assertIsNone(utils.create_ulimits(result))

    def test_unlimited_cputime_preserves_explicit_realtime(self):
        limits = {'cputime': None, 'realtime': 3}
        result = utils.merge_limits_defaults(limits)
        self.assertEqual(result['realtime'], 3)
        self.assertEqual(limits, {'cputime': None, 'realtime': 3})

    def test_reused_limits_can_switch_to_and_from_unlimited(self):
        limits = {'cputime': None}
        unlimited = utils.merge_limits_defaults(limits)
        limits['cputime'] = 2
        finite = utils.merge_limits_defaults(limits)
        limits['cputime'] = None
        unlimited_again = utils.merge_limits_defaults(limits)
        self.assertEqual(unlimited['realtime'], config.DEFAULT_LIMITS['realtime'])
        self.assertEqual(finite['realtime'], 2 * config.CPU_TO_REAL_TIME_FACTOR)
        self.assertEqual(unlimited_again['realtime'],
                         config.DEFAULT_LIMITS['realtime'])
        self.assertEqual(limits, {'cputime': None})


def test_reused_limits_on_real_containers(profile):
    from epicbox import sandboxes
    limits = {'cputime': 1}
    with sandboxes.create(profile.name, limits=limits) as first:
        assert first.realtime_limit == config.CPU_TO_REAL_TIME_FACTOR
        limits['cputime'] = 2
        with sandboxes.create(profile.name, limits=limits) as second:
            assert second.realtime_limit == 2 * config.CPU_TO_REAL_TIME_FACTOR
            assert limits == {'cputime': 2}
            assert first.realtime_limit == config.CPU_TO_REAL_TIME_FACTOR


def test_unlimited_cputime_on_real_containers(profile):
    from epicbox import sandboxes
    for limits in ({'cputime': None}, {'cputime': None, 'realtime': 3}):
        with sandboxes.create(profile.name, command='true',
                              limits=limits) as sandbox:
            assert sandbox.realtime_limit == limits.get(
                'realtime', config.DEFAULT_LIMITS['realtime'])
            ulimits = sandbox.container.attrs['HostConfig'].get('Ulimits') or []
            assert not any(limit['Name'] == 'cpu' for limit in ulimits)
            result = sandboxes.start(sandbox)
            assert result['exit_code'] == 0
            assert result['timeout'] is False

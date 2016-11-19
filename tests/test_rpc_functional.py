import pytest

from unittest.mock import ANY


def test_run_python(profile, rpcepicbox):
    result = rpcepicbox.run(profile.name, 'python3 -c "print(42)"')

    expected_result = {
        'exit_code': 0,
        'stdout': b'42\n',
        'stderr': b'',
        'duration': ANY,
        'timeout': False,
        'oom_killed': False,
    }
    assert result == expected_result
    assert result['duration'] > 0


def test_run_raise_exception(rpcepicbox):
    with pytest.raises(ValueError):
        rpcepicbox.run('unknown', 'true')


def test_run_upload_files(profile, rpcepicbox):
    files = [
        {'name': 'main.py', 'content': b'print(open("file.txt").read())'},
        {'name': 'file.txt', 'content': b'Data in file.txt'},
    ]

    result = rpcepicbox.run(profile.name, 'python3 main.py', files=files)

    assert result['exit_code'] == 0
    assert result['stdout'] == b'Data in file.txt\n'

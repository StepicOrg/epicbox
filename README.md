# EpicBox
[![Build Status](https://travis-ci.org/StepicOrg/epicbox.svg?branch=master)](https://travis-ci.org/StepicOrg/epicbox)

EpicBox runs untrusted code in secure Docker based sandboxes.

## Requirements
Docker version: `>=1.6.0`

## Usage

```python
import epicbox

epicbox.configure(
    profiles=[
        epicbox.Profile('base', 'stepic/epicbox-base'),
        epicbox.Profile('python', 'stepic/epicbox-python', network=True),
    ],
)
files = [{'name': 'main.py', 'content': b'print(42)\n'}]
epicbox.run('python', 'python3 main.py', files=files, limits={'cputime': 1})
```
the return value of the `run` command is as follows:
```python
{'exit_code': 0,
 'duration': 0.100765,
 'stdout': b'42\n',
 'stderr': b'',
 'timeout': False,
 'oom_killed': False}
```

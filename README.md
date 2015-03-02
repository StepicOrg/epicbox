# EpicBox

## Usage

```python
import epicbox

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

EpicBox could be set up as a standalone RPC service, and the api of RPC client is designed to be able to be used as a drop-in replacement for direct library interaction.

```python
from epicbox.rpcapi import EpicBoxAPI

epicbox = EpicBoxAPI('rabbit://guest@localhost:5672//')
epicbox.run('base', 'ps aux')
```

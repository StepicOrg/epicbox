# epicbox
[![Build Status](https://travis-ci.org/StepicOrg/epicbox.svg?branch=master)](https://travis-ci.org/StepicOrg/epicbox)

A Python library to run untrusted code in secure, isolated [Docker](https://www.docker.com/)
based sandboxes. It is used to automatically grade programming assignments
on [Stepik.org](https://stepik.org/).

It allows to spawn a process inside one-time Docker container, send data
to stdin, and obtain its exit code and stdout/stderr output.  It's very similar
to what the [`subprocess`](https://docs.python.org/3/library/subprocess.html#module-subprocess)
module does but additionally you can specify a custom environment for the process
(a Docker [image](https://docs.docker.com/v17.09/engine/userguide/storagedriver/imagesandcontainers/))
and limit the CPU, memory, disk, and network usage for the running process.

## Usage
Run a simple Python script in a one-time Docker container using the
[`python:3.6.5-alpine`](https://hub.docker.com/_/python/) image:
```python
import epicbox

epicbox.configure(
    profiles=[
        epicbox.Profile('python', 'python:3.6.5-alpine')
    ]
)
files = [{'name': 'main.py', 'content': b'print(42)'}]
limits = {'cputime': 1, 'memory': 64}
result = epicbox.run('python', 'python3 main.py', files=files, limits=limits)

```
The `result` value is:
```python
{'exit_code': 0,
 'stdout': b'42\n',
 'stderr': b'',
 'duration': 0.143358,
 'timeout': False,
 'oom_killed': False}
```

### Available Limit Options

The available limit options and default values:

```
DEFAULT_LIMITS = {
    # CPU time in seconds, None for unlimited
    'cputime': 1,
    # Real time in seconds, None for unlimited
    'realtime': 5,
    # Memory in megabytes, None for unlimited
    'memory': 64,

    # limit the max processes the sandbox can have
    # -1 or None for unlimited(default)
    'processes': -1,
}
```

### Advanced usage
A more advanced usage example of `epicbox` is to compile a C++ program and then
run it multiple times on different input data.  In this example `epicbox` will
run containers on a dedicated [Docker Swarm](https://docs.docker.com/swarm/overview/)
cluster instead of locally installed Docker engine:
```python
import epicbox

PROFILES = {
    'gcc_compile': {
        'docker_image': 'stepik/epicbox-gcc:6.3.0',
        'user': 'root',
    },
    'gcc_run': {
        'docker_image': 'stepik/epicbox-gcc:6.3.0',
        # It's safer to run untrusted code as a non-root user (even in a container)
        'user': 'sandbox',
        'read_only': True,
        'network_disabled': False,
    },
}
epicbox.configure(profiles=PROFILES, docker_url='tcp://1.2.3.4:2375')

untrusted_code = b"""
// C++ program
#include <iostream>

int main() {
    int a, b;
    std::cin >> a >> b;
    std::cout << a + b << std::endl;
}
"""
# A working directory allows to preserve files created in a one-time container
# and access them from another one. Internally it is a temporary Docker volume.
with epicbox.working_directory() as workdir:
    epicbox.run('gcc_compile', 'g++ -pipe -O2 -static -o main main.cpp',
                files=[{'name': 'main.cpp', 'content': untrusted_code}],
                workdir=workdir)
    epicbox.run('gcc_run', './main', stdin='2 2',
                limits={'cputime': 1, 'memory': 64},
                workdir=workdir)
    # {'exit_code': 0, 'stdout': b'4\n', 'stderr': b'', 'duration': 0.095318, 'timeout': False, 'oom_killed': False}
    epicbox.run('gcc_run', './main', stdin='14 5',
                limits={'cputime': 1, 'memory': 64},
                workdir=workdir)
    # {'exit_code': 0, 'stdout': b'19\n', 'stderr': b'', 'duration': 0.10285, 'timeout': False, 'oom_killed': False}
```

## Installation
`epicbox` can be installed by running `pip install epicbox`. It's tested on Python 3.4+ and
Docker 1.12+.

You can also check the [epicbox-images](https://github.com/StepicOrg/epicbox-images)
repository that contains Docker images used to automatically grade programming
assignments on [Stepik.org](https://stepik.org/).

## Contributing
Contributions are welcome, and they are greatly appreciated!
More details can be found in [CONTRIBUTING](CONTRIBUTING.rst).

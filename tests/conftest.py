import epicbox
import pytest

import structlog

from epicbox.rpcapi import EpicBoxAPI
from epicbox.utils import get_docker_client


def pytest_addoption(parser):
    parser.addoption('--docker-url', action='store', default=None,
                     help="Use this url to connect to a Docker backend server")
    parser.addoption('--selinux', action='store_true', default=False,
                     help="Use this option if SELinux policy is enforced")
    parser.addoption('--rpc-url', action='store', default=None,
                     help="Use real RPC server transport for functional tests")
    parser.addoption('--base-workdir', action='store', default=None,
                     help="Base working directory for temporary sandboxes")


@pytest.fixture(scope='session')
def docker_url(request):
    return request.config.getoption('docker_url')


@pytest.fixture(scope='session')
def docker_client(docker_url):
    return get_docker_client(base_url=docker_url)


@pytest.fixture
def selinux_enforced(request):
    return request.config.getoption('selinux')


@pytest.fixture
def docker_image():
    return 'stepic/epicbox-python'


@pytest.fixture
def profile(docker_image):
    return epicbox.Profile('python', docker_image,
                           command='python3 -c \'print("profile stdout")\'')


@pytest.fixture(autouse=True)
def configure(profile, docker_url, selinux_enforced, base_workdir):
    epicbox.configure(profiles=[profile],
                      docker_url=docker_url,
                      selinux_enforced=selinux_enforced,
                      base_workdir=base_workdir)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt='iso'),
            structlog.processors.KeyValueRenderer(key_order=['event']),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
    )


@pytest.fixture
def rpc_transport_url(request):
    return request.config.getoption('rpc_url')


@pytest.fixture
def rpcepicbox(rpc_transport_url):
    if rpc_transport_url:
        return EpicBoxAPI(rpc_transport_url)
    return EpicBoxAPI(None, fake_server=True)


@pytest.fixture
def base_workdir(request):
    return request.config.getoption('base_workdir')

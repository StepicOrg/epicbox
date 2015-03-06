import epicbox
import pytest

import structlog

from epicbox.rpcapi import EpicBoxAPI


def pytest_addoption(parser):
    parser.addoption('--docker-url', action='store', default=None,
                     help="Use this url to connect to a Docker backend server")
    parser.addoption('--selinux', action='store_true', default=False,
                     help="Use this option if SELinux policy is enforced")
    parser.addoption('--rpc-url', action='store', default=None,
                     help="Use real RPC server transport for functional tests")


@pytest.fixture
def docker_url(request):
    return request.config.getoption('docker_url')


@pytest.fixture
def selinux_enforced(request):
    return request.config.getoption('selinux')


@pytest.fixture
def skip_if_remote_docker():
    from epicbox import config
    if config.DOCKER_URL and 'unix:' not in config.DOCKER_URL:
        pytest.skip("Skip because the test requires Docker running locally")


@pytest.fixture
def docker_image():
    return 'stepic/epicbox-python'


@pytest.fixture
def profile(docker_image):
    return epicbox.Profile('python', docker_image)


@pytest.fixture(autouse=True)
def configure(profile, docker_url, selinux_enforced):
    epicbox.configure(profiles=[profile],
                      docker_url=docker_url,
                      selinux_enforced=selinux_enforced)
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

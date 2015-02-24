import epicbox
import pytest

import structlog


def pytest_addoption(parser):
    parser.addoption('--docker-url', action='store', default=None,
                     help="Use this url to connect to a Docker backend server")


@pytest.fixture
def docker_url(request):
    return request.config.getoption('docker_url')


@pytest.fixture
def docker_image():
    # TODO: use the base stepic profile
    return 'sandbox-test'


@pytest.fixture
def profile(docker_image):
    return epicbox.Profile('python', docker_image)


@pytest.fixture(autouse=True)
def configure(profile, docker_url):
    epicbox.configure(profiles=[profile], docker_url=docker_url)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt='iso'),
            structlog.processors.KeyValueRenderer(key_order=['event']),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
    )

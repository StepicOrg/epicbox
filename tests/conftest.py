import uuid

import pytest
import structlog

import epicbox
from epicbox import sandboxes
from epicbox.utils import get_docker_client


def pytest_addoption(parser):
    parser.addoption('--docker-url', action='store', default=None,
                     help="Use this url to connect to a Docker backend server")


@pytest.fixture(scope='session')
def docker_url(request):
    return request.config.getoption('docker_url')


@pytest.fixture(scope='session')
def docker_client(docker_url):
    return get_docker_client(base_url=docker_url)


@pytest.fixture(scope='session')
def docker_image():
    return 'stepic/epicbox-python'


@pytest.fixture
def profile(docker_image):
    return epicbox.Profile('python', docker_image,
                           command='python3 -c \'print("profile stdout")\'')


@pytest.fixture
def profile_read_only(docker_image):
    return epicbox.Profile('python_read_only', docker_image,
                           command='python3 -c \'print("profile stdout")\'',
                           read_only=True)


@pytest.fixture(autouse=True)
def configure(profile, profile_read_only, docker_url):
    epicbox.configure(profiles=[profile, profile_read_only],
                      docker_url=docker_url)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt='iso'),
            structlog.processors.KeyValueRenderer(key_order=['event']),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
    )


@pytest.fixture(scope='session', autouse=True)
def isolate_and_cleanup_test_containers(docker_client):
    sandboxes._SANDBOX_NAME_PREFIX = 'epicbox-test-'
    yield
    test_containers = docker_client.containers(
        filters={'name': 'epicbox-test'}, all=True)
    for container in test_containers:
        docker_client.remove_container(container, v=True, force=True)


@pytest.fixture(scope='session')
def test_utils(docker_client, docker_image):
    class TestUtils(object):
        def create_test_container(self, **kwargs):
            kwargs.update(name='epicbox-test-' + str(uuid.uuid4()),
                          stdin_open=kwargs.get('stdin_open', True))
            return docker_client.create_container(docker_image, **kwargs)

    return TestUtils()

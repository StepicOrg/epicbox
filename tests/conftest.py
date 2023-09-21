from __future__ import annotations

import logging
import uuid
from typing import Any, TYPE_CHECKING

import pytest

import epicbox
from epicbox import config as epicbox_config, Profile, sandboxes
from epicbox.utils import get_docker_client

if TYPE_CHECKING:
    from collections.abc import Iterator

    from _pytest.fixtures import FixtureRequest
    from _pytest.logging import LogCaptureFixture
    from docker import DockerClient
    from docker.models.containers import Container


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--docker-url",
        action="store",
        default=None,
        help="Use this url to connect to a Docker backend server",
    )


@pytest.fixture(scope="session")
def docker_url(request: FixtureRequest) -> None:
    return request.config.getoption("docker_url")


@pytest.fixture(scope="session")
def docker_client(docker_url: str) -> DockerClient:
    return get_docker_client(base_url=docker_url)


@pytest.fixture(scope="session")
def docker_image() -> str:
    return "hyperskill.azurecr.io/epicbox/python:3.10.6-011c5b05a"


@pytest.fixture(scope="session")
def profile(docker_image: str) -> Profile:
    return Profile(
        "python",
        docker_image,
        command="python3 -c 'print(\"profile stdout\")'",
    )


@pytest.fixture(scope="session")
def profile_read_only(docker_image: str) -> Profile:
    return Profile(
        "python_read_only",
        docker_image,
        command="python3 -c 'print(\"profile stdout\")'",
        read_only=True,
    )


@pytest.fixture(scope="session")
def profile_unknown_image() -> Profile:
    return Profile("unknown_image", "unknown_image:tag", command="unknown")


@pytest.fixture(scope="session", autouse=True)
def _configure(
    profile: Profile,
    profile_read_only: Profile,
    profile_unknown_image: Profile,
    docker_url: str,
) -> None:
    epicbox.configure(
        profiles=[profile, profile_read_only, profile_unknown_image],
        docker_url=docker_url,
    )
    # Standard logging to console
    console = logging.StreamHandler()
    logging.getLogger().addHandler(console)


@pytest.fixture(autouse=True)
def _configure_pytest_logging(caplog: LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)


@pytest.fixture(scope="session", autouse=True)
def _isolate_and_cleanup_test_containers(docker_client: DockerClient) -> Iterator[None]:
    sandboxes._SANDBOX_NAME_PREFIX = "epicbox-test-"
    yield
    test_containers = docker_client.containers.list(
        filters={"name": "epicbox-test"},
        all=True,
    )
    for container in test_containers:
        container.remove(v=True, force=True)


class BaseTestUtils:
    def create_test_container(self, **kwargs) -> Container:
        raise NotImplementedError


@pytest.fixture(scope="session")
def test_utils(docker_client: DockerClient, docker_image: str) -> BaseTestUtils:
    class TestUtils(BaseTestUtils):
        def create_test_container(self, **kwargs) -> Container:
            kwargs.update(
                name="epicbox-test-" + str(uuid.uuid4()),
                stdin_open=kwargs.get("stdin_open", True),
            )
            return docker_client.containers.create(docker_image, **kwargs)

    return TestUtils()


class ConfigWrapper:
    _orig_attrs: dict[str, Any]

    def __init__(self) -> None:
        self.__dict__["_orig_attrs"] = {}

    def __setattr__(self, attr: str, value: object) -> None:
        # Do not override the original value if already saved
        if attr not in self._orig_attrs:
            self._orig_attrs[attr] = getattr(epicbox_config, attr)
        setattr(epicbox_config, attr, value)

    def restore(self) -> None:
        """Restore attr value."""
        for attr, value in self._orig_attrs.items():
            setattr(epicbox_config, attr, value)


@pytest.fixture()
def config() -> Iterator[ConfigWrapper]:
    """Return wrapped config.

    A fixture to override the config attributes which restores changes after
    the test run.
    """
    wrapper = ConfigWrapper()
    yield wrapper
    wrapper.restore()

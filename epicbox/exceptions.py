from __future__ import annotations


class EpicBoxError(Exception):
    """The base class for custom exceptions raised by epicbox."""


class DockerError(EpicBoxError):
    """An error occurred with the underlying docker system."""

import tempfile

import structlog
import structlog._config


__all__ = ['Profile', 'configure']


PROFILES = []
DOCKER_URL = None
BASE_WORKDIR = None
SELINUX_ENFORCED = False

DEFAULT_LIMITS = {
    # CPU time in seconds, None for unlimited
    'cputime': 1,
    # Real time in seconds, None for unlimited
    'realtime': 5,
    # Memory in megabytes, None for unlimited
    'memory': 64,

    # Allow user process to fork
    #'canfork': False,
    # Limiting the maximum number of user processes in Linux is tricky.
    # http://unix.stackexchange.com/questions/55319/are-limits-conf-values-applied-on-a-per-process-basis
}
DEFAULT_USER = 'sandbox'
CPU_TO_REAL_TIME_FACTOR = 5


class Profile(object):
    def __init__(self, name, docker_image, command=None, user=DEFAULT_USER,
                 network_disabled=True):
        self.name = name
        self.docker_image = docker_image
        self.command = command
        self.user = user
        self.network_disabled = network_disabled


def configure(profiles=[], docker_url=None, base_workdir=None,
              selinux_enforced=False):
    global PROFILES, DOCKER_URL, BASE_WORKDIR, SELINUX_ENFORCED

    PROFILES = {profile.name: profile for profile in profiles}
    DOCKER_URL = docker_url
    if base_workdir is not None:
        BASE_WORKDIR = base_workdir
    else:
        BASE_WORKDIR = tempfile.gettempdir()
    SELINUX_ENFORCED = selinux_enforced


if not structlog._config._CONFIG.is_configured:
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt='iso'),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.KeyValueRenderer(key_order=['event']),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

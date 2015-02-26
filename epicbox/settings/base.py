import os


def get_env_variable(var_name, default=None):
    """Get the environment variable or raise exception."""
    try:
        return os.environ[var_name]
    except KeyError:
        if default is not None:
            return default
        error_msg = "Set the {} environment variable".format(var_name)
        raise KeyError(error_msg)


DEBUG = True

PROFILES = {
    'base': {
        'docker_image': 'sandbox-test',
    }
}
DOCKER_URL = None
BASE_WORKDIR = None
SELINUX_ENFORCED = False

RPC_TRANSPORT_URL = 'rabbit://guest:guest@localhost:5672//'

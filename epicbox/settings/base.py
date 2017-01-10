import os


def get_env_variable(var_name, default='_UNDEFINED_'):
    """Get the environment variable or raise exception."""
    try:
        return os.environ[var_name]
    except KeyError:
        if default != '_UNDEFINED_':
            return default
        error_msg = "Set the {} environment variable".format(var_name)
        raise KeyError(error_msg)


DEBUG = True

PROFILES = {
    'base': {
        'docker_image': 'stepic/epicbox-base',
    }
}
DOCKER_URL = None

RPC_TRANSPORT_URL = 'rabbit://guest:guest@localhost:5672//'

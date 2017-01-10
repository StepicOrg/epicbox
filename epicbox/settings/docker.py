import json

from .base import *


DEBUG = True if get_env_variable('DEBUG', 'false') == 'true' else False

PROFILES_FILE = get_env_variable('PROFILES_FILE', '')
if PROFILES_FILE:
    with open(PROFILES_FILE) as fd:
        PROFILES = json.load(fd)
DOCKER_URL = get_env_variable('DOCKER_URL', DOCKER_URL)

RPC_TRANSPORT_URL = get_env_variable('RPC_TRANSPORT_URL', RPC_TRANSPORT_URL)

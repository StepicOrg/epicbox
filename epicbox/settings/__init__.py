import os


env_settings = os.environ.get('EPICBOX_SETTINGS')
if env_settings == 'docker':
    from .docker import *
else:
    from .local import *

import oslo_messaging

from contextlib import contextmanager

from oslo_config import cfg
from oslo_messaging.rpc.client import _client_opts

from .rpc import RPCSerializer


ALLOWED_EXMODS = ['epicbox.exceptions']


def set_default_response_timeout(timeout):
    """Set default timeout to wait for a response from a call.

    Given timeout is applied for all rpc calls.

    :param timeout: default timeout in seconds

    """
    cfg.CONF.register_opts(_client_opts)
    cfg.CONF.set_default('rpc_response_timeout', timeout)


@contextmanager
def set_response_timeout(timeout):
    """Context manager to set timeout to wait for a response from a call."""

    current_timeout = cfg.CONF.rpc_response_timeout
    set_default_response_timeout(timeout)
    try:
        yield
    finally:
        set_default_response_timeout(current_timeout)


class EpicBoxAPI(object):
    """Client side of the EpicBox RPC API.

    It sets up the RPC client and binds it to the given topic.
    If required, it handles the starting of a fake RPC server.

    """
    topic = 'epicbox'
    version = '0.1'

    def __init__(self, transport_url, fake_server=False, exchange=None):
        if not fake_server:
            transport = oslo_messaging.get_transport(
                cfg.CONF, transport_url, allowed_remote_exmods=ALLOWED_EXMODS)
        else:
            from . import rpc
            fake_rpc_server = rpc.start_fake_server()
            transport = fake_rpc_server.transport
        target = oslo_messaging.Target(exchange=exchange,
                                       topic=self.topic,
                                       version=self.version)
        self.client = oslo_messaging.RPCClient(transport, target,
                                               serializer=RPCSerializer())

    def run(self, profile_name, command=None, files=[], stdin=None,
            limits=None, workdir=None):
        return self.client.call({}, 'run', profile_name=profile_name,
                                command=command, files=files, stdin=stdin,
                                limits=limits, workdir=workdir)

import oslo_messaging

from oslo_config import cfg

from .rpc import RPCSerializer


ALLOWED_EXMODS = ['epicbox.exceptions']


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

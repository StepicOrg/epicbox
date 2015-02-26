import base64
import socket
import threading

import oslo_messaging
import structlog

from oslo_config import cfg

from .exceptions import EpicBoxError
from .sandboxes import run


logger = structlog.get_logger()


class EpicBoxEndpoint(object):
    target = oslo_messaging.Target(version='0.1')

    @oslo_messaging.expected_exceptions(EpicBoxError)
    def run(self, ctxt, profile_name, command, files, stdin, limits, workdir):
        return run(profile_name, command=command, files=files, stdin=stdin,
                   limits=limits, workdir=workdir)


_fake_transport = oslo_messaging.get_transport(cfg.CONF, 'fake:')
_fake_server = None


def get_server(transport_url, fake=False):
    if not fake:
        transport = oslo_messaging.get_transport(cfg.CONF, transport_url)
        server_name = socket.gethostname()
    else:
        transport = _fake_transport
        server_name = 'fake_server'
    target = oslo_messaging.Target(topic='epicbox', server=server_name)
    endpoints = [
        EpicBoxEndpoint(),
    ]
    return oslo_messaging.get_rpc_server(transport, target, endpoints,
                                         executor='blocking',
                                         serializer=RPCSerializer())


def start_fake_server():
    global _fake_server
    if _fake_server:
        return _fake_server
    _fake_server = get_server(None, fake=True)
    logger.info("Starting fake RPC server in thread")
    threading.Thread(target=_fake_server.start, daemon=True).start()
    return _fake_server


class RPCSerializer(oslo_messaging.NoOpSerializer):
    def serialize_entity(self, ctxt, entity):
        if isinstance(entity, (tuple, list)):
            return [self.serialize_entity(ctxt, v) for v in entity]
        elif isinstance(entity, dict):
            return {k: self.serialize_entity(ctxt, v)
                    for k, v in entity.items()}
        elif isinstance(entity, bytes):
            return {'_serialized.bytes': base64.b64encode(entity).decode()}
        return entity

    def deserialize_entity(self, ctxt, entity):
        if isinstance(entity, dict):
            if '_serialized.bytes' in entity:
                return base64.b64decode(entity['_serialized.bytes'])
            return {k: self.deserialize_entity(ctxt, v)
                    for k, v in entity.items()}
        elif isinstance(entity, list):
            return [self.deserialize_entity(ctxt, v) for v in entity]
        return entity

#!/usr/bin/env python3
import logging
import signal
import sys

import structlog

import epicbox

from functools import partial

from oslo_config import cfg

from epicbox import rpc, settings


logger = structlog.get_logger()


def configure():
    logging.basicConfig(level=logging.INFO)

    if isinstance(settings.PROFILES, (tuple, list)):
        profiles = settings.PROFILES
    elif isinstance(settings.PROFILES, dict):
        profiles = [epicbox.Profile(name, **profile_kwargs)
                    for name, profile_kwargs in settings.PROFILES.items()]
    else:
        raise KeyError("'PROFILES' setting should be list of dict")

    epicbox.configure(
        profiles=profiles,
        docker_url=settings.DOCKER_URL,
        base_workdir=settings.BASE_WORKDIR,
        selinux_enforced=settings.SELINUX_ENFORCED,
    )
    cfg.CONF.rpc_acks_late = True


def register_shutdown_handler(handler):
    """Register a handler that will be called on process termination."""

    def _signal_handler(signum, frame):
        logger.info("Signal handler called with signal", signum=signum)
        handler()
        sys.exit(0)

    for sig in [signal.SIGTERM, signal.SIGINT]:
        signal.signal(sig, _signal_handler)


def stop_server(rpc_server):
    """Attempt to stop the RPC server gracefully."""
    logger.info("Stopping RPC server...")
    try:
        rpc_server.stop()
        rpc_server.wait()
    except Exception:
        pass


def main():
    configure()
    rpc_server = rpc.get_server(settings.RPC_TRANSPORT_URL)
    cfg.CONF.oslo_messaging_rabbit.rabbit_prefetch_count = 1

    shutdown_handler = partial(stop_server, rpc_server)
    register_shutdown_handler(shutdown_handler)
    logger.info("Starting EpicBox RPC server...")
    rpc_server.start()
    rpc_server.wait()


if __name__ == '__main__':
    main()

"""Embedded MQTT broker.

Uses `amqtt`, a pure-Python broker, so the project needs no system-level
Mosquitto install and a judge can bring the entire stack up with pip alone. The
wire protocol is standard MQTT 3.1.1, so a real Mosquitto deployment can be
dropped in by changing MQTT_HOST in `.env` and nothing else.
"""

from __future__ import annotations

import asyncio

from amqtt.broker import Broker

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def build_config() -> dict:
    return {
        "listeners": {
            "default": {
                "type": "tcp",
                "bind": f"{settings.mqtt_host}:{settings.mqtt_port}",
                "max_connections": 1000,
            }
        },
        # Publishing broker health to $SYS topics adds noise to the demo and
        # costs throughput; the bridge does not consume it.
        "sys_interval": 0,
        "auth": {
            # Devices on a closed lab network. Switching to credentials means
            # adding a password file here and filling MQTT_USERNAME/PASSWORD.
            "allow-anonymous": True,
            "plugins": ["auth_anonymous"],
        },
        "topic-check": {"enabled": False},
    }


async def run_broker() -> None:
    broker = Broker(build_config())
    await broker.start()
    logger.info("MQTT broker listening on %s:%s", settings.mqtt_host, settings.mqtt_port)
    try:
        # Sleep forever; the broker serves from its own asyncio tasks.
        await asyncio.Event().wait()
    finally:
        await broker.shutdown()
        logger.info("MQTT broker stopped")

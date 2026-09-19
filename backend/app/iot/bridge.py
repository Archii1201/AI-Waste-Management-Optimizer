"""MQTT-to-database bridge.

Subscribes to bin telemetry and feeds it through the *same* service functions
the REST endpoint uses, so validation, idempotency and collection inference
behave identically on both transports.

Messages are buffered in a queue and written in batches rather than one
transaction per message. A fleet of a few hundred bins reporting every 30
seconds would otherwise spend most of its time in transaction overhead, and
batching lets a burst after a network outage drain quickly.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field

import paho.mqtt.client as mqtt

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.iot import payload as payload_codec
from app.schemas.reading import TelemetryIn
from app.services import telemetry_service

logger = get_logger(__name__)

# Flush when either limit is hit: the batch is full, or it has waited too long.
# The time bound matters during quiet periods, when a reading would otherwise
# sit in the queue indefinitely waiting for the batch to fill.
BATCH_MAX_SIZE = 200
BATCH_MAX_WAIT_SECONDS = 2.0
QUEUE_MAX_SIZE = 20_000


@dataclass
class BridgeStats:
    received: int = 0
    malformed: int = 0
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    collections_detected: int = 0
    dropped_queue_full: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_received(self) -> None:
        with self._lock:
            self.received += 1

    def record_malformed(self) -> None:
        with self._lock:
            self.malformed += 1

    def record_dropped(self) -> None:
        with self._lock:
            self.dropped_queue_full += 1

    def record_batch(
        self, *, accepted: int, duplicates: int, rejected: int, collections: int
    ) -> None:
        with self._lock:
            self.accepted += accepted
            self.duplicates += duplicates
            self.rejected += rejected
            self.collections_detected += collections

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "received": self.received,
                "malformed": self.malformed,
                "accepted": self.accepted,
                "duplicates": self.duplicates,
                "rejected": self.rejected,
                "collections_detected": self.collections_detected,
                "dropped_queue_full": self.dropped_queue_full,
            }


class TelemetryBridge:
    def __init__(self) -> None:
        self._queue: queue.Queue[TelemetryIn] = queue.Queue(maxsize=QUEUE_MAX_SIZE)
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self.stats = BridgeStats()

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=settings.mqtt_client_id,
            clean_session=True,
        )
        if settings.mqtt_username:
            self._client.username_pw_set(settings.mqtt_username, settings.mqtt_password)

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    # ------------------------------------------------------------------
    # MQTT callbacks
    # ------------------------------------------------------------------
    def _on_connect(self, client: mqtt.Client, _userdata, _flags, reason_code, _props=None) -> None:
        if reason_code != 0:
            logger.error("MQTT connection refused: %s", reason_code)
            return
        # Subscribing inside on_connect (not after connect()) means the
        # subscription is restored automatically after a reconnect.
        client.subscribe(settings.mqtt_telemetry_topic, qos=1)
        logger.info("Bridge subscribed to %s", settings.mqtt_telemetry_topic)

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, _props=None) -> None:
        if reason_code != 0:
            logger.warning("MQTT disconnected unexpectedly (%s); retrying", reason_code)

    def _on_message(self, _client, _userdata, message: mqtt.MQTTMessage) -> None:
        self.stats.record_received()
        try:
            reading = payload_codec.decode(message.topic, message.payload)
        except ValueError as exc:
            self.stats.record_malformed()
            logger.warning("Dropping message on %s: %s", message.topic, exc)
            return

        try:
            self._queue.put_nowait(reading)
        except queue.Full:
            # Shedding the newest reading is the right trade: the database is
            # behind, and stale buffered readings are more valuable than none.
            self.stats.record_dropped()
            logger.error("Ingest queue full; dropped reading for %s", reading.bin_code)

    # ------------------------------------------------------------------
    # Batch writer
    # ------------------------------------------------------------------
    def _drain_batch(self) -> list[TelemetryIn]:
        batch: list[TelemetryIn] = []
        deadline = time.monotonic() + BATCH_MAX_WAIT_SECONDS

        while len(batch) < BATCH_MAX_SIZE and not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                batch.append(self._queue.get(timeout=min(remaining, 0.25)))
            except queue.Empty:
                if batch:
                    break
                deadline = time.monotonic() + BATCH_MAX_WAIT_SECONDS
        return batch

    def _write_loop(self) -> None:
        while not self._stop.is_set():
            batch = self._drain_batch()
            if not batch:
                continue

            session = SessionLocal()
            try:
                result = telemetry_service.ingest_bulk(session, batch)
                self.stats.record_batch(
                    accepted=result.accepted,
                    duplicates=result.duplicates,
                    rejected=result.rejected,
                    collections=result.collections_detected,
                )
                if result.collections_detected:
                    logger.info("Inferred %d collection(s) from telemetry", result.collections_detected)
            except Exception:  # noqa: BLE001 - a bad batch must not kill the bridge
                session.rollback()
                self.stats.record_batch(
                    accepted=0, duplicates=0, rejected=len(batch), collections=0
                )
                logger.exception("Failed to write a telemetry batch of %d readings", len(batch))
            finally:
                session.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        self._worker = threading.Thread(target=self._write_loop, name="telemetry-writer", daemon=True)
        self._worker.start()

        self._client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=60)
        self._client.loop_start()
        logger.info("Bridge connected to MQTT at %s:%s", settings.mqtt_host, settings.mqtt_port)

    def stop(self) -> None:
        self._stop.set()
        self._client.loop_stop()
        try:
            self._client.disconnect()
        except Exception:  # noqa: BLE001 - already disconnected is fine
            pass
        if self._worker:
            self._worker.join(timeout=5)
        logger.info("Bridge stopped. Stats: %s", self.stats.snapshot())

    def run_forever(self, *, report_interval_seconds: int = 30) -> None:
        self.start()
        try:
            while True:
                time.sleep(report_interval_seconds)
                logger.info("Bridge stats: %s", self.stats.snapshot())
        except KeyboardInterrupt:
            logger.info("Interrupted; shutting down")
        finally:
            self.stop()

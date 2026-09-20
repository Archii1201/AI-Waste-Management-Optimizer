"""Bin sensor fleet simulator.

With no physical hardware available, this *is* the IoT device layer: it models a
fleet of smart-bin sensors and publishes real MQTT telemetry that the bridge
ingests without knowing the difference.

What it models, beyond incrementing a number:

* **Fill physics** from `profiles`, the same module the historical data
  generator uses, so live telemetry and training data share one world.
* **Closed collection loop** - when a bin is emptied (by the API, or inferred
  from a drop), the device resyncs and starts climbing from near zero again.
  That loop is what makes a live demo behave like a real operation.
* **Sensor imperfection** - battery drain, measurement noise, and optional
  dropout, so the alerting step has genuine faults to detect rather than a
  perfectly behaved fleet.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.iot import payload as payload_codec
from app.iot.profiles import (
    CITY_TZ,
    ambient_temperature_c,
    base_daily_fill_pct,
    fill_increment_pct,
)
from app.models.bin import Bin
from app.models.enums import BinStatus, ReadingSource, WasteType, ZoneType
from app.schemas.reading import TelemetryIn
from app.services.waste import BULK_DENSITY_KG_PER_LITER

logger = get_logger(__name__)

# Residual left behind after a collection; a bin is never perfectly empty.
RESIDUAL_AFTER_COLLECTION = (0.5, 3.5)
# Battery drain per simulated day for a solar-assisted ultrasonic sensor.
BATTERY_DRAIN_PCT_PER_DAY = 0.35

# The legacy fixed-schedule crew, modelled for `--auto-collect`. They work one
# day shift and get round to a full bin eventually rather than immediately,
# which is exactly the inefficiency this project exists to replace.
LEGACY_CREW_SHIFT_HOURS = (6, 14)
LEGACY_CREW_VISIT_PROBABILITY = 0.25


@dataclass
class SimulatedDevice:
    """In-memory state for one bin's sensor."""

    bin_id: int
    bin_code: str
    zone_type: ZoneType
    waste_type: WasteType
    base_daily_pct: float
    fill_level: float
    battery_level: float
    capacity_liters: float
    rng: random.Random = field(repr=False)
    # Last collection this device already knows about, used to spot new ones.
    last_emptied_seen: datetime | None = None

    @classmethod
    def from_bin(cls, bin_obj: Bin, seed: int | None) -> "SimulatedDevice":
        # Seeding per bin keeps each device's randomness independent and makes a
        # seeded run byte-for-byte reproducible for debugging.
        rng = random.Random(f"{seed}:{bin_obj.code}" if seed is not None else None)
        return cls(
            bin_id=bin_obj.id,
            bin_code=bin_obj.code,
            zone_type=bin_obj.zone.zone_type,
            waste_type=bin_obj.waste_type,
            base_daily_pct=base_daily_fill_pct(bin_obj.code, bin_obj.zone.zone_type),
            fill_level=bin_obj.current_fill_level,
            battery_level=bin_obj.battery_level if bin_obj.battery_level is not None else 100.0,
            capacity_liters=bin_obj.capacity_liters,
            rng=rng,
            last_emptied_seen=bin_obj.last_emptied_at,
        )

    def advance(self, moment: datetime, hours: float) -> None:
        increment = fill_increment_pct(
            bin_code=self.bin_code,
            zone_type=self.zone_type,
            waste_type=self.waste_type,
            moment=moment,
            hours=hours,
            rng=self.rng,
            base_daily_pct=self.base_daily_pct,
        )
        self.fill_level = min(100.0, self.fill_level + increment)
        self.battery_level = max(
            0.0, self.battery_level - BATTERY_DRAIN_PCT_PER_DAY * hours / 24.0
        )

    def note_collection(self) -> None:
        self.fill_level = self.rng.uniform(*RESIDUAL_AFTER_COLLECTION)

    def build_payload(self, moment: datetime) -> TelemetryIn:
        # Ultrasonic sensors quantise to roughly whole percent and drift a little.
        measured = min(100.0, max(0.0, self.fill_level + self.rng.uniform(-0.8, 0.8)))
        volume = self.capacity_liters * measured / 100.0

        return TelemetryIn(
            bin_code=self.bin_code,
            fill_level=round(measured, 1),
            weight_kg=round(volume * _density(self.waste_type), 2),
            temperature_c=ambient_temperature_c(moment, self.waste_type, self.rng),
            battery_level=round(self.battery_level, 1),
            recorded_at=moment,
            source=ReadingSource.SIMULATOR,
        )


def _density(waste_type: WasteType) -> float:
    return BULK_DENSITY_KG_PER_LITER[waste_type]


class FleetSimulator:
    """Drives a fleet of `SimulatedDevice`s and publishes their telemetry."""

    def __init__(
        self,
        *,
        interval_minutes: int = 30,
        speed: float = 1.0,
        dropout_rate: float = 0.0,
        seed: int | None = None,
        zone_id: int | None = None,
        start_hours_ago: float = 24.0,
        auto_collect: bool = False,
    ) -> None:
        if interval_minutes <= 0:
            raise ValueError("interval_minutes must be positive")
        if speed <= 0:
            raise ValueError("speed must be positive")
        if not 0.0 <= dropout_rate < 1.0:
            raise ValueError("dropout_rate must be in [0, 1)")

        self.interval = timedelta(minutes=interval_minutes)
        self.speed = speed
        self.dropout_rate = dropout_rate
        self.start_hours_ago = max(0.0, start_hours_ago)
        self.auto_collect = auto_collect
        self.seed = seed
        self.zone_id = zone_id
        self.rng = random.Random(seed)

        self.devices: dict[int, SimulatedDevice] = {}
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id=f"bin-simulator-{id(self)}"
        )
        if settings.mqtt_username:
            self._client.username_pw_set(settings.mqtt_username, settings.mqtt_password)

    # ------------------------------------------------------------------
    def load_fleet(self, db: Session) -> int:
        """Build device state from the bins currently in the database."""
        # Eager-load the zone: every device needs its zone type, and lazy
        # loading would fire one query per bin.
        stmt = (
            select(Bin).options(joinedload(Bin.zone)).where(Bin.status == BinStatus.ACTIVE)
        )
        if self.zone_id is not None:
            stmt = stmt.where(Bin.zone_id == self.zone_id)

        self.devices = {
            b.id: SimulatedDevice.from_bin(b, self.seed) for b in db.scalars(stmt)
        }
        logger.info("Simulator loaded %d active bins", len(self.devices))
        return len(self.devices)

    def resync_collections(self, db: Session) -> int:
        """Reset devices for bins that were emptied by something outside the fleet.

        This closes the loop: a dispatcher emptying a bin in the dashboard, or a
        route being completed, makes the physical sensor start over.

        Keyed on `last_emptied_at` rather than on comparing fill levels. The
        comparison approach looks equivalent but breaks badly under load: when
        the ingestion bridge falls behind, the stored fill level lags the device
        and every lagging bin gets misread as a fresh collection.
        """
        if not self.devices:
            return 0

        rows = db.execute(
            select(Bin.id, Bin.last_emptied_at).where(Bin.id.in_(self.devices.keys()))
        ).all()

        reset = 0
        for bin_id, last_emptied_at in rows:
            device = self.devices[bin_id]
            if last_emptied_at is not None and last_emptied_at != device.last_emptied_seen:
                device.last_emptied_seen = last_emptied_at
                device.note_collection()
                reset += 1
        return reset

    # ------------------------------------------------------------------
    def _legacy_crew_visits(self, device: SimulatedDevice, moment: datetime) -> bool:
        """Model the existing fixed-schedule crew emptying a full bin.

        No API call is made. The device simply drops to its residual and the
        next published reading shows the fall, which the bridge recognises as a
        collection. That is exactly how a real truck emptying a real bin appears
        to the system, so the same code path is exercised either way.
        """
        if not self.auto_collect or device.fill_level < settings.bin_full_threshold:
            return False

        start, end = LEGACY_CREW_SHIFT_HOURS
        if not start <= moment.astimezone(CITY_TZ).hour < end:
            return False
        if device.rng.random() >= LEGACY_CREW_VISIT_PROBABILITY:
            return False

        device.note_collection()
        return True

    def tick_payloads(self, moment: datetime) -> tuple[list[TelemetryIn], int]:
        """Advance every device one interval and return telemetry payloads.

        Shared by the MQTT publisher and the dashboard live-simulation endpoint
        so fill physics stay in one place.
        """
        hours = self.interval.total_seconds() / 3600.0
        payloads: list[TelemetryIn] = []
        collected = 0

        for device in self.devices.values():
            device.advance(moment, hours)
            if self._legacy_crew_visits(device, moment):
                collected += 1

            # A dropped reading models a sensor that failed to transmit, which
            # is what the "sensor offline" alert is meant to catch later.
            if self.dropout_rate and self.rng.random() < self.dropout_rate:
                continue

            payloads.append(device.build_payload(moment))

        return payloads, collected

    def tick(self, moment: datetime) -> tuple[int, int]:
        """Advance every device one interval and publish.

        Returns (messages published, bins emptied by the legacy crew).
        """
        payloads, collected = self.tick_payloads(moment)
        for payload in payloads:
            self._client.publish(
                payload_codec.telemetry_topic(payload.bin_code),
                payload_codec.encode(payload),
                qos=1,
            )
        return len(payloads), collected

    # ------------------------------------------------------------------
    def run(self, *, ticks: int | None = None) -> None:
        """Run the fleet until interrupted, or for a fixed number of ticks.

        `speed` compresses time: at speed 60 one real second is one simulated
        minute, so a 30-minute reporting interval publishes every 30 real
        seconds. A demo typically runs at several hundred times real speed so
        bins visibly fill and get collected within a few minutes.

        Simulated time starts `start_hours_ago` in the past and races forward.
        This matters because ingestion rejects future-dated readings as clock
        skew, so an accelerated run must catch up to the present rather than
        overshoot it. Once it does, the simulator drops to real time on its own.
        """
        session = SessionLocal()
        try:
            if not self.load_fleet(session):
                logger.error("No active bins found. Seed the network before simulating.")
                return
        finally:
            session.close()

        self._client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=60)
        self._client.loop_start()
        logger.info(
            "Simulating %d bins at %.0fx speed, publishing every %d simulated minutes",
            len(self.devices),
            self.speed,
            int(self.interval.total_seconds() // 60),
        )

        sim_time = datetime.now(timezone.utc) - timedelta(hours=self.start_hours_ago)
        accelerated_sleep = self.interval.total_seconds() / self.speed
        completed = 0

        try:
            while ticks is None or completed < ticks:
                real_now = datetime.now(timezone.utc)
                if sim_time >= real_now:
                    # Caught up with the wall clock: from here on the fleet
                    # reports in real time, like actual hardware would.
                    sim_time = real_now
                    sleep_seconds = self.interval.total_seconds()
                else:
                    sleep_seconds = accelerated_sleep

                published, collected = self.tick(sim_time)
                completed += 1

                session = SessionLocal()
                try:
                    reset = self.resync_collections(session)
                finally:
                    session.close()

                logger.info(
                    "Tick %d at %s - published %d readings, %d emptied by crew, %d reset externally",
                    completed,
                    sim_time.isoformat(timespec="minutes"),
                    published,
                    collected,
                    reset,
                )

                sim_time += self.interval
                time.sleep(sleep_seconds)
        except KeyboardInterrupt:
            logger.info("Interrupted; stopping simulator")
        finally:
            self._client.loop_stop()
            self._client.disconnect()

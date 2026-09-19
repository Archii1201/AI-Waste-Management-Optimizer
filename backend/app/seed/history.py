"""Historical telemetry generation.

The fill-level model in the next step cannot learn weekly seasonality from a
database that only holds the last few hours of live telemetry. This module
replays the network backwards over months, producing the reading history and
the collection events that a real deployment would have accumulated.

It deliberately reuses `iot.profiles` and the legacy-crew constants from
`iot.simulator`, so the training history and the telemetry arriving during a
demo are produced by one physics engine. Training on a different world than the
one you evaluate in is the classic way to get a model that looks excellent
offline and useless live.

Rows are written with Core bulk inserts rather than the ORM: at 130 bins, 90
days and 30-minute resolution this is over half a million rows, and ORM
instance overhead would dominate the runtime.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, insert, select, update
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.logging import get_logger
from app.iot.profiles import (
    CITY_TZ,
    ambient_temperature_c,
    base_daily_fill_pct,
    fill_increment_pct,
)
from app.iot.simulator import (
    BATTERY_DRAIN_PCT_PER_DAY,
    LEGACY_CREW_SHIFT_HOURS,
    LEGACY_CREW_VISIT_PROBABILITY,
    RESIDUAL_AFTER_COLLECTION,
)
from app.models.bin import Bin, BinReading
from app.models.collection import CollectionEvent
from app.models.enums import BinStatus, ReadingSource
from app.services.telemetry_service import recompute_fill_rate
from app.services.waste import (
    BULK_DENSITY_KG_PER_LITER,
    estimate_weight_kg,
    split_recyclable_kg,
)

logger = get_logger(__name__)

DEFAULT_HISTORY_DAYS = 90
DEFAULT_INTERVAL_MINUTES = 30
INSERT_CHUNK_SIZE = 5_000

# Contamination of a segregated recyclable stream by wrong-bin disposal. Real
# municipal programmes run in this band, and the recycling recommendations in
# the analytics step depend on there being a spread to find outliers in.
CONTAMINATION_RANGE_PCT = (4.0, 26.0)


@dataclass
class HistoryReport:
    bins_processed: int = 0
    readings_created: int = 0
    collections_created: int = 0
    overflow_events: int = 0
    days: int = 0
    deleted_readings: int = 0
    deleted_collections: int = 0

    def __str__(self) -> str:
        base = (
            f"{self.readings_created:,} readings and {self.collections_created:,} "
            f"collections across {self.bins_processed} bins over {self.days} days "
            f"({self.overflow_events:,} overflow events)"
        )
        if self.deleted_readings or self.deleted_collections:
            base += (
                f"; replaced {self.deleted_readings:,} readings and "
                f"{self.deleted_collections:,} collections"
            )
        return base


@dataclass
class _BinState:
    """Mutable simulation state for one bin as history is replayed."""

    bin_obj: Bin
    rng: random.Random = field(repr=False)
    base_daily_pct: float = 0.0
    fill_level: float = 0.0
    battery_level: float = 100.0
    last_emptied_at: datetime | None = None
    overflow_count: int = 0
    last_temperature: float | None = None


def _purge(db: Session, bin_ids: list[int]) -> tuple[int, int]:
    readings = db.execute(
        delete(BinReading).where(BinReading.bin_id.in_(bin_ids))
    ).rowcount or 0
    collections = db.execute(
        delete(CollectionEvent).where(CollectionEvent.bin_id.in_(bin_ids))
    ).rowcount or 0
    db.commit()
    return readings, collections


def _crew_visits(state: _BinState, moment: datetime) -> bool:
    """Whether the legacy fixed-schedule crew empties this bin now.

    Full bins outside the day shift simply wait, which is what produces the
    overnight overflows the optimizer is later measured against.
    """
    if state.fill_level < settings.bin_full_threshold:
        return False
    start, end = LEGACY_CREW_SHIFT_HOURS
    if not start <= moment.astimezone(CITY_TZ).hour < end:
        return False
    return state.rng.random() < LEGACY_CREW_VISIT_PROBABILITY


def _build_collection(state: _BinState, moment: datetime) -> dict:
    bin_obj = state.bin_obj
    fill_before = state.fill_level
    volume = bin_obj.capacity_liters * fill_before / 100.0
    weight = estimate_weight_kg(bin_obj.waste_type, volume)

    contamination = None
    if bin_obj.waste_type.is_recyclable:
        contamination = round(state.rng.uniform(*CONTAMINATION_RANGE_PCT), 1)

    recyclable, non_recyclable = split_recyclable_kg(
        bin_obj.waste_type, weight, contamination
    )

    hours_since_previous = None
    if state.last_emptied_at is not None:
        hours_since_previous = round(
            (moment - state.last_emptied_at).total_seconds() / 3600.0, 2
        )

    return {
        "bin_id": bin_obj.id,
        "vehicle_id": None,
        "route_id": None,
        "collected_at": moment,
        "fill_level_before": round(fill_before, 1),
        "volume_collected_liters": round(volume, 2),
        "weight_collected_kg": weight,
        "recyclable_kg": recyclable,
        "non_recyclable_kg": non_recyclable,
        "waste_type": bin_obj.waste_type,
        "contamination_pct": contamination,
        "was_overflowing": fill_before >= settings.bin_critical_threshold,
        "hours_since_previous": hours_since_previous,
        "notes": "Legacy fixed-schedule collection (historical backfill)",
    }


def _flush(db: Session, table, rows: list[dict]) -> int:
    if not rows:
        return 0
    written = 0
    for start in range(0, len(rows), INSERT_CHUNK_SIZE):
        chunk = rows[start : start + INSERT_CHUNK_SIZE]
        db.execute(insert(table), chunk)
        written += len(chunk)
    rows.clear()
    return written


def generate_history(
    db: Session,
    *,
    days: int = DEFAULT_HISTORY_DAYS,
    interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
    seed: int | None = 20260919,
    reset: bool = False,
    zone_id: int | None = None,
) -> HistoryReport:
    """Replay the network over `days` of history, ending at the current time.

    Set `reset` to wipe any existing readings and collections for the affected
    bins first; without it, generating twice would interleave two inconsistent
    timelines for the same bin.
    """
    if days <= 0:
        raise ValueError("days must be positive")
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")

    stmt = (
        select(Bin).options(joinedload(Bin.zone)).where(Bin.status == BinStatus.ACTIVE)
    )
    if zone_id is not None:
        stmt = stmt.where(Bin.zone_id == zone_id)
    bins = list(db.scalars(stmt))

    report = HistoryReport(days=days, bins_processed=len(bins))
    if not bins:
        logger.warning("No active bins found; seed the network first")
        return report

    if reset:
        report.deleted_readings, report.deleted_collections = _purge(
            db, [b.id for b in bins]
        )

    interval = timedelta(minutes=interval_minutes)
    hours_per_step = interval.total_seconds() / 3600.0
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = end - timedelta(days=days)
    steps = int((end - start) / interval)

    logger.info(
        "Generating %d days of history for %d bins at %d-minute resolution (~%s rows)",
        days,
        len(bins),
        interval_minutes,
        f"{len(bins) * steps:,}",
    )

    reading_rows: list[dict] = []
    collection_rows: list[dict] = []

    for bin_obj in bins:
        zone_type = bin_obj.zone.zone_type
        rng = random.Random(f"{seed}:{bin_obj.code}" if seed is not None else None)
        state = _BinState(
            bin_obj=bin_obj,
            rng=rng,
            base_daily_pct=base_daily_fill_pct(bin_obj.code, zone_type),
            # Start part-filled rather than empty, so the first day of history
            # is not an artificial network-wide reset.
            fill_level=rng.uniform(5.0, 45.0),
            battery_level=100.0,
        )

        moment = start
        for _ in range(steps):
            previous_fill = state.fill_level

            state.fill_level = min(
                100.0,
                state.fill_level
                + fill_increment_pct(
                    bin_code=bin_obj.code,
                    zone_type=zone_type,
                    waste_type=bin_obj.waste_type,
                    moment=moment,
                    hours=hours_per_step,
                    rng=rng,
                    base_daily_pct=state.base_daily_pct,
                ),
            )
            state.battery_level = max(
                0.0,
                state.battery_level - BATTERY_DRAIN_PCT_PER_DAY * hours_per_step / 24.0,
            )

            # Count an overflow on the upward crossing only, matching how live
            # ingestion counts it, so the two histories stay comparable.
            if previous_fill < settings.bin_critical_threshold <= state.fill_level:
                state.overflow_count += 1
                report.overflow_events += 1

            if _crew_visits(state, moment):
                collection_rows.append(_build_collection(state, moment))
                state.last_emptied_at = moment
                state.fill_level = rng.uniform(*RESIDUAL_AFTER_COLLECTION)

            measured = min(100.0, max(0.0, state.fill_level + rng.uniform(-0.8, 0.8)))
            volume = bin_obj.capacity_liters * measured / 100.0
            state.last_temperature = ambient_temperature_c(
                moment, bin_obj.waste_type, rng
            )

            reading_rows.append(
                {
                    "bin_id": bin_obj.id,
                    "recorded_at": moment,
                    "fill_level": round(measured, 1),
                    "weight_kg": round(
                        volume * BULK_DENSITY_KG_PER_LITER[bin_obj.waste_type], 2
                    ),
                    "temperature_c": state.last_temperature,
                    "battery_level": round(state.battery_level, 1),
                    "source": ReadingSource.BACKFILL,
                    "raw_payload": None,
                }
            )
            moment += interval

        # Leave the bin's live snapshot consistent with the history just written.
        final_volume = bin_obj.capacity_liters * state.fill_level / 100.0
        db.execute(
            update(Bin)
            .where(Bin.id == bin_obj.id)
            .values(
                current_fill_level=round(state.fill_level, 1),
                current_weight_kg=round(
                    final_volume * BULK_DENSITY_KG_PER_LITER[bin_obj.waste_type], 2
                ),
                battery_level=round(state.battery_level, 1),
                last_reading_at=moment - interval,
                last_emptied_at=state.last_emptied_at,
                overflow_count=state.overflow_count,
            )
        )

        report.readings_created += _flush(db, BinReading, reading_rows)
        report.collections_created += _flush(db, CollectionEvent, collection_rows)
        db.commit()

    # Seed each bin's rolling fill-rate estimate from the freshly written
    # history, so forecasts are available before the model is trained.
    for bin_obj in bins:
        recompute_fill_rate(db, bin_obj)
    db.commit()

    logger.info("History generation complete: %s", report)
    return report

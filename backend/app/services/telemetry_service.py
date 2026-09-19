"""Telemetry ingestion.

One code path serves both the REST endpoint and the MQTT bridge, so a reading is
validated and interpreted identically regardless of transport. Beyond storing the
sample, ingestion does three pieces of real work:

1. **Idempotency** - a device retrying a publish must not duplicate history,
   because duplicated samples would bias the learned fill rate.
2. **Collection inference** - cheap ultrasonic sensors never report "I was
   emptied". A large drop to a low residual is how we detect it, which is what
   populates `collection_events` without any manual data entry.
3. **Rolling fill-rate estimation** - maintained per bin so a forecast is
   available from day one, before the trained model has enough history.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.bin import Bin, BinReading
from app.models.collection import CollectionEvent
from app.schemas.reading import BulkTelemetryResult, TelemetryIn, TelemetryResult
from app.services.waste import estimate_weight_kg, split_recyclable_kg

logger = get_logger(__name__)


def resolve_bin(db: Session, *, bin_code: str | None, sensor_id: str | None) -> Bin:
    """Find the bin a payload refers to, by sensor id first then by bin code."""
    stmt = select(Bin)
    if sensor_id:
        found = db.scalar(stmt.where(Bin.sensor_id == sensor_id))
        if found:
            return found
    if bin_code:
        found = db.scalar(stmt.where(Bin.code == bin_code))
        if found:
            return found
    raise NotFoundError(
        "No bin matches the supplied identifier",
        details={"bin_code": bin_code, "sensor_id": sensor_id},
    )


def _previous_reading(db: Session, bin_id: int, before: datetime) -> BinReading | None:
    return db.scalar(
        select(BinReading)
        .where(BinReading.bin_id == bin_id, BinReading.recorded_at < before)
        .order_by(BinReading.recorded_at.desc())
        .limit(1)
    )


def recompute_fill_rate(db: Session, bin_obj: Bin) -> float | None:
    """Robust estimate of percentage points gained per hour for one bin.

    Only positive deltas count: a drop means the bin was emptied, not that waste
    was removed gradually. The median is used rather than the mean because a
    single festival-day spike would otherwise drag the estimate up permanently
    and make every subsequent forecast too pessimistic.
    """
    window_start = datetime.now(timezone.utc) - timedelta(days=settings.fill_rate_window_days)
    readings = list(
        db.scalars(
            select(BinReading)
            .where(BinReading.bin_id == bin_obj.id, BinReading.recorded_at >= window_start)
            .order_by(BinReading.recorded_at.asc())
        )
    )
    if len(readings) < 2:
        return None

    rates: list[float] = []
    for earlier, later in zip(readings, readings[1:]):
        hours = (later.recorded_at - earlier.recorded_at).total_seconds() / 3600.0
        # Ignore samples closer than ~3 minutes: sensor noise divided by a tiny
        # time delta produces meaningless, enormous rates.
        if hours < 0.05:
            continue
        delta = later.fill_level - earlier.fill_level
        if delta > 0:
            rates.append(delta / hours)

    if not rates:
        return None

    bin_obj.avg_fill_rate_pct_per_hour = round(median(rates), 4)
    return bin_obj.avg_fill_rate_pct_per_hour


def _record_inferred_collection(
    db: Session,
    bin_obj: Bin,
    *,
    fill_before: float,
    fill_after: float,
    collected_at: datetime,
) -> CollectionEvent:
    """Create the collection event implied by a sharp drop in fill level."""
    emptied_pct = max(0.0, fill_before - fill_after)
    volume = bin_obj.capacity_liters * emptied_pct / 100.0
    weight = estimate_weight_kg(bin_obj.waste_type, volume)
    recyclable, non_recyclable = split_recyclable_kg(bin_obj.waste_type, weight)

    hours_since_previous = None
    if bin_obj.last_emptied_at:
        hours_since_previous = round(
            (collected_at - bin_obj.last_emptied_at).total_seconds() / 3600.0, 2
        )

    event = CollectionEvent(
        bin_id=bin_obj.id,
        collected_at=collected_at,
        fill_level_before=fill_before,
        volume_collected_liters=round(volume, 2),
        weight_collected_kg=weight,
        recyclable_kg=recyclable,
        non_recyclable_kg=non_recyclable,
        waste_type=bin_obj.waste_type,
        was_overflowing=fill_before >= settings.bin_critical_threshold,
        hours_since_previous=hours_since_previous,
        notes="Inferred from sensor fill-level drop",
    )
    db.add(event)
    bin_obj.last_emptied_at = collected_at
    return event


def ingest_reading(
    db: Session,
    payload: TelemetryIn,
    *,
    commit: bool = True,
    recompute_rate: bool = True,
) -> TelemetryResult:
    """Validate, store and interpret a single telemetry sample."""
    bin_obj = resolve_bin(db, bin_code=payload.bin_code, sensor_id=payload.sensor_id)

    now = datetime.now(timezone.utc)
    recorded_at = payload.recorded_at or now
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)

    tolerance = timedelta(minutes=settings.telemetry_future_tolerance_minutes)
    if recorded_at > now + tolerance:
        raise ValidationError(
            "Reading timestamp is in the future; check the device clock",
            details={"recorded_at": recorded_at.isoformat(), "server_time": now.isoformat()},
        )

    existing = db.scalar(
        select(BinReading).where(
            BinReading.bin_id == bin_obj.id, BinReading.recorded_at == recorded_at
        )
    )
    if existing is not None:
        return TelemetryResult(
            bin_id=bin_obj.id,
            bin_code=bin_obj.code,
            accepted=False,
            duplicate=True,
            current_fill_level=bin_obj.current_fill_level,
            message="Reading already recorded for this timestamp",
        )

    previous = _previous_reading(db, bin_obj.id, recorded_at)

    reading = BinReading(
        bin_id=bin_obj.id,
        recorded_at=recorded_at,
        fill_level=payload.fill_level,
        weight_kg=payload.weight_kg,
        temperature_c=payload.temperature_c,
        battery_level=payload.battery_level,
        source=payload.source,
        raw_payload=payload.raw_payload,
    )
    db.add(reading)

    collection_detected = False
    if previous is not None:
        drop = previous.fill_level - payload.fill_level
        if (
            drop >= settings.collection_drop_threshold_pct
            and payload.fill_level <= settings.collection_residual_max_pct
        ):
            _record_inferred_collection(
                db,
                bin_obj,
                fill_before=previous.fill_level,
                fill_after=payload.fill_level,
                collected_at=recorded_at,
            )
            collection_detected = True

        # Count an overflow only on the upward crossing, so a bin sitting at 97%
        # for six hours is one overflow rather than twelve.
        if (
            previous.fill_level < settings.bin_critical_threshold
            <= payload.fill_level
        ):
            bin_obj.overflow_count += 1

    # Out-of-order backfill must not rewind the live snapshot.
    if bin_obj.last_reading_at is None or recorded_at >= bin_obj.last_reading_at:
        bin_obj.current_fill_level = payload.fill_level
        bin_obj.last_reading_at = recorded_at
        if payload.weight_kg is not None:
            bin_obj.current_weight_kg = payload.weight_kg
        if payload.battery_level is not None:
            bin_obj.battery_level = payload.battery_level

    db.flush()
    if recompute_rate:
        recompute_fill_rate(db, bin_obj)

    if commit:
        db.commit()

    return TelemetryResult(
        bin_id=bin_obj.id,
        bin_code=bin_obj.code,
        accepted=True,
        duplicate=False,
        collection_detected=collection_detected,
        current_fill_level=bin_obj.current_fill_level,
    )


def ingest_bulk(db: Session, payloads: list[TelemetryIn]) -> BulkTelemetryResult:
    """Ingest a batch, isolating failures so one bad row cannot lose the rest.

    Gateways buffer readings while offline and then upload hundreds at once; a
    single unknown sensor id in that batch must not discard the valid samples.
    """
    accepted = duplicates = rejected = collections = 0
    errors: list[str] = []
    touched_bin_ids: set[int] = set()

    for index, payload in enumerate(payloads):
        try:
            # A savepoint per row: a rejected row rolls back only itself, while a
            # plain rollback would throw away every row accepted so far.
            with db.begin_nested():
                result = ingest_reading(
                    db, payload, commit=False, recompute_rate=False
                )
        except (NotFoundError, ValidationError) as exc:
            rejected += 1
            errors.append(f"[{index}] {exc.message}")
            continue

        if result.duplicate:
            duplicates += 1
        else:
            accepted += 1
            touched_bin_ids.add(result.bin_id)
        if result.collection_detected:
            collections += 1

    # Recompute the rolling rate once per bin rather than once per reading; a
    # 1000-row upload would otherwise run 1000 window queries.
    for bin_obj in db.scalars(select(Bin).where(Bin.id.in_(touched_bin_ids))):
        recompute_fill_rate(db, bin_obj)

    db.commit()
    logger.info(
        "Bulk telemetry: %d accepted, %d duplicates, %d rejected, %d collections inferred",
        accepted,
        duplicates,
        rejected,
        collections,
    )
    return BulkTelemetryResult(
        total=len(payloads),
        accepted=accepted,
        duplicates=duplicates,
        rejected=rejected,
        collections_detected=collections,
        errors=errors,
    )

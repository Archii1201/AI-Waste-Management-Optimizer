"""Historical telemetry generation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models.bin import Bin, BinReading
from app.models.collection import CollectionEvent
from app.models.zone import Zone
from app.seed.history import generate_history
from app.seed.network import seed_network


@pytest.fixture
def campus(db):
    """One small zone keeps generation fast while exercising every code path."""
    seed_network(db)
    return db.scalar(select(Zone).where(Zone.code == "IITB-C"))


def test_generates_a_reading_per_bin_per_interval(db, campus):
    report = generate_history(db, days=3, interval_minutes=60, zone_id=campus.id)

    assert report.bins_processed == 9
    assert report.readings_created == 9 * 3 * 24
    assert db.scalar(select(func.count()).select_from(BinReading)) == report.readings_created


def test_history_spans_the_requested_window(db, campus):
    generate_history(db, days=5, interval_minutes=120, zone_id=campus.id)

    oldest = db.scalar(select(func.min(BinReading.recorded_at)))
    newest = db.scalar(select(func.max(BinReading.recorded_at)))
    span_days = (newest - oldest).total_seconds() / 86400

    assert 4.5 < span_days <= 5.0
    assert newest <= datetime.now(timezone.utc) + timedelta(minutes=1)


def test_collections_are_recorded_and_bins_get_emptied(db, campus):
    report = generate_history(db, days=14, interval_minutes=60, zone_id=campus.id)

    assert report.collections_created > 0
    events = db.scalars(select(CollectionEvent)).all()
    assert all(e.fill_level_before >= settings.bin_full_threshold for e in events)
    assert all(e.weight_collected_kg >= 0 for e in events)
    assert all(
        e.recyclable_kg + e.non_recyclable_kg == pytest.approx(e.weight_collected_kg)
        for e in events
    )


def test_recyclable_streams_carry_contamination(db, campus):
    generate_history(db, days=21, interval_minutes=60, zone_id=campus.id)

    events = db.scalars(select(CollectionEvent)).all()
    recyclable = [e for e in events if e.waste_type.is_recyclable]
    if recyclable:
        assert all(e.contamination_pct is not None for e in recyclable)
        assert all(0 < e.contamination_pct < 100 for e in recyclable)

    non_recyclable = [e for e in events if not e.waste_type.is_recyclable]
    assert all(e.contamination_pct is None for e in non_recyclable)


def test_bin_snapshot_matches_the_generated_history(db, campus):
    generate_history(db, days=7, interval_minutes=60, zone_id=campus.id)

    for bin_obj in db.scalars(select(Bin).where(Bin.zone_id == campus.id)):
        latest = db.scalar(
            select(BinReading)
            .where(BinReading.bin_id == bin_obj.id)
            .order_by(BinReading.recorded_at.desc())
            .limit(1)
        )
        assert bin_obj.last_reading_at == latest.recorded_at
        assert bin_obj.current_fill_level == pytest.approx(latest.fill_level, abs=1.0)
        # A rolling fill rate must be available before any model is trained.
        assert bin_obj.avg_fill_rate_pct_per_hour is not None


def test_fill_levels_stay_within_range(db, campus):
    generate_history(db, days=10, interval_minutes=60, zone_id=campus.id)

    lowest = db.scalar(select(func.min(BinReading.fill_level)))
    highest = db.scalar(select(func.max(BinReading.fill_level)))
    assert lowest >= 0.0
    assert highest <= 100.0


def test_weekly_seasonality_is_present(db):
    """The model in the next step can only learn a weekday effect if one exists."""
    seed_network(db)
    campus = db.scalar(select(Zone).where(Zone.code == "IITB-C"))
    generate_history(db, days=28, interval_minutes=60, zone_id=campus.id)

    readings = db.scalars(select(BinReading).order_by(BinReading.recorded_at)).all()
    gains_by_weekday: dict[int, float] = {}
    by_bin: dict[int, BinReading] = {}

    for reading in readings:
        previous = by_bin.get(reading.bin_id)
        by_bin[reading.bin_id] = reading
        if previous is None:
            continue
        delta = reading.fill_level - previous.fill_level
        if delta > 0:
            weekday = reading.recorded_at.weekday()
            gains_by_weekday[weekday] = gains_by_weekday.get(weekday, 0.0) + delta

    # An institutional zone is nearly dormant at weekends.
    weekday_total = sum(gains_by_weekday.get(d, 0.0) for d in range(5))
    weekend_total = sum(gains_by_weekday.get(d, 0.0) for d in (5, 6))
    assert weekday_total > weekend_total * 2


def test_reset_replaces_rather_than_appends(db, campus):
    first = generate_history(db, days=3, interval_minutes=120, zone_id=campus.id)
    second = generate_history(
        db, days=3, interval_minutes=120, zone_id=campus.id, reset=True
    )

    assert second.deleted_readings == first.readings_created
    assert db.scalar(select(func.count()).select_from(BinReading)) == second.readings_created


def test_generation_is_reproducible_for_a_given_seed(db, campus):
    generate_history(db, days=4, interval_minutes=120, zone_id=campus.id, seed=99)
    first = [r.fill_level for r in db.scalars(
        select(BinReading).order_by(BinReading.bin_id, BinReading.recorded_at)
    )]

    generate_history(
        db, days=4, interval_minutes=120, zone_id=campus.id, seed=99, reset=True
    )
    second = [r.fill_level for r in db.scalars(
        select(BinReading).order_by(BinReading.bin_id, BinReading.recorded_at)
    )]

    assert first == second


@pytest.mark.parametrize("kwargs", [{"days": 0}, {"days": -1}, {"interval_minutes": 0}])
def test_invalid_arguments_are_rejected(db, campus, kwargs):
    with pytest.raises(ValueError):
        generate_history(db, zone_id=campus.id, **kwargs)


def test_no_bins_is_handled_gracefully(db):
    report = generate_history(db, days=2)
    assert report.bins_processed == 0
    assert report.readings_created == 0

"""The waste-generation physics shared by the simulator and the data generator."""

from __future__ import annotations

import random
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.iot.profiles import (
    HOURLY_PROFILES,
    WEEKDAY_PROFILES,
    ambient_temperature_c,
    base_daily_fill_pct,
    fill_increment_pct,
    hour_factor,
    stable_unit,
    weekday_factor,
)
from app.models.enums import WasteType, ZoneType

MUMBAI = ZoneInfo("Asia/Kolkata")


def at(year=2026, month=9, day=21, hour=12) -> datetime:
    """A local Mumbai moment expressed in UTC, as the system stores it."""
    return datetime(year, month, day, hour, tzinfo=MUMBAI).astimezone(timezone.utc)


def test_every_profile_is_normalised_to_a_mean_of_one():
    """Normalisation is what lets base_daily_fill_pct mean 'points per average day'."""
    for zone_type in ZoneType:
        hourly = HOURLY_PROFILES[zone_type]
        weekly = WEEKDAY_PROFILES[zone_type]
        assert len(hourly) == 24
        assert len(weekly) == 7
        assert sum(hourly) / 24 == pytest.approx(1.0)
        assert sum(weekly) / 7 == pytest.approx(1.0)


def test_stable_unit_is_deterministic_across_calls():
    assert stable_unit("MUM-AND-001") == stable_unit("MUM-AND-001")
    assert stable_unit("MUM-AND-001") != stable_unit("MUM-AND-002")
    assert 0.0 <= stable_unit("anything") < 1.0


def test_residential_peaks_in_the_evening_not_at_dawn():
    evening = hour_factor(ZoneType.RESIDENTIAL, at(hour=19))
    predawn = hour_factor(ZoneType.RESIDENTIAL, at(hour=3))
    assert evening > predawn * 5


def test_commercial_peaks_around_midday():
    midday = hour_factor(ZoneType.COMMERCIAL, at(hour=12))
    midnight = hour_factor(ZoneType.COMMERCIAL, at(hour=0))
    assert midday > midnight * 5


def test_institutional_zones_go_quiet_at_weekends():
    # 2026-09-21 is a Monday, 2026-09-27 a Sunday.
    weekday = weekday_factor(ZoneType.INSTITUTIONAL, at(day=21))
    sunday = weekday_factor(ZoneType.INSTITUTIONAL, at(day=27))
    assert weekday > sunday * 5


def test_public_spaces_are_busier_at_weekends():
    weekday = weekday_factor(ZoneType.PUBLIC, at(day=22))
    sunday = weekday_factor(ZoneType.PUBLIC, at(day=27))
    assert sunday > weekday


def test_hour_factor_uses_city_local_time_not_utc():
    """18:30 UTC is midnight in Mumbai, so it must score as a quiet hour."""
    utc_evening = datetime(2026, 9, 21, 18, 30, tzinfo=timezone.utc)
    assert hour_factor(ZoneType.RESIDENTIAL, utc_evening) < 0.5


def test_commercial_zones_generate_more_than_residential():
    commercial = base_daily_fill_pct("MUM-TEST-001", ZoneType.COMMERCIAL)
    residential = base_daily_fill_pct("MUM-TEST-001", ZoneType.RESIDENTIAL)
    assert commercial > residential


def test_base_rate_is_stable_for_a_given_bin():
    first = base_daily_fill_pct("MUM-AND-007", ZoneType.RESIDENTIAL)
    second = base_daily_fill_pct("MUM-AND-007", ZoneType.RESIDENTIAL)
    assert first == second


def test_increment_is_never_negative_and_zero_for_zero_hours():
    rng = random.Random(1)
    assert fill_increment_pct(
        bin_code="B1", zone_type=ZoneType.RESIDENTIAL, waste_type=WasteType.MIXED,
        moment=at(), hours=0, rng=rng,
    ) == 0.0

    for _ in range(200):
        value = fill_increment_pct(
            bin_code="B1", zone_type=ZoneType.PUBLIC, waste_type=WasteType.PLASTIC,
            moment=at(), hours=1, rng=rng,
        )
        assert value >= 0.0


def test_longer_intervals_accumulate_more_waste():
    short = fill_increment_pct(
        bin_code="B1", zone_type=ZoneType.RESIDENTIAL, waste_type=WasteType.MIXED,
        moment=at(), hours=1, rng=random.Random(7), allow_spikes=False,
    )
    long = fill_increment_pct(
        bin_code="B1", zone_type=ZoneType.RESIDENTIAL, waste_type=WasteType.MIXED,
        moment=at(), hours=6, rng=random.Random(7), allow_spikes=False,
    )
    assert long > short


def test_bulky_streams_consume_volume_faster_than_dense_ones():
    """Plastic fills a bin faster than glass for the same generating population."""
    def total(waste_type: WasteType) -> float:
        rng = random.Random(99)
        return sum(
            fill_increment_pct(
                bin_code="B1", zone_type=ZoneType.RESIDENTIAL, waste_type=waste_type,
                moment=at(hour=h), hours=1, rng=rng, allow_spikes=False,
            )
            for h in range(24)
        )

    assert total(WasteType.PLASTIC) > total(WasteType.GLASS)


def test_a_seeded_run_is_reproducible():
    def run() -> list[float]:
        rng = random.Random(4242)
        return [
            fill_increment_pct(
                bin_code="B1", zone_type=ZoneType.MIXED_USE, waste_type=WasteType.MIXED,
                moment=at(hour=h), hours=1, rng=rng,
            )
            for h in range(24)
        ]

    assert run() == run()


def test_organic_bins_run_hotter_than_other_streams():
    rng = random.Random(3)
    organic = ambient_temperature_c(at(hour=14), WasteType.ORGANIC, rng)
    glass = ambient_temperature_c(at(hour=14), WasteType.GLASS, rng)
    assert organic > glass


def test_temperature_follows_a_daily_cycle():
    rng = random.Random(3)
    afternoon = ambient_temperature_c(at(hour=15), WasteType.MIXED, rng)
    predawn = ambient_temperature_c(at(hour=3), WasteType.MIXED, rng)
    assert afternoon > predawn

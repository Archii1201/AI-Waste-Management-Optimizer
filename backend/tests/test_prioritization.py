"""Tests for collection prioritisation.

The score is a weighted sum, so most of these pin down *relative* ordering —
that the right bin wins a head-to-head — rather than asserting magic constants
that would need rewriting the moment a weight is tuned.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import settings
from app.models.bin import Bin
from app.models.enums import BinStatus, PriorityTier, WasteType
from app.models.prediction import FillPrediction
from app.models.zone import Zone
from app.services import prioritization
from app.services.prioritization import WEIGHTS, prioritize, score_bin, tier_counts


@pytest.fixture
def zone_row(db):
    zone = Zone(
        code="PRI-Z",
        name="Priority Zone",
        zone_type="commercial",
        center_lat=19.10,
        center_lon=72.86,
        population_served=10_000,
    )
    db.add(zone)
    db.commit()
    return zone


def make_bin(
    db,
    zone_row,
    code: str,
    *,
    fill: float = 50.0,
    waste_type: WasteType = WasteType.MIXED,
    capacity: float = 660.0,
    overflow_count: int = 0,
    lat: float = 19.10,
    lon: float = 72.86,
    last_emptied_at=None,
    status: BinStatus = BinStatus.ACTIVE,
) -> Bin:
    bin_obj = Bin(
        code=code,
        zone_id=zone_row.id,
        latitude=lat,
        longitude=lon,
        capacity_liters=capacity,
        waste_type=waste_type,
        status=status,
        current_fill_level=fill,
        overflow_count=overflow_count,
        last_emptied_at=last_emptied_at,
    )
    db.add(bin_obj)
    db.commit()
    return bin_obj


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------
def test_weights_sum_to_one():
    """Otherwise scores are not comparable to the 0..1 tier thresholds."""
    assert sum(WEIGHTS.values()) == pytest.approx(1.0)


def test_every_required_input_has_a_weight():
    """The problem statement names four inputs; all four must count."""
    assert {"fill", "overflow", "waste_type", "location"} <= set(WEIGHTS)


# ---------------------------------------------------------------------------
# Individual components
# ---------------------------------------------------------------------------
def test_a_fuller_bin_outranks_an_emptier_identical_one(db, zone_row):
    low = make_bin(db, zone_row, "B-LOW", fill=40.0)
    high = make_bin(db, zone_row, "B-HIGH", fill=92.0)

    low_score = score_bin(low, hours_to_full=None, distance_km=None).score
    high_score = score_bin(high, hours_to_full=None, distance_km=None).score

    assert high_score > low_score


def test_fill_below_half_the_threshold_contributes_nothing(db, zone_row):
    """Ranking a 10% bin above a 5% bin is a distinction without a difference."""
    nearly_empty = make_bin(db, zone_row, "B-E1", fill=5.0)
    still_empty = make_bin(db, zone_row, "B-E2", fill=30.0)

    a = score_bin(nearly_empty, hours_to_full=None, distance_km=None)
    b = score_bin(still_empty, hours_to_full=None, distance_km=None)

    assert a.components.fill == 0.0
    assert b.components.fill == 0.0


def test_an_overflowing_bin_outscores_one_exactly_at_threshold(db, zone_row):
    at = make_bin(db, zone_row, "B-AT", fill=settings.bin_full_threshold)
    over = make_bin(db, zone_row, "B-OVER", fill=100.0)

    assert (
        score_bin(over, hours_to_full=None, distance_km=None).components.fill
        > score_bin(at, hours_to_full=None, distance_km=None).components.fill
    )


def test_a_sooner_forecast_overflow_raises_the_score(db, zone_row):
    bin_obj = make_bin(db, zone_row, "B-F", fill=70.0)

    urgent = score_bin(bin_obj, hours_to_full=2.0, distance_km=None)
    relaxed = score_bin(bin_obj, hours_to_full=40.0, distance_km=None)

    assert urgent.score > relaxed.score
    assert urgent.components.overflow > relaxed.components.overflow


def test_a_missing_forecast_is_neutral_not_zero(db, zone_row):
    """No prediction is not evidence of no urgency."""
    bin_obj = make_bin(db, zone_row, "B-N", fill=70.0)

    unknown = score_bin(bin_obj, hours_to_full=None, distance_km=None)
    distant = score_bin(bin_obj, hours_to_full=47.0, distance_km=None)

    assert unknown.components.overflow > distant.components.overflow


def test_organic_outranks_glass_at_the_same_fill(db, zone_row):
    """Organic rots; glass does not."""
    organic = make_bin(db, zone_row, "B-ORG", fill=70.0, waste_type=WasteType.ORGANIC)
    glass = make_bin(db, zone_row, "B-GLS", fill=70.0, waste_type=WasteType.GLASS)

    assert (
        score_bin(organic, hours_to_full=10.0, distance_km=1.0).score
        > score_bin(glass, hours_to_full=10.0, distance_km=1.0).score
    )


def test_a_closer_bin_outranks_a_distant_identical_one(db, zone_row):
    bin_obj = make_bin(db, zone_row, "B-D", fill=70.0)

    near = score_bin(bin_obj, hours_to_full=10.0, distance_km=0.5)
    far = score_bin(bin_obj, hours_to_full=10.0, distance_km=20.0)

    assert near.score > far.score
    assert far.components.location == 0.0


def test_chronic_offenders_are_escalated(db, zone_row):
    clean = make_bin(db, zone_row, "B-C1", fill=70.0, overflow_count=0)
    chronic = make_bin(db, zone_row, "B-C2", fill=70.0, overflow_count=12)

    result = score_bin(chronic, hours_to_full=10.0, distance_km=1.0)

    assert result.score > score_bin(clean, hours_to_full=10.0, distance_km=1.0).score
    assert result.components.chronic == 1.0


def test_a_week_without_collection_is_escalated(db, zone_row):
    now = datetime.now(timezone.utc)
    stale = make_bin(
        db, zone_row, "B-OLD", fill=60.0, last_emptied_at=now - timedelta(days=9)
    )
    recent = make_bin(
        db, zone_row, "B-NEW", fill=60.0, last_emptied_at=now - timedelta(hours=6)
    )

    overdue = score_bin(stale, hours_to_full=20.0, distance_km=1.0, now=now)

    assert overdue.is_overdue is True
    assert overdue.score > score_bin(recent, hours_to_full=20.0, distance_km=1.0, now=now).score


# ---------------------------------------------------------------------------
# Score bounds, tiers and explanations
# ---------------------------------------------------------------------------
def test_scores_stay_within_zero_and_one(db, zone_row):
    worst = make_bin(
        db,
        zone_row,
        "B-WORST",
        fill=100.0,
        waste_type=WasteType.ORGANIC,
        overflow_count=50,
        last_emptied_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    best = make_bin(db, zone_row, "B-BEST", fill=0.0, waste_type=WasteType.GLASS)

    high = score_bin(worst, hours_to_full=0.0, distance_km=0.0)
    low = score_bin(best, hours_to_full=500.0, distance_km=100.0)

    assert 0.0 <= low.score <= high.score <= 1.0


def test_tiers_follow_the_score(db, zone_row):
    critical = make_bin(
        db, zone_row, "B-P0", fill=99.0, waste_type=WasteType.ORGANIC, overflow_count=10
    )
    routine = make_bin(db, zone_row, "B-P3", fill=10.0, waste_type=WasteType.GLASS)

    assert score_bin(critical, hours_to_full=0.5, distance_km=0.2).tier is (
        PriorityTier.P0_CRITICAL
    )
    assert score_bin(routine, hours_to_full=200.0, distance_km=30.0).tier is (
        PriorityTier.P3_ROUTINE
    )


def test_reasons_explain_an_urgent_ranking(db, zone_row):
    bin_obj = make_bin(
        db, zone_row, "B-WHY", fill=97.0, waste_type=WasteType.ORGANIC, overflow_count=8
    )

    reasons = score_bin(bin_obj, hours_to_full=1.0, distance_km=1.0).reasons

    assert any("Overflowing" in r for r in reasons)
    assert any("Organic" in r for r in reasons)
    assert any("Chronic" in r for r in reasons)


def test_a_routine_bin_needs_no_justification(db, zone_row):
    bin_obj = make_bin(db, zone_row, "B-CALM", fill=20.0, waste_type=WasteType.GLASS)

    assert score_bin(bin_obj, hours_to_full=200.0, distance_km=5.0).reasons == []


def test_expected_load_is_derived_from_fill_and_capacity(db, zone_row):
    bin_obj = make_bin(db, zone_row, "B-LOAD", fill=50.0, capacity=1000.0)

    result = score_bin(bin_obj, hours_to_full=None, distance_km=None)

    assert result.expected_volume_liters == pytest.approx(500.0)
    assert result.expected_weight_kg > 0


# ---------------------------------------------------------------------------
# Ranking over the database
# ---------------------------------------------------------------------------
def test_prioritize_sorts_highest_first(db, zone_row):
    make_bin(db, zone_row, "B-1", fill=20.0)
    make_bin(db, zone_row, "B-2", fill=95.0)
    make_bin(db, zone_row, "B-3", fill=60.0)

    ranked = prioritize(db)
    scores = [item.score for item in ranked]

    assert scores == sorted(scores, reverse=True)
    assert ranked[0].bin.code == "B-2"


def test_prioritize_is_deterministic_for_tied_bins(db, zone_row):
    for index in range(5):
        make_bin(db, zone_row, f"B-T{index}", fill=70.0)

    first = [item.bin.code for item in prioritize(db)]
    second = [item.bin.code for item in prioritize(db)]

    assert first == second


def test_prioritize_ignores_inactive_bins(db, zone_row):
    make_bin(db, zone_row, "B-ON", fill=90.0)
    make_bin(db, zone_row, "B-OFF", fill=99.0, status=BinStatus.DECOMMISSIONED)

    assert [item.bin.code for item in prioritize(db)] == ["B-ON"]


def test_prioritize_uses_the_latest_stored_forecast(db, zone_row):
    bin_obj = make_bin(db, zone_row, "B-FC", fill=60.0)
    now = datetime.now(timezone.utc)

    db.add_all(
        [
            FillPrediction(
                bin_id=bin_obj.id,
                generated_at=now - timedelta(hours=5),
                fill_level_at_generation=40.0,
                predicted_fill_rate_pct_per_hour=1.0,
                hours_to_full=60.0,
            ),
            FillPrediction(
                bin_id=bin_obj.id,
                generated_at=now,
                fill_level_at_generation=60.0,
                predicted_fill_rate_pct_per_hour=8.0,
                hours_to_full=3.0,
            ),
        ]
    )
    db.commit()

    assert prioritize(db)[0].hours_to_full == pytest.approx(3.0)


def test_prioritize_filters_by_zone_waste_type_and_score(db, zone_row):
    make_bin(db, zone_row, "B-P", fill=95.0, waste_type=WasteType.PLASTIC)
    make_bin(db, zone_row, "B-O", fill=95.0, waste_type=WasteType.ORGANIC)
    make_bin(db, zone_row, "B-Q", fill=5.0, waste_type=WasteType.PLASTIC)

    plastic = prioritize(db, waste_types=[WasteType.PLASTIC])
    urgent = prioritize(db, min_score=0.5)
    elsewhere = prioritize(db, zone_id=zone_row.id + 999)

    assert {i.bin.code for i in plastic} == {"B-P", "B-Q"}
    assert "B-Q" not in {i.bin.code for i in urgent}
    assert elsewhere == []


def test_prioritize_respects_the_limit(db, zone_row):
    for index in range(10):
        make_bin(db, zone_row, f"B-L{index}", fill=50.0 + index)

    assert len(prioritize(db, limit=4)) == 4


def test_origin_activates_the_proximity_term(db, zone_row):
    near = make_bin(db, zone_row, "B-NEAR", fill=80.0, lat=19.100, lon=72.860)
    make_bin(db, zone_row, "B-FAR", fill=80.0, lat=19.300, lon=73.050)

    ranked = prioritize(db, origin=(near.latitude, near.longitude))

    assert ranked[0].bin.code == "B-NEAR"
    assert ranked[0].distance_km == pytest.approx(0.0, abs=0.01)


def test_tier_counts_covers_every_tier(db, zone_row):
    make_bin(db, zone_row, "B-X", fill=99.0, waste_type=WasteType.ORGANIC)

    counts = tier_counts(prioritize(db))

    assert set(counts) == {tier.value for tier in PriorityTier}
    assert sum(counts.values()) == 1


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def test_priority_endpoint_returns_explainable_rows(client, api_prefix, db, zone_row):
    make_bin(db, zone_row, "B-API", fill=96.0, waste_type=WasteType.ORGANIC)

    response = client.get(f"{api_prefix}/priorities")

    assert response.status_code == 200
    row = response.json()[0]
    assert row["code"] == "B-API"
    assert set(row["components"]) == {
        "fill",
        "overflow",
        "waste_type",
        "location",
        "chronic",
    }
    assert row["weights"]["fill"] == WEIGHTS["fill"]
    assert row["reasons"]


def test_priority_summary_endpoint(client, api_prefix, db, zone_row):
    make_bin(db, zone_row, "B-S1", fill=96.0)
    make_bin(db, zone_row, "B-S2", fill=10.0)

    response = client.get(f"{api_prefix}/priorities/summary")

    body = response.json()
    assert body["total"] == 2
    assert body["bins_over_threshold"] == 1
    assert sum(body["by_tier"].values()) == 2


def test_priority_endpoint_accepts_an_origin(client, api_prefix, db, zone_row):
    make_bin(db, zone_row, "B-GEO", fill=90.0, lat=19.10, lon=72.86)

    response = client.get(
        f"{api_prefix}/priorities",
        params={"origin_lat": 19.10, "origin_lon": 72.86},
    )

    assert response.json()[0]["distance_km"] == pytest.approx(0.0, abs=0.01)

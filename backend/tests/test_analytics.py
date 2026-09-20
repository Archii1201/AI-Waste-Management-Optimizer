"""Tests for operational analytics and recommendations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models.bin import Bin
from app.models.collection import CollectionEvent
from app.models.enums import BinStatus, WasteType
from app.models.zone import Zone
from app.services import analytics_service
from app.services.analytics_service import (
    collection_stats,
    fill_stats,
    recommendations,
    route_stats,
    waste_stats,
    zone_stats,
)


@pytest.fixture
def zone_row(db):
    zone = Zone(
        code="AN-Z",
        name="Analytics Zone",
        zone_type="commercial",
        center_lat=19.1,
        center_lon=72.86,
        population_served=10_000,
    )
    db.add(zone)
    db.commit()
    return zone


def make_bin(db, zone_row, code: str, *, fill: float = 40.0, overflow_count: int = 0) -> Bin:
    bin_obj = Bin(
        code=code,
        zone_id=zone_row.id,
        latitude=19.1,
        longitude=72.86,
        capacity_liters=1000.0,
        waste_type=WasteType.MIXED,
        status=BinStatus.ACTIVE,
        current_fill_level=fill,
        overflow_count=overflow_count,
    )
    db.add(bin_obj)
    db.commit()
    return bin_obj


def add_collection(
    db,
    bin_obj,
    *,
    fill_before: float = 90.0,
    weight: float = 100.0,
    recyclable: float = 40.0,
    contamination: float | None = None,
    was_overflowing: bool = False,
    days_ago: float = 1.0,
    waste_type: WasteType = WasteType.MIXED,
):
    db.add(
        CollectionEvent(
            bin_id=bin_obj.id,
            collected_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
            fill_level_before=fill_before,
            volume_collected_liters=weight * 5,
            weight_collected_kg=weight,
            recyclable_kg=recyclable,
            non_recyclable_kg=weight - recyclable,
            waste_type=waste_type,
            contamination_pct=contamination,
            was_overflowing=was_overflowing,
        )
    )
    db.commit()


# ---------------------------------------------------------------------------
# Empty state
# ---------------------------------------------------------------------------
def test_every_block_is_well_formed_with_no_data(db):
    assert collection_stats(db)["collections"] == 0
    assert route_stats(db)["routes"] == 0
    assert fill_stats(db)["active_bins"] == 0
    assert waste_stats(db)["total_weight_kg"] == 0.0
    assert zone_stats(db) == []


def test_overview_bundles_every_block(db):
    overview = analytics_service.overview(db)

    assert set(overview) == {"window_days", "collections", "routes", "fill", "waste", "alerts"}


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------
def test_collection_stats_aggregate_tonnage_and_split(db, zone_row):
    bin_obj = make_bin(db, zone_row, "AN-1")
    add_collection(db, bin_obj, weight=100.0, recyclable=40.0)
    add_collection(db, bin_obj, weight=60.0, recyclable=20.0)

    stats = collection_stats(db)

    assert stats["collections"] == 2
    assert stats["total_weight_kg"] == 160.0
    assert stats["recyclable_kg"] == 60.0
    assert stats["non_recyclable_kg"] == 100.0
    assert stats["recyclable_pct"] == pytest.approx(37.5)


def test_collections_outside_the_window_are_excluded(db, zone_row):
    bin_obj = make_bin(db, zone_row, "AN-OLD")
    add_collection(db, bin_obj, days_ago=200)

    assert collection_stats(db, days=30)["collections"] == 0
    assert collection_stats(db, days=365)["collections"] == 1


def test_early_collections_are_counted(db, zone_row):
    """Emptying a bin at 20% means the truck collected mostly air."""
    bin_obj = make_bin(db, zone_row, "AN-EARLY")
    add_collection(db, bin_obj, fill_before=20.0)
    add_collection(db, bin_obj, fill_before=90.0)

    stats = collection_stats(db)

    assert stats["early_collections"] == 1
    assert stats["early_collection_pct"] == 50.0


def test_overflow_collections_are_counted(db, zone_row):
    bin_obj = make_bin(db, zone_row, "AN-OVER")
    add_collection(db, bin_obj, was_overflowing=True)
    add_collection(db, bin_obj, was_overflowing=False)

    assert collection_stats(db)["overflow_collection_pct"] == 50.0


def test_collections_break_down_by_waste_type(db, zone_row):
    bin_obj = make_bin(db, zone_row, "AN-WT")
    add_collection(db, bin_obj, waste_type=WasteType.PLASTIC, weight=50.0)
    add_collection(db, bin_obj, waste_type=WasteType.GLASS, weight=80.0)

    by_type = collection_stats(db)["by_waste_type"]

    assert by_type["plastic"]["weight_kg"] == 50.0
    assert by_type["glass"]["collections"] == 1


# ---------------------------------------------------------------------------
# Fill and waste
# ---------------------------------------------------------------------------
def test_fill_stats_describe_the_live_network(db, zone_row):
    make_bin(db, zone_row, "AN-F1", fill=20.0)
    make_bin(db, zone_row, "AN-F2", fill=98.0)

    stats = fill_stats(db)

    assert stats["active_bins"] == 2
    assert stats["avg_fill_level"] == 59.0
    assert stats["bins_critical"] == 1
    assert stats["capacity_in_use_pct"] == 59.0


def test_waste_estimation_reports_diversion_by_stream(db, zone_row):
    bin_obj = make_bin(db, zone_row, "AN-W")
    add_collection(db, bin_obj, waste_type=WasteType.PLASTIC, weight=100.0, recyclable=90.0)
    add_collection(db, bin_obj, waste_type=WasteType.MIXED, weight=100.0, recyclable=30.0)

    stats = waste_stats(db)

    assert stats["total_weight_kg"] == 200.0
    assert stats["recyclable_kg"] == 120.0
    assert stats["diversion_rate_pct"] == 60.0
    assert stats["by_stream"]["plastic"]["diversion_pct"] == 90.0


def test_annual_projection_scales_from_the_window(db, zone_row):
    bin_obj = make_bin(db, zone_row, "AN-P")
    add_collection(db, bin_obj, weight=100.0)

    stats = waste_stats(db, days=10)

    assert stats["projected_annual_weight_kg"] == pytest.approx(100.0 / 10 * 365)


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------
def test_zone_stats_rank_by_generation_per_bin(db):
    busy = Zone(
        code="Z-BUSY",
        name="Busy",
        zone_type="commercial",
        center_lat=19.1,
        center_lon=72.86,
        population_served=1000,
    )
    quiet = Zone(
        code="Z-QUIET",
        name="Quiet",
        zone_type="residential",
        center_lat=19.2,
        center_lon=72.9,
        population_served=1000,
    )
    db.add_all([busy, quiet])
    db.commit()

    busy_bin = make_bin(db, busy, "Z-B1", overflow_count=4)
    quiet_bin = make_bin(db, quiet, "Z-Q1")
    add_collection(db, busy_bin, weight=500.0)
    add_collection(db, quiet_bin, weight=10.0)

    ranked = zone_stats(db)

    assert ranked[0]["zone_code"] == "Z-BUSY"
    assert ranked[0]["overflow_events"] == 4
    assert ranked[0]["kg_per_bin_per_day"] > ranked[1]["kg_per_bin_per_day"]


def test_zones_without_bins_are_skipped(db, zone_row):
    assert zone_stats(db) == []


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------
def test_over_servicing_is_recommended_against(db, zone_row):
    bin_obj = make_bin(db, zone_row, "AN-R1")
    for _ in range(4):
        add_collection(db, bin_obj, fill_before=20.0)

    advice = recommendations(db)
    schedule = [r for r in advice if r["category"] == "collection_schedule"]

    assert schedule
    assert schedule[0]["priority"] == "high"
    assert "early_collection_pct" in schedule[0]["evidence"]


def test_under_servicing_is_recommended_against(db, zone_row):
    bin_obj = make_bin(db, zone_row, "AN-R2")
    for _ in range(4):
        add_collection(db, bin_obj, fill_before=99.0, was_overflowing=True)

    titles = [r["title"] for r in recommendations(db)]

    assert any("after they overflow" in t for t in titles)


def test_overflowing_bins_produce_a_critical_recommendation(db, zone_row):
    make_bin(db, zone_row, "AN-R3", fill=99.0)

    advice = recommendations(db)

    assert advice[0]["priority"] == "critical"
    assert advice[0]["category"] == "immediate_action"


def test_contamination_triggers_a_recycling_recommendation(db, zone_row):
    bin_obj = make_bin(db, zone_row, "AN-R4")
    add_collection(db, bin_obj, contamination=35.0)

    categories = [r["category"] for r in recommendations(db)]

    assert "recycling" in categories


def test_low_diversion_triggers_a_recycling_recommendation(db, zone_row):
    bin_obj = make_bin(db, zone_row, "AN-R5")
    add_collection(db, bin_obj, weight=100.0, recyclable=5.0)

    titles = [r["title"] for r in recommendations(db)]

    assert any("Diversion" in t for t in titles)


def test_every_recommendation_carries_evidence(db, zone_row):
    bin_obj = make_bin(db, zone_row, "AN-R6", fill=99.0)
    add_collection(db, bin_obj, fill_before=10.0, contamination=40.0)

    for item in recommendations(db):
        assert item["evidence"]
        assert item["action"]
        assert item["priority"] in {"critical", "high", "medium", "info"}


def test_recommendations_are_ordered_by_urgency(db, zone_row):
    bin_obj = make_bin(db, zone_row, "AN-R7", fill=99.0)
    for _ in range(4):
        add_collection(db, bin_obj, fill_before=10.0)

    order = {"critical": 0, "high": 1, "medium": 2, "info": 3}
    priorities = [order[r["priority"]] for r in recommendations(db)]

    assert priorities == sorted(priorities)


def test_missing_forecasts_are_flagged_as_a_data_gap(db, zone_row):
    make_bin(db, zone_row, "AN-R8", fill=50.0)

    categories = [r["category"] for r in recommendations(db)]

    assert "data_quality" in categories


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def test_analytics_endpoints_respond(client, api_prefix, db, zone_row):
    bin_obj = make_bin(db, zone_row, "AN-API", fill=98.0)
    add_collection(db, bin_obj, weight=100.0, recyclable=40.0)

    for path in (
        "overview",
        "collections",
        "routes",
        "fill",
        "waste-estimation",
        "zones",
        "recommendations",
    ):
        response = client.get(f"{api_prefix}/analytics/{path}")
        assert response.status_code == 200, path


def test_overview_endpoint_includes_alerts(client, api_prefix, db, zone_row):
    make_bin(db, zone_row, "AN-OV", fill=98.0)

    body = client.get(f"{api_prefix}/analytics/overview").json()

    assert body["fill"]["bins_critical"] == 1
    assert "open" in body["alerts"]


def test_waste_estimation_endpoint_reports_the_split(client, api_prefix, db, zone_row):
    bin_obj = make_bin(db, zone_row, "AN-WE")
    add_collection(db, bin_obj, weight=200.0, recyclable=50.0)

    body = client.get(f"{api_prefix}/analytics/waste-estimation").json()

    assert body["recyclable_kg"] == 50.0
    assert body["non_recyclable_kg"] == 150.0


def test_window_parameter_is_honoured(client, api_prefix, db, zone_row):
    bin_obj = make_bin(db, zone_row, "AN-WIN")
    add_collection(db, bin_obj, days_ago=100)

    narrow = client.get(f"{api_prefix}/analytics/collections", params={"days": 30}).json()
    wide = client.get(f"{api_prefix}/analytics/collections", params={"days": 365}).json()

    assert narrow["collections"] == 0
    assert wide["collections"] == 1

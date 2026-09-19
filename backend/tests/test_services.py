"""Unit tests for the pure helpers, independent of HTTP and the database."""

from __future__ import annotations

import pytest

from app.models.enums import WasteType
from app.services.geo import bounding_box, haversine_km
from app.services.waste import estimate_weight_kg, split_recyclable_kg


def test_haversine_matches_a_known_distance():
    # Mumbai CST to Andheri East is roughly 19 km in a straight line.
    distance = haversine_km(18.9398, 72.8355, 19.1136, 72.8697)
    assert distance == pytest.approx(19.5, abs=1.5)


def test_haversine_is_zero_for_the_same_point():
    assert haversine_km(19.1, 72.8, 19.1, 72.8) == pytest.approx(0.0)


def test_bounding_box_encloses_the_radius():
    min_lat, max_lat, min_lon, max_lon = bounding_box(19.0760, 72.8777, 5.0)
    assert min_lat < 19.0760 < max_lat
    assert min_lon < 72.8777 < max_lon
    # Longitude degrees are shorter than latitude degrees away from the equator,
    # so the box must be wider in longitude to cover the same distance.
    assert (max_lon - min_lon) > (max_lat - min_lat)


def test_denser_streams_weigh_more_for_the_same_volume():
    glass = estimate_weight_kg(WasteType.GLASS, 1000)
    plastic = estimate_weight_kg(WasteType.PLASTIC, 1000)
    assert glass > plastic


def test_segregated_recyclables_are_fully_recoverable():
    recyclable, non_recyclable = split_recyclable_kg(WasteType.GLASS, 100.0)
    assert recyclable == 100.0
    assert non_recyclable == 0.0


def test_contamination_reduces_the_recoverable_share():
    recyclable, non_recyclable = split_recyclable_kg(WasteType.PLASTIC, 100.0, contamination_pct=20.0)
    assert recyclable == pytest.approx(80.0)
    assert non_recyclable == pytest.approx(20.0)


def test_mixed_waste_yields_only_its_recoverable_fraction():
    recyclable, non_recyclable = split_recyclable_kg(WasteType.MIXED, 100.0)
    assert 0 < recyclable < 100.0
    assert recyclable + non_recyclable == pytest.approx(100.0)


def test_other_waste_is_entirely_non_recyclable():
    recyclable, non_recyclable = split_recyclable_kg(WasteType.OTHER, 50.0)
    assert recyclable == 0.0
    assert non_recyclable == 50.0


def test_organic_counts_as_diverted_via_composting():
    recyclable, _ = split_recyclable_kg(WasteType.ORGANIC, 80.0)
    assert recyclable == 80.0


def test_split_never_exceeds_the_total_weight():
    for waste_type in WasteType:
        recyclable, non_recyclable = split_recyclable_kg(waste_type, 42.0)
        assert recyclable + non_recyclable == pytest.approx(42.0)
        assert recyclable <= 42.0

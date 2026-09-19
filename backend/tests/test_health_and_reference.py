"""Health probes and the reference-data contract the dashboard depends on."""

from __future__ import annotations

from app.models.enums import WasteType


def test_liveness(client, api_prefix):
    body = client.get(f"{api_prefix}/health").json()
    assert body["status"] == "ok"
    assert body["city"] == "Mumbai"


def test_readiness_checks_the_database(client, api_prefix):
    body = client.get(f"{api_prefix}/health/ready").json()
    assert body["status"] == "ready"
    assert body["database"]["connected"] is True


def test_reference_exposes_all_six_spec_categories(client, api_prefix):
    body = client.get(f"{api_prefix}/reference").json()
    values = {w["value"] for w in body["waste_types"]}
    assert {"plastic", "paper", "metal", "glass", "organic", "other"} <= values


def test_reference_marks_recyclable_streams_correctly(client, api_prefix):
    body = client.get(f"{api_prefix}/reference").json()
    recyclable = {w["value"] for w in body["waste_types"] if w["recyclable"]}
    assert recyclable == {"plastic", "paper", "metal", "glass"}


def test_organic_carries_the_highest_decay_factor(client, api_prefix):
    body = client.get(f"{api_prefix}/reference").json()
    factors = {w["value"]: w["decay_factor"] for w in body["waste_types"]}
    assert factors["organic"] == max(factors.values())
    assert factors["organic"] > factors["glass"]


def test_reference_thresholds_match_configuration(client, api_prefix):
    from app.core.config import settings

    body = client.get(f"{api_prefix}/reference").json()
    assert body["thresholds"]["bin_full"] == settings.bin_full_threshold
    assert body["thresholds"]["bin_critical"] == settings.bin_critical_threshold
    assert body["map"]["center"] == [settings.city_center_lat, settings.city_center_lon]


def test_every_waste_type_has_a_density(client, api_prefix):
    body = client.get(f"{api_prefix}/reference").json()
    densities = {w["value"]: w["density_kg_per_liter"] for w in body["waste_types"]}
    assert len(densities) == len(WasteType)
    assert all(value > 0 for value in densities.values())

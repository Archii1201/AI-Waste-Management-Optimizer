"""Bin CRUD, filtering and summary behaviour."""

from __future__ import annotations

import pytest


def test_created_bin_starts_empty_with_derived_fields(created_bin):
    assert created_bin["current_fill_level"] == 0.0
    assert created_bin["fill_status"] == "low"
    assert created_bin["needs_collection"] is False
    assert created_bin["is_overflowing"] is False
    assert created_bin["current_volume_liters"] == 0.0
    # A mixed bin is not a segregated recyclable stream.
    assert created_bin["is_recyclable_stream"] is False


def test_effective_threshold_uses_override(client, api_prefix, created_bin):
    response = client.patch(
        f"{api_prefix}/bins/{created_bin['id']}", json={"fill_threshold_override": 60.0}
    )
    assert response.json()["effective_threshold"] == 60.0

    client.post(f"{api_prefix}/telemetry", json={"bin_code": created_bin["code"], "fill_level": 65.0})
    body = client.get(f"{api_prefix}/bins/{created_bin['id']}").json()
    assert body["needs_collection"] is True
    assert body["fill_status"] == "high"


def test_duplicate_code_and_sensor_are_rejected(client, api_prefix, bin_payload, created_bin):
    clash = client.post(f"{api_prefix}/bins", json=bin_payload)
    assert clash.status_code == 409

    other = {**bin_payload, "code": "MUM-AND-0002"}
    assert client.post(f"{api_prefix}/bins", json=other).status_code == 409


def test_bin_requires_existing_zone(client, api_prefix, bin_payload):
    payload = {**bin_payload, "code": "MUM-X-0001", "sensor_id": "SENSOR-X", "zone_id": 9999}
    assert client.post(f"{api_prefix}/bins", json=payload).status_code == 404


@pytest.mark.parametrize(
    "field,value",
    [
        ("capacity_liters", 0),
        ("latitude", 91.0),
        ("longitude", -181.0),
        ("waste_type", "radioactive"),
    ],
)
def test_invalid_bin_fields_are_rejected(client, api_prefix, bin_payload, field, value):
    payload = {**bin_payload, "code": "MUM-BAD-1", "sensor_id": "SENSOR-BAD", field: value}
    assert client.post(f"{api_prefix}/bins", json=payload).status_code == 422


def test_get_bin_by_printed_code(client, api_prefix, created_bin):
    response = client.get(f"{api_prefix}/bins/by-code/{created_bin['code']}")
    assert response.status_code == 200
    assert response.json()["id"] == created_bin["id"]


def _make_bin(client, api_prefix, zone_id, code, **overrides):
    payload = {
        "code": code,
        "zone_id": zone_id,
        "latitude": 19.11,
        "longitude": 72.86,
        "capacity_liters": 240.0,
        "waste_type": "plastic",
        **overrides,
    }
    response = client.post(f"{api_prefix}/bins", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_filters_by_waste_type_and_fill_level(client, api_prefix, zone):
    plastic = _make_bin(client, api_prefix, zone["id"], "P-1", waste_type="plastic")
    _make_bin(client, api_prefix, zone["id"], "G-1", waste_type="glass")
    client.post(f"{api_prefix}/telemetry", json={"bin_code": plastic["code"], "fill_level": 90.0})

    by_type = client.get(f"{api_prefix}/bins", params={"waste_type": "plastic"}).json()
    assert by_type["total"] == 1
    assert by_type["items"][0]["code"] == "P-1"

    needing = client.get(f"{api_prefix}/bins", params={"needs_collection": True}).json()
    assert [b["code"] for b in needing["items"]] == ["P-1"]

    not_needing = client.get(f"{api_prefix}/bins", params={"needs_collection": False}).json()
    assert [b["code"] for b in not_needing["items"]] == ["G-1"]


def test_radius_search_returns_only_nearby_bins(client, api_prefix, zone):
    _make_bin(client, api_prefix, zone["id"], "NEAR-1", latitude=19.1100, longitude=72.8600)
    # Roughly 30 km south, comfortably outside a 5 km radius.
    _make_bin(client, api_prefix, zone["id"], "FAR-1", latitude=18.8500, longitude=72.8600)

    body = client.get(
        f"{api_prefix}/bins",
        params={"lat": 19.1100, "lon": 72.8600, "radius_km": 5},
    ).json()
    assert [b["code"] for b in body["items"]] == ["NEAR-1"]
    assert body["total"] == 1


def test_radius_search_requires_all_three_parameters(client, api_prefix):
    response = client.get(f"{api_prefix}/bins", params={"lat": 19.1, "radius_km": 5})
    assert response.status_code == 422
    assert "lat, lon and radius_km" in response.json()["error"]["message"]


def test_bbox_filter(client, api_prefix, zone):
    _make_bin(client, api_prefix, zone["id"], "IN-1", latitude=19.11, longitude=72.86)
    _make_bin(client, api_prefix, zone["id"], "OUT-1", latitude=19.90, longitude=72.86)

    body = client.get(f"{api_prefix}/bins", params={"bbox": "19.0,72.8,19.2,72.9"}).json()
    assert [b["code"] for b in body["items"]] == ["IN-1"]


def test_malformed_bbox_is_rejected(client, api_prefix):
    response = client.get(f"{api_prefix}/bins", params={"bbox": "19.0,72.8"})
    assert response.status_code == 422


def test_sorting_and_pagination(client, api_prefix, zone):
    for index in range(5):
        _make_bin(client, api_prefix, zone["id"], f"S-{index}")

    page = client.get(
        f"{api_prefix}/bins", params={"page": 2, "page_size": 2, "sort_by": "code"}
    ).json()
    assert page["total"] == 5
    assert page["pages"] == 3
    assert [b["code"] for b in page["items"]] == ["S-2", "S-3"]


def test_summary_counts_bands_and_stale_sensors(client, api_prefix, zone):
    full = _make_bin(client, api_prefix, zone["id"], "F-1", capacity_liters=1000.0)
    _make_bin(client, api_prefix, zone["id"], "E-1", capacity_liters=1000.0)
    client.post(f"{api_prefix}/telemetry", json={"bin_code": full["code"], "fill_level": 97.0})

    body = client.get(f"{api_prefix}/bins/summary").json()
    assert body["total_bins"] == 2
    assert body["bins_overflowing"] == 1
    assert body["bins_needing_collection"] == 1
    assert body["by_fill_band"]["critical"] == 1
    assert body["by_fill_band"]["low"] == 1
    assert body["total_capacity_liters"] == 2000.0
    assert body["estimated_current_volume_liters"] == 970.0
    # E-1 has never reported, so it counts as a stale sensor.
    assert body["stale_sensors"] == 1


def test_manual_empty_creates_collection_and_resets_bin(client, api_prefix, created_bin):
    client.post(f"{api_prefix}/telemetry", json={"bin_code": created_bin["code"], "fill_level": 80.0})

    response = client.post(f"{api_prefix}/bins/{created_bin['id']}/empty", json={"notes": "Ad-hoc"})
    assert response.status_code == 201

    event = response.json()
    assert event["fill_level_before"] == 80.0
    assert event["volume_collected_liters"] == pytest.approx(528.0)
    assert event["weight_collected_kg"] > 0
    # A mixed stream yields only its recoverable fraction.
    assert 0 < event["recyclable_kg"] < event["weight_collected_kg"]

    refreshed = client.get(f"{api_prefix}/bins/{created_bin['id']}").json()
    assert refreshed["current_fill_level"] == 0.0
    assert refreshed["last_emptied_at"] is not None


def test_emptying_an_empty_bin_is_rejected(client, api_prefix, created_bin):
    response = client.post(f"{api_prefix}/bins/{created_bin['id']}/empty", json={})
    assert response.status_code == 422
    assert "already empty" in response.json()["error"]["message"]


def test_delete_bin(client, api_prefix, created_bin):
    assert client.delete(f"{api_prefix}/bins/{created_bin['id']}").status_code == 204
    assert client.get(f"{api_prefix}/bins/{created_bin['id']}").status_code == 404

"""Vehicle CRUD and position tracking."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import iso


@pytest.fixture
def vehicle_payload() -> dict:
    return {
        "code": "MUM-TRK-01",
        "registration_number": "MH01AB1234",
        "vehicle_type": "compactor",
        "capacity_liters": 8000.0,
        "capacity_kg": 5000.0,
        "depot_lat": 19.0760,
        "depot_lon": 72.8777,
        "driver_name": "R. Sharma",
    }


@pytest.fixture
def vehicle(client, api_prefix, vehicle_payload) -> dict:
    response = client.post(f"{api_prefix}/vehicles", json=vehicle_payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_created_vehicle_starts_empty_and_idle(vehicle):
    assert vehicle["status"] == "idle"
    assert vehicle["current_load_liters"] == 0.0
    assert vehicle["load_utilisation_pct"] == 0.0
    assert vehicle["remaining_capacity_liters"] == 8000.0


def test_duplicate_code_and_registration_are_rejected(client, api_prefix, vehicle_payload, vehicle):
    assert client.post(f"{api_prefix}/vehicles", json=vehicle_payload).status_code == 409

    other = {**vehicle_payload, "code": "MUM-TRK-02"}
    assert client.post(f"{api_prefix}/vehicles", json=other).status_code == 409


def test_shift_window_must_be_ordered(client, api_prefix, vehicle_payload):
    payload = {
        **vehicle_payload,
        "code": "MUM-TRK-09",
        "registration_number": "MH01ZZ9999",
        "shift_start": "14:00:00",
        "shift_end": "06:00:00",
    }
    assert client.post(f"{api_prefix}/vehicles", json=payload).status_code == 422


def test_patch_rechecks_the_shift_window_against_stored_values(client, api_prefix, vehicle):
    """Patching only the start time must still be validated against the stored end."""
    response = client.patch(
        f"{api_prefix}/vehicles/{vehicle['id']}", json={"shift_start": "18:00:00"}
    )
    assert response.status_code == 422


def test_accepted_waste_types_round_trip_as_strings(client, api_prefix, vehicle_payload):
    payload = {
        **vehicle_payload,
        "code": "MUM-REC-01",
        "registration_number": "MH01RC0001",
        "vehicle_type": "recycling_truck",
        "accepted_waste_types": ["plastic", "paper", "metal", "glass"],
    }
    body = client.post(f"{api_prefix}/vehicles", json=payload).json()
    assert body["accepted_waste_types"] == ["plastic", "paper", "metal", "glass"]


def test_position_update_moves_the_vehicle(client, api_prefix, vehicle):
    response = client.post(
        f"{api_prefix}/vehicles/{vehicle['id']}/position",
        json={
            "latitude": 19.1000,
            "longitude": 72.8600,
            "status": "en_route",
            "current_load_liters": 2000.0,
        },
    )
    assert response.status_code == 200

    body = response.json()
    assert body["current_lat"] == 19.1000
    assert body["status"] == "en_route"
    assert body["load_utilisation_pct"] == 25.0
    assert body["remaining_capacity_liters"] == 6000.0


def test_stale_position_is_rejected(client, api_prefix, vehicle, now):
    client.post(
        f"{api_prefix}/vehicles/{vehicle['id']}/position",
        json={"latitude": 19.10, "longitude": 72.86, "recorded_at": iso(now)},
    )
    late = client.post(
        f"{api_prefix}/vehicles/{vehicle['id']}/position",
        json={
            "latitude": 19.20,
            "longitude": 72.90,
            "recorded_at": iso(now - timedelta(minutes=10)),
        },
    )
    assert late.status_code == 422
    assert "newer position" in late.json()["error"]["message"]


def test_filter_by_status(client, api_prefix, vehicle):
    client.post(
        f"{api_prefix}/vehicles/{vehicle['id']}/position",
        json={"latitude": 19.1, "longitude": 72.86, "status": "collecting"},
    )
    body = client.get(f"{api_prefix}/vehicles", params={"status": "collecting"}).json()
    assert body["total"] == 1

    empty = client.get(f"{api_prefix}/vehicles", params={"status": "idle"}).json()
    assert empty["total"] == 0

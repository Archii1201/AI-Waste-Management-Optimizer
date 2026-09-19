"""Zone endpoint behaviour."""

from __future__ import annotations


def test_create_and_fetch_zone(client, api_prefix, zone):
    response = client.get(f"{api_prefix}/zones/{zone['id']}")
    assert response.status_code == 200

    body = response.json()
    assert body["code"] == "AND-E"
    assert body["zone_type"] == "mixed_use"
    assert body["population_served"] == 180000


def test_duplicate_zone_code_is_rejected(client, api_prefix, zone):
    response = client.post(
        f"{api_prefix}/zones",
        json={
            "code": zone["code"],
            "name": "Duplicate",
            "center_lat": 19.0,
            "center_lon": 72.8,
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_coordinates_are_validated(client, api_prefix):
    response = client.post(
        f"{api_prefix}/zones",
        json={"code": "BAD", "name": "Bad", "center_lat": 120.0, "center_lon": 72.8},
    )
    assert response.status_code == 422


def test_patch_updates_only_supplied_fields(client, api_prefix, zone):
    response = client.patch(f"{api_prefix}/zones/{zone['id']}", json={"name": "Andheri East Ward"})
    assert response.status_code == 200

    body = response.json()
    assert body["name"] == "Andheri East Ward"
    assert body["zone_type"] == zone["zone_type"]
    assert body["center_lat"] == zone["center_lat"]


def test_zone_stats_reflect_its_bins(client, api_prefix, zone, created_bin):
    client.post(
        f"{api_prefix}/telemetry",
        json={"bin_code": created_bin["code"], "fill_level": 92.0},
    )

    body = client.get(f"{api_prefix}/zones/{zone['id']}/stats").json()
    assert body["total_bins"] == 1
    assert body["average_fill_level"] == 92.0
    assert body["bins_needing_collection"] == 1
    assert body["total_capacity_liters"] == 660.0


def test_zone_with_bins_cannot_be_deleted(client, api_prefix, zone, created_bin):
    response = client.delete(f"{api_prefix}/zones/{zone['id']}")
    assert response.status_code == 409
    assert response.json()["error"]["details"]["bin_count"] == 1


def test_empty_zone_can_be_deleted(client, api_prefix, zone):
    assert client.delete(f"{api_prefix}/zones/{zone['id']}").status_code == 204
    assert client.get(f"{api_prefix}/zones/{zone['id']}").status_code == 404

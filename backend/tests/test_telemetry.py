"""Telemetry ingestion: idempotency, collection inference and rate estimation."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import hours_ago, iso


def test_ingest_updates_the_bin_snapshot(client, api_prefix, created_bin):
    response = client.post(
        f"{api_prefix}/telemetry",
        json={
            "bin_code": created_bin["code"],
            "fill_level": 42.5,
            "battery_level": 88.0,
            "temperature_c": 31.2,
        },
    )
    assert response.status_code == 202

    body = response.json()
    assert body["accepted"] is True
    assert body["duplicate"] is False
    assert body["current_fill_level"] == 42.5

    refreshed = client.get(f"{api_prefix}/bins/{created_bin['id']}").json()
    assert refreshed["current_fill_level"] == 42.5
    assert refreshed["battery_level"] == 88.0
    assert refreshed["last_reading_at"] is not None


def test_sensor_id_resolves_the_bin(client, api_prefix, created_bin):
    response = client.post(
        f"{api_prefix}/telemetry", json={"sensor_id": "SENSOR-0001", "fill_level": 10.0}
    )
    assert response.status_code == 202
    assert response.json()["bin_id"] == created_bin["id"]


def test_payload_without_any_identifier_is_rejected(client, api_prefix):
    response = client.post(f"{api_prefix}/telemetry", json={"fill_level": 10.0})
    assert response.status_code == 422


def test_unknown_bin_is_reported_as_not_found(client, api_prefix):
    response = client.post(
        f"{api_prefix}/telemetry", json={"bin_code": "DOES-NOT-EXIST", "fill_level": 10.0}
    )
    assert response.status_code == 404


def test_repeated_publish_is_idempotent(client, api_prefix, created_bin, now):
    payload = {
        "bin_code": created_bin["code"],
        "fill_level": 30.0,
        "recorded_at": iso(now - timedelta(hours=1)),
    }
    assert client.post(f"{api_prefix}/telemetry", json=payload).json()["accepted"] is True

    retry = client.post(f"{api_prefix}/telemetry", json=payload).json()
    assert retry["accepted"] is False
    assert retry["duplicate"] is True

    readings = client.get(f"{api_prefix}/bins/{created_bin['id']}/readings").json()
    assert len(readings) == 1


def test_future_timestamps_are_rejected(client, api_prefix, created_bin, now):
    response = client.post(
        f"{api_prefix}/telemetry",
        json={
            "bin_code": created_bin["code"],
            "fill_level": 30.0,
            "recorded_at": iso(now + timedelta(hours=2)),
        },
    )
    assert response.status_code == 422
    assert "device clock" in response.json()["error"]["message"]


def test_fill_level_outside_range_is_rejected(client, api_prefix, created_bin):
    response = client.post(
        f"{api_prefix}/telemetry", json={"bin_code": created_bin["code"], "fill_level": 140.0}
    )
    assert response.status_code == 422


def test_sharp_drop_is_inferred_as_a_collection(client, api_prefix, created_bin, now):
    client.post(
        f"{api_prefix}/telemetry",
        json={
            "bin_code": created_bin["code"],
            "fill_level": 88.0,
            "recorded_at": hours_ago(now, 3),
        },
    )
    response = client.post(
        f"{api_prefix}/telemetry",
        json={
            "bin_code": created_bin["code"],
            "fill_level": 4.0,
            "recorded_at": hours_ago(now, 2),
        },
    ).json()
    assert response["collection_detected"] is True

    collections = client.get(f"{api_prefix}/bins/{created_bin['id']}/collections").json()
    assert len(collections) == 1
    assert collections[0]["fill_level_before"] == 88.0
    assert collections[0]["was_overflowing"] is False
    assert collections[0]["notes"] == "Inferred from sensor fill-level drop"

    refreshed = client.get(f"{api_prefix}/bins/{created_bin['id']}").json()
    assert refreshed["last_emptied_at"] is not None


def test_gradual_decrease_is_not_a_collection(client, api_prefix, created_bin, now):
    """Compaction or settling drops the level slightly; that is not an emptying."""
    client.post(
        f"{api_prefix}/telemetry",
        json={"bin_code": created_bin["code"], "fill_level": 70.0, "recorded_at": hours_ago(now, 3)},
    )
    response = client.post(
        f"{api_prefix}/telemetry",
        json={"bin_code": created_bin["code"], "fill_level": 60.0, "recorded_at": hours_ago(now, 2)},
    ).json()
    assert response["collection_detected"] is False
    assert client.get(f"{api_prefix}/bins/{created_bin['id']}/collections").json() == []


def test_large_drop_to_a_high_residual_is_not_a_collection(client, api_prefix, created_bin, now):
    """A 40-point drop that leaves the bin half full means the sensor glitched."""
    client.post(
        f"{api_prefix}/telemetry",
        json={"bin_code": created_bin["code"], "fill_level": 95.0, "recorded_at": hours_ago(now, 3)},
    )
    response = client.post(
        f"{api_prefix}/telemetry",
        json={"bin_code": created_bin["code"], "fill_level": 55.0, "recorded_at": hours_ago(now, 2)},
    ).json()
    assert response["collection_detected"] is False


def test_overflow_is_counted_once_per_upward_crossing(client, api_prefix, created_bin, now):
    for index, level in enumerate([80.0, 97.0, 98.0, 99.0]):
        client.post(
            f"{api_prefix}/telemetry",
            json={
                "bin_code": created_bin["code"],
                "fill_level": level,
                "recorded_at": hours_ago(now, 10 - index),
            },
        )
    assert client.get(f"{api_prefix}/bins/{created_bin['id']}").json()["overflow_count"] == 1


def test_out_of_order_reading_is_stored_without_rewinding_the_snapshot(
    client, api_prefix, created_bin, now
):
    client.post(
        f"{api_prefix}/telemetry",
        json={"bin_code": created_bin["code"], "fill_level": 60.0, "recorded_at": hours_ago(now, 1)},
    )
    client.post(
        f"{api_prefix}/telemetry",
        json={"bin_code": created_bin["code"], "fill_level": 20.0, "recorded_at": hours_ago(now, 5)},
    )

    assert client.get(f"{api_prefix}/bins/{created_bin['id']}").json()["current_fill_level"] == 60.0
    readings = client.get(f"{api_prefix}/bins/{created_bin['id']}/readings").json()
    # History is returned oldest first regardless of arrival order.
    assert [r["fill_level"] for r in readings] == [20.0, 60.0]


def test_rolling_fill_rate_is_learned_from_history(client, api_prefix, created_bin, now):
    for index, level in enumerate([10.0, 20.0, 30.0, 40.0]):
        client.post(
            f"{api_prefix}/telemetry",
            json={
                "bin_code": created_bin["code"],
                "fill_level": level,
                "recorded_at": hours_ago(now, 20 - index * 5),
            },
        )

    # Ten points gained every five hours is two points per hour.
    rate = client.get(f"{api_prefix}/bins/{created_bin['id']}").json()["avg_fill_rate_pct_per_hour"]
    assert rate == pytest.approx(2.0, abs=0.01)


def test_bulk_upload_accepts_valid_rows_and_isolates_failures(
    client, api_prefix, created_bin, now
):
    response = client.post(
        f"{api_prefix}/telemetry/bulk",
        json={
            "readings": [
                {
                    "bin_code": created_bin["code"],
                    "fill_level": 15.0,
                    "recorded_at": hours_ago(now, 6),
                },
                {"bin_code": "GHOST-BIN", "fill_level": 50.0},
                {
                    "bin_code": created_bin["code"],
                    "fill_level": 25.0,
                    "recorded_at": hours_ago(now, 4),
                },
            ]
        },
    )
    assert response.status_code == 202

    body = response.json()
    assert body["total"] == 3
    assert body["accepted"] == 2
    assert body["rejected"] == 1
    assert len(body["errors"]) == 1

    # The valid rows survived the rejected one.
    readings = client.get(f"{api_prefix}/bins/{created_bin['id']}/readings").json()
    assert [r["fill_level"] for r in readings] == [15.0, 25.0]


def test_bulk_upload_reports_duplicates(client, api_prefix, created_bin, now):
    reading = {
        "bin_code": created_bin["code"],
        "fill_level": 15.0,
        "recorded_at": hours_ago(now, 6),
    }
    client.post(f"{api_prefix}/telemetry/bulk", json={"readings": [reading]})

    body = client.post(f"{api_prefix}/telemetry/bulk", json={"readings": [reading]}).json()
    assert body["duplicates"] == 1
    assert body["accepted"] == 0


def test_recent_readings_feed(client, api_prefix, created_bin, now):
    for index in range(3):
        client.post(
            f"{api_prefix}/telemetry",
            json={
                "bin_code": created_bin["code"],
                "fill_level": 10.0 * (index + 1),
                "recorded_at": hours_ago(now, 5 - index),
            },
        )

    feed = client.get(f"{api_prefix}/telemetry/recent", params={"limit": 2}).json()
    assert len(feed) == 2
    # Newest first.
    assert feed[0]["fill_level"] == 30.0

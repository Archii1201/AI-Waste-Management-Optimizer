"""Tests for alert detection and lifecycle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.models.alert import Alert
from app.models.bin import Bin, BinReading
from app.models.enums import (
    AlertSeverity,
    AlertStatus,
    AlertType,
    BinStatus,
    ReadingSource,
    WasteType,
)
from app.models.prediction import FillPrediction
from app.models.zone import Zone
from app.services import alert_service
from app.services.alert_service import RAPID_FILL_PCT_PER_HOUR, detect


@pytest.fixture
def zone_row(db):
    zone = Zone(
        code="AL-Z",
        name="Alert Zone",
        zone_type="commercial",
        center_lat=19.1,
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
    fill: float = 40.0,
    battery: float | None = 90.0,
    last_reading_at: datetime | None = None,
    waste_type: WasteType = WasteType.MIXED,
) -> Bin:
    bin_obj = Bin(
        code=code,
        zone_id=zone_row.id,
        latitude=19.1,
        longitude=72.86,
        capacity_liters=660.0,
        waste_type=waste_type,
        status=BinStatus.ACTIVE,
        current_fill_level=fill,
        battery_level=battery,
        last_reading_at=last_reading_at or datetime.now(timezone.utc),
    )
    db.add(bin_obj)
    db.commit()
    return bin_obj


def add_readings(db, bin_obj, samples):
    db.add_all(
        BinReading(
            bin_id=bin_obj.id,
            recorded_at=moment,
            fill_level=fill,
            source=ReadingSource.SIMULATOR,
        )
        for moment, fill in samples
    )
    db.commit()


def open_alerts(db, alert_type=None):
    return alert_service.list_alerts(db, open_only=True, alert_type=alert_type)


# ---------------------------------------------------------------------------
# Overflow rules
# ---------------------------------------------------------------------------
def test_a_critical_bin_raises_a_critical_alert(db, zone_row):
    make_bin(db, zone_row, "AL-1", fill=98.0)

    detect(db)
    alerts = open_alerts(db, AlertType.BIN_OVERFLOWING)

    assert len(alerts) == 1
    assert alerts[0].severity is AlertSeverity.CRITICAL
    assert alerts[0].details["fill_level"] == 98.0


def test_a_healthy_bin_raises_nothing(db, zone_row):
    make_bin(db, zone_row, "AL-OK", fill=30.0)

    report = detect(db)

    assert report.created == 0
    assert open_alerts(db) == []


def test_a_forecast_overflow_raises_an_imminent_alert(db, zone_row):
    bin_obj = make_bin(db, zone_row, "AL-2", fill=70.0)
    db.add(
        FillPrediction(
            bin_id=bin_obj.id,
            generated_at=datetime.now(timezone.utc),
            fill_level_at_generation=70.0,
            predicted_fill_rate_pct_per_hour=8.0,
            hours_to_full=3.0,
        )
    )
    db.commit()

    detect(db)
    alerts = open_alerts(db, AlertType.BIN_OVERFLOW_IMMINENT)

    assert len(alerts) == 1
    assert alerts[0].details["hours_to_full"] == 3.0


def test_an_already_overflowing_bin_is_not_double_alerted(db, zone_row):
    """The louder overflowing alert covers it; two rows would double-count."""
    bin_obj = make_bin(db, zone_row, "AL-3", fill=99.0)
    db.add(
        FillPrediction(
            bin_id=bin_obj.id,
            generated_at=datetime.now(timezone.utc),
            fill_level_at_generation=99.0,
            predicted_fill_rate_pct_per_hour=8.0,
            hours_to_full=0.5,
        )
    )
    db.commit()

    detect(db)

    assert open_alerts(db, AlertType.BIN_OVERFLOW_IMMINENT) == []
    assert len(open_alerts(db, AlertType.BIN_OVERFLOWING)) == 1


def test_a_distant_forecast_does_not_alert(db, zone_row):
    bin_obj = make_bin(db, zone_row, "AL-4", fill=40.0)
    db.add(
        FillPrediction(
            bin_id=bin_obj.id,
            generated_at=datetime.now(timezone.utc),
            fill_level_at_generation=40.0,
            predicted_fill_rate_pct_per_hour=0.5,
            hours_to_full=120.0,
        )
    )
    db.commit()

    detect(db)

    assert open_alerts(db, AlertType.BIN_OVERFLOW_IMMINENT) == []


# ---------------------------------------------------------------------------
# Anomaly rules
# ---------------------------------------------------------------------------
def test_a_rapidly_filling_bin_is_flagged_as_an_anomaly(db, zone_row):
    bin_obj = make_bin(db, zone_row, "AL-FAST", fill=80.0)
    now = datetime.now(timezone.utc)
    # 20 percentage points per hour over 4 hours: well past normal accumulation.
    add_readings(
        db,
        bin_obj,
        [(now - timedelta(hours=4 - i), 10.0 + i * 20.0) for i in range(5)],
    )

    detect(db)
    alerts = open_alerts(db, AlertType.HIGH_GENERATION_ANOMALY)

    assert len(alerts) == 1
    assert alerts[0].details["observed_rate_pct_per_hour"] > RAPID_FILL_PCT_PER_HOUR
    assert alerts[0].anomaly_score > 1.0


def test_normal_accumulation_is_not_an_anomaly(db, zone_row):
    bin_obj = make_bin(db, zone_row, "AL-SLOW", fill=50.0)
    now = datetime.now(timezone.utc)
    add_readings(
        db, bin_obj, [(now - timedelta(hours=4 - i), 40.0 + i * 2.0) for i in range(5)]
    )

    detect(db)

    assert open_alerts(db, AlertType.HIGH_GENERATION_ANOMALY) == []


def test_a_collection_inside_the_window_is_not_read_as_generation(db, zone_row):
    """A fill drop means a truck came, not negative waste."""
    bin_obj = make_bin(db, zone_row, "AL-COL", fill=10.0)
    now = datetime.now(timezone.utc)
    add_readings(
        db,
        bin_obj,
        [(now - timedelta(hours=4), 95.0), (now - timedelta(hours=1), 5.0)],
    )

    detect(db)

    assert open_alerts(db, AlertType.HIGH_GENERATION_ANOMALY) == []


# ---------------------------------------------------------------------------
# Sensor health
# ---------------------------------------------------------------------------
def test_a_silent_sensor_raises_an_offline_alert(db, zone_row):
    make_bin(
        db,
        zone_row,
        "AL-QUIET",
        last_reading_at=datetime.now(timezone.utc)
        - timedelta(hours=settings.sensor_stale_hours + 10),
    )

    detect(db)

    assert len(open_alerts(db, AlertType.SENSOR_OFFLINE)) == 1


def test_a_low_battery_raises_an_info_alert(db, zone_row):
    make_bin(db, zone_row, "AL-BAT", battery=8.0)

    detect(db)
    alerts = open_alerts(db, AlertType.BATTERY_LOW)

    assert len(alerts) == 1
    assert alerts[0].severity is AlertSeverity.INFO


# ---------------------------------------------------------------------------
# Deduplication and auto-resolution
# ---------------------------------------------------------------------------
def test_running_detection_twice_does_not_duplicate(db, zone_row):
    make_bin(db, zone_row, "AL-DUP", fill=98.0)

    first = detect(db)
    second = detect(db)

    assert first.created == 1
    assert second.created == 0
    assert second.suppressed >= 1
    assert len(open_alerts(db)) == 1


def test_each_bin_gets_its_own_alert(db, zone_row):
    make_bin(db, zone_row, "AL-A", fill=97.0)
    make_bin(db, zone_row, "AL-B", fill=99.0)

    detect(db)

    assert len(open_alerts(db, AlertType.BIN_OVERFLOWING)) == 2


def test_an_emptied_bin_auto_resolves_its_alert(db, zone_row):
    bin_obj = make_bin(db, zone_row, "AL-FIX", fill=98.0)
    detect(db)
    assert len(open_alerts(db)) == 1

    bin_obj.current_fill_level = 5.0
    db.commit()
    report = detect(db)

    assert report.auto_resolved == 1
    assert open_alerts(db) == []
    assert db.scalar(select(Alert.status)) is AlertStatus.RESOLVED


def test_a_resolved_condition_is_held_down_by_the_cooldown(db, zone_row):
    """Stops a bin hovering at the threshold from flapping."""
    bin_obj = make_bin(db, zone_row, "AL-FLAP", fill=98.0)
    detect(db)

    bin_obj.current_fill_level = 20.0
    db.commit()
    detect(db)

    bin_obj.current_fill_level = 98.0
    db.commit()
    report = detect(db)

    assert report.created == 0
    assert report.suppressed >= 1


def test_detection_can_be_scoped_to_one_zone(db, zone_row):
    make_bin(db, zone_row, "AL-Z1", fill=98.0)

    report = detect(db, zone_id=zone_row.id + 999)

    assert report.created == 0


def test_inactive_bins_are_ignored(db, zone_row):
    bin_obj = make_bin(db, zone_row, "AL-OFF", fill=99.0)
    bin_obj.status = BinStatus.DECOMMISSIONED
    db.commit()

    assert detect(db).created == 0


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
def test_acknowledge_then_resolve(db, zone_row):
    make_bin(db, zone_row, "AL-LC", fill=98.0)
    detect(db)
    alert = open_alerts(db)[0]

    acknowledged = alert_service.acknowledge(db, alert.id, who="dispatcher")
    assert acknowledged.status is AlertStatus.ACKNOWLEDGED
    assert acknowledged.acknowledged_by == "dispatcher"

    resolved = alert_service.resolve(db, alert.id, note="Collected")
    assert resolved.status is AlertStatus.RESOLVED
    assert resolved.resolution_note == "Collected"


def test_an_acknowledged_alert_is_still_open(db, zone_row):
    """Acknowledged means seen, not fixed."""
    make_bin(db, zone_row, "AL-ACK", fill=98.0)
    detect(db)
    alert = open_alerts(db)[0]

    alert_service.acknowledge(db, alert.id, who="dispatcher")

    assert len(open_alerts(db)) == 1


def test_cannot_acknowledge_twice(db, zone_row):
    make_bin(db, zone_row, "AL-2X", fill=98.0)
    detect(db)
    alert = open_alerts(db)[0]
    alert_service.acknowledge(db, alert.id, who="a")

    with pytest.raises(ValidationError):
        alert_service.acknowledge(db, alert.id, who="b")


def test_summary_counts_by_severity(db, zone_row):
    make_bin(db, zone_row, "AL-S1", fill=99.0)
    make_bin(db, zone_row, "AL-S2", battery=5.0)
    detect(db)

    summary = alert_service.alert_summary(db)

    assert summary["open"] == 2
    assert summary["critical_open"] == 1
    assert summary["by_severity"]["critical"] == 1


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def test_detect_endpoint_and_listing(client, api_prefix, db, zone_row):
    make_bin(db, zone_row, "AL-API", fill=98.0)

    detected = client.post(f"{api_prefix}/alerts/detect", json={})
    assert detected.status_code == 200
    assert detected.json()["created"] == 1

    listing = client.get(f"{api_prefix}/alerts", params={"open_only": True})
    assert len(listing.json()) == 1
    assert listing.json()[0]["is_open"] is True


def test_alert_endpoints_round_trip(client, api_prefix, db, zone_row):
    make_bin(db, zone_row, "AL-RT", fill=98.0)
    client.post(f"{api_prefix}/alerts/detect", json={})
    alert_id = client.get(f"{api_prefix}/alerts").json()[0]["id"]

    assert client.get(f"{api_prefix}/alerts/{alert_id}").status_code == 200

    acked = client.post(
        f"{api_prefix}/alerts/{alert_id}/acknowledge",
        json={"acknowledged_by": "dispatcher"},
    )
    assert acked.json()["status"] == "acknowledged"

    resolved = client.post(
        f"{api_prefix}/alerts/{alert_id}/resolve", json={"note": "Done"}
    )
    assert resolved.json()["status"] == "resolved"

    assert client.get(f"{api_prefix}/alerts/summary").json()["resolved"] == 1


def test_unknown_alert_returns_404(client, api_prefix):
    assert client.get(f"{api_prefix}/alerts/999999").status_code == 404


def test_alerts_filter_by_severity(client, api_prefix, db, zone_row):
    make_bin(db, zone_row, "AL-F1", fill=99.0)
    make_bin(db, zone_row, "AL-F2", battery=5.0)
    client.post(f"{api_prefix}/alerts/detect", json={})

    critical = client.get(f"{api_prefix}/alerts", params={"severity": "critical"})
    info = client.get(f"{api_prefix}/alerts", params={"severity": "info"})

    assert len(critical.json()) == 1
    assert len(info.json()) == 1

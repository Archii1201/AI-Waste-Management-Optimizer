"""In-process live simulation for the dashboard.

Reuses `FleetSimulator` fill physics and the existing telemetry ingest + alert
detection path. MQTT is not required: the dashboard ticks this over HTTP.

State lives in this process only. Production must run a single uvicorn worker;
restarting the service resets the simulation.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import ValidationError
from app.iot.simulator import FleetSimulator
from app.models.bin import Bin
from app.services import alert_service, prediction_service, route_service, telemetry_service

_lock = threading.Lock()
_fleet: FleetSimulator | None = None
_running = False
_ticks = 0
_last_update: datetime | None = None
_last_stats: dict = {}


def _snapshot() -> dict:
    return {
        "running": _running,
        "ticks": _ticks,
        "device_count": len(_fleet.devices) if _fleet else 0,
        "last_update": _last_update.isoformat() if _last_update else None,
        "bins_updated": _last_stats.get("bins_updated"),
        "predictions_refreshed": _last_stats.get("predictions_refreshed"),
        "new_alerts": _last_stats.get("new_alerts"),
        "affected_routes": _last_stats.get("affected_routes"),
    }


def status() -> dict:
    with _lock:
        return _snapshot()


def start(db: Session, *, interval_minutes: int = 60, auto_collect: bool = True) -> dict:
    global _fleet, _running, _ticks, _last_update, _last_stats
    with _lock:
        if _running and _fleet is not None:
            return _snapshot()

        fleet = FleetSimulator(
            interval_minutes=interval_minutes,
            speed=1.0,
            start_hours_ago=0.0,
            auto_collect=auto_collect,
        )
        if fleet.load_fleet(db) == 0:
            raise ValidationError("No active bins to simulate. Seed the network first.")

        _fleet = fleet
        _running = True
        _ticks = 0
        _last_update = None
        _last_stats = {}
        return _snapshot()


def stop() -> dict:
    global _fleet, _running
    with _lock:
        _running = False
        _fleet = None
        return _snapshot()


def tick(db: Session) -> dict:
    global _ticks, _last_update, _last_stats
    with _lock:
        if not _running or _fleet is None:
            raise ValidationError("Live simulation is not running")
        fleet = _fleet
        next_tick = _ticks + 1

    fleet.resync_collections(db)
    moment = datetime.now(timezone.utc)
    payloads, _collected = fleet.tick_payloads(moment)
    ingest = telemetry_service.ingest_bulk(db, payloads)
    report = alert_service.detect(db)

    codes = [payload.bin_code for payload in payloads]
    touched = set(db.scalars(select(Bin.id).where(Bin.code.in_(codes)))) if codes else set()
    routes = route_service.list_routes(db, limit=50)
    affected = sum(
        1
        for route in routes
        if any(stop.bin_id in touched for stop in (route.stops or []))
    )

    predictions_refreshed = None
    # Recomputing forecasts is CPU-heavy (network-wide fill simulation).
    # Keep every 3rd telemetry tick. The dashboard still re-reads stored
    # predictions on its normal analytics poll.
    if next_tick % 3 == 0:
        try:
            pred = prediction_service.refresh_predictions(db, keep_history=True)
            predictions_refreshed = pred.bins
        except Exception:
            predictions_refreshed = None

    with _lock:
        _ticks = next_tick
        _last_update = moment
        _last_stats = {
            "bins_updated": ingest.accepted,
            "predictions_refreshed": predictions_refreshed,
            "new_alerts": report.created,
            "affected_routes": affected,
        }
        return _snapshot()

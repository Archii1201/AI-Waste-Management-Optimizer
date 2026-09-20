"""Alert generation, deduplication and lifecycle.

Detection is a sweep rather than a trigger on every reading: telemetry arrives
in bursts of hundreds of messages, and evaluating rules per message would raise
the same alert repeatedly. The sweep is idempotent — running it twice in a row
produces no second copy of anything — which is what `dedup_key` exists for.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.models.alert import Alert
from app.models.bin import Bin, BinReading
from app.models.enums import (
    AlertSeverity,
    AlertStatus,
    AlertType,
    BinStatus,
)
from app.models.prediction import FillPrediction
from app.models.zone import Zone

logger = get_logger(__name__)

OPEN_STATUSES = (AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED)

# A zone needs this many recent collections before a z-score means anything.
MIN_SAMPLES_FOR_ANOMALY = 6
# Standard deviations above the zone mean before generation counts as unusual.
ANOMALY_Z_THRESHOLD = 2.0
# Fill rate (percentage points per hour) that is implausible for normal use and
# usually means a dumping incident rather than ordinary accumulation.
RAPID_FILL_PCT_PER_HOUR = 12.0
LOW_BATTERY_PCT = 15.0


@dataclass
class DetectionReport:
    created: int = 0
    suppressed: int = 0
    auto_resolved: int = 0
    by_type: dict[str, int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.by_type is None:
            self.by_type = {}

    def record(self, alert_type: AlertType) -> None:
        self.created += 1
        self.by_type[alert_type.value] = self.by_type.get(alert_type.value, 0) + 1

    def __str__(self) -> str:
        return (
            f"{self.created} raised, {self.suppressed} suppressed as duplicates, "
            f"{self.auto_resolved} auto-resolved"
        )


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------
def raise_alert(
    db: Session,
    *,
    alert_type: AlertType,
    severity: AlertSeverity,
    dedup_key: str,
    title: str,
    message: str,
    bin_id: int | None = None,
    zone_id: int | None = None,
    details: dict | None = None,
    anomaly_score: float | None = None,
    now: datetime | None = None,
) -> Alert | None:
    """Create an alert unless the same condition is already open or cooling down.

    Returns None when suppressed, so callers can count duplicates rather than
    having to re-query.
    """
    now = now or datetime.now(timezone.utc)

    existing = db.scalar(
        select(Alert)
        .where(Alert.dedup_key == dedup_key)
        .order_by(Alert.triggered_at.desc())
        .limit(1)
    )

    if existing is not None:
        if existing.status in OPEN_STATUSES:
            return None
        # Recently resolved conditions are held down for the cooldown window so
        # a bin hovering either side of a threshold cannot flap.
        cooldown = timedelta(minutes=settings.alert_cooldown_minutes)
        if existing.resolved_at and now - existing.resolved_at < cooldown:
            return None

    alert = Alert(
        alert_type=alert_type,
        severity=severity,
        status=AlertStatus.OPEN,
        bin_id=bin_id,
        zone_id=zone_id,
        title=title,
        message=message,
        details=details,
        dedup_key=dedup_key,
        triggered_at=now,
        anomaly_score=anomaly_score,
    )
    db.add(alert)
    return alert


# ---------------------------------------------------------------------------
# Detection rules
# ---------------------------------------------------------------------------
def _threshold_of(bin_obj: Bin) -> float:
    return bin_obj.fill_threshold_override or settings.bin_full_threshold


def _detect_overflowing(db: Session, bins: list[Bin], report: DetectionReport, now) -> None:
    for bin_obj in bins:
        if bin_obj.current_fill_level < settings.bin_critical_threshold:
            continue
        created = raise_alert(
            db,
            alert_type=AlertType.BIN_OVERFLOWING,
            severity=AlertSeverity.CRITICAL,
            dedup_key=f"overflowing:bin:{bin_obj.id}",
            title=f"{bin_obj.code} is overflowing",
            message=(
                f"{bin_obj.code} is at {bin_obj.current_fill_level:.0f}%, past the "
                f"{settings.bin_critical_threshold:.0f}% critical threshold. "
                "Collect immediately."
            ),
            bin_id=bin_obj.id,
            zone_id=bin_obj.zone_id,
            details={
                "fill_level": bin_obj.current_fill_level,
                "critical_threshold": settings.bin_critical_threshold,
                "waste_type": bin_obj.waste_type.value,
            },
            now=now,
        )
        report.record(AlertType.BIN_OVERFLOWING) if created else _suppress(report)


def _detect_overflow_imminent(
    db: Session, bins: list[Bin], forecasts: dict[int, float], report: DetectionReport, now
) -> None:
    horizon = settings.overflow_alert_horizon_hours

    for bin_obj in bins:
        hours = forecasts.get(bin_obj.id)
        if hours is None or hours > horizon:
            continue
        # Already-overflowing bins have their own, louder alert; raising both
        # would double-count the same bin in the dashboard.
        if bin_obj.current_fill_level >= settings.bin_critical_threshold:
            continue

        created = raise_alert(
            db,
            alert_type=AlertType.BIN_OVERFLOW_IMMINENT,
            severity=AlertSeverity.WARNING,
            dedup_key=f"imminent:bin:{bin_obj.id}",
            title=f"{bin_obj.code} will overflow in {hours:.1f}h",
            message=(
                f"{bin_obj.code} is at {bin_obj.current_fill_level:.0f}% and is "
                f"forecast to reach capacity in {hours:.1f} hours."
            ),
            bin_id=bin_obj.id,
            zone_id=bin_obj.zone_id,
            details={
                "fill_level": bin_obj.current_fill_level,
                "hours_to_full": round(hours, 2),
                "horizon_hours": horizon,
            },
            now=now,
        )
        report.record(AlertType.BIN_OVERFLOW_IMMINENT) if created else _suppress(report)


def _recent_fill_rate(db: Session, bin_id: int, since: datetime) -> float | None:
    """Observed percentage-points per hour over the recent readings."""
    rows = db.execute(
        select(BinReading.recorded_at, BinReading.fill_level)
        .where(BinReading.bin_id == bin_id, BinReading.recorded_at >= since)
        .order_by(BinReading.recorded_at)
    ).all()

    if len(rows) < 2:
        return None

    first_time, first_fill = rows[0]
    last_time, last_fill = rows[-1]
    hours = (last_time - first_time).total_seconds() / 3600.0
    if hours <= 0:
        return None

    delta = last_fill - first_fill
    # A drop means a collection happened inside the window, which tells us
    # nothing about the generation rate.
    return delta / hours if delta >= 0 else None


def _detect_rapid_fill(db: Session, bins: list[Bin], report: DetectionReport, now) -> None:
    since = now - timedelta(hours=6)

    for bin_obj in bins:
        rate = _recent_fill_rate(db, bin_obj.id, since)
        if rate is None or rate < RAPID_FILL_PCT_PER_HOUR:
            continue

        created = raise_alert(
            db,
            alert_type=AlertType.HIGH_GENERATION_ANOMALY,
            severity=AlertSeverity.WARNING,
            dedup_key=f"rapidfill:bin:{bin_obj.id}",
            title=f"{bin_obj.code} is filling unusually fast",
            message=(
                f"{bin_obj.code} gained {rate:.1f} percentage points per hour over the "
                "last 6 hours, well above normal accumulation. Possible illegal "
                "dumping or a nearby event."
            ),
            bin_id=bin_obj.id,
            zone_id=bin_obj.zone_id,
            details={
                "observed_rate_pct_per_hour": round(rate, 2),
                "threshold_pct_per_hour": RAPID_FILL_PCT_PER_HOUR,
                "window_hours": 6,
            },
            anomaly_score=round(rate / RAPID_FILL_PCT_PER_HOUR, 3),
            now=now,
        )
        report.record(AlertType.HIGH_GENERATION_ANOMALY) if created else _suppress(report)


def _detect_zone_anomalies(db: Session, report: DetectionReport, now) -> None:
    """Flag zones generating far more than their own recent norm.

    Compared against each zone's own history rather than against other zones:
    a market ward is legitimately busier than a residential one, and a
    cross-zone comparison would alert on that difference forever.
    """
    since = now - timedelta(days=14)

    rows = db.execute(
        select(
            Bin.zone_id,
            func.date(BinReading.recorded_at).label("day"),
            func.avg(BinReading.fill_level),
        )
        .join(Bin, Bin.id == BinReading.bin_id)
        .where(BinReading.recorded_at >= since)
        .group_by(Bin.zone_id, "day")
    ).all()

    per_zone: dict[int, list[float]] = {}
    for zone_id, _, average in rows:
        per_zone.setdefault(zone_id, []).append(float(average or 0.0))

    for zone_id, series in per_zone.items():
        if len(series) < MIN_SAMPLES_FOR_ANOMALY:
            continue

        history, latest = series[:-1], series[-1]
        spread = pstdev(history)
        if spread <= 0:
            continue

        z_score = (latest - mean(history)) / spread
        if z_score < ANOMALY_Z_THRESHOLD:
            continue

        zone = db.get(Zone, zone_id)
        created = raise_alert(
            db,
            alert_type=AlertType.HIGH_GENERATION_ANOMALY,
            severity=AlertSeverity.WARNING,
            dedup_key=f"zoneanomaly:zone:{zone_id}",
            title=f"Unusually high waste generation in {zone.name if zone else zone_id}",
            message=(
                f"Average fill across this zone is {z_score:.1f} standard deviations "
                "above its own two-week norm. Consider an extra collection round."
            ),
            zone_id=zone_id,
            details={
                "z_score": round(z_score, 2),
                "latest_avg_fill": round(latest, 1),
                "baseline_avg_fill": round(mean(history), 1),
                "days_compared": len(history),
            },
            anomaly_score=round(z_score, 3),
            now=now,
        )
        report.record(AlertType.HIGH_GENERATION_ANOMALY) if created else _suppress(report)


def _detect_sensor_health(db: Session, bins: list[Bin], report: DetectionReport, now) -> None:
    stale_before = now - timedelta(hours=settings.sensor_stale_hours)

    for bin_obj in bins:
        if bin_obj.last_reading_at is not None and bin_obj.last_reading_at < stale_before:
            silent_hours = (now - bin_obj.last_reading_at).total_seconds() / 3600.0
            created = raise_alert(
                db,
                alert_type=AlertType.SENSOR_OFFLINE,
                severity=AlertSeverity.WARNING,
                dedup_key=f"offline:bin:{bin_obj.id}",
                title=f"Sensor on {bin_obj.code} has gone quiet",
                message=(
                    f"No telemetry from {bin_obj.code} for {silent_hours:.0f} hours. "
                    "Its fill level on the dashboard is stale."
                ),
                bin_id=bin_obj.id,
                zone_id=bin_obj.zone_id,
                details={
                    "last_reading_at": bin_obj.last_reading_at.isoformat(),
                    "silent_hours": round(silent_hours, 1),
                },
                now=now,
            )
            report.record(AlertType.SENSOR_OFFLINE) if created else _suppress(report)

        if bin_obj.battery_level is not None and bin_obj.battery_level <= LOW_BATTERY_PCT:
            created = raise_alert(
                db,
                alert_type=AlertType.BATTERY_LOW,
                severity=AlertSeverity.INFO,
                dedup_key=f"battery:bin:{bin_obj.id}",
                title=f"Sensor battery low on {bin_obj.code}",
                message=(
                    f"Battery at {bin_obj.battery_level:.0f}%. Schedule a swap before "
                    "the bin drops off the network."
                ),
                bin_id=bin_obj.id,
                zone_id=bin_obj.zone_id,
                details={"battery_level": bin_obj.battery_level},
                now=now,
            )
            report.record(AlertType.BATTERY_LOW) if created else _suppress(report)


def _suppress(report: DetectionReport) -> None:
    report.suppressed += 1


# ---------------------------------------------------------------------------
# Auto-resolution
# ---------------------------------------------------------------------------
def _auto_resolve(db: Session, bins: list[Bin], report: DetectionReport, now) -> None:
    """Close bin alerts whose condition has cleared.

    Without this the dashboard fills with alerts for bins that were emptied
    hours ago, and operators learn to ignore the alert list entirely.
    """
    by_id = {b.id: b for b in bins}

    open_alerts = list(
        db.scalars(
            select(Alert).where(
                Alert.status.in_(OPEN_STATUSES),
                Alert.bin_id.is_not(None),
            )
        )
    )

    for alert in open_alerts:
        bin_obj = by_id.get(alert.bin_id or -1)
        if bin_obj is None:
            continue

        cleared = False
        if alert.alert_type is AlertType.BIN_OVERFLOWING:
            cleared = bin_obj.current_fill_level < settings.bin_critical_threshold
        elif alert.alert_type is AlertType.BIN_OVERFLOW_IMMINENT:
            cleared = bin_obj.current_fill_level < _threshold_of(bin_obj) / 2
        elif alert.alert_type is AlertType.BATTERY_LOW:
            cleared = (bin_obj.battery_level or 0) > LOW_BATTERY_PCT
        elif alert.alert_type is AlertType.SENSOR_OFFLINE:
            cleared = (
                bin_obj.last_reading_at is not None
                and bin_obj.last_reading_at
                >= now - timedelta(hours=settings.sensor_stale_hours)
            )

        if cleared:
            alert.status = AlertStatus.RESOLVED
            alert.resolved_at = now
            alert.resolution_note = "Condition cleared automatically"
            report.auto_resolved += 1


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------
def _latest_forecasts(db: Session, bin_ids: list[int]) -> dict[int, float]:
    if not bin_ids:
        return {}

    rows = db.execute(
        select(FillPrediction.bin_id, FillPrediction.hours_to_full)
        .where(FillPrediction.bin_id.in_(bin_ids))
        .order_by(FillPrediction.bin_id, FillPrediction.generated_at.desc())
    ).all()

    latest: dict[int, float] = {}
    for bin_id, hours in rows:
        if bin_id not in latest and hours is not None:
            latest[bin_id] = hours
    return latest


def detect(
    db: Session, *, zone_id: int | None = None, now: datetime | None = None
) -> DetectionReport:
    """Run every rule across the network and persist what it finds."""
    now = now or datetime.now(timezone.utc)

    stmt = select(Bin).options(joinedload(Bin.zone)).where(Bin.status == BinStatus.ACTIVE)
    if zone_id is not None:
        stmt = stmt.where(Bin.zone_id == zone_id)
    bins = list(db.scalars(stmt))

    report = DetectionReport()
    if not bins:
        return report

    # Resolve first: a bin emptied since the last sweep should close its alert
    # rather than have a fresh duplicate suppressed against the stale one.
    _auto_resolve(db, bins, report, now)

    forecasts = _latest_forecasts(db, [b.id for b in bins])

    _detect_overflowing(db, bins, report, now)
    _detect_overflow_imminent(db, bins, forecasts, report, now)
    _detect_rapid_fill(db, bins, report, now)
    _detect_sensor_health(db, bins, report, now)
    _detect_zone_anomalies(db, report, now)

    db.commit()
    logger.info("Alert sweep: %s", report)
    return report


# ---------------------------------------------------------------------------
# Query and lifecycle
# ---------------------------------------------------------------------------
def list_alerts(
    db: Session,
    *,
    status: AlertStatus | None = None,
    severity: AlertSeverity | None = None,
    alert_type: AlertType | None = None,
    bin_id: int | None = None,
    zone_id: int | None = None,
    open_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[Alert]:
    stmt = select(Alert)

    if open_only:
        stmt = stmt.where(Alert.status.in_(OPEN_STATUSES))
    if status is not None:
        stmt = stmt.where(Alert.status == status)
    if severity is not None:
        stmt = stmt.where(Alert.severity == severity)
    if alert_type is not None:
        stmt = stmt.where(Alert.alert_type == alert_type)
    if bin_id is not None:
        stmt = stmt.where(Alert.bin_id == bin_id)
    if zone_id is not None:
        stmt = stmt.where(Alert.zone_id == zone_id)

    stmt = stmt.order_by(Alert.triggered_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt))


def get_alert(db: Session, alert_id: int) -> Alert:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise NotFoundError(f"Alert {alert_id} not found")
    return alert


def acknowledge(db: Session, alert_id: int, *, who: str) -> Alert:
    alert = get_alert(db, alert_id)
    if alert.status is not AlertStatus.OPEN:
        raise ValidationError(f"Only open alerts can be acknowledged (this one is {alert.status.value})")

    alert.status = AlertStatus.ACKNOWLEDGED
    alert.acknowledged_at = datetime.now(timezone.utc)
    alert.acknowledged_by = who
    db.commit()
    db.refresh(alert)
    return alert


def resolve(db: Session, alert_id: int, *, note: str | None = None) -> Alert:
    alert = get_alert(db, alert_id)
    if alert.status is AlertStatus.RESOLVED:
        raise ValidationError("Alert is already resolved")

    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = datetime.now(timezone.utc)
    alert.resolution_note = note
    db.commit()
    db.refresh(alert)
    return alert


def alert_summary(db: Session) -> dict:
    alerts = list(db.scalars(select(Alert)))
    open_alerts = [a for a in alerts if a.is_open]

    by_severity: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for alert in open_alerts:
        by_severity[alert.severity.value] = by_severity.get(alert.severity.value, 0) + 1
        by_type[alert.alert_type.value] = by_type.get(alert.alert_type.value, 0) + 1

    return {
        "total": len(alerts),
        "open": len(open_alerts),
        "critical_open": sum(
            1 for a in open_alerts if a.severity is AlertSeverity.CRITICAL
        ),
        "acknowledged": sum(
            1 for a in alerts if a.status is AlertStatus.ACKNOWLEDGED
        ),
        "resolved": sum(1 for a in alerts if a.status is AlertStatus.RESOLVED),
        "by_severity": by_severity,
        "by_type": by_type,
    }

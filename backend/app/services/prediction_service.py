"""Forecast generation, caching and accuracy scoring.

Forecasts are materialised into `fill_predictions` on a schedule instead of
being computed per request. The dashboard asks for the whole network at once, a
14-day forward simulation is not something to run inside an HTTP handler, and
storing each forecast lets us later compare it against what actually happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import median

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.ml.fill_prediction.dataset import BinContext, build_inference_contexts
from app.ml.fill_prediction.predictor import (
    DEFAULT_HORIZON_HOURS,
    MIN_MEANINGFUL_RATE,
    FillRatePredictor,
    Forecast,
    fallback_forecast,
)
from app.models.bin import Bin
from app.models.enums import PredictionMethod
from app.models.prediction import FillPrediction

logger = get_logger(__name__)

# A bin needs a bit of observed history before the model's rolling features mean
# anything; below this it is served by the fallback estimator instead.
MIN_HISTORY_HOURS_FOR_MODEL = 12.0


@dataclass
class RefreshReport:
    bins: int = 0
    model_forecasts: int = 0
    fallback_forecasts: int = 0
    insufficient_data: int = 0
    model_version: str | None = None

    def __str__(self) -> str:
        return (
            f"{self.bins} bins: {self.model_forecasts} from the model, "
            f"{self.fallback_forecasts} from fallbacks, "
            f"{self.insufficient_data} without enough history"
        )


def _zone_baselines(contexts: list[BinContext]) -> dict[str, float]:
    """Median observed rate per zone type, used as a last-resort estimate."""
    grouped: dict[str, list[float]] = {}
    for context in contexts:
        rate = context.rate_24h or context.rate_7d
        if rate and rate >= MIN_MEANINGFUL_RATE:
            grouped.setdefault(context.zone_type, []).append(rate)
    return {zone: median(rates) for zone, rates in grouped.items() if rates}


def _has_enough_history(context: BinContext) -> bool:
    return (
        context.rate_24h >= MIN_MEANINGFUL_RATE
        or context.rate_7d >= MIN_MEANINGFUL_RATE
        or context.hours_since_collection >= MIN_HISTORY_HOURS_FOR_MODEL
    )


def generate_forecasts(
    db: Session,
    *,
    zone_id: int | None = None,
    horizon_hours: int = DEFAULT_HORIZON_HOURS,
    now: datetime | None = None,
) -> list[Forecast]:
    """Forecast every active bin, routing each to the model or to a fallback."""
    contexts = build_inference_contexts(db, zone_id=zone_id)
    if not contexts:
        return []

    baselines = _zone_baselines(contexts)
    model_contexts = [c for c in contexts if _has_enough_history(c)]
    fallback_contexts = [c for c in contexts if not _has_enough_history(c)]

    forecasts: list[Forecast] = []

    if model_contexts and FillRatePredictor.available():
        predictor = FillRatePredictor.load()
        forecasts.extend(
            predictor.forecast(model_contexts, now=now, horizon_hours=horizon_hours)
        )
    else:
        # No trained artifact yet; everything degrades to the rolling estimate
        # rather than the API simply failing.
        fallback_contexts = contexts

    for context in fallback_contexts:
        forecasts.append(
            fallback_forecast(
                context, now=now, zone_baseline_rate=baselines.get(context.zone_type)
            )
        )

    return forecasts


def refresh_predictions(
    db: Session,
    *,
    zone_id: int | None = None,
    horizon_hours: int = DEFAULT_HORIZON_HOURS,
    keep_history: bool = True,
) -> RefreshReport:
    """Recompute and persist forecasts for the network."""
    now = datetime.now(timezone.utc)
    forecasts = generate_forecasts(db, zone_id=zone_id, horizon_hours=horizon_hours, now=now)

    report = RefreshReport(bins=len(forecasts))
    for forecast in forecasts:
        if forecast.method is PredictionMethod.GRADIENT_BOOSTING:
            report.model_forecasts += 1
            report.model_version = forecast.model_version
        elif forecast.method is PredictionMethod.INSUFFICIENT_DATA:
            report.insufficient_data += 1
        else:
            report.fallback_forecasts += 1

        db.add(
            FillPrediction(
                bin_id=forecast.bin_id,
                generated_at=now,
                fill_level_at_generation=forecast.fill_level,
                predicted_fill_rate_pct_per_hour=forecast.predicted_rate,
                hours_to_full=forecast.hours_to_full,
                predicted_full_at=forecast.predicted_full_at,
                hours_to_full_low=forecast.hours_to_full_low,
                hours_to_full_high=forecast.hours_to_full_high,
                method=forecast.method,
                model_version=forecast.model_version,
                features=forecast.features,
            )
        )

    if not keep_history:
        db.flush()
        db.execute(delete(FillPrediction).where(FillPrediction.generated_at < now))

    db.commit()
    logger.info("Prediction refresh: %s", report)
    return report


def latest_predictions(
    db: Session,
    *,
    zone_id: int | None = None,
    limit: int | None = None,
) -> list[FillPrediction]:
    """The most recent forecast for each bin, soonest to overflow first."""
    # Portable "latest row per bin": join back against the per-bin maximum
    # timestamp. PostgreSQL's DISTINCT ON would be marginally faster but would
    # not run against the SQLite test database.
    subquery = (
        select(
            FillPrediction.bin_id.label("bin_id"),
            func.max(FillPrediction.generated_at).label("generated_at"),
        )
        .group_by(FillPrediction.bin_id)
        .subquery()
    )

    stmt = select(FillPrediction).join(
        subquery,
        (FillPrediction.bin_id == subquery.c.bin_id)
        & (FillPrediction.generated_at == subquery.c.generated_at),
    )

    if zone_id is not None:
        stmt = stmt.join(Bin, Bin.id == FillPrediction.bin_id).where(Bin.zone_id == zone_id)

    # NULL hours_to_full means "not expected to fill within the horizon", which
    # belongs at the bottom of an urgency-ordered list, not the top.
    stmt = stmt.order_by(
        FillPrediction.hours_to_full.is_(None),
        FillPrediction.hours_to_full.asc(),
    )
    if limit:
        stmt = stmt.limit(limit)

    return list(db.scalars(stmt))


def get_prediction(db: Session, bin_id: int) -> FillPrediction:
    if db.get(Bin, bin_id) is None:
        raise NotFoundError(f"Bin {bin_id} not found")

    prediction = db.scalar(
        select(FillPrediction)
        .where(FillPrediction.bin_id == bin_id)
        .order_by(FillPrediction.generated_at.desc())
        .limit(1)
    )
    if prediction is None:
        raise NotFoundError(
            f"No forecast stored for bin {bin_id}; run a prediction refresh first"
        )
    return prediction


def score_past_predictions(db: Session, *, lookback_days: int = 7) -> int:
    """Compare stored forecasts against what actually happened.

    Turns `fill_predictions` into a live accuracy scoreboard: for every forecast
    whose predicted overflow time has passed, find when the bin genuinely
    reached the critical threshold and record the error in hours.
    """
    from app.models.bin import BinReading

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=lookback_days)

    pending = list(
        db.scalars(
            select(FillPrediction).where(
                FillPrediction.actual_full_at.is_(None),
                FillPrediction.predicted_full_at.is_not(None),
                FillPrediction.generated_at >= window_start,
                FillPrediction.predicted_full_at <= now,
            )
        )
    )

    scored = 0
    for prediction in pending:
        actual = db.scalar(
            select(BinReading.recorded_at)
            .where(
                BinReading.bin_id == prediction.bin_id,
                BinReading.recorded_at > prediction.generated_at,
                BinReading.fill_level >= settings.bin_critical_threshold,
            )
            .order_by(BinReading.recorded_at.asc())
            .limit(1)
        )
        if actual is None:
            continue

        prediction.actual_full_at = actual
        prediction.absolute_error_hours = round(
            abs((actual - prediction.predicted_full_at).total_seconds()) / 3600.0, 3
        )
        scored += 1

    db.commit()
    logger.info("Scored %d past forecasts against actual overflows", scored)
    return scored

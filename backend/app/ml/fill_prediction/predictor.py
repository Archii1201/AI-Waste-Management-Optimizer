"""Inference: turning the learned fill rate into "when will this bin be full?".

The model predicts an *instantaneous* rate for the conditions at one moment.
Answering "when will it overflow" therefore means stepping forward hour by hour,
re-predicting at each step because the hour of day and the day of week change as
the forecast advances. A bin that is 70% full at 9pm fills very differently over
the next eight hours than one that is 70% full at 9am, and a single-shot
multiplication would miss that entirely.

Every step is predicted for all bins at once, so a 14-day horizon across the
whole network is a few hundred batched calls rather than tens of thousands of
individual ones.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from app.core.config import settings
from app.core.exceptions import ModelNotTrainedError
from app.core.logging import get_logger
from app.ml.fill_prediction.dataset import BinContext
from app.ml.fill_prediction.features import as_model_frame, build_feature_row
from app.ml.fill_prediction.train import artifact_file
from app.models.enums import PredictionMethod

logger = get_logger(__name__)

DEFAULT_HORIZON_HOURS = 24 * 14
STEP_HOURS = 1.0

# Below this, a bin is effectively not filling and any hours-to-full figure
# would be noise dressed up as a forecast.
MIN_MEANINGFUL_RATE = 0.02


@dataclass
class Forecast:
    bin_id: int
    fill_level: float
    predicted_rate: float
    hours_to_full: float | None
    predicted_full_at: datetime | None
    hours_to_full_low: float | None
    hours_to_full_high: float | None
    method: PredictionMethod
    model_version: str | None
    features: dict[str, Any] = field(default_factory=dict)


class FillRatePredictor:
    """Loads the trained artifact once and serves forecasts from it."""

    _lock = threading.Lock()
    _instance: "FillRatePredictor | None" = None

    def __init__(self, artifact: dict[str, Any]) -> None:
        self.model = artifact["model"]
        self.model_version: str = artifact["model_version"]
        self.trained_at: str = artifact["trained_at"]
        self.metrics: dict[str, float] = artifact.get("metrics", {})
        self.residual_quantiles: dict[str, float] = artifact.get("residual_quantiles", {})
        self.rows_train: int = artifact.get("rows_train", 0)
        self.rows_validation: int = artifact.get("rows_validation", 0)

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, *, refresh: bool = False) -> "FillRatePredictor":
        with cls._lock:
            if cls._instance is not None and not refresh:
                return cls._instance

            path: Path = artifact_file()
            if not path.exists():
                raise ModelNotTrainedError(
                    "No trained fill-rate model found. Run `python -m app.cli train-fill-model`.",
                    details={"expected_artifact": str(path)},
                )
            cls._instance = cls(joblib.load(path))
            logger.info("Loaded fill-rate model %s", cls._instance.model_version)
            return cls._instance

    @classmethod
    def available(cls) -> bool:
        return artifact_file().exists()

    @classmethod
    def reset_cache(cls) -> None:
        with cls._lock:
            cls._instance = None

    # ------------------------------------------------------------------
    def predict_rates(
        self,
        contexts: list[BinContext],
        moments: list[datetime],
        fill_levels: np.ndarray,
        hours_since_collection: np.ndarray,
    ) -> np.ndarray:
        rows = [
            build_feature_row(
                moment=moments[index],
                fill_level=float(fill_levels[index]),
                capacity_liters=context.capacity_liters,
                zone_type=context.zone_type,
                waste_type=context.waste_type,
                hours_since_collection=float(hours_since_collection[index]),
                rate_24h=context.rate_24h,
                rate_7d=context.rate_7d,
            )
            for index, context in enumerate(contexts)
        ]
        return np.clip(self.model.predict(as_model_frame(rows)), 0.0, None)

    # ------------------------------------------------------------------
    def _simulate(
        self,
        contexts: list[BinContext],
        start: datetime,
        horizon_hours: int,
        rate_adjustment: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Step the whole fleet forward, returning (hours_to_full, first_rate).

        `rate_adjustment` shifts every predicted rate by a residual quantile,
        which is how the optimistic and pessimistic bounds are produced.
        """
        count = len(contexts)
        fill = np.array([c.fill_level for c in contexts], dtype=float)
        since_collection = np.array(
            [c.hours_since_collection for c in contexts], dtype=float
        )
        hours_to_full = np.full(count, np.nan)
        first_rate = np.zeros(count)

        moment = start
        for step in range(int(horizon_hours / STEP_HOURS)):
            pending = np.isnan(hours_to_full) & (fill < 100.0)
            if not pending.any():
                break

            rates = self.predict_rates(
                contexts, [moment] * count, fill, since_collection
            )
            rates = np.clip(rates + rate_adjustment, 0.0, None)
            if step == 0:
                first_rate = rates.copy()

            previous_fill = fill.copy()
            fill = np.where(pending, np.minimum(100.0, fill + rates * STEP_HOURS), fill)

            # Interpolate inside the hour the bin crosses 100%, rather than
            # rounding every forecast up to a whole hour.
            crossed = pending & (fill >= 100.0) & (rates > 0)
            if crossed.any():
                fraction = (100.0 - previous_fill[crossed]) / rates[crossed]
                hours_to_full[crossed] = step * STEP_HOURS + fraction

            since_collection += STEP_HOURS
            moment += timedelta(hours=STEP_HOURS)

        return hours_to_full, first_rate

    # ------------------------------------------------------------------
    def forecast(
        self,
        contexts: list[BinContext],
        *,
        now: datetime | None = None,
        horizon_hours: int = DEFAULT_HORIZON_HOURS,
    ) -> list[Forecast]:
        if not contexts:
            return []

        start = now or datetime.now(timezone.utc)
        hours, rates = self._simulate(contexts, start, horizon_hours, 0.0)

        # A faster rate means the bin fills sooner, so the upper residual
        # quantile produces the lower (most urgent) bound.
        p10 = self.residual_quantiles.get("p10", 0.0)
        p90 = self.residual_quantiles.get("p90", 0.0)
        hours_low, _ = self._simulate(contexts, start, horizon_hours, p90)
        hours_high, _ = self._simulate(contexts, start, horizon_hours, p10)

        forecasts: list[Forecast] = []
        for index, context in enumerate(contexts):
            hours_to_full = None if np.isnan(hours[index]) else float(hours[index])
            forecasts.append(
                Forecast(
                    bin_id=context.bin_id,
                    fill_level=context.fill_level,
                    predicted_rate=float(rates[index]),
                    hours_to_full=hours_to_full,
                    predicted_full_at=(
                        start + timedelta(hours=hours_to_full)
                        if hours_to_full is not None
                        else None
                    ),
                    hours_to_full_low=_optional(hours_low[index]),
                    hours_to_full_high=_optional(hours_high[index]),
                    method=PredictionMethod.GRADIENT_BOOSTING,
                    model_version=self.model_version,
                    features={
                        "fill_level": context.fill_level,
                        "hours_since_collection": round(context.hours_since_collection, 2),
                        "rate_24h": round(context.rate_24h, 4),
                        "rate_7d": round(context.rate_7d, 4),
                        "zone_type": context.zone_type,
                        "waste_type": context.waste_type,
                    },
                )
            )
        return forecasts


def _optional(value: float) -> float | None:
    return None if np.isnan(value) else float(value)


def fallback_forecast(
    context: BinContext,
    *,
    now: datetime | None = None,
    zone_baseline_rate: float | None = None,
) -> Forecast:
    """Forecast for a bin the model cannot serve.

    A newly installed bin has no history to condition on. Rather than refusing
    to answer or silently guessing, fall back through progressively weaker
    estimates and report which one was used, so the dashboard can show honest
    confidence.
    """
    start = now or datetime.now(timezone.utc)

    rate = context.rate_24h or context.rate_7d or 0.0
    method = PredictionMethod.ROLLING_MEDIAN

    if rate < MIN_MEANINGFUL_RATE and zone_baseline_rate:
        rate = zone_baseline_rate
        method = PredictionMethod.ZONE_BASELINE

    if rate < MIN_MEANINGFUL_RATE:
        return Forecast(
            bin_id=context.bin_id,
            fill_level=context.fill_level,
            predicted_rate=0.0,
            hours_to_full=None,
            predicted_full_at=None,
            hours_to_full_low=None,
            hours_to_full_high=None,
            method=PredictionMethod.INSUFFICIENT_DATA,
            model_version=None,
            features={"reason": "no usable fill history for this bin"},
        )

    remaining = max(0.0, 100.0 - context.fill_level)
    hours_to_full = remaining / rate

    return Forecast(
        bin_id=context.bin_id,
        fill_level=context.fill_level,
        predicted_rate=rate,
        hours_to_full=hours_to_full,
        predicted_full_at=start + timedelta(hours=hours_to_full),
        # A flat-rate estimate deserves a wide band, not false precision.
        hours_to_full_low=hours_to_full * 0.6,
        hours_to_full_high=hours_to_full * 1.8,
        method=method,
        model_version=None,
        features={"rate_source": method.value, "rate": round(rate, 4)},
    )

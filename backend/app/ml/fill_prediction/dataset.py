"""Turns raw bin readings into supervised training rows.

Two rules govern everything here:

1. **No leakage.** Every rolling feature is computed from readings strictly
   *before* the row it describes. A rolling window that includes the current
   reading would let the model see part of its own answer and produce
   validation scores that collapse in production.
2. **Collections are not negative generation.** A drop in fill level means a
   truck came, not that waste vanished. Those intervals are excluded from the
   target and used instead to reset `hours_since_collection`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.logging import get_logger
from app.ml.fill_prediction.features import TARGET_COLUMN, build_feature_row
from app.models.bin import Bin, BinReading
from app.models.enums import BinStatus

logger = get_logger(__name__)

# Intervals shorter than this are dominated by sensor quantisation noise; longer
# ones span too much unobserved behaviour to attribute to a single rate.
MIN_INTERVAL_HOURS = 0.08
MAX_INTERVAL_HOURS = 3.0

# Physically implausible rates are sensor faults, not waste generation.
MAX_PLAUSIBLE_RATE = 60.0

MIN_READINGS_PER_BIN = 24


@dataclass
class BinContext:
    """Static bin attributes plus the causal rolling state at a point in time."""

    bin_id: int
    bin_code: str
    zone_type: str
    waste_type: str
    capacity_liters: float
    fill_level: float
    hours_since_collection: float
    rate_24h: float
    rate_7d: float
    last_reading_at: datetime | None


def _load_readings(db: Session, bin_ids: list[int], since: datetime | None) -> pd.DataFrame:
    stmt = select(
        BinReading.bin_id, BinReading.recorded_at, BinReading.fill_level
    ).where(BinReading.bin_id.in_(bin_ids))
    if since is not None:
        stmt = stmt.where(BinReading.recorded_at >= since)

    rows = db.execute(stmt.order_by(BinReading.bin_id, BinReading.recorded_at)).all()
    return pd.DataFrame(rows, columns=["bin_id", "recorded_at", "fill_level"])


def _causal_mean(times: np.ndarray, rates: np.ndarray, index: int, window_hours: float) -> float:
    """Mean of the observed rates inside the trailing window, excluding `index`.

    Excluding the current row is what keeps the feature causal.
    """
    if index == 0:
        return 0.0
    # `index` may sit one past the end: inference anchors the window on "now",
    # which is just after the most recent reading.
    anchor = times[index] if index < times.size else times[-1]
    cutoff = anchor - window_hours
    start = np.searchsorted(times[:index], cutoff, side="left")
    window = rates[start:index]
    window = window[~np.isnan(window)]
    return float(window.mean()) if window.size else 0.0


def _per_bin_series(group: pd.DataFrame) -> dict[str, np.ndarray]:
    """Derive intervals, observed rates and collection markers for one bin."""
    times = pd.to_datetime(group["recorded_at"], utc=True)
    fills = group["fill_level"].to_numpy(dtype=float)

    # Hours since the first reading, as a float axis for window arithmetic.
    # Timezone-aware timestamps do not survive a plain `to_numpy()` subtraction,
    # so the delta is taken in pandas before dropping to NumPy.
    hours_axis = (times - times.iloc[0]).dt.total_seconds().to_numpy() / 3600.0

    delta_hours = np.diff(hours_axis, prepend=np.nan)
    delta_fill = np.diff(fills, prepend=np.nan)

    with np.errstate(invalid="ignore", divide="ignore"):
        rates = delta_fill / delta_hours

    usable = (
        (delta_hours >= MIN_INTERVAL_HOURS)
        & (delta_hours <= MAX_INTERVAL_HOURS)
        & (delta_fill >= 0)
        & (rates <= MAX_PLAUSIBLE_RATE)
    )
    rates = np.where(usable, rates, np.nan)

    is_collection = delta_fill <= -settings.collection_drop_threshold_pct

    return {
        "hours_axis": hours_axis,
        "fills": fills,
        "rates": rates,
        "usable": usable,
        "is_collection": is_collection,
    }


def _hours_since_collection(series: dict[str, np.ndarray]) -> np.ndarray:
    """Hours elapsed since this bin was last emptied, at each reading."""
    hours_axis = series["hours_axis"]
    is_collection = series["is_collection"]

    result = np.empty_like(hours_axis)
    last_collection = 0.0  # assume the window starts just after a collection
    for index in range(hours_axis.size):
        if is_collection[index]:
            last_collection = hours_axis[index]
        result[index] = hours_axis[index] - last_collection
    return result


def build_training_frame(
    db: Session,
    *,
    days: int | None = None,
    zone_id: int | None = None,
) -> pd.DataFrame:
    """Assemble the supervised dataset.

    Each row describes the state of a bin at one reading, with the target being
    the fill rate observed over the interval that *follows* it.
    """
    stmt = select(Bin).options(joinedload(Bin.zone)).where(Bin.status == BinStatus.ACTIVE)
    if zone_id is not None:
        stmt = stmt.where(Bin.zone_id == zone_id)
    bins = {b.id: b for b in db.scalars(stmt)}
    if not bins:
        return pd.DataFrame(columns=[*[], TARGET_COLUMN])

    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    readings = _load_readings(db, list(bins), since)
    if readings.empty:
        return pd.DataFrame(columns=[TARGET_COLUMN])

    readings["recorded_at"] = pd.to_datetime(readings["recorded_at"], utc=True)

    rows: list[dict] = []
    for bin_id, group in readings.groupby("bin_id", sort=False):
        if len(group) < MIN_READINGS_PER_BIN:
            continue

        bin_obj = bins[bin_id]
        series = _per_bin_series(group.reset_index(drop=True))
        since_collection = _hours_since_collection(series)
        hours_axis = series["hours_axis"]
        rates = series["rates"]
        timestamps = group["recorded_at"].tolist()

        # Row i is described by the state at reading i and targets the rate
        # observed between reading i and reading i+1.
        for index in range(len(group) - 1):
            target = rates[index + 1]
            if np.isnan(target):
                continue

            row = build_feature_row(
                moment=timestamps[index],
                fill_level=series["fills"][index],
                capacity_liters=bin_obj.capacity_liters,
                zone_type=bin_obj.zone.zone_type,
                waste_type=bin_obj.waste_type,
                hours_since_collection=since_collection[index],
                rate_24h=_causal_mean(hours_axis, rates, index, 24.0),
                rate_7d=_causal_mean(hours_axis, rates, index, 24.0 * 7),
            )
            row[TARGET_COLUMN] = float(target)
            row["bin_id"] = bin_id
            row["recorded_at"] = timestamps[index]
            rows.append(row)

    frame = pd.DataFrame(rows)
    logger.info(
        "Built training frame: %d rows from %d bins", len(frame), readings["bin_id"].nunique()
    )
    return frame


def build_inference_contexts(
    db: Session, *, zone_id: int | None = None
) -> list[BinContext]:
    """Current state and rolling features for every active bin, for forecasting."""
    stmt = select(Bin).options(joinedload(Bin.zone)).where(Bin.status == BinStatus.ACTIVE)
    if zone_id is not None:
        stmt = stmt.where(Bin.zone_id == zone_id)
    bins = {b.id: b for b in db.scalars(stmt)}
    if not bins:
        return []

    window_start = datetime.now(timezone.utc) - timedelta(
        days=settings.fill_rate_window_days
    )
    readings = _load_readings(db, list(bins), window_start)
    if not readings.empty:
        readings["recorded_at"] = pd.to_datetime(readings["recorded_at"], utc=True)

    grouped = (
        {bin_id: group.reset_index(drop=True) for bin_id, group in readings.groupby("bin_id")}
        if not readings.empty
        else {}
    )

    contexts: list[BinContext] = []
    for bin_id, bin_obj in bins.items():
        group = grouped.get(bin_id)

        if group is None or len(group) < 2:
            contexts.append(
                BinContext(
                    bin_id=bin_id,
                    bin_code=bin_obj.code,
                    zone_type=bin_obj.zone.zone_type.value,
                    waste_type=bin_obj.waste_type.value,
                    capacity_liters=bin_obj.capacity_liters,
                    fill_level=bin_obj.current_fill_level,
                    hours_since_collection=0.0,
                    rate_24h=bin_obj.avg_fill_rate_pct_per_hour or 0.0,
                    rate_7d=bin_obj.avg_fill_rate_pct_per_hour or 0.0,
                    last_reading_at=bin_obj.last_reading_at,
                )
            )
            continue

        series = _per_bin_series(group)
        since_collection = _hours_since_collection(series)
        last = len(group) - 1

        contexts.append(
            BinContext(
                bin_id=bin_id,
                bin_code=bin_obj.code,
                zone_type=bin_obj.zone.zone_type.value,
                waste_type=bin_obj.waste_type.value,
                capacity_liters=bin_obj.capacity_liters,
                fill_level=bin_obj.current_fill_level,
                hours_since_collection=float(since_collection[last]),
                # `last + 1` includes the final observed interval, which is
                # legitimate at inference time: it is already in the past.
                rate_24h=_causal_mean(series["hours_axis"], series["rates"], last + 1, 24.0),
                rate_7d=_causal_mean(
                    series["hours_axis"], series["rates"], last + 1, 24.0 * 7
                ),
                last_reading_at=bin_obj.last_reading_at,
            )
        )

    return contexts

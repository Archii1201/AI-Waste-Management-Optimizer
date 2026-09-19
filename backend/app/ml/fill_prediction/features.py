"""Feature definitions for the fill-rate model.

Training and inference build their feature rows through this one module. If the
two ever constructed features independently they would drift, and the model
would silently degrade in production while still scoring well offline.

The target is the **hourly fill rate**, not the fill percentage. Predicting the
percentage directly is fragile because it resets to zero at every collection, so
a model would have to learn the collection schedule to be right. Predicting the
rate and integrating it forward stays correct immediately after a collection,
which is exactly when a naive model is most wrong.
"""

from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.core.config import settings
from app.models.enums import WasteType, ZoneType

CITY_TZ = ZoneInfo(settings.city_timezone)

TARGET_COLUMN = "target_rate"

# Cyclic encodings rather than a raw hour integer: hour 23 and hour 0 are
# adjacent in reality, and a tree splitting on a raw integer cannot express that.
NUMERIC_FEATURES = [
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "is_weekend",
    "fill_level",
    "capacity_liters",
    "hours_since_collection",
    "rate_24h",
    "rate_7d",
]

CATEGORICAL_FEATURES = ["zone_type", "waste_type"]

FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES

ZONE_TYPE_CATEGORIES = [z.value for z in ZoneType]
WASTE_TYPE_CATEGORIES = [w.value for w in WasteType]


def local_time_parts(moment: datetime) -> tuple[int, int]:
    """Hour and weekday in city-local time.

    Waste generation follows human routine, so a bin's rhythm must be measured
    where the people are, not in UTC.
    """
    local = moment.astimezone(CITY_TZ)
    return local.hour, local.weekday()


def cyclic(value: float, period: float) -> tuple[float, float]:
    angle = 2.0 * math.pi * value / period
    return math.sin(angle), math.cos(angle)


def encode_time(moment: datetime) -> dict[str, float]:
    hour, weekday = local_time_parts(moment)
    hour_sin, hour_cos = cyclic(hour, 24.0)
    dow_sin, dow_cos = cyclic(weekday, 7.0)
    return {
        "hour_sin": hour_sin,
        "hour_cos": hour_cos,
        "dow_sin": dow_sin,
        "dow_cos": dow_cos,
        "is_weekend": 1.0 if weekday >= 5 else 0.0,
    }


def build_feature_row(
    *,
    moment: datetime,
    fill_level: float,
    capacity_liters: float,
    zone_type: ZoneType | str,
    waste_type: WasteType | str,
    hours_since_collection: float,
    rate_24h: float,
    rate_7d: float,
) -> dict[str, float | str]:
    """One feature row, used identically by the dataset builder and the predictor."""
    row: dict[str, float | str] = dict(encode_time(moment))
    row["fill_level"] = float(fill_level)
    row["capacity_liters"] = float(capacity_liters)
    row["hours_since_collection"] = float(hours_since_collection)
    row["rate_24h"] = float(rate_24h)
    row["rate_7d"] = float(rate_7d)
    row["zone_type"] = zone_type.value if isinstance(zone_type, ZoneType) else str(zone_type)
    row["waste_type"] = waste_type.value if isinstance(waste_type, WasteType) else str(waste_type)
    return row


def as_model_frame(rows: list[dict] | pd.DataFrame) -> pd.DataFrame:
    """Coerce feature rows into the exact column order and dtypes the model expects.

    Categories are pinned to the full enum list rather than inferred from the
    data. Inferring them would give a single-row inference frame a different
    encoding than training used, which is a classic source of silently wrong
    predictions.
    """
    frame = pd.DataFrame(rows) if not isinstance(rows, pd.DataFrame) else rows.copy()

    for column in NUMERIC_FEATURES:
        if column not in frame:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("float64")

    frame["zone_type"] = pd.Categorical(
        frame.get("zone_type", pd.Series(dtype=str)), categories=ZONE_TYPE_CATEGORIES
    )
    frame["waste_type"] = pd.Categorical(
        frame.get("waste_type", pd.Series(dtype=str)), categories=WASTE_TYPE_CATEGORIES
    )

    frame = frame[FEATURE_COLUMNS]
    return frame.replace([np.inf, -np.inf], np.nan)

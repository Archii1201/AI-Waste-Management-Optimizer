"""Training pipeline for the fill-rate model.

Model choice: `HistGradientBoostingRegressor`. The features are a mix of
numeric and categorical with strong non-linear interactions (a commercial bin
at noon on Saturday behaves nothing like the same bin at 3am on Tuesday), which
is exactly where gradient boosting on trees beats a linear model. It also
handles categoricals natively and trains in seconds on CPU, so retraining is
cheap enough to run on a schedule.

Loss: absolute error. The target contains genuine outliers, namely the event
spikes when someone dumps a shop's worth of stock into a street bin. Squared
error would chase those spikes and inflate every routine forecast; absolute
error fits the conditional median and stays robust.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.ml.fill_prediction.dataset import build_training_frame
from app.ml.fill_prediction.features import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    TARGET_COLUMN,
    as_model_frame,
)

logger = get_logger(__name__)

MIN_TRAINING_ROWS = 500
DEFAULT_VALIDATION_FRACTION = 0.2


@dataclass
class TrainingResult:
    model_version: str
    artifact_path: Path
    rows_total: int
    rows_train: int
    rows_validation: int
    metrics: dict[str, float] = field(default_factory=dict)
    residual_quantiles: dict[str, float] = field(default_factory=dict)
    feature_importance: dict[str, float] = field(default_factory=dict)

    def __str__(self) -> str:
        return (
            f"{self.model_version}: trained on {self.rows_train:,} rows, "
            f"validated on {self.rows_validation:,}. "
            f"MAE {self.metrics.get('val_mae', float('nan')):.4f} pp/h, "
            f"R2 {self.metrics.get('val_r2', float('nan')):.3f}"
        )


def artifact_file() -> Path:
    return settings.artifact_path / f"{settings.fill_model_name}.joblib"


def metrics_file() -> Path:
    return settings.artifact_path / f"{settings.fill_model_name}.metrics.json"


def _time_split(frame: pd.DataFrame, validation_fraction: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split chronologically, never randomly.

    A random split would put a bin's Tuesday-evening reading in training and its
    Tuesday-9pm reading in validation. The model would effectively interpolate
    between neighbouring samples and report an accuracy it cannot reproduce on
    genuinely unseen future data.
    """
    ordered = frame.sort_values("recorded_at").reset_index(drop=True)
    cutoff = int(len(ordered) * (1.0 - validation_fraction))
    return ordered.iloc[:cutoff], ordered.iloc[cutoff:]


def _hours_to_full_error(frame: pd.DataFrame, predicted_rate: np.ndarray) -> float:
    """Mean absolute error expressed in hours-to-full, the number operators see.

    A rate error of 0.3 pp/h is meaningless to a dispatcher; "we were 2 hours out
    on when that bin would overflow" is not.
    """
    remaining = (100.0 - frame["fill_level"]).to_numpy(dtype=float)
    actual_rate = frame[TARGET_COLUMN].to_numpy(dtype=float)

    valid = (actual_rate > 0.05) & (predicted_rate > 0.05) & (remaining > 0)
    if not valid.any():
        return float("nan")

    actual_hours = remaining[valid] / actual_rate[valid]
    predicted_hours = remaining[valid] / predicted_rate[valid]

    # Clip to a fortnight: beyond that the comparison is dominated by bins that
    # are barely filling at all, where the hours figure is not meaningful.
    horizon = 24.0 * 14
    return float(
        np.mean(np.abs(np.clip(actual_hours, 0, horizon) - np.clip(predicted_hours, 0, horizon)))
    )


def train_fill_model(
    db: Session,
    *,
    days: int | None = None,
    validation_fraction: float = DEFAULT_VALIDATION_FRACTION,
    random_state: int = 42,
) -> TrainingResult:
    frame = build_training_frame(db, days=days)

    if len(frame) < MIN_TRAINING_ROWS:
        raise ValueError(
            f"Only {len(frame)} usable training rows; need at least {MIN_TRAINING_ROWS}. "
            "Generate more history first with `python -m app.cli generate-history`."
        )

    train_frame, validation_frame = _time_split(frame, validation_fraction)
    x_train = as_model_frame(train_frame)
    x_validation = as_model_frame(validation_frame)
    y_train = train_frame[TARGET_COLUMN].to_numpy(dtype=float)
    y_validation = validation_frame[TARGET_COLUMN].to_numpy(dtype=float)

    model = HistGradientBoostingRegressor(
        loss="absolute_error",
        learning_rate=0.08,
        max_iter=400,
        max_depth=None,
        max_leaf_nodes=31,
        min_samples_leaf=40,
        l2_regularization=1.0,
        categorical_features=CATEGORICAL_FEATURES,
        early_stopping=True,
        validation_fraction=0.15,
        n_iter_no_change=25,
        random_state=random_state,
    )
    model.fit(x_train, y_train)

    train_prediction = np.clip(model.predict(x_train), 0.0, None)
    validation_prediction = np.clip(model.predict(x_validation), 0.0, None)

    # Residuals drive the forecast interval shown in the UI.
    residuals = y_validation - validation_prediction
    residual_quantiles = {
        "p10": float(np.percentile(residuals, 10)),
        "p50": float(np.percentile(residuals, 50)),
        "p90": float(np.percentile(residuals, 90)),
    }

    metrics = {
        "train_mae": float(mean_absolute_error(y_train, train_prediction)),
        "val_mae": float(mean_absolute_error(y_validation, validation_prediction)),
        "val_r2": float(r2_score(y_validation, validation_prediction)),
        "val_median_abs_error": float(np.median(np.abs(residuals))),
        "val_hours_to_full_mae": _hours_to_full_error(validation_frame, validation_prediction),
        "baseline_mae_rolling_24h": float(
            mean_absolute_error(y_validation, validation_frame["rate_24h"].to_numpy(dtype=float))
        ),
        "target_mean": float(y_train.mean()),
        "iterations": int(model.n_iter_),
    }

    importance = _permutation_importance(model, x_validation, y_validation, random_state)

    model_version = f"{settings.fill_model_name}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    artifact = {
        "model": model,
        "model_version": model_version,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "feature_columns": FEATURE_COLUMNS,
        "categorical_features": CATEGORICAL_FEATURES,
        "metrics": metrics,
        "residual_quantiles": residual_quantiles,
        "rows_train": len(train_frame),
        "rows_validation": len(validation_frame),
    }

    path = artifact_file()
    joblib.dump(artifact, path)
    metrics_file().write_text(
        json.dumps(
            {
                "model_version": model_version,
                "trained_at": artifact["trained_at"],
                "rows_total": len(frame),
                "rows_train": len(train_frame),
                "rows_validation": len(validation_frame),
                "metrics": metrics,
                "residual_quantiles": residual_quantiles,
                "feature_importance": importance,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    result = TrainingResult(
        model_version=model_version,
        artifact_path=path,
        rows_total=len(frame),
        rows_train=len(train_frame),
        rows_validation=len(validation_frame),
        metrics=metrics,
        residual_quantiles=residual_quantiles,
        feature_importance=importance,
    )
    logger.info("Training complete: %s", result)
    return result


def _permutation_importance(
    model: Any, x_validation: pd.DataFrame, y_validation: np.ndarray, random_state: int
) -> dict[str, float]:
    """How much validation MAE worsens when each feature is shuffled.

    Reported so the model is defensible rather than a black box: if
    `hours_since_collection` dominates, that is a claim you can explain.
    """
    from sklearn.inspection import permutation_importance

    result = permutation_importance(
        model,
        x_validation,
        y_validation,
        scoring="neg_mean_absolute_error",
        n_repeats=3,
        random_state=random_state,
    )
    return {
        column: float(value)
        for column, value in sorted(
            zip(x_validation.columns, result.importances_mean),
            key=lambda pair: pair[1],
            reverse=True,
        )
    }

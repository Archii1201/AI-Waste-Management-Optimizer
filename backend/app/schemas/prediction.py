"""Fill-level forecast contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.core.config import settings
from app.models.enums import PredictionMethod


class FillPredictionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bin_id: int
    generated_at: datetime
    fill_level_at_generation: float
    predicted_fill_rate_pct_per_hour: float
    hours_to_full: float | None
    predicted_full_at: datetime | None
    hours_to_full_low: float | None
    hours_to_full_high: float | None
    method: PredictionMethod
    model_version: str | None
    features: dict[str, Any] | None
    actual_full_at: datetime | None
    absolute_error_hours: float | None

    @computed_field  # type: ignore[prop-decorator]
    def is_model_backed(self) -> bool:
        """False when a fallback estimator produced this, so the UI can say so."""
        return self.method is PredictionMethod.GRADIENT_BOOSTING

    @computed_field  # type: ignore[prop-decorator]
    def overflow_within_horizon(self) -> bool:
        """Whether this bin is expected to overflow inside the alerting window."""
        if self.hours_to_full is None:
            return False
        return self.hours_to_full <= settings.overflow_alert_horizon_hours

    @computed_field  # type: ignore[prop-decorator]
    def urgency(self) -> str:
        if self.hours_to_full is None:
            return "none"
        if self.hours_to_full <= 6:
            return "imminent"
        if self.hours_to_full <= settings.overflow_alert_horizon_hours:
            return "soon"
        if self.hours_to_full <= 48:
            return "planned"
        return "later"


class RefreshRequest(BaseModel):
    zone_id: int | None = None
    horizon_hours: int = Field(
        default=336, ge=1, le=720, description="How far ahead to simulate, in hours"
    )
    keep_history: bool = Field(
        default=True,
        description="Keep older forecasts so model accuracy can be scored later",
    )


class RefreshResponse(BaseModel):
    bins: int
    model_forecasts: int
    fallback_forecasts: int
    insufficient_data: int
    model_version: str | None


class ModelInfo(BaseModel):
    """Training provenance and accuracy, surfaced for the demo and for trust."""

    available: bool
    model_version: str | None = None
    trained_at: str | None = None
    rows_train: int | None = None
    rows_validation: int | None = None
    metrics: dict[str, float] | None = None
    residual_quantiles: dict[str, float] | None = None
    message: str | None = None


class AccuracyReport(BaseModel):
    scored_predictions: int
    mean_absolute_error_hours: float | None
    median_absolute_error_hours: float | None
    within_2_hours_pct: float | None
    within_6_hours_pct: float | None

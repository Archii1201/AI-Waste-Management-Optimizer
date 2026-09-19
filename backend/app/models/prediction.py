"""Cached fill-level forecasts produced by the prediction service.

Forecasts are materialised on a schedule instead of computed per request: the
dashboard asks for hundreds of bins at once, and a stored prediction also gives
us a historical record to score the model's accuracy against what really
happened.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import PredictionMethod
from app.models.types import UTCDateTime, bigint_pk, enum_type, json_type

if TYPE_CHECKING:
    from app.models.bin import Bin


class FillPrediction(Base):
    __tablename__ = "fill_predictions"
    __table_args__ = (
        Index("ix_fill_predictions_bin_generated", "bin_id", "generated_at"),
        Index("ix_fill_predictions_predicted_full_at", "predicted_full_at"),
    )

    id: Mapped[int] = mapped_column(bigint_pk(), primary_key=True)
    bin_id: Mapped[int] = mapped_column(
        ForeignKey("bins.id", ondelete="CASCADE"), nullable=False
    )

    generated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    fill_level_at_generation: Mapped[float] = mapped_column(Float, nullable=False)

    # The model's actual output: expected percentage-points gained per hour.
    predicted_fill_rate_pct_per_hour: Mapped[float] = mapped_column(Float, nullable=False)

    # Derived by integrating the rate forward from the current level.
    hours_to_full: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted_full_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    # Interval from the residual quantiles of the training set, so the UI can
    # show "full in 6-11 hours" rather than false precision.
    hours_to_full_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    hours_to_full_high: Mapped[float | None] = mapped_column(Float, nullable=True)

    method: Mapped[PredictionMethod] = mapped_column(
        enum_type(PredictionMethod), nullable=False, default=PredictionMethod.GRADIENT_BOOSTING
    )
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Feature vector fed to the model, kept for debugging surprising forecasts.
    features: Mapped[dict[str, Any] | None] = mapped_column(json_type(), nullable=True)

    # Filled in later by the evaluation job once the bin genuinely filled up,
    # which turns this table into a live model-accuracy scoreboard.
    actual_full_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    absolute_error_hours: Mapped[float | None] = mapped_column(Float, nullable=True)

    bin: Mapped["Bin"] = relationship()

    def __repr__(self) -> str:
        return f"<FillPrediction bin={self.bin_id} full in {self.hours_to_full}h ({self.method.value})>"

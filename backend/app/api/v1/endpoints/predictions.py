"""Fill-level forecast endpoints."""

from __future__ import annotations

from statistics import mean, median

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import ModelNotTrainedError
from app.ml.fill_prediction.predictor import FillRatePredictor
from app.models.prediction import FillPrediction
from app.schemas.prediction import (
    AccuracyReport,
    FillPredictionRead,
    ModelInfo,
    RefreshRequest,
    RefreshResponse,
)
from app.services import prediction_service

router = APIRouter(prefix="/predictions", tags=["predictions"])


@router.get("", response_model=list[FillPredictionRead], summary="Latest forecast per bin")
def list_predictions(
    zone_id: int | None = Query(None, description="Restrict to one zone"),
    urgent_only: bool = Query(
        False, description="Only bins expected to overflow within the alert horizon"
    ),
    limit: int | None = Query(None, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[FillPrediction]:
    """Most recent forecast for every bin, ordered by how soon it overflows."""
    predictions = prediction_service.latest_predictions(db, zone_id=zone_id, limit=limit)

    if urgent_only:
        predictions = [
            p
            for p in predictions
            if p.hours_to_full is not None
            and p.hours_to_full <= settings.overflow_alert_horizon_hours
        ]
    return predictions


@router.get("/model", response_model=ModelInfo, summary="Trained model provenance")
def model_info() -> ModelInfo:
    """Version, training size and validation metrics of the deployed model."""
    if not FillRatePredictor.available():
        return ModelInfo(
            available=False,
            message="No model trained yet. Run `python -m app.cli train-fill-model`.",
        )

    predictor = FillRatePredictor.load()
    return ModelInfo(
        available=True,
        model_version=predictor.model_version,
        trained_at=predictor.trained_at,
        rows_train=predictor.rows_train,
        rows_validation=predictor.rows_validation,
        metrics=predictor.metrics,
        residual_quantiles=predictor.residual_quantiles,
    )


@router.get(
    "/accuracy",
    response_model=AccuracyReport,
    summary="Live accuracy against observed overflows",
)
def accuracy(
    lookback_days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
) -> AccuracyReport:
    """How far off past forecasts turned out to be, in hours.

    Offline validation metrics can flatter a model; this compares forecasts the
    system actually served against what the sensors later reported.
    """
    prediction_service.score_past_predictions(db, lookback_days=lookback_days)

    errors = list(
        db.scalars(
            select(FillPrediction.absolute_error_hours).where(
                FillPrediction.absolute_error_hours.is_not(None)
            )
        )
    )
    if not errors:
        return AccuracyReport(
            scored_predictions=0,
            mean_absolute_error_hours=None,
            median_absolute_error_hours=None,
            within_2_hours_pct=None,
            within_6_hours_pct=None,
        )

    total = len(errors)
    return AccuracyReport(
        scored_predictions=total,
        mean_absolute_error_hours=round(mean(errors), 2),
        median_absolute_error_hours=round(median(errors), 2),
        within_2_hours_pct=round(100.0 * sum(e <= 2 for e in errors) / total, 1),
        within_6_hours_pct=round(100.0 * sum(e <= 6 for e in errors) / total, 1),
    )


@router.post(
    "/refresh",
    response_model=RefreshResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Recompute forecasts for the network",
)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)) -> RefreshResponse:
    report = prediction_service.refresh_predictions(
        db,
        zone_id=payload.zone_id,
        horizon_hours=payload.horizon_hours,
        keep_history=payload.keep_history,
    )
    return RefreshResponse(
        bins=report.bins,
        model_forecasts=report.model_forecasts,
        fallback_forecasts=report.fallback_forecasts,
        insufficient_data=report.insufficient_data,
        model_version=report.model_version,
    )


@router.post(
    "/reload-model",
    response_model=ModelInfo,
    summary="Pick up a newly trained artifact without a restart",
)
def reload_model() -> ModelInfo:
    """Drops the cached model so the next forecast loads the latest artifact."""
    FillRatePredictor.reset_cache()
    if not FillRatePredictor.available():
        raise ModelNotTrainedError("No model artifact on disk to load.")
    return model_info()


@router.get(
    "/bins/{bin_id}",
    response_model=FillPredictionRead,
    summary="Latest forecast for one bin",
)
def bin_prediction(bin_id: int, db: Session = Depends(get_db)) -> FillPrediction:
    return prediction_service.get_prediction(db, bin_id)


@router.get(
    "/bins/{bin_id}/history",
    response_model=list[FillPredictionRead],
    summary="Forecast history for one bin",
)
def bin_prediction_history(
    bin_id: int,
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[FillPrediction]:
    """Successive forecasts for a bin, for plotting how the estimate converged."""
    return list(
        db.scalars(
            select(FillPrediction)
            .where(FillPrediction.bin_id == bin_id)
            .order_by(FillPrediction.generated_at.desc())
            .limit(limit)
        )
    )

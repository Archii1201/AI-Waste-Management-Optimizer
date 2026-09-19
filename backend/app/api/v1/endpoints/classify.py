"""Waste image classification endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import ModelNotTrainedError
from app.ml.classification.labels import CLASS_LABELS
from app.ml.classification.predictor import WasteClassifier
from app.models.classification import WasteClassification
from app.models.enums import WasteType
from app.schemas.classification import (
    ClassificationRead,
    ClassificationResponse,
    ClassifierInfo,
    ClassifierStats,
    ContaminationInfo,
    ReviewRequest,
)
from app.services import classification_service

router = APIRouter(prefix="/classify", tags=["classification"])


@router.get("/model", response_model=ClassifierInfo, summary="Trained classifier details")
def model_info() -> ClassifierInfo:
    """Version, accuracy and per-class scores of the deployed classifier."""
    if not WasteClassifier.available():
        return ClassifierInfo(
            available=False,
            class_labels=CLASS_LABELS,
            message="No classifier trained yet. Run `python -m app.cli train-classifier`.",
        )

    classifier = WasteClassifier.load()
    return ClassifierInfo(
        available=True,
        model_version=classifier.model_version,
        trained_at=classifier.trained_at,
        architecture="mobilenet_v3_small",
        class_labels=classifier.class_labels,
        image_size=classifier.image_size,
        temperature=round(classifier.temperature, 4),
        confidence_threshold=settings.classifier_confidence_threshold,
        metrics=classifier.metrics,
        per_class=classifier.per_class,
        confusion_matrix=classifier.confusion_matrix,
    )


@router.get("/stats", response_model=ClassifierStats, summary="Classification aggregates")
def stats(
    bin_id: int | None = Query(None, description="Restrict to one bin"),
    db: Session = Depends(get_db),
) -> ClassifierStats:
    """Counts by category, recyclable share, contamination rate and review queue."""
    return ClassifierStats(**classification_service.classification_stats(db, bin_id=bin_id))


@router.get("", response_model=list[ClassificationRead], summary="Browse classifications")
def list_classifications(
    bin_id: int | None = Query(None),
    predicted_class: WasteType | None = Query(None),
    needs_review: bool | None = Query(
        None, description="Filter the low-confidence review queue"
    ),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[WasteClassification]:
    return classification_service.list_classifications(
        db,
        bin_id=bin_id,
        predicted_class=predicted_class,
        needs_review=needs_review,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=ClassificationResponse, summary="Classify a waste photo")
def classify(
    file: UploadFile = File(..., description="JPEG, PNG or WebP image of the waste item"),
    bin_id: int | None = Form(
        None, description="Bin the photo was taken at; enables contamination checking"
    ),
    confidence_threshold: float | None = Form(
        None, description="Override the review threshold for this request"
    ),
    db: Session = Depends(get_db),
) -> ClassificationResponse:
    """Classify an uploaded image into one of the six waste categories.

    Supplying `bin_id` additionally checks the item against what that bin is
    meant to hold, which is how stream contamination is detected.
    """
    record, result, contamination = classification_service.classify_and_store(
        db,
        data=file.file.read(),
        filename=file.filename or "upload",
        bin_id=bin_id,
        confidence_threshold=confidence_threshold,
    )

    return ClassificationResponse(
        classification=ClassificationRead.model_validate(record),
        contamination=ContaminationInfo(
            checked=contamination.checked,
            is_contaminant=contamination.is_contaminant,
            expected_stream=contamination.expected_stream,
            message=contamination.message,
        ),
        runner_up=result.runner_up,
        margin=result.margin,
    )


@router.post(
    "/reload-model",
    response_model=ClassifierInfo,
    summary="Pick up a newly trained classifier without a restart",
)
def reload_model() -> ClassifierInfo:
    WasteClassifier.reset_cache()
    if not WasteClassifier.available():
        raise ModelNotTrainedError("No classifier artifact on disk to load.")
    return model_info()


@router.get(
    "/{classification_id}",
    response_model=ClassificationRead,
    summary="Fetch one classification",
)
def get_classification(
    classification_id: int, db: Session = Depends(get_db)
) -> WasteClassification:
    return classification_service.get_classification(db, classification_id)


@router.post(
    "/{classification_id}/review",
    response_model=ClassificationRead,
    summary="Correct a classification",
)
def review(
    classification_id: int,
    payload: ReviewRequest,
    db: Session = Depends(get_db),
) -> WasteClassification:
    """Record a human verdict, which also becomes training data for the next model."""
    return classification_service.review_classification(
        db,
        classification_id,
        corrected_class=payload.corrected_class,
        reviewer=payload.reviewer,
    )

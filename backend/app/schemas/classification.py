"""Waste classification contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.enums import WasteType


class ContaminationInfo(BaseModel):
    checked: bool
    is_contaminant: bool
    expected_stream: WasteType | None = None
    message: str | None = None


class ClassificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bin_id: int | None
    image_filename: str
    image_sha256: str | None
    predicted_class: WasteType
    confidence: float
    probabilities: dict[str, float] | None
    is_recyclable: bool
    needs_review: bool
    reviewed_class: WasteType | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    model_version: str | None
    inference_ms: float | None
    metadata_json: dict[str, Any] | None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    def effective_class(self) -> WasteType:
        """What the item actually is: the human correction when one exists."""
        return self.reviewed_class or self.predicted_class

    @computed_field  # type: ignore[prop-decorator]
    def model_was_correct(self) -> bool | None:
        """None until a human has reviewed it."""
        if self.reviewed_class is None:
            return None
        return self.reviewed_class == self.predicted_class


class ClassificationResponse(BaseModel):
    """What the upload endpoint returns: the stored row plus the live verdict."""

    classification: ClassificationRead
    contamination: ContaminationInfo
    runner_up: str | None = None
    margin: float = Field(
        default=0.0,
        description="Probability gap to the runner-up; a small gap means the "
        "model is torn between two categories",
    )


class ReviewRequest(BaseModel):
    corrected_class: WasteType
    reviewer: str = Field(min_length=1, max_length=120)


class ClassifierStats(BaseModel):
    total: int
    by_class: dict[str, int]
    recyclable_count: int
    recyclable_pct: float
    pending_review: int
    reviewed: int
    model_agreement_pct: float | None
    contamination_count: int
    contamination_pct: float
    mean_confidence: float | None


class ClassifierInfo(BaseModel):
    available: bool
    model_version: str | None = None
    trained_at: str | None = None
    architecture: str | None = None
    class_labels: list[str] | None = None
    image_size: int | None = None
    temperature: float | None = None
    confidence_threshold: float | None = None
    metrics: dict[str, float] | None = None
    per_class: dict[str, dict[str, float]] | None = None
    confusion_matrix: list[list[int]] | None = None
    message: str | None = None

"""Classification persistence, contamination detection and the review loop.

Every classification is stored rather than returned and forgotten, for three
reasons: the aggregate drives the recycling-efficiency analytics, low-confidence
rows form a review queue, and human corrections become labelled training data
for the next model — so the system improves by being used.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.ml.classification.labels import CLASS_LABELS
from app.ml.classification.predictor import Classification, WasteClassifier, decode_image
from app.models.bin import Bin
from app.models.classification import WasteClassification
from app.models.enums import WasteType

logger = get_logger(__name__)

EXTENSION_BY_FORMAT = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "BMP": ".bmp",
    "GIF": ".gif",
}


@dataclass
class ContaminationCheck:
    """Whether a photographed item belongs in the bin it was photographed at."""

    checked: bool
    is_contaminant: bool
    expected_stream: WasteType | None = None
    message: str | None = None


def _store_image(data: bytes, digest: str, image_format: str | None) -> Path:
    """Write the upload to disk, sharded by digest prefix.

    Flat directories with tens of thousands of files are slow to list and awkward
    to back up, so the first two hex characters become a subdirectory.
    """
    extension = EXTENSION_BY_FORMAT.get((image_format or "").upper(), ".img")
    directory = settings.upload_path / digest[:2]
    directory.mkdir(parents=True, exist_ok=True)

    path = directory / f"{digest}{extension}"
    if not path.exists():
        path.write_bytes(data)
    return path


def check_contamination(
    bin_obj: Bin | None, predicted: WasteType
) -> ContaminationCheck:
    """Compare what was photographed against what the bin is meant to hold.

    Only meaningful for segregated streams. A MIXED bin accepts everything, so
    flagging contamination there would generate noise rather than insight.
    """
    if bin_obj is None:
        return ContaminationCheck(checked=False, is_contaminant=False)

    expected = bin_obj.waste_type
    if expected is WasteType.MIXED:
        return ContaminationCheck(
            checked=False,
            is_contaminant=False,
            expected_stream=expected,
            message="Bin accepts mixed waste, so nothing counts as contamination",
        )

    if predicted is expected:
        return ContaminationCheck(
            checked=True, is_contaminant=False, expected_stream=expected
        )

    return ContaminationCheck(
        checked=True,
        is_contaminant=True,
        expected_stream=expected,
        message=(
            f"{predicted.value} item found in a {expected.value} bin; "
            "this contaminates the stream"
        ),
    )


def classify_and_store(
    db: Session,
    *,
    data: bytes,
    filename: str,
    bin_id: int | None = None,
    confidence_threshold: float | None = None,
) -> tuple[WasteClassification, Classification, ContaminationCheck]:
    """Classify an uploaded photo and persist the result."""
    if len(data) > settings.max_upload_bytes:
        raise ValidationError(
            f"Image is {len(data) / 1_048_576:.1f} MB; the limit is "
            f"{settings.max_upload_bytes / 1_048_576:.0f} MB"
        )

    bin_obj = None
    if bin_id is not None:
        bin_obj = db.get(Bin, bin_id)
        if bin_obj is None:
            raise NotFoundError(f"Bin {bin_id} not found")

    image = decode_image(data)
    digest = hashlib.sha256(data).hexdigest()
    threshold = (
        confidence_threshold
        if confidence_threshold is not None
        else settings.classifier_confidence_threshold
    )

    classifier = WasteClassifier.load()
    result = classifier.classify_image(image, confidence_threshold=threshold)
    contamination = check_contamination(bin_obj, result.predicted_class)

    record = WasteClassification(
        bin_id=bin_id,
        image_filename=filename,
        image_path=str(_store_image(data, digest, image.format)),
        image_sha256=digest,
        predicted_class=result.predicted_class,
        confidence=result.confidence,
        probabilities=result.probabilities,
        is_recyclable=result.is_recyclable,
        needs_review=result.needs_review,
        model_version=result.model_version,
        inference_ms=result.inference_ms,
        metadata_json={
            "runner_up": result.runner_up,
            "margin": result.margin,
            "confidence_threshold": threshold,
            "image_size": list(image.size),
            "contamination": {
                "checked": contamination.checked,
                "is_contaminant": contamination.is_contaminant,
                "expected_stream": (
                    contamination.expected_stream.value
                    if contamination.expected_stream
                    else None
                ),
            },
        },
        created_at=datetime.now(timezone.utc),
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    if contamination.is_contaminant:
        logger.info(
            "Contamination at bin %s: %s in a %s bin",
            bin_obj.code if bin_obj else bin_id,
            result.predicted_class.value,
            contamination.expected_stream.value if contamination.expected_stream else "?",
        )

    return record, result, contamination


def get_classification(db: Session, classification_id: int) -> WasteClassification:
    record = db.get(WasteClassification, classification_id)
    if record is None:
        raise NotFoundError(f"Classification {classification_id} not found")
    return record


def list_classifications(
    db: Session,
    *,
    bin_id: int | None = None,
    predicted_class: WasteType | None = None,
    needs_review: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[WasteClassification]:
    stmt = select(WasteClassification)

    if bin_id is not None:
        stmt = stmt.where(WasteClassification.bin_id == bin_id)
    if predicted_class is not None:
        stmt = stmt.where(WasteClassification.predicted_class == predicted_class)
    if needs_review is not None:
        stmt = stmt.where(WasteClassification.needs_review.is_(needs_review))

    stmt = stmt.order_by(WasteClassification.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(stmt))


def review_classification(
    db: Session,
    classification_id: int,
    *,
    corrected_class: WasteType,
    reviewer: str,
) -> WasteClassification:
    """Record a human correction.

    The original prediction is kept alongside the correction rather than being
    overwritten, because the disagreement between the two is the measurement of
    how good the model actually is in the field.
    """
    if corrected_class is WasteType.MIXED:
        raise ValidationError(
            "A photographed item belongs to a concrete category; "
            f"choose one of: {', '.join(CLASS_LABELS)}"
        )

    record = get_classification(db, classification_id)
    record.reviewed_class = corrected_class
    record.reviewed_by = reviewer
    record.reviewed_at = datetime.now(timezone.utc)
    record.is_recyclable = corrected_class.is_recyclable
    record.needs_review = False

    db.commit()
    db.refresh(record)
    return record


def classification_stats(db: Session, *, bin_id: int | None = None) -> dict:
    """Aggregates behind the recycling-efficiency view."""
    base = select(WasteClassification)
    if bin_id is not None:
        base = base.where(WasteClassification.bin_id == bin_id)

    records = list(db.scalars(base))
    total = len(records)

    if total == 0:
        return {
            "total": 0,
            "by_class": {label: 0 for label in CLASS_LABELS},
            "recyclable_count": 0,
            "recyclable_pct": 0.0,
            "pending_review": 0,
            "reviewed": 0,
            "model_agreement_pct": None,
            "contamination_count": 0,
            "contamination_pct": 0.0,
            "mean_confidence": None,
        }

    by_class = {label: 0 for label in CLASS_LABELS}
    for record in records:
        # Count the corrected label where a human has supplied one: the stats
        # should describe reality, not the model's opinion of it.
        by_class[record.effective_class.value] = (
            by_class.get(record.effective_class.value, 0) + 1
        )

    reviewed = [r for r in records if r.reviewed_class is not None]
    agreed = [r for r in reviewed if r.reviewed_class == r.predicted_class]
    recyclable = sum(1 for r in records if r.effective_class.is_recyclable)
    contaminants = sum(
        1
        for r in records
        if (r.metadata_json or {}).get("contamination", {}).get("is_contaminant")
    )
    checked = sum(
        1 for r in records if (r.metadata_json or {}).get("contamination", {}).get("checked")
    )

    return {
        "total": total,
        "by_class": by_class,
        "recyclable_count": recyclable,
        "recyclable_pct": round(100.0 * recyclable / total, 1),
        "pending_review": sum(1 for r in records if r.needs_review),
        "reviewed": len(reviewed),
        "model_agreement_pct": (
            round(100.0 * len(agreed) / len(reviewed), 1) if reviewed else None
        ),
        "contamination_count": contaminants,
        # Denominated by checks actually performed, not by every upload: mixed
        # bins are excluded from contamination checking entirely.
        "contamination_pct": round(100.0 * contaminants / checked, 1) if checked else 0.0,
        "mean_confidence": round(
            db.scalar(select(func.avg(WasteClassification.confidence))) or 0.0, 4
        ),
    }


def export_reviewed_images(db: Session, destination: Path) -> dict[str, int]:
    """Copy human-corrected images into a training-set layout.

    Closes the loop: the cases the model got wrong are exactly the examples the
    next training run most needs.
    """
    import shutil

    records = list(
        db.scalars(
            select(WasteClassification).where(WasteClassification.reviewed_class.is_not(None))
        )
    )

    exported: dict[str, int] = {}
    for record in records:
        if not record.image_path:
            continue
        source = Path(record.image_path)
        if not source.exists():
            continue

        label = record.reviewed_class.value  # type: ignore[union-attr]
        target_dir = destination / label
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target_dir / source.name)
        exported[label] = exported.get(label, 0) + 1

    logger.info("Exported %d reviewed images to %s", sum(exported.values()), destination)
    return exported

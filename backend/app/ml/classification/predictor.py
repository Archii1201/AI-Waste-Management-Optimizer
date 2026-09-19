"""Inference for the waste image classifier."""

from __future__ import annotations

import io
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import torch
from PIL import Image, UnidentifiedImageError

from app.core.exceptions import ModelNotTrainedError, ValidationError
from app.core.logging import get_logger
from app.ml.classification.dataset import build_transforms
from app.ml.classification.labels import CLASS_LABELS, label_to_waste_type
from app.ml.classification.model import build_model, select_device
from app.ml.classification.train import artifact_file, softmax
from app.models.enums import WasteType

logger = get_logger(__name__)


@dataclass
class Classification:
    predicted_class: WasteType
    confidence: float
    probabilities: dict[str, float]
    is_recyclable: bool
    needs_review: bool
    model_version: str
    inference_ms: float
    runner_up: str | None = None
    margin: float = 0.0
    metadata: dict = field(default_factory=dict)


class WasteClassifier:
    """Loads the trained network once and serves predictions from it."""

    _lock = threading.Lock()
    _instance: "WasteClassifier | None" = None

    def __init__(self, artifact: dict) -> None:
        self.model_version: str = artifact["model_version"]
        self.trained_at: str = artifact["trained_at"]
        self.class_labels: list[str] = artifact.get("class_labels", CLASS_LABELS)
        self.image_size: int = artifact.get("image_size", 224)
        self.temperature: float = artifact.get("temperature", 1.0)
        self.metrics: dict = artifact.get("metrics", {})
        self.per_class: dict = artifact.get("per_class", {})
        self.confusion_matrix: list[list[int]] = artifact.get("confusion_matrix", [])

        self.device = select_device()
        # The architecture is rebuilt unpretrained and then overwritten by the
        # saved weights, so loading never reaches for ImageNet weights over the
        # network on a machine that may be offline.
        model = build_model(pretrained=False)
        model.load_state_dict(artifact["state_dict"])
        model.eval().to(self.device)
        self.model = model

        self.transform = build_transforms(self.image_size, training=False)

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, *, refresh: bool = False) -> "WasteClassifier":
        with cls._lock:
            if cls._instance is not None and not refresh:
                return cls._instance

            path: Path = artifact_file()
            if not path.exists():
                raise ModelNotTrainedError(
                    "No trained waste classifier found. Run "
                    "`python -m app.cli train-classifier`.",
                    details={"expected_artifact": str(path)},
                )
            cls._instance = cls(torch.load(path, map_location="cpu", weights_only=False))
            logger.info("Loaded waste classifier %s", cls._instance.model_version)
            return cls._instance

    @classmethod
    def available(cls) -> bool:
        return artifact_file().exists()

    @classmethod
    def reset_cache(cls) -> None:
        with cls._lock:
            cls._instance = None

    # ------------------------------------------------------------------
    def _prepare(self, image: Image.Image) -> torch.Tensor:
        return self.transform(image.convert("RGB")).unsqueeze(0).to(self.device)

    def classify_image(
        self, image: Image.Image, *, confidence_threshold: float
    ) -> Classification:
        started = time.perf_counter()

        with torch.no_grad():
            logits = self.model(self._prepare(image)).cpu().numpy()

        # Temperature from calibration, so the confidence compared against the
        # review threshold reflects real-world hit rate.
        probabilities = softmax(logits, self.temperature)[0]
        order = np.argsort(probabilities)[::-1]
        top, second = int(order[0]), int(order[1]) if len(order) > 1 else None

        label = self.class_labels[top]
        waste_type = label_to_waste_type(label)
        confidence = float(probabilities[top])

        return Classification(
            predicted_class=waste_type,
            confidence=round(confidence, 4),
            probabilities={
                name: round(float(probabilities[index]), 4)
                for index, name in enumerate(self.class_labels)
            },
            is_recyclable=waste_type.is_recyclable,
            needs_review=confidence < confidence_threshold,
            model_version=self.model_version,
            inference_ms=round((time.perf_counter() - started) * 1000, 2),
            runner_up=self.class_labels[second] if second is not None else None,
            # A small margin means the model is torn between two categories,
            # which is a different kind of doubt than uniformly low confidence
            # and worth surfacing separately.
            margin=round(float(probabilities[top] - probabilities[second]), 4)
            if second is not None
            else 0.0,
        )


def decode_image(data: bytes) -> Image.Image:
    """Decode uploaded bytes, rejecting anything that is not a real image."""
    if not data:
        raise ValidationError("Uploaded file is empty")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValidationError(
            "Uploaded file is not a readable image (expected JPEG, PNG or WebP)"
        ) from exc
    return image

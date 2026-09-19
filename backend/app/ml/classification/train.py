"""Training loop for the waste image classifier.

Structure of a run:

1. **Warmup** — the backbone is frozen while the randomly initialised head
   learns. Sending gradients from a random head into pretrained features
   corrupts them, so the head is given a few epochs to become sensible first.
2. **Fine-tuning** — the backbone is unfrozen and trained at a much lower rate
   than the head, adapting ImageNet features to waste imagery.
3. **Calibration** — a single temperature parameter is fitted on the validation
   set so the reported confidence means something.

That last step matters more than it sounds. The API flags low-confidence
predictions for human review, and neural networks trained with cross-entropy are
systematically overconfident. Without calibration a "0.85 confidence" prediction
is wrong far more often than 15% of the time, so the review queue silently
misses the cases it exists to catch.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader

from app.core.config import settings
from app.core.logging import get_logger
from app.ml.classification.dataset import build_dataloaders, class_weights, describe
from app.ml.classification.labels import CLASS_LABELS, NUM_CLASSES
from app.ml.classification.model import (
    build_model,
    freeze_backbone,
    parameter_groups,
    select_device,
)

logger = get_logger(__name__)

DEFAULT_EPOCHS = 12
DEFAULT_WARMUP_EPOCHS = 2
DEFAULT_BATCH_SIZE = 32
LABEL_SMOOTHING = 0.1


@dataclass
class ClassifierTrainingResult:
    model_version: str
    artifact_path: Path
    epochs_run: int
    best_epoch: int
    class_counts: dict[str, int]
    metrics: dict[str, float] = field(default_factory=dict)
    per_class: dict[str, dict[str, float]] = field(default_factory=dict)
    confusion_matrix: list[list[int]] = field(default_factory=list)
    temperature: float = 1.0

    def __str__(self) -> str:
        return (
            f"{self.model_version}: accuracy {self.metrics.get('val_accuracy', 0):.3f}, "
            f"macro-F1 {self.metrics.get('val_macro_f1', 0):.3f} "
            f"(best epoch {self.best_epoch}/{self.epochs_run})"
        )


def artifact_file() -> Path:
    return settings.artifact_path / f"{settings.classifier_model_name}.pt"


def metrics_file() -> Path:
    return settings.artifact_path / f"{settings.classifier_model_name}.metrics.json"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def confusion(targets: np.ndarray, predictions: np.ndarray) -> np.ndarray:
    matrix = np.zeros((NUM_CLASSES, NUM_CLASSES), dtype=int)
    for actual, predicted in zip(targets, predictions):
        matrix[actual, predicted] += 1
    return matrix


def per_class_metrics(matrix: np.ndarray) -> dict[str, dict[str, float]]:
    results: dict[str, dict[str, float]] = {}
    for index, label in enumerate(CLASS_LABELS):
        true_positive = int(matrix[index, index])
        predicted_positive = int(matrix[:, index].sum())
        actual_positive = int(matrix[index, :].sum())

        precision = true_positive / predicted_positive if predicted_positive else 0.0
        recall = true_positive / actual_positive if actual_positive else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall)
            else 0.0
        )
        results[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": actual_positive,
        }
    return results


def macro_f1(per_class: dict[str, dict[str, float]]) -> float:
    """Average F1 over classes that actually appear in the validation set.

    Macro rather than micro because the categories are imbalanced: micro-F1
    would let strong performance on plentiful paper images hide the model being
    useless at metal.
    """
    scores = [stats["f1"] for stats in per_class.values() if stats["support"] > 0]
    return float(np.mean(scores)) if scores else 0.0


def expected_calibration_error(
    probabilities: np.ndarray, targets: np.ndarray, bins: int = 10
) -> float:
    """Gap between stated confidence and observed accuracy, averaged over bins."""
    confidences = probabilities.max(axis=1)
    predictions = probabilities.argmax(axis=1)
    correct = (predictions == targets).astype(float)

    error = 0.0
    for lower in np.linspace(0.0, 1.0, bins + 1)[:-1]:
        upper = lower + 1.0 / bins
        mask = (confidences > lower) & (confidences <= upper)
        if mask.sum() == 0:
            continue
        error += mask.mean() * abs(correct[mask].mean() - confidences[mask].mean())
    return float(error)


# ---------------------------------------------------------------------------
# Loops
# ---------------------------------------------------------------------------
def _run_epoch(
    model: nn.Module,
    loader: DataLoader,
    criterion: nn.Module,
    device: torch.device,
    optimizer: torch.optim.Optimizer | None = None,
) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
    training = optimizer is not None
    model.train(training)

    losses: list[float] = []
    all_logits: list[np.ndarray] = []
    all_targets: list[np.ndarray] = []

    with torch.set_grad_enabled(training):
        for images, targets in loader:
            images, targets = images.to(device), targets.to(device)
            logits = model(images)
            loss = criterion(logits, targets)

            if optimizer is not None:
                optimizer.zero_grad()
                loss.backward()
                # Small, imbalanced batches occasionally produce large gradients;
                # clipping keeps a single odd batch from derailing the run.
                nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
                optimizer.step()

            losses.append(loss.item())
            all_logits.append(logits.detach().cpu().numpy())
            all_targets.append(targets.detach().cpu().numpy())

    logits_array = np.concatenate(all_logits) if all_logits else np.empty((0, NUM_CLASSES))
    targets_array = np.concatenate(all_targets) if all_targets else np.empty(0, dtype=int)
    predictions = logits_array.argmax(axis=1) if len(logits_array) else np.empty(0, dtype=int)

    return float(np.mean(losses)) if losses else 0.0, logits_array, targets_array, predictions


def fit_temperature(logits: np.ndarray, targets: np.ndarray) -> float:
    """Fit a single scalar that divides the logits, minimising validation loss.

    Temperature scaling cannot change which class is predicted — dividing every
    logit by the same positive number preserves their order — so accuracy is
    untouched. It only stretches or compresses the probabilities, which is
    exactly the knob needed to make confidence trustworthy.
    """
    if len(logits) == 0:
        return 1.0

    logits_tensor = torch.tensor(logits, dtype=torch.float32)
    targets_tensor = torch.tensor(targets, dtype=torch.long)
    log_temperature = torch.zeros(1, requires_grad=True)

    optimizer = torch.optim.LBFGS([log_temperature], lr=0.1, max_iter=60)
    criterion = nn.CrossEntropyLoss()

    def closure() -> torch.Tensor:
        optimizer.zero_grad()
        # Optimise in log space so the temperature cannot go negative.
        loss = criterion(logits_tensor / log_temperature.exp(), targets_tensor)
        loss.backward()
        return loss

    optimizer.step(closure)  # type: ignore[arg-type]
    return float(log_temperature.exp().item())


def softmax(logits: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    scaled = logits / temperature
    scaled = scaled - scaled.max(axis=1, keepdims=True)
    exponentiated = np.exp(scaled)
    return exponentiated / exponentiated.sum(axis=1, keepdims=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def train_classifier(
    *,
    data_dir: Path | None = None,
    epochs: int = DEFAULT_EPOCHS,
    warmup_epochs: int = DEFAULT_WARMUP_EPOCHS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    image_size: int | None = None,
    head_lr: float = 1e-3,
    backbone_lr: float = 1e-4,
    validation_fraction: float = 0.2,
    pretrained: bool = True,
    patience: int = 4,
    seed: int = 42,
    num_workers: int = 0,
) -> ClassifierTrainingResult:
    torch.manual_seed(seed)
    np.random.seed(seed)
    image_size = image_size or settings.classifier_image_size

    train_loader, validation_loader, train_samples, validation_samples = build_dataloaders(
        data_dir,
        batch_size=batch_size,
        image_size=image_size,
        validation_fraction=validation_fraction,
        seed=seed,
        num_workers=num_workers,
    )

    device = select_device()
    model = build_model(pretrained=pretrained).to(device)

    criterion = nn.CrossEntropyLoss(
        weight=class_weights(train_samples).to(device),
        label_smoothing=LABEL_SMOOTHING,
    )
    optimizer = torch.optim.AdamW(
        parameter_groups(model, head_lr=head_lr, backbone_lr=backbone_lr),
        weight_decay=1e-4,
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(1, epochs))

    best_score = -1.0
    best_state: dict | None = None
    best_epoch = 0
    best_logits = np.empty((0, NUM_CLASSES))
    best_targets = np.empty(0, dtype=int)
    epochs_without_improvement = 0
    started = time.perf_counter()

    for epoch in range(1, epochs + 1):
        freeze_backbone(model, frozen=epoch <= warmup_epochs)

        train_loss, _, _, _ = _run_epoch(model, train_loader, criterion, device, optimizer)
        val_loss, logits, targets, predictions = _run_epoch(
            model, validation_loader, criterion, device
        )
        scheduler.step()

        matrix = confusion(targets, predictions)
        per_class = per_class_metrics(matrix)
        score = macro_f1(per_class)
        accuracy = float((predictions == targets).mean()) if len(targets) else 0.0

        logger.info(
            "Epoch %d/%d | train loss %.4f | val loss %.4f | accuracy %.3f | macro-F1 %.3f%s",
            epoch,
            epochs,
            train_loss,
            val_loss,
            accuracy,
            score,
            " (backbone frozen)" if epoch <= warmup_epochs else "",
        )

        if score > best_score:
            best_score = score
            best_epoch = epoch
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            best_logits, best_targets = logits, targets
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
            # Stop only after the backbone has had a chance; a plateau during
            # the frozen warmup says nothing about the full model.
            if epoch > warmup_epochs and epochs_without_improvement >= patience:
                logger.info("Early stopping: no improvement for %d epochs", patience)
                break

    if best_state is not None:
        model.load_state_dict(best_state)

    temperature = fit_temperature(best_logits, best_targets)
    probabilities = softmax(best_logits, temperature)
    predictions = best_logits.argmax(axis=1)
    matrix = confusion(best_targets, predictions)
    per_class = per_class_metrics(matrix)

    metrics = {
        "val_accuracy": float((predictions == best_targets).mean()) if len(best_targets) else 0.0,
        "val_macro_f1": macro_f1(per_class),
        "val_calibration_error": expected_calibration_error(probabilities, best_targets),
        "val_calibration_error_uncalibrated": expected_calibration_error(
            softmax(best_logits), best_targets
        ),
        "recyclable_accuracy": _recyclable_accuracy(best_targets, predictions),
        "train_images": len(train_samples),
        "validation_images": len(validation_samples),
        "training_seconds": round(time.perf_counter() - started, 1),
    }

    model_version = (
        f"{settings.classifier_model_name}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
    )
    path = artifact_file()
    torch.save(
        {
            "state_dict": model.state_dict(),
            "model_version": model_version,
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "class_labels": CLASS_LABELS,
            "image_size": image_size,
            "temperature": temperature,
            "metrics": metrics,
            "per_class": per_class,
            "confusion_matrix": matrix.tolist(),
            "architecture": "mobilenet_v3_small",
        },
        path,
    )

    class_counts = describe(train_samples + validation_samples)
    metrics_file().write_text(
        json.dumps(
            {
                "model_version": model_version,
                "trained_at": datetime.now(timezone.utc).isoformat(),
                "class_counts": class_counts,
                "metrics": metrics,
                "per_class": per_class,
                "confusion_matrix": matrix.tolist(),
                "class_labels": CLASS_LABELS,
                "temperature": temperature,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    result = ClassifierTrainingResult(
        model_version=model_version,
        artifact_path=path,
        epochs_run=best_epoch + epochs_without_improvement,
        best_epoch=best_epoch,
        class_counts=class_counts,
        metrics=metrics,
        per_class=per_class,
        confusion_matrix=matrix.tolist(),
        temperature=temperature,
    )
    logger.info("Classifier training complete: %s", result)
    return result


def _recyclable_accuracy(targets: np.ndarray, predictions: np.ndarray) -> float:
    """Accuracy of the recyclable / not-recyclable decision.

    Reported separately because it is the decision with operational
    consequences: confusing plastic with metal still sends the item to a
    recycling stream, but confusing plastic with organic contaminates a load.
    """
    if len(targets) == 0:
        return 0.0

    from app.ml.classification.labels import is_recyclable

    recyclable_flags = np.array([is_recyclable(label) for label in CLASS_LABELS])
    return float((recyclable_flags[targets] == recyclable_flags[predictions]).mean())

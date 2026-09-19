"""Tests for the waste image classifier.

Two deliberate choices about how these run:

* Nothing downloads ImageNet weights. Training tests pass `pretrained=False`, so
  the suite works offline and in CI without a 10MB fetch per run.
* The service and API tests use a stub classifier rather than a trained network.
  What they are testing is contamination logic, the review loop and the HTTP
  contract, none of which should fail or pass depending on whether a real model
  happened to guess an image correctly.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
import torch
from PIL import Image
from sqlalchemy import select

from app.core.config import settings
from app.ml.classification.dataset import (
    class_weights,
    describe,
    discover_samples,
    stratified_split,
    verify_images,
)
from app.ml.classification.labels import (
    CLASS_LABELS,
    NUM_CLASSES,
    is_recyclable,
    normalise_folder,
)
from app.ml.classification.predictor import Classification, WasteClassifier, decode_image
from app.ml.classification.train import (
    confusion,
    expected_calibration_error,
    fit_temperature,
    macro_f1,
    per_class_metrics,
    softmax,
    train_classifier,
)
from app.models.classification import WasteClassification
from app.models.enums import WasteType
from app.services import classification_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
SOLID_COLOURS = {
    "plastic": (220, 30, 30),
    "paper": (30, 200, 60),
    "metal": (40, 60, 230),
}

IMAGES_PER_CLASS = 16


def write_image(path: Path, colour: tuple[int, int, int], *, size: int = 96) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    noise = np.random.default_rng(abs(hash(path.name)) % 2**32).integers(
        -18, 18, size=(size, size, 3)
    )
    pixels = np.clip(np.array(colour, dtype=int) + noise, 0, 255).astype(np.uint8)
    Image.fromarray(pixels).save(path)


def image_bytes(colour: tuple[int, int, int] = (200, 40, 40), fmt: str = "JPEG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), colour).save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.fixture
def dataset_dir(tmp_path: Path) -> Path:
    """A tiny three-category dataset, one distinct colour per class."""
    root = tmp_path / "waste"
    for label, colour in SOLID_COLOURS.items():
        for index in range(IMAGES_PER_CLASS):
            write_image(root / label / f"{label}_{index}.jpg", colour)
    return root


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    """Redirect artifact and upload directories into the test's temp folder."""
    monkeypatch.setattr(
        type(settings), "artifact_path", property(lambda _s: tmp_path / "artifacts"),
        raising=False,
    )
    (tmp_path / "artifacts").mkdir(exist_ok=True)
    monkeypatch.setattr(
        type(settings), "upload_path", property(lambda _s: tmp_path / "uploads"),
        raising=False,
    )
    (tmp_path / "uploads").mkdir(exist_ok=True)
    WasteClassifier.reset_cache()
    yield tmp_path
    WasteClassifier.reset_cache()


class StubClassifier:
    """A deterministic stand-in with the same surface as `WasteClassifier`."""

    model_version = "stub-1"
    trained_at = "2026-09-19T00:00:00+00:00"
    class_labels = CLASS_LABELS
    image_size = 224
    temperature = 1.0
    metrics: dict = {}
    per_class: dict = {}
    confusion_matrix: list = []

    def __init__(self, predicted: WasteType, confidence: float = 0.92) -> None:
        self.predicted = predicted
        self.confidence = confidence

    def classify_image(self, image, *, confidence_threshold: float) -> Classification:
        remainder = (1.0 - self.confidence) / (NUM_CLASSES - 1)
        probabilities = {
            label: (self.confidence if label == self.predicted.value else remainder)
            for label in CLASS_LABELS
        }
        return Classification(
            predicted_class=self.predicted,
            confidence=self.confidence,
            probabilities=probabilities,
            is_recyclable=self.predicted.is_recyclable,
            needs_review=self.confidence < confidence_threshold,
            model_version=self.model_version,
            inference_ms=1.0,
            runner_up="paper",
            margin=self.confidence - remainder,
        )


@pytest.fixture
def stub_classifier(artifacts, monkeypatch):
    def install(predicted: WasteType, confidence: float = 0.92) -> StubClassifier:
        stub = StubClassifier(predicted, confidence)
        monkeypatch.setattr(WasteClassifier, "load", classmethod(lambda cls, **_: stub))
        return stub

    return install


# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------
def test_the_six_required_categories_are_the_label_set():
    assert CLASS_LABELS == ["plastic", "paper", "metal", "glass", "organic", "other"]


def test_mixed_is_not_a_predictable_class():
    """MIXED describes a bin, not something visible in a photo of an item."""
    assert WasteType.MIXED.value not in CLASS_LABELS


def test_dataset_folder_aliases_map_onto_our_categories():
    assert normalise_folder("cardboard") == "paper"
    assert normalise_folder("trash") == "other"
    assert normalise_folder("biological") == "organic"
    assert normalise_folder("Green-Glass") == "glass"
    assert normalise_folder("unrelated") is None


def test_recyclability_matches_the_domain_model():
    assert is_recyclable("plastic") and is_recyclable("metal")
    assert not is_recyclable("organic") and not is_recyclable("other")


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------
def test_discovery_finds_images_and_labels_them(dataset_dir):
    samples = discover_samples(dataset_dir)
    total = IMAGES_PER_CLASS * len(SOLID_COLOURS)

    assert len(samples) == total
    assert describe(samples)["plastic"] == IMAGES_PER_CLASS
    assert describe(samples)["glass"] == 0


def test_unknown_folders_are_ignored_not_fatal(dataset_dir):
    write_image(dataset_dir / "screenshots" / "x.jpg", (10, 10, 10))

    assert len(discover_samples(dataset_dir)) == IMAGES_PER_CLASS * len(SOLID_COLOURS)


def test_corrupt_files_are_filtered_before_training(dataset_dir):
    (dataset_dir / "plastic" / "broken.jpg").write_text("<html>404</html>", encoding="utf-8")

    samples = discover_samples(dataset_dir)
    valid = verify_images(samples)
    total = IMAGES_PER_CLASS * len(SOLID_COLOURS)

    assert len(samples) == total + 1
    assert len(valid) == total


def test_split_is_stratified_so_every_class_appears_in_both_halves(dataset_dir):
    samples = discover_samples(dataset_dir)

    train, validation = stratified_split(samples, validation_fraction=0.25, seed=1)

    assert set(describe(train)) == set(describe(validation))
    for label in SOLID_COLOURS:
        assert describe(train)[label] > 0
        assert describe(validation)[label] > 0


def test_split_never_puts_the_same_image_in_both_halves(dataset_dir):
    train, validation = stratified_split(
        discover_samples(dataset_dir), validation_fraction=0.3, seed=1
    )

    assert not {s.path for s in train} & {s.path for s in validation}


def test_class_weights_compensate_for_imbalance(dataset_dir):
    for index in range(IMAGES_PER_CLASS * 3):
        write_image(dataset_dir / "paper" / f"extra_{index}.jpg", SOLID_COLOURS["paper"])

    weights = class_weights(discover_samples(dataset_dir))

    # Paper is now the majority class, so it must carry the smaller weight.
    assert weights[CLASS_LABELS.index("paper")] < weights[CLASS_LABELS.index("plastic")]


def test_missing_dataset_directory_explains_the_expected_layout(tmp_path):
    with pytest.raises(FileNotFoundError, match="one subfolder per category"):
        discover_samples(tmp_path / "nope")


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def test_per_class_metrics_match_a_hand_computed_confusion_matrix():
    targets = np.array([0, 0, 0, 1, 1])
    predictions = np.array([0, 0, 1, 1, 0])

    scores = per_class_metrics(confusion(targets, predictions))

    # plastic: 2 correct of 3 actual, 2 of 3 predicted. Scores are rounded to
    # four places for storage, so compare at that precision.
    assert scores["plastic"]["recall"] == pytest.approx(2 / 3, abs=1e-4)
    assert scores["plastic"]["precision"] == pytest.approx(2 / 3, abs=1e-4)
    assert scores["paper"]["recall"] == pytest.approx(0.5)
    assert scores["paper"]["support"] == 2


def test_macro_f1_ignores_classes_with_no_validation_images():
    targets = np.array([0, 0, 1])
    predictions = np.array([0, 0, 1])

    scores = per_class_metrics(confusion(targets, predictions))

    # Four of six categories are absent; a perfect score must still be 1.0
    # rather than being dragged to a third by empty classes.
    assert macro_f1(scores) == pytest.approx(1.0)


def test_macro_f1_punishes_ignoring_a_minority_class():
    # Nine majority images and one minority image, all predicted majority.
    targets = np.array([0] * 9 + [1])
    predictions = np.array([0] * 10)

    scores = per_class_metrics(confusion(targets, predictions))

    assert (predictions == targets).mean() == pytest.approx(0.9)
    # Accuracy says 90%; macro-F1 exposes that one category is never predicted.
    assert macro_f1(scores) < 0.6


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------
def test_temperature_scaling_does_not_change_predictions():
    rng = np.random.default_rng(0)
    logits = rng.normal(size=(200, NUM_CLASSES)) * 4.0
    targets = logits.argmax(axis=1)

    temperature = fit_temperature(logits, targets)

    assert softmax(logits, temperature).argmax(axis=1).tolist() == targets.tolist()


def test_temperature_scaling_improves_calibration_of_overconfident_logits():
    rng = np.random.default_rng(3)
    truth = rng.integers(0, NUM_CLASSES, size=400)

    # Logits that are right 70% of the time but hugely overconfident, which is
    # how a cross-entropy-trained network behaves without calibration.
    logits = rng.normal(size=(400, NUM_CLASSES))
    for row, label in enumerate(truth):
        target = label if rng.random() < 0.7 else (label + 1) % NUM_CLASSES
        logits[row, target] += 12.0

    temperature = fit_temperature(logits, truth)
    before = expected_calibration_error(softmax(logits), truth)
    after = expected_calibration_error(softmax(logits, temperature), truth)

    assert temperature > 1.0  # confidence needed softening
    assert after < before


def test_calibration_error_is_zero_for_a_perfectly_calibrated_predictor():
    probabilities = np.tile(np.eye(NUM_CLASSES)[0], (50, 1))
    targets = np.zeros(50, dtype=int)

    assert expected_calibration_error(probabilities, targets) == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------
def test_training_refuses_a_single_category_dataset(tmp_path, artifacts):
    root = tmp_path / "one"
    for index in range(10):
        write_image(root / "plastic" / f"{index}.jpg", (200, 20, 20))

    with pytest.raises(ValueError, match="at least two"):
        train_classifier(data_dir=root, epochs=1, pretrained=False)


def test_training_refuses_an_empty_dataset(tmp_path, artifacts):
    (tmp_path / "empty" / "plastic").mkdir(parents=True)

    with pytest.raises(ValueError, match="No usable images"):
        train_classifier(data_dir=tmp_path / "empty", epochs=1, pretrained=False)


@pytest.fixture
def trained_classifier(dataset_dir, artifacts):
    """A real but deliberately tiny run, from scratch so nothing is downloaded.

    Two epochs at 32px is not enough to produce an accurate model, and these
    tests do not claim otherwise — they check that the pipeline saves, reports
    and reloads correctly. Whether the architecture can learn is a separate
    question, answered by the optimizer test below.
    """
    return train_classifier(
        data_dir=dataset_dir,
        epochs=2,
        warmup_epochs=0,
        batch_size=8,
        image_size=32,
        validation_fraction=0.25,
        pretrained=False,
        seed=0,
    )


def test_training_writes_an_artifact_and_a_metrics_file(trained_classifier):
    assert trained_classifier.artifact_path.exists()
    assert trained_classifier.metrics["train_images"] > 0
    assert trained_classifier.metrics["validation_images"] > 0
    assert len(trained_classifier.confusion_matrix) == NUM_CLASSES


def test_training_reports_every_category_even_absent_ones(trained_classifier):
    assert set(trained_classifier.per_class) == set(CLASS_LABELS)
    assert trained_classifier.per_class["glass"]["support"] == 0


def test_the_optimizer_actually_drives_the_loss_down(dataset_dir):
    """The training step must fit the data it is shown.

    Run without augmentation on a fixed, balanced batch: if repeated epochs do
    not reduce the loss, the loop is broken regardless of what any accuracy
    figure elsewhere happens to say.
    """
    from torch import nn
    from torch.utils.data import DataLoader

    from app.ml.classification.dataset import WasteImageDataset, build_transforms
    from app.ml.classification.model import build_model, parameter_groups
    from app.ml.classification.train import _run_epoch

    samples = discover_samples(dataset_dir)
    balanced = [s for label in SOLID_COLOURS for s in
                [x for x in samples if x.label == label][:8]]

    loader = DataLoader(
        WasteImageDataset(balanced, build_transforms(32, training=False)),
        batch_size=8,
        shuffle=False,
    )
    model = build_model(pretrained=False)
    optimizer = torch.optim.AdamW(
        parameter_groups(model, head_lr=1e-3, backbone_lr=1e-4)
    )
    criterion = nn.CrossEntropyLoss()
    device = torch.device("cpu")

    first_loss, *_ = _run_epoch(model, loader, criterion, device, optimizer)
    for _ in range(5):
        last_loss, *_ = _run_epoch(model, loader, criterion, device, optimizer)

    assert last_loss < first_loss


def test_saved_model_reloads_and_predicts(trained_classifier, artifacts):
    classifier = WasteClassifier.load(refresh=True)
    image = Image.new("RGB", (96, 96), SOLID_COLOURS["plastic"])

    result = classifier.classify_image(image, confidence_threshold=0.6)

    assert result.predicted_class.value in CLASS_LABELS
    assert set(result.probabilities) == set(CLASS_LABELS)
    assert sum(result.probabilities.values()) == pytest.approx(1.0, abs=1e-3)
    assert result.model_version == trained_classifier.model_version


def test_missing_artifact_raises_a_clear_error(artifacts):
    from app.core.exceptions import ModelNotTrainedError

    with pytest.raises(ModelNotTrainedError, match="train-classifier"):
        WasteClassifier.load(refresh=True)


def test_reloading_does_not_need_network_weights(trained_classifier, artifacts, monkeypatch):
    """Loading must never reach for ImageNet weights on an offline machine."""
    import app.ml.classification.model as model_module

    original = model_module.mobilenet_v3_small

    def guarded(weights=None, **kwargs):
        assert weights is None, "model load attempted to fetch pretrained weights"
        return original(weights=None, **kwargs)

    monkeypatch.setattr(model_module, "mobilenet_v3_small", guarded)
    assert WasteClassifier.load(refresh=True) is not None


# ---------------------------------------------------------------------------
# Image decoding
# ---------------------------------------------------------------------------
def test_decoding_rejects_non_image_uploads():
    from app.core.exceptions import ValidationError

    with pytest.raises(ValidationError, match="not a readable image"):
        decode_image(b"this is a text file, not a photo")


def test_decoding_rejects_empty_uploads():
    from app.core.exceptions import ValidationError

    with pytest.raises(ValidationError, match="empty"):
        decode_image(b"")


def test_decoding_accepts_png_and_jpeg():
    assert decode_image(image_bytes(fmt="PNG")).size == (64, 64)
    assert decode_image(image_bytes(fmt="JPEG")).size == (64, 64)


# ---------------------------------------------------------------------------
# Contamination
# ---------------------------------------------------------------------------
def test_matching_item_in_a_segregated_bin_is_clean(db, created_bin):
    from app.models.bin import Bin

    bin_obj = db.get(Bin, created_bin["id"])
    bin_obj.waste_type = WasteType.PLASTIC

    check = classification_service.check_contamination(bin_obj, WasteType.PLASTIC)

    assert check.checked and not check.is_contaminant


def test_wrong_item_in_a_segregated_bin_is_contamination(db, created_bin):
    from app.models.bin import Bin

    bin_obj = db.get(Bin, created_bin["id"])
    bin_obj.waste_type = WasteType.PAPER

    check = classification_service.check_contamination(bin_obj, WasteType.ORGANIC)

    assert check.is_contaminant
    assert "paper bin" in check.message


def test_a_mixed_bin_cannot_be_contaminated(db, created_bin):
    """Nothing is out of place in a bin that accepts everything."""
    from app.models.bin import Bin

    bin_obj = db.get(Bin, created_bin["id"])
    bin_obj.waste_type = WasteType.MIXED

    check = classification_service.check_contamination(bin_obj, WasteType.ORGANIC)

    assert not check.checked
    assert not check.is_contaminant


def test_no_bin_means_no_contamination_check():
    check = classification_service.check_contamination(None, WasteType.GLASS)

    assert not check.checked


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
def test_classification_is_persisted_with_its_image(db, stub_classifier):
    stub_classifier(WasteType.GLASS)

    record, result, _ = classification_service.classify_and_store(
        db, data=image_bytes(), filename="bottle.jpg"
    )

    assert record.id is not None
    assert record.predicted_class is WasteType.GLASS
    assert record.is_recyclable is True
    assert Path(record.image_path).exists()
    assert record.image_sha256 and len(record.image_sha256) == 64
    assert result.confidence == pytest.approx(0.92)


def test_low_confidence_predictions_are_flagged_for_review(db, stub_classifier):
    stub_classifier(WasteType.OTHER, confidence=0.31)

    record, _, _ = classification_service.classify_and_store(
        db, data=image_bytes(), filename="blurry.jpg"
    )

    assert record.needs_review is True


def test_contamination_is_recorded_against_the_bin(db, created_bin, stub_classifier):
    from app.models.bin import Bin

    db.get(Bin, created_bin["id"]).waste_type = WasteType.PAPER
    db.commit()
    stub_classifier(WasteType.PLASTIC)

    record, _, contamination = classification_service.classify_and_store(
        db, data=image_bytes(), filename="wrapper.jpg", bin_id=created_bin["id"]
    )

    assert contamination.is_contaminant
    assert record.metadata_json["contamination"]["expected_stream"] == "paper"


def test_classifying_against_an_unknown_bin_is_a_404(db, stub_classifier):
    from app.core.exceptions import NotFoundError

    stub_classifier(WasteType.PLASTIC)

    with pytest.raises(NotFoundError):
        classification_service.classify_and_store(
            db, data=image_bytes(), filename="x.jpg", bin_id=999999
        )


def test_oversized_uploads_are_rejected(db, stub_classifier, monkeypatch):
    stub_classifier(WasteType.PLASTIC)
    monkeypatch.setattr(settings, "max_upload_bytes", 10)

    from app.core.exceptions import ValidationError

    with pytest.raises(ValidationError, match="limit is"):
        classification_service.classify_and_store(
            db, data=image_bytes(), filename="big.jpg"
        )


def test_identical_images_share_one_stored_file(db, stub_classifier):
    stub_classifier(WasteType.METAL)
    data = image_bytes()

    first, _, _ = classification_service.classify_and_store(db, data=data, filename="a.jpg")
    second, _, _ = classification_service.classify_and_store(db, data=data, filename="b.jpg")

    # Two audit rows, but the bytes are stored once, keyed by content hash.
    assert first.id != second.id
    assert first.image_path == second.image_path


# ---------------------------------------------------------------------------
# Review loop
# ---------------------------------------------------------------------------
def test_review_records_the_correction_without_losing_the_prediction(db, stub_classifier):
    stub_classifier(WasteType.PLASTIC, confidence=0.4)
    record, _, _ = classification_service.classify_and_store(
        db, data=image_bytes(), filename="x.jpg"
    )

    reviewed = classification_service.review_classification(
        db, record.id, corrected_class=WasteType.METAL, reviewer="supervisor"
    )

    assert reviewed.predicted_class is WasteType.PLASTIC  # original kept
    assert reviewed.reviewed_class is WasteType.METAL
    assert reviewed.effective_class is WasteType.METAL
    assert reviewed.needs_review is False
    assert reviewed.reviewed_by == "supervisor"


def test_review_updates_recyclability(db, stub_classifier):
    stub_classifier(WasteType.PLASTIC, confidence=0.4)
    record, _, _ = classification_service.classify_and_store(
        db, data=image_bytes(), filename="x.jpg"
    )
    assert record.is_recyclable is True

    reviewed = classification_service.review_classification(
        db, record.id, corrected_class=WasteType.ORGANIC, reviewer="supervisor"
    )

    assert reviewed.is_recyclable is False


def test_review_cannot_set_the_mixed_pseudo_category(db, stub_classifier):
    from app.core.exceptions import ValidationError

    stub_classifier(WasteType.PLASTIC)
    record, _, _ = classification_service.classify_and_store(
        db, data=image_bytes(), filename="x.jpg"
    )

    with pytest.raises(ValidationError):
        classification_service.review_classification(
            db, record.id, corrected_class=WasteType.MIXED, reviewer="supervisor"
        )


def test_reviewed_images_export_into_a_training_layout(db, stub_classifier, tmp_path):
    stub_classifier(WasteType.PLASTIC, confidence=0.4)
    record, _, _ = classification_service.classify_and_store(
        db, data=image_bytes(), filename="x.jpg"
    )
    classification_service.review_classification(
        db, record.id, corrected_class=WasteType.GLASS, reviewer="supervisor"
    )

    exported = classification_service.export_reviewed_images(db, tmp_path / "next")

    assert exported == {"glass": 1}
    assert list((tmp_path / "next" / "glass").iterdir())


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------
def test_stats_are_empty_but_well_formed_with_no_data(db):
    stats = classification_service.classification_stats(db)

    assert stats["total"] == 0
    assert stats["by_class"] == {label: 0 for label in CLASS_LABELS}
    assert stats["model_agreement_pct"] is None


def test_stats_count_the_corrected_label_not_the_prediction(db, stub_classifier):
    stub_classifier(WasteType.PLASTIC, confidence=0.4)
    record, _, _ = classification_service.classify_and_store(
        db, data=image_bytes(), filename="x.jpg"
    )
    classification_service.review_classification(
        db, record.id, corrected_class=WasteType.ORGANIC, reviewer="s"
    )

    stats = classification_service.classification_stats(db)

    assert stats["by_class"]["organic"] == 1
    assert stats["by_class"]["plastic"] == 0
    assert stats["recyclable_pct"] == 0.0
    assert stats["model_agreement_pct"] == 0.0


def test_contamination_rate_is_denominated_by_checks_performed(
    db, created_bin, stub_classifier
):
    from app.models.bin import Bin

    db.get(Bin, created_bin["id"]).waste_type = WasteType.PAPER
    db.commit()

    stub_classifier(WasteType.PLASTIC)
    classification_service.classify_and_store(
        db, data=image_bytes((1, 1, 1)), filename="a.jpg", bin_id=created_bin["id"]
    )
    # An upload with no bin cannot be checked, so it must not dilute the rate.
    classification_service.classify_and_store(db, data=image_bytes((2, 2, 2)), filename="b.jpg")

    stats = classification_service.classification_stats(db)

    assert stats["total"] == 2
    assert stats["contamination_count"] == 1
    assert stats["contamination_pct"] == 100.0


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def test_model_endpoint_reports_when_nothing_is_trained(client, api_prefix, artifacts):
    response = client.get(f"{api_prefix}/classify/model")

    body = response.json()
    assert response.status_code == 200
    assert body["available"] is False
    assert body["class_labels"] == CLASS_LABELS


def test_upload_endpoint_returns_the_full_verdict(
    client, api_prefix, db, created_bin, stub_classifier
):
    from app.models.bin import Bin

    db.get(Bin, created_bin["id"]).waste_type = WasteType.GLASS
    db.commit()
    stub_classifier(WasteType.ORGANIC)

    response = client.post(
        f"{api_prefix}/classify",
        files={"file": ("banana.jpg", image_bytes(), "image/jpeg")},
        data={"bin_id": str(created_bin["id"])},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["classification"]["predicted_class"] == "organic"
    assert body["classification"]["is_recyclable"] is False
    assert body["contamination"]["is_contaminant"] is True
    assert body["contamination"]["expected_stream"] == "glass"


def test_upload_endpoint_rejects_a_non_image(client, api_prefix, stub_classifier):
    stub_classifier(WasteType.PLASTIC)

    response = client.post(
        f"{api_prefix}/classify",
        files={"file": ("notes.txt", b"just some text", "text/plain")},
    )

    assert response.status_code == 422


def test_review_queue_and_correction_round_trip(client, api_prefix, db, stub_classifier):
    stub_classifier(WasteType.PLASTIC, confidence=0.2)
    client.post(
        f"{api_prefix}/classify",
        files={"file": ("x.jpg", image_bytes(), "image/jpeg")},
    )

    queue = client.get(f"{api_prefix}/classify", params={"needs_review": True})
    assert queue.status_code == 200
    assert len(queue.json()) == 1
    classification_id = queue.json()[0]["id"]

    corrected = client.post(
        f"{api_prefix}/classify/{classification_id}/review",
        json={"corrected_class": "metal", "reviewer": "supervisor"},
    )
    assert corrected.status_code == 200
    assert corrected.json()["effective_class"] == "metal"
    assert corrected.json()["model_was_correct"] is False

    assert client.get(f"{api_prefix}/classify", params={"needs_review": True}).json() == []


def test_stats_endpoint_matches_the_stored_rows(client, api_prefix, db, stub_classifier):
    stub_classifier(WasteType.PAPER)
    for index in range(3):
        client.post(
            f"{api_prefix}/classify",
            files={"file": (f"{index}.jpg", image_bytes((index, 40, 40)), "image/jpeg")},
        )

    response = client.get(f"{api_prefix}/classify/stats")

    body = response.json()
    assert body["total"] == 3
    assert body["by_class"]["paper"] == 3
    assert body["recyclable_pct"] == 100.0
    assert db.scalar(select(WasteClassification.id)) is not None


def test_unknown_classification_returns_404(client, api_prefix):
    assert client.get(f"{api_prefix}/classify/999999").status_code == 404


def test_classification_list_filters_by_predicted_class(client, api_prefix, stub_classifier):
    stub_classifier(WasteType.METAL)
    client.post(
        f"{api_prefix}/classify", files={"file": ("a.jpg", image_bytes(), "image/jpeg")}
    )

    matching = client.get(f"{api_prefix}/classify", params={"predicted_class": "metal"})
    other = client.get(f"{api_prefix}/classify", params={"predicted_class": "glass"})

    assert len(matching.json()) == 1
    assert other.json() == []

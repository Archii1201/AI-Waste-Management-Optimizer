"""Dataset loading, augmentation and splitting for the waste classifier.

Images come from a folder-per-category layout, which is what every public waste
dataset ships and what a user can assemble by hand without tooling.

Two choices worth stating:

* The split is **stratified** by class. Waste datasets are imbalanced — glass
  and paper are plentiful, "other" is scarce — and a random split can leave a
  minority class with a handful of validation images, making its recall score
  pure noise.
* Augmentation is applied to the training set only. Evaluating on augmented
  images would measure the model on a distribution it will never see in
  production, where photos arrive upright and uncropped.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import torch
from PIL import Image, UnidentifiedImageError
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms

from app.core.config import settings
from app.core.logging import get_logger
from app.ml.classification.labels import CLASS_LABELS, index_of, normalise_folder

logger = get_logger(__name__)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# ImageNet statistics. The backbone was pretrained with these, so inputs must be
# normalised the same way or the transferred features are meaningless.
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)

MIN_IMAGES_PER_CLASS = 8


@dataclass
class Sample:
    path: Path
    label: str
    target: int


class WasteImageDataset(Dataset):
    def __init__(self, samples: list[Sample], transform: transforms.Compose) -> None:
        self.samples = samples
        self.transform = transform

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, int]:
        sample = self.samples[index]
        # Convert to RGB explicitly: waste photos are frequently PNGs with an
        # alpha channel or greyscale scans, and the model expects three channels.
        with Image.open(sample.path) as image:
            tensor = self.transform(image.convert("RGB"))
        return tensor, sample.target


def build_transforms(image_size: int, *, training: bool) -> transforms.Compose:
    if not training:
        return transforms.Compose(
            [
                transforms.Resize(int(image_size * 1.14)),
                transforms.CenterCrop(image_size),
                transforms.ToTensor(),
                transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
            ]
        )

    # Waste photos arrive at wildly varying distances, angles and lighting —
    # phone camera in daylight, fixed bin camera at dusk. The augmentations
    # mirror that variation rather than being generic defaults. No vertical
    # flip: it produces upside-down bottles the model will never encounter.
    return transforms.Compose(
        [
            transforms.RandomResizedCrop(image_size, scale=(0.65, 1.0), ratio=(0.75, 1.33)),
            transforms.RandomHorizontalFlip(),
            transforms.RandomRotation(degrees=15),
            transforms.ColorJitter(brightness=0.3, contrast=0.3, saturation=0.25, hue=0.03),
            transforms.ToTensor(),
            transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
            # Simulates occlusion, which is the normal state of waste in a bin.
            transforms.RandomErasing(p=0.25, scale=(0.02, 0.15)),
        ]
    )


def discover_samples(data_dir: Path) -> list[Sample]:
    """Walk a folder-per-category tree, mapping folder names onto our labels."""
    if not data_dir.exists():
        raise FileNotFoundError(
            f"Dataset directory {data_dir} does not exist. Expected one subfolder "
            f"per category, named from: {', '.join(CLASS_LABELS)}."
        )

    samples: list[Sample] = []
    skipped_folders: list[str] = []

    for folder in sorted(p for p in data_dir.iterdir() if p.is_dir()):
        label = normalise_folder(folder.name)
        if label is None:
            skipped_folders.append(folder.name)
            continue

        target = index_of(label)
        for path in sorted(folder.rglob("*")):
            if path.suffix.lower() in IMAGE_EXTENSIONS and path.is_file():
                samples.append(Sample(path=path, label=label, target=target))

    if skipped_folders:
        logger.warning(
            "Ignored %d unrecognised folder(s): %s",
            len(skipped_folders),
            ", ".join(skipped_folders),
        )
    return samples


def verify_images(samples: list[Sample]) -> list[Sample]:
    """Drop files that are not decodable images.

    Scraped waste datasets reliably contain a few truncated downloads and HTML
    error pages saved with a .jpg extension. Finding those mid-epoch crashes a
    long training run, so they are filtered up front.
    """
    valid: list[Sample] = []
    for sample in samples:
        try:
            with Image.open(sample.path) as image:
                image.verify()
            valid.append(sample)
        except (UnidentifiedImageError, OSError, ValueError):
            logger.warning("Skipping unreadable image: %s", sample.path)
    return valid


def stratified_split(
    samples: list[Sample], *, validation_fraction: float, seed: int
) -> tuple[list[Sample], list[Sample]]:
    """Split per class, so every category appears in both halves."""
    rng = random.Random(seed)
    by_label: dict[str, list[Sample]] = {}
    for sample in samples:
        by_label.setdefault(sample.label, []).append(sample)

    train: list[Sample] = []
    validation: list[Sample] = []

    for label, items in sorted(by_label.items()):
        rng.shuffle(items)
        # At least one validation image per class, but never the whole class.
        count = max(1, min(len(items) - 1, round(len(items) * validation_fraction)))
        validation.extend(items[:count])
        train.extend(items[count:])

    rng.shuffle(train)
    return train, validation


def class_weights(samples: list[Sample]) -> torch.Tensor:
    """Inverse-frequency weights to stop the majority class dominating the loss.

    Without this the model can reach a respectable overall accuracy by rarely
    predicting the scarce categories at all, which is precisely the failure that
    matters: misrouting the unusual item is what contaminates a recycling load.
    """
    counts = Counter(sample.target for sample in samples)
    total = sum(counts.values())
    weights = [
        total / (len(CLASS_LABELS) * counts[index]) if counts.get(index) else 0.0
        for index in range(len(CLASS_LABELS))
    ]
    return torch.tensor(weights, dtype=torch.float32)


def describe(samples: list[Sample]) -> dict[str, int]:
    counts = Counter(sample.label for sample in samples)
    return {label: counts.get(label, 0) for label in CLASS_LABELS}


def build_dataloaders(
    data_dir: Path | None = None,
    *,
    batch_size: int = 32,
    image_size: int | None = None,
    validation_fraction: float = 0.2,
    seed: int = 42,
    num_workers: int = 0,
) -> tuple[DataLoader, DataLoader, list[Sample], list[Sample]]:
    directory = data_dir or settings.dataset_path
    image_size = image_size or settings.classifier_image_size

    samples = verify_images(discover_samples(directory))
    if not samples:
        raise ValueError(
            f"No usable images under {directory}. Expected subfolders such as "
            f"{directory / 'plastic'} containing .jpg or .png files."
        )

    counts = describe(samples)
    present = {label: count for label, count in counts.items() if count}
    if len(present) < 2:
        raise ValueError(
            f"Found images for only {len(present)} category; a classifier needs at "
            f"least two. Counts: {counts}"
        )

    thin = {label: count for label, count in present.items() if count < MIN_IMAGES_PER_CLASS}
    if thin:
        logger.warning(
            "These categories have very few images and will score unreliably: %s", thin
        )

    train_samples, validation_samples = stratified_split(
        samples, validation_fraction=validation_fraction, seed=seed
    )

    train_loader = DataLoader(
        WasteImageDataset(train_samples, build_transforms(image_size, training=True)),
        batch_size=batch_size,
        shuffle=True,
        num_workers=num_workers,
        drop_last=len(train_samples) > batch_size,
    )
    validation_loader = DataLoader(
        WasteImageDataset(validation_samples, build_transforms(image_size, training=False)),
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
    )

    logger.info(
        "Dataset: %d images (%d train / %d validation) across %d categories %s",
        len(samples),
        len(train_samples),
        len(validation_samples),
        len(present),
        counts,
    )
    return train_loader, validation_loader, train_samples, validation_samples

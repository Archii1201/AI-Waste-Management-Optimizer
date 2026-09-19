"""The six waste categories and how public datasets map onto them.

The problem statement fixes the label set: plastic, paper, metal, glass,
organic, other. `MIXED` is deliberately excluded — it describes a bin whose
contents are not segregated, which is a property of the bin, not something
visible in a photograph of an item. Including it as a class would teach the
model to hedge instead of committing to a category.
"""

from __future__ import annotations

from app.models.enums import WasteType

CLASS_LABELS: list[str] = [
    WasteType.PLASTIC.value,
    WasteType.PAPER.value,
    WasteType.METAL.value,
    WasteType.GLASS.value,
    WasteType.ORGANIC.value,
    WasteType.OTHER.value,
]

NUM_CLASSES = len(CLASS_LABELS)

# Public waste datasets use their own folder names. Rather than making the user
# rename thousands of files, accept the common variants and fold them into our
# six categories. TrashNet's "cardboard" is paper; its "trash" is our "other".
FOLDER_ALIASES: dict[str, str] = {
    "plastic": "plastic",
    "plastics": "plastic",
    "pet": "plastic",
    "hdpe": "plastic",
    "paper": "paper",
    "cardboard": "paper",
    "carton": "paper",
    "newspaper": "paper",
    "metal": "metal",
    "metals": "metal",
    "aluminium": "metal",
    "aluminum": "metal",
    "can": "metal",
    "cans": "metal",
    "steel": "metal",
    "glass": "glass",
    "brown-glass": "glass",
    "green-glass": "glass",
    "white-glass": "glass",
    "organic": "organic",
    "biological": "organic",
    "food": "organic",
    "food_waste": "organic",
    "compost": "organic",
    "vegetation": "organic",
    "other": "other",
    "trash": "other",
    "rubbish": "other",
    "residual": "other",
    "battery": "other",
    "clothes": "other",
    "shoes": "other",
}


def normalise_folder(name: str) -> str | None:
    """Map a dataset folder name onto one of the six classes, or None if unknown."""
    return FOLDER_ALIASES.get(name.strip().lower().replace(" ", "_"))


def label_to_waste_type(label: str) -> WasteType:
    return WasteType(label)


def index_of(label: str) -> int:
    return CLASS_LABELS.index(label)


def is_recyclable(label: str) -> bool:
    return label_to_waste_type(label).is_recyclable

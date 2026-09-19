"""Waste-stream physical properties.

Sensors report how *full* a bin is, but capacity planning and the recyclable
tonnage estimates need mass. These densities convert between the two when a bin
has no load cell, which is the common case for low-cost ultrasonic sensors.

Values are typical loose (uncompacted) bulk densities for municipal solid waste
streams, in kilograms per litre.
"""

from __future__ import annotations

from app.models.enums import WasteType

BULK_DENSITY_KG_PER_LITER: dict[WasteType, float] = {
    WasteType.PLASTIC: 0.04,   # bottles and film, mostly air
    WasteType.PAPER: 0.09,     # loose paper and flattened cardboard
    WasteType.METAL: 0.16,     # cans, largely hollow
    WasteType.GLASS: 0.30,     # the densest common stream
    WasteType.ORGANIC: 0.45,   # food and garden waste, high water content
    WasteType.OTHER: 0.12,
    WasteType.MIXED: 0.18,     # blended municipal waste
}

# Share of a MIXED bin that is realistically recoverable, used when estimating
# recyclable tonnage from bins that are not segregated at source. Derived from
# typical Indian municipal waste composition studies.
MIXED_RECYCLABLE_FRACTION = 0.32


def estimate_weight_kg(waste_type: WasteType, volume_liters: float) -> float:
    """Convert a collected volume into an estimated mass."""
    density = BULK_DENSITY_KG_PER_LITER.get(waste_type, BULK_DENSITY_KG_PER_LITER[WasteType.MIXED])
    return round(max(0.0, volume_liters) * density, 3)


def split_recyclable_kg(waste_type: WasteType, weight_kg: float, contamination_pct: float | None = None) -> tuple[float, float]:
    """Split a collected mass into (recyclable_kg, non_recyclable_kg).

    A segregated recyclable stream is fully recoverable minus contamination
    (wrong items thrown into the wrong bin). A mixed stream only yields the
    recoverable fraction. Organic is counted as diverted-from-landfill because
    it goes to composting, which is what municipal diversion targets measure.
    """
    weight_kg = max(0.0, weight_kg)
    contamination = (contamination_pct or 0.0) / 100.0

    if waste_type.is_recyclable:
        recyclable = weight_kg * (1.0 - contamination)
    elif waste_type is WasteType.ORGANIC:
        recyclable = weight_kg * (1.0 - contamination)
    elif waste_type is WasteType.MIXED:
        recyclable = weight_kg * MIXED_RECYCLABLE_FRACTION * (1.0 - contamination)
    else:
        recyclable = 0.0

    recyclable = round(min(recyclable, weight_kg), 3)
    return recyclable, round(weight_kg - recyclable, 3)

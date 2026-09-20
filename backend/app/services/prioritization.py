"""Collection prioritisation: which bins deserve a truck, in what order.

The problem statement asks for four inputs — current fill level, predicted
overflow time, location and waste type — so the score is an explicit weighted
sum of exactly those four, plus a chronic-offender term. A single opaque number
would be useless to a dispatcher who has to justify why one ward was served
before another, so every component is returned alongside the total.

Each component is normalised to 0..1 before weighting. Without that, the term
with the largest raw range silently dominates: hours-to-full runs to hundreds
while fill level caps at 100.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.models.bin import Bin
from app.models.enums import BinStatus, PriorityTier, WasteType
from app.models.prediction import FillPrediction
from app.services.geo import haversine_km
from app.services.waste import estimate_weight_kg

# Weights sum to 1.0. Fill level and predicted overflow carry the most because
# they describe the actual risk; location is a tie-breaker between bins of
# similar urgency, not a reason to serve a near-empty bin.
WEIGHTS = {
    "fill": 0.35,
    "overflow": 0.30,
    "waste_type": 0.15,
    "location": 0.10,
    "chronic": 0.10,
}

# Hours-to-full beyond this is treated as "not urgent at all"; compressing the
# tail stops a bin that fills in 200 hours scoring meaningfully differently
# from one that fills in 400.
URGENCY_HORIZON_HOURS = 48.0

# Distance beyond which proximity stops discriminating between bins.
LOCATION_HORIZON_KM = 15.0

TIER_THRESHOLDS = (
    (0.75, PriorityTier.P0_CRITICAL),
    (0.55, PriorityTier.P1_HIGH),
    (0.35, PriorityTier.P2_MEDIUM),
)


@dataclass
class PriorityComponents:
    fill: float
    overflow: float
    waste_type: float
    location: float
    chronic: float

    def as_dict(self) -> dict[str, float]:
        return {
            "fill": round(self.fill, 4),
            "overflow": round(self.overflow, 4),
            "waste_type": round(self.waste_type, 4),
            "location": round(self.location, 4),
            "chronic": round(self.chronic, 4),
        }


@dataclass
class PrioritizedBin:
    bin: Bin
    score: float
    tier: PriorityTier
    components: PriorityComponents
    hours_to_full: float | None
    is_overdue: bool
    distance_km: float | None
    expected_volume_liters: float
    expected_weight_kg: float
    reasons: list[str] = field(default_factory=list)

    @property
    def bin_id(self) -> int:
        return self.bin.id


def _fill_component(fill_level: float, threshold: float) -> float:
    """Ramps from 0 at half the threshold to 1 at the threshold, then saturates.

    A linear function of raw fill level would rank a 10% bin above a 5% bin,
    which is a distinction without a difference. What matters is proximity to
    the point where the bin needs emptying.
    """
    floor = threshold / 2.0
    if fill_level <= floor:
        return 0.0
    if fill_level >= threshold:
        # Overfull bins keep gaining, so an already-overflowing bin still
        # outranks one sitting exactly at the threshold. The overshoot is kept
        # inside 0..1 so the weighted total stays bounded.
        overshoot = (fill_level - threshold) / max(1.0, 100.0 - threshold)
        return 0.9 + 0.1 * min(1.0, overshoot)
    return (fill_level - floor) / (threshold - floor)


def _overflow_component(hours_to_full: float | None) -> float:
    """1.0 for a bin overflowing now, decaying to 0 at the urgency horizon."""
    if hours_to_full is None:
        # No forecast is not the same as no urgency; fall back to neutral so
        # the fill component decides rather than pushing the bin to the bottom.
        return 0.3
    if hours_to_full <= 0:
        return 1.0
    return max(0.0, 1.0 - hours_to_full / URGENCY_HORIZON_HOURS)


def _waste_type_component(waste_type: WasteType) -> float:
    """Normalised decay urgency: organic rots, glass does not."""
    factors = [w.decay_factor for w in WasteType]
    low, high = min(factors), max(factors)
    return (waste_type.decay_factor - low) / (high - low) if high > low else 0.5


def _location_component(distance_km: float | None) -> float:
    if distance_km is None:
        return 0.5
    return max(0.0, 1.0 - distance_km / LOCATION_HORIZON_KM)


def _chronic_component(overflow_count: int) -> float:
    """Saturating function of past overflows.

    A bin that has overflowed ten times is a recurring failure; the difference
    between ten and fifty is not worth ranking on, so the curve flattens.
    """
    return min(1.0, overflow_count / 10.0)


def _tier_for(score: float) -> PriorityTier:
    for threshold, tier in TIER_THRESHOLDS:
        if score >= threshold:
            return tier
    return PriorityTier.P3_ROUTINE


def _reasons(
    bin_obj: Bin, fill: float, threshold: float, hours: float | None, overdue: bool
) -> list[str]:
    """Plain-language justification, so the ranking is defensible to an operator."""
    notes: list[str] = []
    if fill >= settings.bin_critical_threshold:
        notes.append(f"Overflowing at {fill:.0f}%")
    elif fill >= threshold:
        notes.append(f"Past the {threshold:.0f}% collection threshold")

    if hours is not None and hours <= settings.overflow_alert_horizon_hours:
        notes.append(f"Forecast to fill in {hours:.1f}h")

    if bin_obj.waste_type is WasteType.ORGANIC and fill >= 50:
        notes.append("Organic waste; odour and vermin risk rises quickly")

    if overdue:
        notes.append("No collection recorded in over a week")

    if bin_obj.overflow_count >= 5:
        notes.append(f"Chronic problem bin ({bin_obj.overflow_count} past overflows)")

    return notes


def _latest_forecast_hours(db: Session, bin_ids: list[int]) -> dict[int, float | None]:
    """Newest hours-to-full per bin, from the Step 5 forecasts."""
    if not bin_ids:
        return {}

    rows = db.execute(
        select(
            FillPrediction.bin_id,
            FillPrediction.hours_to_full,
            FillPrediction.generated_at,
        )
        .where(FillPrediction.bin_id.in_(bin_ids))
        .order_by(FillPrediction.bin_id, FillPrediction.generated_at.desc())
    ).all()

    latest: dict[int, float | None] = {}
    for bin_id, hours, _ in rows:
        if bin_id not in latest:
            latest[bin_id] = hours
    return latest


def score_bin(
    bin_obj: Bin,
    *,
    hours_to_full: float | None,
    distance_km: float | None,
    now: datetime | None = None,
) -> PrioritizedBin:
    now = now or datetime.now(timezone.utc)
    threshold = bin_obj.fill_threshold_override or settings.bin_full_threshold
    fill = bin_obj.current_fill_level

    overdue = (
        bin_obj.last_emptied_at is not None
        and (now - bin_obj.last_emptied_at).total_seconds() > 7 * 24 * 3600
    )

    components = PriorityComponents(
        fill=_fill_component(fill, threshold),
        overflow=_overflow_component(hours_to_full),
        waste_type=_waste_type_component(bin_obj.waste_type),
        location=_location_component(distance_km),
        chronic=_chronic_component(bin_obj.overflow_count),
    )

    score = (
        WEIGHTS["fill"] * components.fill
        + WEIGHTS["overflow"] * components.overflow
        + WEIGHTS["waste_type"] * components.waste_type
        + WEIGHTS["location"] * components.location
        + WEIGHTS["chronic"] * components.chronic
    )

    # A bin left uncollected for a week is an operational failure regardless of
    # what the sensors say, so it is escalated rather than merely nudged.
    if overdue:
        score = min(1.0, score + 0.10)

    volume = bin_obj.capacity_liters * fill / 100.0

    return PrioritizedBin(
        bin=bin_obj,
        score=round(score, 4),
        tier=_tier_for(score),
        components=components,
        hours_to_full=hours_to_full,
        is_overdue=overdue,
        distance_km=round(distance_km, 3) if distance_km is not None else None,
        expected_volume_liters=round(volume, 2),
        expected_weight_kg=estimate_weight_kg(bin_obj.waste_type, volume),
        reasons=_reasons(bin_obj, fill, threshold, hours_to_full, overdue),
    )


def prioritize(
    db: Session,
    *,
    zone_id: int | None = None,
    waste_types: list[WasteType] | None = None,
    origin: tuple[float, float] | None = None,
    min_score: float | None = None,
    min_fill: float | None = None,
    limit: int | None = None,
    now: datetime | None = None,
) -> list[PrioritizedBin]:
    """Rank active bins by collection priority, highest first.

    `origin` is the point distances are measured from — normally a depot. When
    omitted the location term goes neutral rather than being dropped, which
    keeps scores comparable between calls that do and do not supply one.
    """
    stmt = select(Bin).options(joinedload(Bin.zone)).where(Bin.status == BinStatus.ACTIVE)
    if zone_id is not None:
        stmt = stmt.where(Bin.zone_id == zone_id)
    if waste_types:
        stmt = stmt.where(Bin.waste_type.in_(waste_types))
    if min_fill is not None:
        stmt = stmt.where(Bin.current_fill_level >= min_fill)

    bins = list(db.scalars(stmt))
    forecasts = _latest_forecast_hours(db, [b.id for b in bins])

    ranked = [
        score_bin(
            bin_obj,
            hours_to_full=forecasts.get(bin_obj.id),
            distance_km=(
                haversine_km(origin[0], origin[1], bin_obj.latitude, bin_obj.longitude)
                if origin
                else None
            ),
            now=now,
        )
        for bin_obj in bins
    ]

    if min_score is not None:
        ranked = [item for item in ranked if item.score >= min_score]

    # Ties broken by fill level so the ordering is deterministic across calls,
    # which matters because the route optimizer consumes this list.
    ranked.sort(key=lambda item: (-item.score, -item.bin.current_fill_level, item.bin.id))
    return ranked[:limit] if limit else ranked


def tier_counts(ranked: list[PrioritizedBin]) -> dict[str, int]:
    counts = {tier.value: 0 for tier in PriorityTier}
    for item in ranked:
        counts[item.tier.value] += 1
    return counts

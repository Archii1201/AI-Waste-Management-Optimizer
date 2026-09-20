"""Operational analytics and the recommendations derived from them.

Everything here reads tables the rest of the system already writes — no new
storage and no background aggregation job. At city scale the queries are
cheap, and a demo that computes its numbers live is more convincing than one
reading a table somebody could have hand-filled.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.alert import Alert
from app.models.bin import Bin
from app.models.classification import WasteClassification
from app.models.collection import CollectionEvent
from app.models.enums import (
    AlertStatus,
    BinStatus,
    RouteStatus,
    StopStatus,
    WasteType,
)
from app.models.prediction import FillPrediction
from app.models.route import Route, RouteStop
from app.models.zone import Zone

logger = get_logger(__name__)

DEFAULT_WINDOW_DAYS = 30

# A bin emptied below this was not worth the trip; the truck burned fuel and
# crew time to collect mostly air.
EARLY_COLLECTION_FILL_PCT = 50.0
# Share of collections that may be "early" before the schedule is judged wasteful.
OVER_SERVICE_TOLERANCE_PCT = 25.0
# Contamination above this makes a recyclable load uneconomic to process.
CONTAMINATION_CONCERN_PCT = 15.0


def _window(days: int) -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc)
    return end - timedelta(days=days), end


# ---------------------------------------------------------------------------
# Metric blocks
# ---------------------------------------------------------------------------
def collection_stats(db: Session, *, days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """Tonnage, recyclable split and service quality over the window."""
    start, _ = _window(days)

    events = list(
        db.scalars(
            select(CollectionEvent).where(CollectionEvent.collected_at >= start)
        )
    )

    if not events:
        return {
            "window_days": days,
            "collections": 0,
            "total_weight_kg": 0.0,
            "total_volume_liters": 0.0,
            "recyclable_kg": 0.0,
            "non_recyclable_kg": 0.0,
            "recyclable_pct": 0.0,
            "avg_fill_at_collection": 0.0,
            "early_collections": 0,
            "early_collection_pct": 0.0,
            "overflow_collections": 0,
            "overflow_collection_pct": 0.0,
            "avg_contamination_pct": None,
            "by_waste_type": {},
            "collections_per_day": 0.0,
        }

    total_weight = sum(e.weight_collected_kg for e in events)
    recyclable = sum(e.recyclable_kg for e in events)
    early = sum(1 for e in events if e.fill_level_before < EARLY_COLLECTION_FILL_PCT)
    overflowing = sum(1 for e in events if e.was_overflowing)
    contamination = [e.contamination_pct for e in events if e.contamination_pct is not None]

    by_type: dict[str, dict] = {}
    for event in events:
        bucket = by_type.setdefault(
            event.waste_type.value,
            {"collections": 0, "weight_kg": 0.0, "recyclable_kg": 0.0},
        )
        bucket["collections"] += 1
        bucket["weight_kg"] += event.weight_collected_kg
        bucket["recyclable_kg"] += event.recyclable_kg

    for bucket in by_type.values():
        bucket["weight_kg"] = round(bucket["weight_kg"], 2)
        bucket["recyclable_kg"] = round(bucket["recyclable_kg"], 2)

    return {
        "window_days": days,
        "collections": len(events),
        "total_weight_kg": round(total_weight, 2),
        "total_volume_liters": round(sum(e.volume_collected_liters for e in events), 2),
        "recyclable_kg": round(recyclable, 2),
        "non_recyclable_kg": round(total_weight - recyclable, 2),
        "recyclable_pct": round(100.0 * recyclable / total_weight, 1) if total_weight else 0.0,
        "avg_fill_at_collection": round(
            sum(e.fill_level_before for e in events) / len(events), 1
        ),
        "early_collections": early,
        "early_collection_pct": round(100.0 * early / len(events), 1),
        "overflow_collections": overflowing,
        "overflow_collection_pct": round(100.0 * overflowing / len(events), 1),
        "avg_contamination_pct": (
            round(sum(contamination) / len(contamination), 1) if contamination else None
        ),
        "by_waste_type": by_type,
        "collections_per_day": round(len(events) / max(1, days), 2),
    }


def route_stats(db: Session, *, days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """Distance, cost and how much optimisation actually saved."""
    start, _ = _window(days)
    routes = list(db.scalars(select(Route).where(Route.created_at >= start)))

    if not routes:
        return {
            "window_days": days,
            "routes": 0,
            "total_stops": 0,
            "total_distance_km": 0.0,
            "baseline_distance_km": 0.0,
            "distance_saved_km": 0.0,
            "distance_saved_pct": None,
            "estimated_cost": 0.0,
            "avg_stops_per_route": 0.0,
            "completion_rate_pct": None,
            "deferred_bins": 0,
        }

    optimised = sum(r.total_distance_km for r in routes)
    baseline = sum(r.baseline_distance_km or 0.0 for r in routes)
    total_stops = sum(r.total_stops for r in routes)

    completed_stops = db.scalar(
        select(func.count())
        .select_from(RouteStop)
        .where(
            RouteStop.route_id.in_([r.id for r in routes]),
            RouteStop.status == StopStatus.COLLECTED,
        )
    ) or 0

    return {
        "window_days": days,
        "routes": len(routes),
        "total_stops": total_stops,
        "total_distance_km": round(optimised, 2),
        "baseline_distance_km": round(baseline, 2),
        "distance_saved_km": round(baseline - optimised, 2),
        "distance_saved_pct": (
            round((1 - optimised / baseline) * 100.0, 1) if baseline > 0 else None
        ),
        "estimated_cost": round(sum(r.estimated_cost or 0.0 for r in routes), 2),
        "avg_stops_per_route": round(total_stops / len(routes), 1),
        "completion_rate_pct": (
            round(100.0 * completed_stops / total_stops, 1) if total_stops else None
        ),
        "deferred_bins": sum(len(r.deferred_bins or []) for r in routes),
    }


def fill_stats(db: Session) -> dict:
    """Live network state plus how well the forecasts are holding up."""
    bins = list(db.scalars(select(Bin).where(Bin.status == BinStatus.ACTIVE)))

    if not bins:
        return {
            "active_bins": 0,
            "avg_fill_level": 0.0,
            "bins_over_threshold": 0,
            "bins_critical": 0,
            "total_capacity_liters": 0.0,
            "capacity_in_use_pct": 0.0,
            "forecasts_stored": 0,
            "forecast_mae_hours": None,
            "bins_forecast_to_overflow_24h": 0,
        }

    capacity = sum(b.capacity_liters for b in bins)
    in_use = sum(b.capacity_liters * b.current_fill_level / 100.0 for b in bins)

    scored = list(
        db.scalars(
            select(FillPrediction.absolute_error_hours).where(
                FillPrediction.absolute_error_hours.is_not(None)
            )
        )
    )

    soon = db.scalar(
        select(func.count(func.distinct(FillPrediction.bin_id))).where(
            FillPrediction.hours_to_full.is_not(None),
            FillPrediction.hours_to_full <= 24,
        )
    ) or 0

    return {
        "active_bins": len(bins),
        "avg_fill_level": round(sum(b.current_fill_level for b in bins) / len(bins), 1),
        "bins_over_threshold": sum(
            1
            for b in bins
            if b.current_fill_level
            >= (b.fill_threshold_override or settings.bin_full_threshold)
        ),
        "bins_critical": sum(
            1 for b in bins if b.current_fill_level >= settings.bin_critical_threshold
        ),
        "total_capacity_liters": round(capacity, 1),
        "capacity_in_use_pct": round(100.0 * in_use / capacity, 1) if capacity else 0.0,
        "forecasts_stored": db.scalar(
            select(func.count()).select_from(FillPrediction)
        ) or 0,
        "forecast_mae_hours": (
            round(sum(scored) / len(scored), 2) if scored else None
        ),
        "bins_forecast_to_overflow_24h": soon,
    }


def waste_stats(db: Session, *, days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """The recyclable/non-recyclable estimate the problem statement asks for."""
    start, _ = _window(days)

    rows = db.execute(
        select(
            CollectionEvent.waste_type,
            func.sum(CollectionEvent.weight_collected_kg),
            func.sum(CollectionEvent.recyclable_kg),
            func.count(),
        )
        .where(CollectionEvent.collected_at >= start)
        .group_by(CollectionEvent.waste_type)
    ).all()

    streams = {}
    total_weight = 0.0
    total_recyclable = 0.0

    for waste_type, weight, recyclable, count in rows:
        weight = float(weight or 0.0)
        recyclable = float(recyclable or 0.0)
        total_weight += weight
        total_recyclable += recyclable
        streams[waste_type.value] = {
            "collections": count,
            "weight_kg": round(weight, 2),
            "recyclable_kg": round(recyclable, 2),
            "diversion_pct": round(100.0 * recyclable / weight, 1) if weight else 0.0,
        }

    classifications = db.scalar(
        select(func.count()).select_from(WasteClassification)
    ) or 0
    contaminated = db.scalar(
        select(func.count())
        .select_from(WasteClassification)
        .where(WasteClassification.needs_review.is_(True))
    ) or 0

    return {
        "window_days": days,
        "total_weight_kg": round(total_weight, 2),
        "recyclable_kg": round(total_recyclable, 2),
        "non_recyclable_kg": round(total_weight - total_recyclable, 2),
        "diversion_rate_pct": (
            round(100.0 * total_recyclable / total_weight, 1) if total_weight else 0.0
        ),
        "by_stream": streams,
        "images_classified": classifications,
        "classifications_pending_review": contaminated,
        "projected_annual_weight_kg": round(total_weight / max(1, days) * 365, 1),
    }


def zone_stats(db: Session, *, days: int = DEFAULT_WINDOW_DAYS) -> list[dict]:
    """Per-zone comparison, which is what drives schedule recommendations."""
    start, _ = _window(days)
    zones = list(db.scalars(select(Zone)))
    results: list[dict] = []

    for zone in zones:
        bins = list(
            db.scalars(
                select(Bin).where(Bin.zone_id == zone.id, Bin.status == BinStatus.ACTIVE)
            )
        )
        if not bins:
            continue

        bin_ids = [b.id for b in bins]
        events = list(
            db.scalars(
                select(CollectionEvent).where(
                    CollectionEvent.bin_id.in_(bin_ids),
                    CollectionEvent.collected_at >= start,
                )
            )
        )

        overflow_count = sum(b.overflow_count for b in bins)
        weight = sum(e.weight_collected_kg for e in events)

        results.append(
            {
                "zone_id": zone.id,
                "zone_code": zone.code,
                "zone_name": zone.name,
                "zone_type": (
                    zone.zone_type.value
                    if hasattr(zone.zone_type, "value")
                    else zone.zone_type
                ),
                "bins": len(bins),
                "avg_fill_level": round(
                    sum(b.current_fill_level for b in bins) / len(bins), 1
                ),
                "collections": len(events),
                "collected_weight_kg": round(weight, 2),
                "kg_per_bin_per_day": round(
                    weight / len(bins) / max(1, days), 3
                ),
                "overflow_events": overflow_count,
                "avg_fill_at_collection": (
                    round(sum(e.fill_level_before for e in events) / len(events), 1)
                    if events
                    else None
                ),
                "open_alerts": db.scalar(
                    select(func.count())
                    .select_from(Alert)
                    .where(
                        Alert.zone_id == zone.id,
                        Alert.status.in_([AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED]),
                    )
                )
                or 0,
            }
        )

    results.sort(key=lambda z: -z["kg_per_bin_per_day"])
    return results


def overview(db: Session, *, days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """Everything the dashboard landing page needs, in one call."""
    from app.services import alert_service

    return {
        "window_days": days,
        "collections": collection_stats(db, days=days),
        "routes": route_stats(db, days=days),
        "fill": fill_stats(db),
        "waste": waste_stats(db, days=days),
        "alerts": alert_service.alert_summary(db),
    }


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------
def _recommendation(
    category: str, priority: str, title: str, detail: str, action: str, evidence: dict
) -> dict:
    return {
        "category": category,
        "priority": priority,
        "title": title,
        "detail": detail,
        "action": action,
        "evidence": evidence,
    }


def recommendations(db: Session, *, days: int = DEFAULT_WINDOW_DAYS) -> list[dict]:
    """Turn the metrics into concrete operational advice.

    Deliberately rule-based rather than learned: a recommendation an operator
    cannot trace back to a number is one they will not act on, and every rule
    here carries the evidence that triggered it.
    """
    collections = collection_stats(db, days=days)
    routes = route_stats(db, days=days)
    fill = fill_stats(db)
    waste = waste_stats(db, days=days)
    zones = zone_stats(db, days=days)

    output: list[dict] = []

    # ---------- Schedule: over-servicing ----------
    if (
        collections["collections"] > 0
        and collections["early_collection_pct"] > OVER_SERVICE_TOLERANCE_PCT
    ):
        output.append(
            _recommendation(
                "collection_schedule",
                "high",
                "Trucks are emptying bins that are barely full",
                f"{collections['early_collection_pct']:.0f}% of collections happened below "
                f"{EARLY_COLLECTION_FILL_PCT:.0f}% fill, averaging "
                f"{collections['avg_fill_at_collection']:.0f}% across the window. Those "
                "trips cost fuel and crew hours for very little waste.",
                "Switch these bins from fixed-schedule to demand-driven collection, "
                "planning from /api/v1/priorities instead of a fixed round.",
                {
                    "early_collection_pct": collections["early_collection_pct"],
                    "avg_fill_at_collection": collections["avg_fill_at_collection"],
                    "collections": collections["collections"],
                },
            )
        )

    # ---------- Schedule: under-servicing ----------
    if collections["collections"] > 0 and collections["overflow_collection_pct"] > 10:
        output.append(
            _recommendation(
                "collection_schedule",
                "high",
                "Bins are being collected only after they overflow",
                f"{collections['overflow_collection_pct']:.0f}% of collections found the bin "
                "already past the critical threshold, which is the failure mode residents "
                "actually complain about.",
                "Increase collection frequency in the worst zones and act on "
                "overflow-imminent alerts before they escalate.",
                {
                    "overflow_collection_pct": collections["overflow_collection_pct"],
                    "overflow_collections": collections["overflow_collections"],
                },
            )
        )

    # ---------- Immediate capacity risk ----------
    if fill["bins_critical"] > 0:
        output.append(
            _recommendation(
                "immediate_action",
                "critical",
                f"{fill['bins_critical']} bins are overflowing right now",
                f"{fill['bins_critical']} of {fill['active_bins']} active bins are at or "
                f"past {settings.bin_critical_threshold:.0f}% fill.",
                "Dispatch a route immediately via POST /api/v1/routes/optimize.",
                {
                    "bins_critical": fill["bins_critical"],
                    "bins_over_threshold": fill["bins_over_threshold"],
                },
            )
        )

    # ---------- Recycling quality ----------
    if (
        collections["avg_contamination_pct"] is not None
        and collections["avg_contamination_pct"] > CONTAMINATION_CONCERN_PCT
    ):
        output.append(
            _recommendation(
                "recycling",
                "medium",
                "Recyclable loads are being contaminated",
                f"Average contamination is {collections['avg_contamination_pct']:.0f}%, above "
                f"the {CONTAMINATION_CONCERN_PCT:.0f}% level at which reprocessing stops "
                "being economic.",
                "Run a segregation awareness push in the worst zones and use the image "
                "classifier at drop-off points to catch wrong-bin disposal.",
                {"avg_contamination_pct": collections["avg_contamination_pct"]},
            )
        )

    if waste["total_weight_kg"] > 0 and waste["diversion_rate_pct"] < 40:
        output.append(
            _recommendation(
                "recycling",
                "medium",
                "Diversion from landfill is low",
                f"Only {waste['diversion_rate_pct']:.0f}% of collected tonnage is being "
                "recovered. Most of the remainder is mixed waste that was never "
                "segregated at source.",
                "Add segregated recyclable bins in the highest-tonnage zones; mixed "
                "streams recover far less than sorted ones.",
                {
                    "diversion_rate_pct": waste["diversion_rate_pct"],
                    "total_weight_kg": waste["total_weight_kg"],
                },
            )
        )

    # ---------- Fleet capacity ----------
    if routes["deferred_bins"] > 0:
        output.append(
            _recommendation(
                "fleet",
                "high",
                "The fleet could not service every priority bin",
                f"{routes['deferred_bins']} bin-visits were deferred because vehicle "
                "capacity or shift length ran out.",
                "Add a shift or a vehicle, or split the worst zone across two rounds.",
                {"deferred_bins": routes["deferred_bins"]},
            )
        )

    if routes["routes"] > 0 and routes["distance_saved_pct"] is not None:
        output.append(
            _recommendation(
                "fleet",
                "info",
                "Route optimisation is reducing distance travelled",
                f"Optimised routes covered {routes['total_distance_km']:.1f}km against a "
                f"{routes['baseline_distance_km']:.1f}km unoptimised baseline, a "
                f"{routes['distance_saved_pct']:.1f}% reduction.",
                "Keep planning from the optimizer rather than fixed rounds.",
                {
                    "distance_saved_km": routes["distance_saved_km"],
                    "distance_saved_pct": routes["distance_saved_pct"],
                },
            )
        )

    # ---------- Zone hot spots ----------
    if zones:
        busiest = zones[0]
        if busiest["overflow_events"] > 0 or (
            busiest["avg_fill_level"] >= settings.bin_full_threshold
        ):
            output.append(
                _recommendation(
                    "zone_capacity",
                    "medium",
                    f"{busiest['zone_name']} is the network's pressure point",
                    f"It generates {busiest['kg_per_bin_per_day']:.2f} kg per bin per day "
                    f"across {busiest['bins']} bins, with {busiest['overflow_events']} "
                    "recorded overflows.",
                    "Add bin capacity here, or schedule a second daily round for this zone.",
                    {
                        "zone_code": busiest["zone_code"],
                        "kg_per_bin_per_day": busiest["kg_per_bin_per_day"],
                        "overflow_events": busiest["overflow_events"],
                    },
                )
            )

    # ---------- Data quality ----------
    if fill["forecasts_stored"] == 0:
        output.append(
            _recommendation(
                "data_quality",
                "medium",
                "No fill-level forecasts have been generated",
                "Prioritisation falls back to current fill alone without predicted "
                "overflow times, which weakens the ranking.",
                "Run `python -m app.cli predict` to populate forecasts.",
                {"forecasts_stored": 0},
            )
        )

    order = {"critical": 0, "high": 1, "medium": 2, "info": 3}
    output.sort(key=lambda r: order.get(r["priority"], 9))
    return output

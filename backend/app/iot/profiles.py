"""Waste-generation physics.

This module is the single source of truth for *how fast a bin fills*. Both the
live device simulator and the historical data generator call into it, so the
90 days of training history and the telemetry arriving during a demo come from
exactly the same process. If they diverged, the model would be trained on one
world and evaluated in another.

The model is deliberately simple and explainable:

    increment = base_daily_rate / 24 * hours
                * hour_of_day_factor(zone)
                * day_of_week_factor(zone)
                * waste_stream_factor
                * noise
                * occasional_event_spike

Every profile is normalised to a mean of 1.0, so `base_daily_rate` keeps its
plain meaning of "percentage points this bin gains on an average day".
"""

from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.models.enums import WasteType, ZoneType

CITY_TZ = ZoneInfo(settings.city_timezone)


def _normalise(values: tuple[float, ...]) -> tuple[float, ...]:
    mean = sum(values) / len(values)
    return tuple(v / mean for v in values)


# ---------------------------------------------------------------------------
# Hour-of-day shapes (index 0 = midnight, local city time)
# ---------------------------------------------------------------------------
# Residential waste arrives in two spikes: households clear up after breakfast
# and again after the evening meal.
_RESIDENTIAL_HOURS = (
    0.2, 0.1, 0.1, 0.1, 0.2, 0.5, 1.2, 2.2, 2.4, 1.6, 1.0, 0.8,
    0.9, 0.8, 0.7, 0.8, 1.1, 1.8, 2.6, 2.8, 2.2, 1.4, 0.8, 0.4,
)
# Commercial waste tracks trading hours with a lunchtime peak.
_COMMERCIAL_HOURS = (
    0.2, 0.1, 0.1, 0.1, 0.2, 0.4, 0.8, 1.2, 1.8, 2.2, 2.4, 2.8,
    3.0, 2.6, 2.2, 2.0, 1.9, 1.8, 1.6, 1.2, 0.8, 0.5, 0.3, 0.2,
)
# Institutional (schools, offices) is a flat weekday block.
_INSTITUTIONAL_HOURS = (
    0.1, 0.1, 0.1, 0.1, 0.1, 0.2, 0.6, 1.4, 2.2, 2.4, 2.4, 2.3,
    2.5, 2.4, 2.2, 2.0, 1.4, 0.8, 0.4, 0.3, 0.2, 0.2, 0.1, 0.1,
)
# Industrial runs on shifts and is the steadiest of all.
_INDUSTRIAL_HOURS = (
    0.6, 0.6, 0.6, 0.6, 0.7, 0.9, 1.3, 1.6, 1.7, 1.7, 1.7, 1.6,
    1.5, 1.6, 1.7, 1.7, 1.6, 1.4, 1.1, 0.9, 0.8, 0.7, 0.6, 0.6,
)
# Public spaces (parks, promenades, markets) fill in the evening.
_PUBLIC_HOURS = (
    0.3, 0.2, 0.1, 0.1, 0.1, 0.3, 0.7, 1.0, 1.2, 1.3, 1.4, 1.6,
    1.7, 1.6, 1.5, 1.7, 2.2, 2.8, 3.2, 3.0, 2.4, 1.6, 0.9, 0.5,
)

HOURLY_PROFILES: dict[ZoneType, tuple[float, ...]] = {
    ZoneType.RESIDENTIAL: _normalise(_RESIDENTIAL_HOURS),
    ZoneType.COMMERCIAL: _normalise(_COMMERCIAL_HOURS),
    ZoneType.INSTITUTIONAL: _normalise(_INSTITUTIONAL_HOURS),
    ZoneType.INDUSTRIAL: _normalise(_INDUSTRIAL_HOURS),
    ZoneType.PUBLIC: _normalise(_PUBLIC_HOURS),
    # A mixed-use ward is the average of its residential and commercial halves.
    ZoneType.MIXED_USE: _normalise(
        tuple((r + c) / 2 for r, c in zip(_RESIDENTIAL_HOURS, _COMMERCIAL_HOURS))
    ),
}

# ---------------------------------------------------------------------------
# Day-of-week shapes (index 0 = Monday)
# ---------------------------------------------------------------------------
WEEKDAY_PROFILES: dict[ZoneType, tuple[float, ...]] = {
    # People are home more at weekends, and cook more.
    ZoneType.RESIDENTIAL: _normalise((0.95, 0.92, 0.94, 0.98, 1.10, 1.25, 1.18)),
    # Saturday is the busiest retail day; Sunday trading is lighter.
    ZoneType.COMMERCIAL: _normalise((1.00, 1.00, 1.02, 1.08, 1.20, 1.30, 0.75)),
    # Schools and offices are nearly empty at weekends.
    ZoneType.INSTITUTIONAL: _normalise((1.30, 1.30, 1.30, 1.30, 1.25, 0.25, 0.12)),
    ZoneType.INDUSTRIAL: _normalise((1.15, 1.15, 1.15, 1.15, 1.15, 0.85, 0.40)),
    # Parks and promenades are a weekend destination.
    ZoneType.PUBLIC: _normalise((0.75, 0.72, 0.75, 0.82, 1.15, 1.75, 1.85)),
    ZoneType.MIXED_USE: _normalise((1.00, 0.98, 1.00, 1.05, 1.15, 1.25, 0.95)),
}

# ---------------------------------------------------------------------------
# Waste-stream factors
# ---------------------------------------------------------------------------
# How quickly each stream consumes volume. Plastic and paper are bulky and fill
# a bin fast for very little mass; glass and metal accumulate slowly.
WASTE_STREAM_FACTOR: dict[WasteType, float] = {
    WasteType.PLASTIC: 1.35,
    WasteType.PAPER: 1.20,
    WasteType.ORGANIC: 1.00,
    WasteType.MIXED: 1.00,
    WasteType.OTHER: 0.80,
    WasteType.METAL: 0.55,
    WasteType.GLASS: 0.50,
}

# An unusual dumping event (a shop clearing stock, a wedding, a festival) makes
# a bin fill several times faster for one interval. These are what the anomaly
# detector in the alerting step is expected to catch.
EVENT_SPIKE_PROBABILITY_PER_HOUR = 0.006
EVENT_SPIKE_RANGE = (2.5, 6.0)

# Multiplicative sensor/behavioural noise applied to every interval.
NOISE_RANGE = (0.70, 1.30)


def stable_unit(seed_text: str) -> float:
    """Deterministic number in [0, 1) derived from a string.

    Python's built-in `hash()` is randomised per process, which would give a bin
    a different personality on every restart. A real hash keeps bin MUM-AND-0142
    a consistently fast filler across runs, restarts and machines.
    """
    digest = hashlib.blake2b(seed_text.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64


def base_daily_fill_pct(bin_code: str, zone_type: ZoneType) -> float:
    """Percentage points an individual bin gains on an average day.

    Derived from the bin's code so each bin has a stable character, then scaled
    by how much waste its zone type generates. A value of 60 means the bin goes
    from empty to 60% full in a typical day, i.e. needs collecting every ~1.5 days.
    """
    zone_scale = {
        ZoneType.RESIDENTIAL: 1.00,
        ZoneType.COMMERCIAL: 1.45,
        ZoneType.INSTITUTIONAL: 0.85,
        ZoneType.INDUSTRIAL: 1.15,
        ZoneType.PUBLIC: 1.30,
        ZoneType.MIXED_USE: 1.20,
    }[zone_type]

    # 22-82 points per day before scaling, so the fleet spans bins that need
    # daily collection and bins that can wait most of a week.
    spread = 22.0 + stable_unit(f"rate:{bin_code}") * 60.0
    return spread * zone_scale


def hour_factor(zone_type: ZoneType, moment: datetime) -> float:
    return HOURLY_PROFILES[zone_type][_local(moment).hour]


def weekday_factor(zone_type: ZoneType, moment: datetime) -> float:
    return WEEKDAY_PROFILES[zone_type][_local(moment).weekday()]


def _local(moment: datetime) -> datetime:
    """Convert to city-local time.

    Hour-of-day and day-of-week patterns are human behaviour, so they must be
    evaluated in the city's timezone even though everything is stored in UTC.
    """
    return moment.astimezone(CITY_TZ)


def fill_increment_pct(
    *,
    bin_code: str,
    zone_type: ZoneType,
    waste_type: WasteType,
    moment: datetime,
    hours: float,
    rng: random.Random,
    base_daily_pct: float | None = None,
    allow_spikes: bool = True,
) -> float:
    """Percentage points a bin gains over `hours` starting at `moment`."""
    if hours <= 0:
        return 0.0

    daily = base_daily_pct if base_daily_pct is not None else base_daily_fill_pct(bin_code, zone_type)

    increment = (daily / 24.0) * hours
    increment *= hour_factor(zone_type, moment)
    increment *= weekday_factor(zone_type, moment)
    increment *= WASTE_STREAM_FACTOR.get(waste_type, 1.0)
    increment *= rng.uniform(*NOISE_RANGE)

    if allow_spikes and rng.random() < EVENT_SPIKE_PROBABILITY_PER_HOUR * hours:
        increment *= rng.uniform(*EVENT_SPIKE_RANGE)

    return max(0.0, increment)


def ambient_temperature_c(moment: datetime, waste_type: WasteType, rng: random.Random) -> float:
    """Plausible in-bin temperature.

    Mumbai sits around 27-33 degrees. Decomposing organic waste self-heats,
    which is a genuine signal: an unusually hot organic bin is one that has gone
    too long without collection.
    """
    local = _local(moment)
    # Cosine peaking at 15:00 and bottoming out twelve hours later, at 03:00.
    diurnal = 4.0 * math.cos(2 * math.pi * (local.hour - 15) / 24.0)
    organic_heat = 6.0 if waste_type is WasteType.ORGANIC else 0.0
    return round(29.5 + diurnal + organic_heat + rng.uniform(-1.2, 1.2), 1)

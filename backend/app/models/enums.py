"""Controlled vocabularies shared by the database models, API schemas and ML code.

These are plain `str` enums so they serialise cleanly to JSON and are stored as
readable values in PostgreSQL rather than opaque integers.
"""

from __future__ import annotations

from enum import Enum


class WasteType(str, Enum):
    """The six categories required by the problem statement, plus MIXED.

    MIXED represents a general-purpose bin whose contents are not segregated at
    source; the image classifier is what breaks MIXED down into real categories.
    """

    PLASTIC = "plastic"
    PAPER = "paper"
    METAL = "metal"
    GLASS = "glass"
    ORGANIC = "organic"
    OTHER = "other"
    MIXED = "mixed"

    @property
    def is_recyclable(self) -> bool:
        return self in {WasteType.PLASTIC, WasteType.PAPER, WasteType.METAL, WasteType.GLASS}

    @property
    def decay_factor(self) -> float:
        """Urgency multiplier used by the prioritisation engine.

        Organic waste rots, attracts vermin and generates odour complaints, so a
        half-full organic bin is a bigger problem than a half-full glass bin.
        """
        return {
            WasteType.ORGANIC: 1.60,
            WasteType.MIXED: 1.25,
            WasteType.OTHER: 1.10,
            WasteType.PLASTIC: 1.00,
            WasteType.PAPER: 0.95,
            WasteType.METAL: 0.85,
            WasteType.GLASS: 0.85,
        }[self]


class ZoneType(str, Enum):
    """Land-use class of a zone; drives the generation profile and analytics."""

    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    INDUSTRIAL = "industrial"
    INSTITUTIONAL = "institutional"
    PUBLIC = "public"
    MIXED_USE = "mixed_use"


class BinStatus(str, Enum):
    """Operational state of a bin (distinct from how full it is)."""

    ACTIVE = "active"
    MAINTENANCE = "maintenance"
    OFFLINE = "offline"
    DECOMMISSIONED = "decommissioned"


class VehicleType(str, Enum):
    COMPACTOR = "compactor"
    TIPPER = "tipper"
    RECYCLING_TRUCK = "recycling_truck"
    MINI_TRUCK = "mini_truck"


class VehicleStatus(str, Enum):
    IDLE = "idle"
    EN_ROUTE = "en_route"
    COLLECTING = "collecting"
    UNLOADING = "unloading"
    MAINTENANCE = "maintenance"
    OFFLINE = "offline"


class RouteStatus(str, Enum):
    PLANNED = "planned"
    DISPATCHED = "dispatched"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class StopStatus(str, Enum):
    PENDING = "pending"
    ARRIVED = "arrived"
    COLLECTED = "collected"
    SKIPPED = "skipped"


class PriorityTier(str, Enum):
    """Human-facing bucket derived from the continuous priority score."""

    P0_CRITICAL = "p0_critical"
    P1_HIGH = "p1_high"
    P2_MEDIUM = "p2_medium"
    P3_ROUTINE = "p3_routine"


class AlertType(str, Enum):
    BIN_OVERFLOW_IMMINENT = "bin_overflow_imminent"
    BIN_OVERFLOWING = "bin_overflowing"
    HIGH_GENERATION_ANOMALY = "high_generation_anomaly"
    SENSOR_FAULT = "sensor_fault"
    SENSOR_OFFLINE = "sensor_offline"
    BATTERY_LOW = "battery_low"
    MISSED_COLLECTION = "missed_collection"
    ROUTE_DELAY = "route_delay"


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"


class PredictionMethod(str, Enum):
    """Which estimator produced a fill-level forecast.

    Surfaced through the API so the dashboard can be honest about confidence
    when a newly installed bin has no history to learn from.
    """

    GRADIENT_BOOSTING = "gradient_boosting"
    ROLLING_MEDIAN = "rolling_median"
    ZONE_BASELINE = "zone_baseline"
    INSUFFICIENT_DATA = "insufficient_data"


class ReadingSource(str, Enum):
    MQTT = "mqtt"
    REST = "rest"
    SIMULATOR = "simulator"
    MANUAL = "manual"
    BACKFILL = "backfill"


class UserRole(str, Enum):
    ADMIN = "admin"
    DISPATCHER = "dispatcher"
    DRIVER = "driver"
    VIEWER = "viewer"

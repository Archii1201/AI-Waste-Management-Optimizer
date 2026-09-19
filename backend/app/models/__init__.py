"""Model package.

Every model is imported here so that `Base.metadata` is fully populated by the
time Alembic autogenerate or `create_all` inspects it. Missing an import is the
classic cause of a table silently vanishing from a migration.
"""

from app.models.alert import Alert
from app.models.base import Base, TimestampMixin
from app.models.bin import Bin, BinReading
from app.models.classification import WasteClassification
from app.models.collection import CollectionEvent
from app.models.enums import (
    AlertSeverity,
    AlertStatus,
    AlertType,
    BinStatus,
    PredictionMethod,
    PriorityTier,
    ReadingSource,
    RouteStatus,
    StopStatus,
    UserRole,
    VehicleStatus,
    VehicleType,
    WasteType,
    ZoneType,
)
from app.models.prediction import FillPrediction
from app.models.route import Route, RouteStop
from app.models.user import User
from app.models.vehicle import Vehicle
from app.models.zone import Zone

__all__ = [
    "Alert",
    "AlertSeverity",
    "AlertStatus",
    "AlertType",
    "Base",
    "Bin",
    "BinReading",
    "BinStatus",
    "CollectionEvent",
    "FillPrediction",
    "PredictionMethod",
    "PriorityTier",
    "ReadingSource",
    "Route",
    "RouteStatus",
    "RouteStop",
    "StopStatus",
    "TimestampMixin",
    "User",
    "UserRole",
    "Vehicle",
    "VehicleStatus",
    "VehicleType",
    "WasteClassification",
    "WasteType",
    "Zone",
    "ZoneType",
]

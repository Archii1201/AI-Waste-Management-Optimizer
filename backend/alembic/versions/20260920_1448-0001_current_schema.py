"""Current EcoFlow AI schema.

Revision ID: 0001_current_schema
Revises:
Create Date: 2026-09-20 14:48:00+00:00

First revision. Matches the live SQLAlchemy models. Production databases
should be created with `alembic upgrade head` from `backend/`, not `init-db`.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum as PyEnum

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models.enums import (
    AlertSeverity,
    AlertStatus,
    AlertType,
    BinStatus,
    PredictionMethod,
    ReadingSource,
    RouteStatus,
    StopStatus,
    UserRole,
    VehicleStatus,
    VehicleType,
    WasteType,
    ZoneType,
)

revision: str = "0001_current_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BIGINT_PK = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def _enum(enum_cls: type[PyEnum], name: str, length: int = 40) -> sa.Enum:
    return sa.Enum(
        enum_cls,
        name=name,
        native_enum=False,
        length=length,
        values_callable=lambda cls: [member.value for member in cls],
        validate_strings=True,
    )


def upgrade() -> None:
    op.create_table(
        "zones",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("zone_type", _enum(ZoneType, "zones_zone_type"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("center_lat", sa.Float(), nullable=False),
        sa.Column("center_lon", sa.Float(), nullable=False),
        sa.Column("boundary", JSON_TYPE, nullable=True),
        sa.Column("population_served", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_zones"),
        sa.UniqueConstraint("code", name="uq_zones_code"),
    )
    op.create_index("ix_zones_code", "zones", ["code"], unique=False)

    op.create_table(
        "vehicles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("registration_number", sa.String(length=32), nullable=True),
        sa.Column("vehicle_type", _enum(VehicleType, "vehicles_vehicle_type"), nullable=False),
        sa.Column("capacity_liters", sa.Float(), nullable=False),
        sa.Column("capacity_kg", sa.Float(), nullable=False),
        sa.Column("accepted_waste_types", JSON_TYPE, nullable=True),
        sa.Column("depot_lat", sa.Float(), nullable=False),
        sa.Column("depot_lon", sa.Float(), nullable=False),
        sa.Column("shift_start", sa.Time(), nullable=False),
        sa.Column("shift_end", sa.Time(), nullable=False),
        sa.Column("avg_speed_kmph", sa.Float(), nullable=False),
        sa.Column("cost_per_km", sa.Float(), nullable=False),
        sa.Column("status", _enum(VehicleStatus, "vehicles_status"), nullable=False),
        sa.Column("current_lat", sa.Float(), nullable=True),
        sa.Column("current_lon", sa.Float(), nullable=True),
        sa.Column("current_load_liters", sa.Float(), nullable=False),
        sa.Column("current_load_kg", sa.Float(), nullable=False),
        sa.Column("last_position_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("driver_name", sa.String(length=120), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("capacity_liters > 0", name="ck_vehicles_capacity_liters_positive"),
        sa.CheckConstraint("capacity_kg > 0", name="ck_vehicles_capacity_kg_positive"),
        sa.PrimaryKeyConstraint("id", name="pk_vehicles"),
        sa.UniqueConstraint("code", name="uq_vehicles_code"),
        sa.UniqueConstraint("registration_number", name="uq_vehicles_registration_number"),
    )
    op.create_index("ix_vehicles_code", "vehicles", ["code"], unique=False)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("role", _enum(UserRole, "users_role"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"], name="fk_users_vehicle_id_vehicles", ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=False)

    op.create_table(
        "bins",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("zone_id", sa.Integer(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("capacity_liters", sa.Float(), nullable=False),
        sa.Column("waste_type", _enum(WasteType, "bins_waste_type"), nullable=False),
        sa.Column("status", _enum(BinStatus, "bins_status"), nullable=False),
        sa.Column("current_fill_level", sa.Float(), nullable=False),
        sa.Column("current_weight_kg", sa.Float(), nullable=True),
        sa.Column("battery_level", sa.Float(), nullable=True),
        sa.Column("last_reading_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_emptied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fill_threshold_override", sa.Float(), nullable=True),
        sa.Column("avg_fill_rate_pct_per_hour", sa.Float(), nullable=True),
        sa.Column("overflow_count", sa.Integer(), nullable=False),
        sa.Column("sensor_id", sa.String(length=64), nullable=True),
        sa.Column("installed_on", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("capacity_liters > 0", name="ck_bins_capacity_positive"),
        sa.CheckConstraint(
            "current_fill_level >= 0 AND current_fill_level <= 100",
            name="ck_bins_fill_level_range",
        ),
        sa.CheckConstraint("latitude >= -90 AND latitude <= 90", name="ck_bins_latitude_range"),
        sa.CheckConstraint("longitude >= -180 AND longitude <= 180", name="ck_bins_longitude_range"),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], name="fk_bins_zone_id_zones", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_bins"),
        sa.UniqueConstraint("code", name="uq_bins_code"),
        sa.UniqueConstraint("sensor_id", name="uq_bins_sensor_id"),
    )
    op.create_index("ix_bins_code", "bins", ["code"], unique=False)
    op.create_index("ix_bins_zone_id", "bins", ["zone_id"], unique=False)
    op.create_index("ix_bins_waste_type", "bins", ["waste_type"], unique=False)
    op.create_index("ix_bins_last_reading_at", "bins", ["last_reading_at"], unique=False)
    op.create_index("ix_bins_fill_level", "bins", ["current_fill_level"], unique=False)
    op.create_index("ix_bins_zone_status", "bins", ["zone_id", "status"], unique=False)

    op.create_table(
        "bin_readings",
        sa.Column("id", BIGINT_PK, autoincrement=True, nullable=False),
        sa.Column("bin_id", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fill_level", sa.Float(), nullable=False),
        sa.Column("weight_kg", sa.Float(), nullable=True),
        sa.Column("temperature_c", sa.Float(), nullable=True),
        sa.Column("battery_level", sa.Float(), nullable=True),
        sa.Column("source", _enum(ReadingSource, "bin_readings_source"), nullable=False),
        sa.Column("raw_payload", JSON_TYPE, nullable=True),
        sa.CheckConstraint("fill_level >= 0 AND fill_level <= 100", name="ck_bin_readings_fill_level_range"),
        sa.ForeignKeyConstraint(["bin_id"], ["bins.id"], name="fk_bin_readings_bin_id_bins", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_bin_readings"),
        sa.UniqueConstraint("bin_id", "recorded_at", name="uq_bin_readings_bin_id_recorded_at"),
    )
    op.create_index("ix_bin_readings_bin_recorded", "bin_readings", ["bin_id", "recorded_at"], unique=False)
    op.create_index("ix_bin_readings_recorded_at", "bin_readings", ["recorded_at"], unique=False)

    op.create_table(
        "routes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=48), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=True),
        sa.Column("planned_for", sa.Date(), nullable=False),
        sa.Column("status", _enum(RouteStatus, "routes_status"), nullable=False),
        sa.Column("total_stops", sa.Integer(), nullable=False),
        sa.Column("total_distance_km", sa.Float(), nullable=False),
        sa.Column("total_duration_minutes", sa.Float(), nullable=False),
        sa.Column("planned_volume_liters", sa.Float(), nullable=False),
        sa.Column("planned_weight_kg", sa.Float(), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("baseline_distance_km", sa.Float(), nullable=True),
        sa.Column("solver_status", sa.String(length=40), nullable=True),
        sa.Column("solve_time_seconds", sa.Float(), nullable=True),
        sa.Column("optimization_params", JSON_TYPE, nullable=True),
        sa.Column("deferred_bins", JSON_TYPE, nullable=True),
        sa.Column("geometry", JSON_TYPE, nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.id"], name="fk_routes_vehicle_id_vehicles", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_routes"),
        sa.UniqueConstraint("code", name="uq_routes_code"),
    )
    op.create_index("ix_routes_code", "routes", ["code"], unique=False)
    op.create_index("ix_routes_vehicle_id", "routes", ["vehicle_id"], unique=False)
    op.create_index("ix_routes_date_status", "routes", ["planned_for", "status"], unique=False)

    op.create_table(
        "route_stops",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("route_id", sa.Integer(), nullable=False),
        sa.Column("bin_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", _enum(StopStatus, "route_stops_status"), nullable=False),
        sa.Column("priority_score", sa.Float(), nullable=True),
        sa.Column("distance_from_previous_km", sa.Float(), nullable=False),
        sa.Column("travel_time_minutes", sa.Float(), nullable=False),
        sa.Column("service_time_minutes", sa.Float(), nullable=False),
        sa.Column("planned_arrival", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_arrival", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expected_volume_liters", sa.Float(), nullable=False),
        sa.Column("expected_weight_kg", sa.Float(), nullable=False),
        sa.Column("skip_reason", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["route_id"], ["routes.id"], name="fk_route_stops_route_id_routes", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bin_id"], ["bins.id"], name="fk_route_stops_bin_id_bins", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_route_stops"),
        sa.UniqueConstraint("route_id", "sequence", name="uq_route_stops_route_id_sequence"),
    )
    op.create_index("ix_route_stops_bin", "route_stops", ["bin_id"], unique=False)

    op.create_table(
        "collection_events",
        sa.Column("id", BIGINT_PK, autoincrement=True, nullable=False),
        sa.Column("bin_id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=True),
        sa.Column("route_id", sa.Integer(), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fill_level_before", sa.Float(), nullable=False),
        sa.Column("volume_collected_liters", sa.Float(), nullable=False),
        sa.Column("weight_collected_kg", sa.Float(), nullable=False),
        sa.Column("recyclable_kg", sa.Float(), nullable=False),
        sa.Column("non_recyclable_kg", sa.Float(), nullable=False),
        sa.Column("waste_type", _enum(WasteType, "collection_events_waste_type"), nullable=False),
        sa.Column("contamination_pct", sa.Float(), nullable=True),
        sa.Column("was_overflowing", sa.Boolean(), nullable=False),
        sa.Column("hours_since_previous", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.CheckConstraint("volume_collected_liters >= 0", name="ck_collection_events_volume_non_negative"),
        sa.CheckConstraint("weight_collected_kg >= 0", name="ck_collection_events_weight_non_negative"),
        sa.ForeignKeyConstraint(
            ["bin_id"], ["bins.id"], name="fk_collection_events_bin_id_bins", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.id"], name="fk_collection_events_vehicle_id_vehicles", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["route_id"], ["routes.id"], name="fk_collection_events_route_id_routes", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_collection_events"),
    )
    op.create_index("ix_collection_events_bin_time", "collection_events", ["bin_id", "collected_at"], unique=False)
    op.create_index("ix_collection_events_collected_at", "collection_events", ["collected_at"], unique=False)

    op.create_table(
        "fill_predictions",
        sa.Column("id", BIGINT_PK, autoincrement=True, nullable=False),
        sa.Column("bin_id", sa.Integer(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fill_level_at_generation", sa.Float(), nullable=False),
        sa.Column("predicted_fill_rate_pct_per_hour", sa.Float(), nullable=False),
        sa.Column("hours_to_full", sa.Float(), nullable=True),
        sa.Column("predicted_full_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hours_to_full_low", sa.Float(), nullable=True),
        sa.Column("hours_to_full_high", sa.Float(), nullable=True),
        sa.Column("method", _enum(PredictionMethod, "fill_predictions_method"), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=True),
        sa.Column("features", JSON_TYPE, nullable=True),
        sa.Column("actual_full_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("absolute_error_hours", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["bin_id"], ["bins.id"], name="fk_fill_predictions_bin_id_bins", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_fill_predictions"),
    )
    op.create_index(
        "ix_fill_predictions_bin_generated", "fill_predictions", ["bin_id", "generated_at"], unique=False
    )
    op.create_index("ix_fill_predictions_predicted_full_at", "fill_predictions", ["predicted_full_at"], unique=False)

    op.create_table(
        "alerts",
        sa.Column("id", BIGINT_PK, autoincrement=True, nullable=False),
        sa.Column("alert_type", _enum(AlertType, "alerts_alert_type", 48), nullable=False),
        sa.Column("severity", _enum(AlertSeverity, "alerts_severity"), nullable=False),
        sa.Column("status", _enum(AlertStatus, "alerts_status"), nullable=False),
        sa.Column("bin_id", sa.Integer(), nullable=True),
        sa.Column("zone_id", sa.Integer(), nullable=True),
        sa.Column("vehicle_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", JSON_TYPE, nullable=True),
        sa.Column("dedup_key", sa.String(length=160), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by", sa.String(length=120), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("anomaly_score", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["bin_id"], ["bins.id"], name="fk_alerts_bin_id_bins", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["zone_id"], ["zones.id"], name="fk_alerts_zone_id_zones", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.id"], name="fk_alerts_vehicle_id_vehicles", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_alerts"),
    )
    op.create_index("ix_alerts_bin_id", "alerts", ["bin_id"], unique=False)
    op.create_index("ix_alerts_zone_id", "alerts", ["zone_id"], unique=False)
    op.create_index("ix_alerts_status_severity", "alerts", ["status", "severity"], unique=False)
    op.create_index("ix_alerts_triggered_at", "alerts", ["triggered_at"], unique=False)
    op.create_index("ix_alerts_dedup_key", "alerts", ["dedup_key"], unique=False)

    op.create_table(
        "waste_classifications",
        sa.Column("id", BIGINT_PK, autoincrement=True, nullable=False),
        sa.Column("bin_id", sa.Integer(), nullable=True),
        sa.Column("image_filename", sa.String(length=255), nullable=False),
        sa.Column("image_path", sa.String(length=512), nullable=True),
        sa.Column("image_sha256", sa.String(length=64), nullable=True),
        sa.Column("predicted_class", _enum(WasteType, "waste_classifications_predicted_class"), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("probabilities", JSON_TYPE, nullable=True),
        sa.Column("is_recyclable", sa.Boolean(), nullable=False),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("reviewed_class", _enum(WasteType, "waste_classifications_reviewed_class"), nullable=True),
        sa.Column("reviewed_by", sa.String(length=120), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_version", sa.String(length=64), nullable=True),
        sa.Column("inference_ms", sa.Float(), nullable=True),
        sa.Column("metadata_json", JSON_TYPE, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["bin_id"], ["bins.id"], name="fk_waste_classifications_bin_id_bins", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_waste_classifications"),
    )
    op.create_index("ix_waste_classifications_bin_id", "waste_classifications", ["bin_id"], unique=False)
    op.create_index("ix_waste_classifications_image_sha256", "waste_classifications", ["image_sha256"], unique=False)
    op.create_index("ix_waste_classifications_created", "waste_classifications", ["created_at"], unique=False)
    op.create_index(
        "ix_waste_classifications_predicted", "waste_classifications", ["predicted_class"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_waste_classifications_predicted", table_name="waste_classifications")
    op.drop_index("ix_waste_classifications_created", table_name="waste_classifications")
    op.drop_index("ix_waste_classifications_image_sha256", table_name="waste_classifications")
    op.drop_index("ix_waste_classifications_bin_id", table_name="waste_classifications")
    op.drop_table("waste_classifications")

    op.drop_index("ix_alerts_dedup_key", table_name="alerts")
    op.drop_index("ix_alerts_triggered_at", table_name="alerts")
    op.drop_index("ix_alerts_status_severity", table_name="alerts")
    op.drop_index("ix_alerts_zone_id", table_name="alerts")
    op.drop_index("ix_alerts_bin_id", table_name="alerts")
    op.drop_table("alerts")

    op.drop_index("ix_fill_predictions_predicted_full_at", table_name="fill_predictions")
    op.drop_index("ix_fill_predictions_bin_generated", table_name="fill_predictions")
    op.drop_table("fill_predictions")

    op.drop_index("ix_collection_events_collected_at", table_name="collection_events")
    op.drop_index("ix_collection_events_bin_time", table_name="collection_events")
    op.drop_table("collection_events")

    op.drop_index("ix_route_stops_bin", table_name="route_stops")
    op.drop_table("route_stops")

    op.drop_index("ix_routes_date_status", table_name="routes")
    op.drop_index("ix_routes_vehicle_id", table_name="routes")
    op.drop_index("ix_routes_code", table_name="routes")
    op.drop_table("routes")

    op.drop_index("ix_bin_readings_recorded_at", table_name="bin_readings")
    op.drop_index("ix_bin_readings_bin_recorded", table_name="bin_readings")
    op.drop_table("bin_readings")

    op.drop_index("ix_bins_zone_status", table_name="bins")
    op.drop_index("ix_bins_fill_level", table_name="bins")
    op.drop_index("ix_bins_last_reading_at", table_name="bins")
    op.drop_index("ix_bins_waste_type", table_name="bins")
    op.drop_index("ix_bins_zone_id", table_name="bins")
    op.drop_index("ix_bins_code", table_name="bins")
    op.drop_table("bins")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    op.drop_index("ix_vehicles_code", table_name="vehicles")
    op.drop_table("vehicles")

    op.drop_index("ix_zones_code", table_name="zones")
    op.drop_table("zones")

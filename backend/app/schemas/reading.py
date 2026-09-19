"""Telemetry contracts shared by the REST endpoint and the MQTT bridge.

`TelemetryIn` is deliberately the single payload definition for both transports,
so a device can publish the same JSON over MQTT or POST it over HTTP and get
identical validation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import ReadingSource


class TelemetryIn(BaseModel):
    # A device identifies itself by either its bin code or its sensor id.
    bin_code: str | None = Field(default=None, max_length=32)
    sensor_id: str | None = Field(default=None, max_length=64)

    fill_level: float = Field(ge=0, le=100, description="Percent full, 0-100")
    weight_kg: float | None = Field(default=None, ge=0)
    temperature_c: float | None = Field(default=None, ge=-40, le=120)
    battery_level: float | None = Field(default=None, ge=0, le=100)

    recorded_at: datetime | None = Field(
        default=None, description="Device clock; server time is used if omitted"
    )
    source: ReadingSource = ReadingSource.REST
    raw_payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _require_an_identifier(self) -> "TelemetryIn":
        if not self.bin_code and not self.sensor_id:
            raise ValueError("Either bin_code or sensor_id must be provided")
        return self


class BulkTelemetryIn(BaseModel):
    """Batched upload for gateways that buffer readings while offline."""

    readings: list[TelemetryIn] = Field(min_length=1, max_length=1000)


class ReadingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bin_id: int
    recorded_at: datetime
    fill_level: float
    weight_kg: float | None
    temperature_c: float | None
    battery_level: float | None
    source: ReadingSource


class TelemetryResult(BaseModel):
    """Outcome of ingesting one reading."""

    bin_id: int
    bin_code: str
    accepted: bool
    duplicate: bool = Field(
        default=False,
        description="True when this exact (bin, timestamp) was already stored",
    )
    collection_detected: bool = Field(
        default=False,
        description="True when the fill level dropped enough to infer an emptying",
    )
    current_fill_level: float
    message: str | None = None


class BulkTelemetryResult(BaseModel):
    total: int
    accepted: int
    duplicates: int
    rejected: int
    collections_detected: int
    errors: list[str] = Field(default_factory=list)

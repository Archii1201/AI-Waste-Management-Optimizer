"""MQTT topic and payload conventions.

Kept in one module so the simulator (publisher) and the bridge (subscriber)
cannot drift apart, and so a third-party device vendor has a single file to
read when integrating.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import ValidationError as PydanticValidationError

from app.models.enums import ReadingSource
from app.schemas.reading import TelemetryIn

TOPIC_ROOT = "waste/bins"


def telemetry_topic(bin_code: str) -> str:
    """Per-device topic, e.g. `waste/bins/MUM-AND-0142/telemetry`.

    One topic per bin rather than a single shared topic, so the broker can fan
    out selectively and an operator can subscribe to a single problem bin.
    """
    return f"{TOPIC_ROOT}/{bin_code}/telemetry"


def bin_code_from_topic(topic: str) -> str | None:
    """Recover the bin code from a topic, for payloads that omit it."""
    parts = topic.split("/")
    if len(parts) == 4 and parts[0] == "waste" and parts[1] == "bins" and parts[3] == "telemetry":
        return parts[2]
    return None


def encode(payload: TelemetryIn) -> bytes:
    body = payload.model_dump(mode="json", exclude_none=True)
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def decode(topic: str, raw: bytes) -> TelemetryIn:
    """Parse a device message into the same schema the REST endpoint validates.

    Raises ValueError with a readable reason; the bridge logs and drops the
    message rather than letting one malformed device stall ingestion.
    """
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"payload is not valid JSON: {exc}") from exc

    if not isinstance(body, dict):
        raise ValueError("payload must be a JSON object")

    # The topic already identifies the device, so a payload that omits the code
    # is still usable. This is common with constrained firmware.
    body.setdefault("bin_code", bin_code_from_topic(topic))
    body.setdefault("source", ReadingSource.MQTT.value)

    try:
        return TelemetryIn.model_validate(body)
    except PydanticValidationError as exc:
        raise ValueError(f"payload failed validation: {exc.error_count()} problem(s)") from exc


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

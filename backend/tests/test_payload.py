"""MQTT topic and payload conventions shared by the simulator and the bridge."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.iot.payload import bin_code_from_topic, decode, encode, telemetry_topic
from app.models.enums import ReadingSource
from app.schemas.reading import TelemetryIn


def test_topic_round_trip():
    topic = telemetry_topic("MUM-AND-042")
    assert topic == "waste/bins/MUM-AND-042/telemetry"
    assert bin_code_from_topic(topic) == "MUM-AND-042"


@pytest.mark.parametrize(
    "topic",
    ["waste/bins/MUM-001", "waste/bins/MUM-001/commands", "other/bins/MUM-001/telemetry"],
)
def test_unrelated_topics_yield_no_bin_code(topic):
    assert bin_code_from_topic(topic) is None


def test_encode_decode_preserves_the_reading():
    original = TelemetryIn(
        bin_code="MUM-AND-042",
        fill_level=63.5,
        weight_kg=21.4,
        temperature_c=31.2,
        battery_level=88.0,
        recorded_at=datetime(2026, 9, 19, 6, 30, tzinfo=timezone.utc),
        source=ReadingSource.SIMULATOR,
    )
    decoded = decode(telemetry_topic("MUM-AND-042"), encode(original))

    assert decoded.bin_code == original.bin_code
    assert decoded.fill_level == original.fill_level
    assert decoded.recorded_at == original.recorded_at
    assert decoded.source is ReadingSource.SIMULATOR


def test_bin_code_is_recovered_from_the_topic_when_omitted():
    """Constrained firmware often publishes only the measurement."""
    raw = json.dumps({"fill_level": 40.0}).encode()
    decoded = decode("waste/bins/MUM-BAN-007/telemetry", raw)

    assert decoded.bin_code == "MUM-BAN-007"
    # Transport is recorded so ingestion can distinguish MQTT from REST.
    assert decoded.source is ReadingSource.MQTT


def test_explicit_source_survives_decoding():
    raw = json.dumps({"fill_level": 40.0, "source": "simulator"}).encode()
    assert decode("waste/bins/X/telemetry", raw).source is ReadingSource.SIMULATOR


@pytest.mark.parametrize(
    "raw",
    [
        b"not json at all",
        b"[1, 2, 3]",
        b'{"fill_level": 250}',
        b'{"fill_level": "very full"}',
        b"{}",
        b"\xff\xfe\x00",
    ],
)
def test_bad_payloads_raise_a_readable_error(raw):
    """The bridge logs and drops these; one bad device must not stall ingestion."""
    with pytest.raises(ValueError):
        decode("waste/bins/MUM-001/telemetry", raw)


def test_payload_without_identifier_on_an_unparseable_topic_is_rejected():
    raw = json.dumps({"fill_level": 40.0}).encode()
    with pytest.raises(ValueError):
        decode("some/other/topic", raw)


def test_encoded_payload_omits_empty_fields():
    """Keeping messages small matters on metered NB-IoT links."""
    body = json.loads(encode(TelemetryIn(bin_code="B", fill_level=10.0)))
    assert "temperature_c" not in body
    assert "weight_kg" not in body
    assert body["fill_level"] == 10.0

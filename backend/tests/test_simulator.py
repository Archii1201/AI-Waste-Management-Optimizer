"""Device simulator behaviour, exercised without touching MQTT."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.iot.simulator import RESIDUAL_AFTER_COLLECTION, FleetSimulator, SimulatedDevice
from app.models.bin import Bin
from app.models.enums import BinStatus, WasteType, ZoneType
from app.seed.network import seed_network


@pytest.fixture
def seeded(db):
    seed_network(db)
    return db


@pytest.fixture
def device(seeded) -> SimulatedDevice:
    bin_obj = seeded.scalar(select(Bin).where(Bin.code == "MUM-AND-E-001"))
    return SimulatedDevice.from_bin(bin_obj, seed=11)


def moment(hour: int = 12) -> datetime:
    return datetime(2026, 9, 21, hour, tzinfo=timezone.utc)


def test_device_inherits_state_from_its_bin(device, seeded):
    bin_obj = seeded.scalar(select(Bin).where(Bin.code == "MUM-AND-E-001"))
    assert device.bin_code == bin_obj.code
    assert device.fill_level == bin_obj.current_fill_level
    assert device.capacity_liters == bin_obj.capacity_liters
    assert device.base_daily_pct > 0


def test_fill_level_climbs_over_time(device):
    start = device.fill_level
    for hour in range(12):
        device.advance(moment(hour), hours=1)
    assert device.fill_level > start


def test_fill_level_is_capped_at_one_hundred(device):
    for day in range(30):
        device.advance(moment() + timedelta(days=day), hours=24)
    assert device.fill_level == 100.0


def test_battery_drains_slowly_and_never_goes_negative(device):
    device.battery_level = 0.2
    device.advance(moment(), hours=240)
    assert device.battery_level == 0.0


def test_collection_resets_the_device_to_a_small_residual(device):
    device.fill_level = 96.0
    device.note_collection()

    low, high = RESIDUAL_AFTER_COLLECTION
    assert low <= device.fill_level <= high


def test_published_payload_is_valid_and_self_consistent(device):
    device.fill_level = 55.0
    payload = device.build_payload(moment())

    assert payload.bin_code == device.bin_code
    assert 0 <= payload.fill_level <= 100
    # Measurement noise is small, not a different reading entirely.
    assert abs(payload.fill_level - 55.0) < 1.5
    assert payload.weight_kg > 0
    assert payload.recorded_at == moment()


def test_empty_bin_reports_no_weight(device):
    device.fill_level = 0.0
    assert device.build_payload(moment()).weight_kg == 0.0


def test_two_devices_with_the_same_seed_behave_identically(seeded):
    bin_obj = seeded.scalar(select(Bin).where(Bin.code == "MUM-POW-R-001"))
    a = SimulatedDevice.from_bin(bin_obj, seed=5)
    b = SimulatedDevice.from_bin(bin_obj, seed=5)

    for hour in range(24):
        a.advance(moment(hour), hours=1)
        b.advance(moment(hour), hours=1)

    assert a.fill_level == b.fill_level


def test_different_bins_diverge_even_with_one_seed(seeded):
    first = seeded.scalar(select(Bin).where(Bin.code == "MUM-POW-R-001"))
    second = seeded.scalar(select(Bin).where(Bin.code == "MUM-POW-R-002"))

    a = SimulatedDevice.from_bin(first, seed=5)
    b = SimulatedDevice.from_bin(second, seed=5)
    for hour in range(24):
        a.advance(moment(hour), hours=1)
        b.advance(moment(hour), hours=1)

    assert a.fill_level != b.fill_level


def test_fleet_loads_only_active_bins(seeded):
    parked = seeded.scalar(select(Bin).where(Bin.code == "MUM-AND-E-002"))
    parked.status = BinStatus.MAINTENANCE
    seeded.commit()

    simulator = FleetSimulator(seed=1)
    loaded = simulator.load_fleet(seeded)

    assert parked.id not in simulator.devices
    assert loaded == seeded.query(Bin).filter(Bin.status == BinStatus.ACTIVE).count()


def test_fleet_can_be_restricted_to_one_zone(seeded):
    from app.models.zone import Zone

    zone = seeded.scalar(select(Zone).where(Zone.code == "IITB-C"))
    simulator = FleetSimulator(seed=1, zone_id=zone.id)

    assert simulator.load_fleet(seeded) == 9


def test_devices_resync_after_a_bin_is_emptied(seeded):
    """The closed loop: a collection in the database resets the physical sensor."""
    simulator = FleetSimulator(seed=1)
    simulator.load_fleet(seeded)

    target = seeded.scalar(select(Bin).where(Bin.code == "MUM-AND-E-001"))
    device = simulator.devices[target.id]
    device.fill_level = 90.0

    # The bin is emptied through the API or a route completion.
    target.last_emptied_at = datetime.now(timezone.utc)
    target.current_fill_level = 0.0
    seeded.commit()

    assert simulator.resync_collections(seeded) == 1
    assert device.fill_level < 5.0


def test_the_same_collection_only_resets_a_device_once(seeded):
    simulator = FleetSimulator(seed=1)
    simulator.load_fleet(seeded)

    target = seeded.scalar(select(Bin).where(Bin.code == "MUM-AND-E-001"))
    target.last_emptied_at = datetime.now(timezone.utc)
    seeded.commit()

    assert simulator.resync_collections(seeded) == 1
    assert simulator.resync_collections(seeded) == 0


def test_a_lagging_database_is_not_mistaken_for_a_collection(seeded):
    """Regression: under ingestion backlog the stored level trails the device.

    Comparing fill levels would read that lag as a fleet-wide collection event
    and fabricate collection records, so resync keys off last_emptied_at.
    """
    simulator = FleetSimulator(seed=1)
    simulator.load_fleet(seeded)

    for device in simulator.devices.values():
        device.fill_level = 85.0
    # Nothing has been ingested yet, so every bin still reads zero.
    assert all(b.current_fill_level == 0.0 for b in seeded.scalars(select(Bin)))

    assert simulator.resync_collections(seeded) == 0
    assert all(d.fill_level == 85.0 for d in simulator.devices.values())


def test_legacy_crew_empties_full_bins_during_its_shift(seeded):
    simulator = FleetSimulator(seed=1, auto_collect=True)
    simulator.load_fleet(seeded)
    for device in simulator.devices.values():
        device.fill_level = 95.0

    # 09:00 in Mumbai is 03:30 UTC, inside the 06:00-14:00 local shift.
    _, collected = simulator.tick(datetime(2026, 9, 21, 3, 30, tzinfo=timezone.utc))
    assert collected > 0


def test_legacy_crew_does_not_work_overnight(seeded):
    simulator = FleetSimulator(seed=1, auto_collect=True)
    simulator.load_fleet(seeded)
    for device in simulator.devices.values():
        device.fill_level = 95.0

    # 02:00 local, well outside the shift.
    _, collected = simulator.tick(datetime(2026, 9, 20, 20, 30, tzinfo=timezone.utc))
    assert collected == 0


def test_no_collections_happen_when_auto_collect_is_off(seeded):
    simulator = FleetSimulator(seed=1, auto_collect=False)
    simulator.load_fleet(seeded)
    for device in simulator.devices.values():
        device.fill_level = 99.0

    _, collected = simulator.tick(datetime(2026, 9, 21, 3, 30, tzinfo=timezone.utc))
    assert collected == 0


def test_crew_ignores_bins_below_the_collection_threshold(seeded):
    simulator = FleetSimulator(seed=1, auto_collect=True)
    simulator.load_fleet(seeded)
    for device in simulator.devices.values():
        device.fill_level = 10.0

    _, collected = simulator.tick(datetime(2026, 9, 21, 3, 30, tzinfo=timezone.utc))
    assert collected == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"interval_minutes": 0},
        {"speed": 0},
        {"speed": -1},
        {"dropout_rate": 1.0},
        {"dropout_rate": -0.1},
    ],
)
def test_invalid_simulator_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        FleetSimulator(**kwargs)


def test_device_from_a_detached_bin_uses_its_zone_type(seeded):
    bin_obj = seeded.scalar(select(Bin).where(Bin.code == "MUM-VIK-I-001"))
    device = SimulatedDevice.from_bin(bin_obj, seed=2)

    assert device.zone_type is ZoneType.INDUSTRIAL
    assert isinstance(device.waste_type, WasteType)
    assert isinstance(device.rng, random.Random)

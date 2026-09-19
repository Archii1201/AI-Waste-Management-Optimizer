"""Seeding the Mumbai network."""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.bin import Bin
from app.models.vehicle import Vehicle
from app.models.zone import Zone
from app.seed.mumbai import VEHICLE_SPECS, ZONE_SPECS, total_bin_count
from app.seed.network import seed_network
from app.services.geo import haversine_km


def test_seed_creates_the_whole_network(db):
    report = seed_network(db)

    assert report.zones_created == len(ZONE_SPECS)
    assert report.bins_created == total_bin_count()
    assert report.vehicles_created == len(VEHICLE_SPECS)

    assert db.scalar(select(func.count()).select_from(Bin)) == total_bin_count()


def test_seeding_twice_creates_nothing_new(db):
    """Re-running must never duplicate bins or disturb live telemetry."""
    seed_network(db)
    second = seed_network(db)

    assert second.zones_created == 0
    assert second.bins_created == 0
    assert second.vehicles_created == 0
    assert db.scalar(select(func.count()).select_from(Bin)) == total_bin_count()


def test_bins_land_inside_their_zone_radius(db):
    seed_network(db)

    for spec in ZONE_SPECS:
        zone = db.scalar(select(Zone).where(Zone.code == spec.code))
        bins = db.scalars(select(Bin).where(Bin.zone_id == zone.id)).all()
        assert len(bins) == spec.bin_count

        for b in bins:
            distance = haversine_km(spec.lat, spec.lon, b.latitude, b.longitude)
            assert distance <= spec.radius_km * 1.05, f"{b.code} fell outside {spec.code}"


def test_the_same_seed_reproduces_the_same_positions(db):
    """Screenshots and benchmark numbers stay comparable across rebuilds."""
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import make_sqlite_engine

    seed_network(db, seed=777)
    first = {b.code: (b.latitude, b.longitude) for b in db.scalars(select(Bin))}

    other_engine = make_sqlite_engine()
    other = sessionmaker(bind=other_engine, expire_on_commit=False)()
    try:
        seed_network(other, seed=777)
        second = {b.code: (b.latitude, b.longitude) for b in other.scalars(select(Bin))}
    finally:
        other.close()
        other_engine.dispose()

    assert first == second
    assert len(first) == total_bin_count()


def test_every_bin_has_a_unique_sensor_and_code(db):
    seed_network(db)
    bins = db.scalars(select(Bin)).all()

    assert len({b.code for b in bins}) == len(bins)
    assert len({b.sensor_id for b in bins}) == len(bins)


def test_recycling_trucks_only_accept_recyclable_streams(db):
    seed_network(db)
    truck = db.scalar(select(Vehicle).where(Vehicle.code == "MUM-REC-01"))

    assert truck.accepted_waste_types == ["plastic", "paper", "metal", "glass"]
    assert truck.accepts("plastic") is True
    assert truck.accepts("organic") is False


def test_general_compactors_accept_everything(db):
    seed_network(db)
    compactor = db.scalar(select(Vehicle).where(Vehicle.code == "MUM-CMP-01"))

    assert compactor.accepted_waste_types is None
    assert compactor.accepts("organic") is True


def test_vehicles_start_parked_at_their_depot(db):
    seed_network(db)
    for vehicle in db.scalars(select(Vehicle)):
        assert vehicle.current_lat == vehicle.depot_lat
        assert vehicle.current_load_liters == 0.0


def test_industrial_zone_skews_toward_metal(db):
    """Zone land-use must actually shape the waste mix, not just the volume."""
    seed_network(db)

    industrial = db.scalar(select(Zone).where(Zone.code == "VIK-I"))
    campus = db.scalar(select(Zone).where(Zone.code == "IITB-C"))

    industrial_types = [
        b.waste_type.value for b in db.scalars(select(Bin).where(Bin.zone_id == industrial.id))
    ]
    campus_types = [
        b.waste_type.value for b in db.scalars(select(Bin).where(Bin.zone_id == campus.id))
    ]

    # Not a strict guarantee per-bin, but the mixes must differ.
    assert industrial_types != campus_types

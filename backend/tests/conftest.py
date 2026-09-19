"""Test fixtures.

The suite runs against an in-memory SQLite database so it needs no network and
no running PostgreSQL. The models use portable column types (`json_type`,
`UTCDateTime`) specifically to make this possible while production still gets
JSONB and real timezone-aware timestamps.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.main import app
from app.models import Base


def make_sqlite_engine():
    """Build an isolated in-memory database with the schema already created."""
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        # One shared connection, so the in-memory database survives across
        # sessions within a single test.
        poolclass=StaticPool,
    )

    # pysqlite opens transactions implicitly and in a way that breaks SAVEPOINT.
    # The bulk-telemetry path relies on savepoints, so apply SQLAlchemy's
    # documented fix: take over transaction control from the driver.
    @event.listens_for(eng, "connect")
    def _disable_driver_transactions(dbapi_connection, _record):  # noqa: ANN001
        dbapi_connection.isolation_level = None

    @event.listens_for(eng, "begin")
    def _emit_explicit_begin(connection):  # noqa: ANN001
        connection.exec_driver_sql("BEGIN")

    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def engine():
    eng = make_sqlite_engine()
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine) -> Generator[Session, None, None]:
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db: Session) -> Generator[TestClient, None, None]:
    def _override() -> Generator[Session, None, None]:
        yield db

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def api_prefix() -> str:
    from app.core.config import settings

    return settings.api_v1_prefix


@pytest.fixture
def zone(client: TestClient, api_prefix: str) -> dict:
    response = client.post(
        f"{api_prefix}/zones",
        json={
            "code": "AND-E",
            "name": "Andheri East",
            "zone_type": "mixed_use",
            "center_lat": 19.1136,
            "center_lon": 72.8697,
            "population_served": 180000,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def bin_payload(zone: dict) -> dict:
    return {
        "code": "MUM-AND-0001",
        "label": "Chakala Junction",
        "zone_id": zone["id"],
        "latitude": 19.1105,
        "longitude": 72.8620,
        "capacity_liters": 660.0,
        "waste_type": "mixed",
        "sensor_id": "SENSOR-0001",
    }


@pytest.fixture
def created_bin(client: TestClient, api_prefix: str, bin_payload: dict) -> dict:
    response = client.post(f"{api_prefix}/bins", json=bin_payload)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.isoformat()


def hours_ago(base: datetime, hours: float) -> str:
    return iso(base - timedelta(hours=hours))

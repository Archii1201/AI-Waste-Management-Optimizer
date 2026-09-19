"""Reusable SQLAlchemy column types."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import JSON, BigInteger, DateTime, Integer
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator


def enum_type(enum_cls: type[PyEnum], length: int = 40) -> SAEnum:
    """Store a Python enum as VARCHAR + CHECK rather than a native PG ENUM type.

    Native PostgreSQL enums require an ALTER TYPE migration every time a value is
    added, and they cannot drop values at all. A checked VARCHAR stores the same
    readable values, keeps the constraint, and makes schema evolution a normal
    column migration.
    """
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=length,
        values_callable=lambda cls: [member.value for member in cls],
        validate_strings=True,
    )


def json_type() -> JSON:
    """JSONB on PostgreSQL, plain JSON elsewhere.

    Production runs on PostgreSQL and gets JSONB's binary storage and indexing.
    The variant lets the test suite run against in-memory SQLite, which keeps
    the whole suite offline and fast.
    """
    return JSON().with_variant(JSONB(), "postgresql")


def bigint_pk() -> BigInteger:
    """Auto-incrementing 64-bit primary key for the high-volume tables.

    The reading and event tables will outgrow a 32-bit key. SQLite only
    auto-increments a column declared exactly `INTEGER PRIMARY KEY`, so the
    variant keeps BIGINT in PostgreSQL while letting the test suite run.
    """
    return BigInteger().with_variant(Integer, "sqlite")


class UTCDateTime(TypeDecorator):
    """Timestamp column that is always timezone-aware UTC in Python.

    Waste generation is analysed by hour-of-day and day-of-week, so a naive
    timestamp sneaking in would silently shift a bin's learned fill pattern.
    This normalises on the way in and re-attaches UTC on the way out, including
    on backends such as SQLite that do not persist an offset.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

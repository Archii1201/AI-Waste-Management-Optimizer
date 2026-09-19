"""Reusable SQLAlchemy column types."""

from __future__ import annotations

from enum import Enum as PyEnum

from sqlalchemy import Enum as SAEnum


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

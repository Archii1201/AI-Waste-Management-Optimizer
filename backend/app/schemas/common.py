"""Shared response envelopes and query-parameter dependencies."""

from __future__ import annotations

from math import ceil
from typing import Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Paged collection response used by every list endpoint."""

    items: list[T]
    total: int = Field(description="Total rows matching the filters, ignoring pagination")
    page: int
    page_size: int
    pages: int

    @classmethod
    def build(cls, items: list[T], total: int, page: int, page_size: int) -> "Page[T]":
        return cls(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            pages=max(1, ceil(total / page_size)) if page_size else 1,
        )


class MessageResponse(BaseModel):
    message: str


class PaginationParams:
    """Injectable page/size pair with a hard upper bound.

    The cap exists so a stray `page_size=100000` cannot pull the entire reading
    history into memory and stall the API.
    """

    def __init__(
        self,
        page: int = Query(1, ge=1, description="1-based page number"),
        page_size: int = Query(50, ge=1, le=500, description="Rows per page (max 500)"),
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size

"""Results from the image-based waste classifier.

Stored rather than discarded because the aggregate of these rows is what drives
the contamination metrics and the recycling-improvement recommendations, and
because human-corrected rows become extra training data for the next model.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.enums import WasteType
from app.models.types import enum_type

if TYPE_CHECKING:
    from app.models.bin import Bin


class WasteClassification(Base):
    __tablename__ = "waste_classifications"
    __table_args__ = (
        Index("ix_waste_classifications_created", "created_at"),
        Index("ix_waste_classifications_predicted", "predicted_class"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    bin_id: Mapped[int | None] = mapped_column(
        ForeignKey("bins.id", ondelete="SET NULL"), nullable=True, index=True
    )

    image_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    image_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    predicted_class: Mapped[WasteType] = mapped_column(enum_type(WasteType), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    # Full softmax distribution over the six categories, so the UI can show a
    # probability bar chart and the operator can see the runner-up guess.
    probabilities: Mapped[dict[str, float] | None] = mapped_column(JSONB, nullable=True)

    is_recyclable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Set when confidence falls below the configured threshold. Low-confidence
    # predictions are surfaced for review rather than silently trusted.
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reviewed_class: Mapped[WasteType | None] = mapped_column(enum_type(WasteType), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    inference_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    bin: Mapped["Bin | None"] = relationship()

    @property
    def effective_class(self) -> WasteType:
        """Human correction wins over the model when one exists."""
        return self.reviewed_class or self.predicted_class

    def __repr__(self) -> str:
        return f"<WasteClassification {self.predicted_class.value} {self.confidence:.2f}>"

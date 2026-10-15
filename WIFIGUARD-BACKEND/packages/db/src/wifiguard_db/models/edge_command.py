"""Durable command outbox and correlated device acknowledgements."""
from datetime import datetime
import uuid
from sqlalchemy import DateTime, Integer, Text, CheckConstraint, Index, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from ..base import Base, uuid_pk, uuid_fk

ACTIVE_COMMAND_STATES = ("PENDING", "SENT", "ACCEPTED", "PROGRESS")


class EdgeCommand(Base):
    __tablename__ = "edge_commands"
    id: Mapped[uuid.UUID] = uuid_pk()
    device_id: Mapped[uuid.UUID] = uuid_fk("devices.id", ondelete="CASCADE", nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = uuid_fk("users.id", ondelete="SET NULL")
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    args: Mapped[dict] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_ack_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=-1)
    stage: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[float | None] = mapped_column()
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        CheckConstraint("action IN ('calibrate','set_config','ping')", name="action"),
        CheckConstraint("state IN ('PENDING','SENT','ACCEPTED','PROGRESS','DONE','ERROR','EXPIRED')", name="state"),
        Index("ix_edge_commands_next_attempt_at", "next_attempt_at"),
        Index("uq_edge_commands_active_device", "device_id", unique=True,
              postgresql_where=text("state IN ('PENDING','SENT','ACCEPTED','PROGRESS')")),
    )

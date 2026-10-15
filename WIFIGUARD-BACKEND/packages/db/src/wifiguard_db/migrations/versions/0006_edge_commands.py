"""Durable remote command outbox, additive to existing device data."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0006"
down_revision = "0005"
branch_labels = depends_on = None


def upgrade():
    op.create_table("edge_commands",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("device_id", pg.UUID(as_uuid=True), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False), sa.Column("args", pg.JSONB(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False), sa.Column("last_ack_seq", sa.Integer(), nullable=False),
        sa.Column("stage", sa.Text()), sa.Column("progress", sa.Float()),
        sa.Column("result", pg.JSONB()), sa.Column("error", sa.Text()),
        sa.CheckConstraint("action IN ('calibrate','set_config','ping')", name=op.f("ck_edge_commands_action")),
        sa.CheckConstraint("state IN ('PENDING','SENT','ACCEPTED','PROGRESS','DONE','ERROR','EXPIRED')", name=op.f("ck_edge_commands_state")),
    )
    op.create_index("ix_edge_commands_next_attempt_at", "edge_commands", ["next_attempt_at"])
    op.create_index("uq_edge_commands_active_device", "edge_commands", ["device_id"], unique=True,
                    postgresql_where=sa.text("state IN ('PENDING','SENT','ACCEPTED','PROGRESS')"))


def downgrade():
    op.drop_table("edge_commands")

"""Create durable webhook, queue, and audit tables."""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rules",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("keyword", sa.Text(), nullable=False),
        sa.Column("keyword_folded", sa.Text(), nullable=False),
        sa.Column("dm_message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "webhook_events",
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("comment_id", sa.String(length=128), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_webhook_events_comment_id", "webhook_events", ["comment_id"])
    op.create_table(
        "comments",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_at_source", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "dm_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("rule_id", sa.String(length=36), nullable=False),
        sa.Column("recipient_user_id", sa.String(length=128), nullable=False),
        sa.Column("comment_id", sa.String(length=128), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("delivery_attempt", sa.Integer(), nullable=False),
        sa.Column("poll_attempt", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dm_id", sa.String(length=128), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["comment_id"], ["comments.id"]),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", "recipient_user_id", name="uq_dm_job_rule_recipient"),
        sa.UniqueConstraint("dm_id"),
    )
    op.create_index("ix_dm_jobs_comment_id", "dm_jobs", ["comment_id"])
    op.create_index("ix_dm_jobs_next_attempt_at", "dm_jobs", ["next_attempt_at"])
    op.create_index("ix_dm_jobs_recipient_user_id", "dm_jobs", ["recipient_user_id"])
    op.create_index("ix_dm_jobs_rule_id", "dm_jobs", ["rule_id"])
    op.create_index("ix_dm_jobs_status", "dm_jobs", ["status"])
    op.create_table(
        "api_request_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["dm_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_request_log_job_id", "api_request_log", ["job_id"])
    op.create_index("ix_api_request_log_requested_at", "api_request_log", ["requested_at"])
    op.create_table(
        "duplicate_blocks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("rule_id", sa.String(length=36), nullable=False),
        sa.Column("recipient_user_id", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["webhook_events.event_id"]),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", "rule_id", name="uq_duplicate_block_event_rule"),
    )


def downgrade() -> None:
    op.drop_table("duplicate_blocks")
    op.drop_table("api_request_log")
    op.drop_table("dm_jobs")
    op.drop_table("comments")
    op.drop_table("webhook_events")
    op.drop_table("rules")


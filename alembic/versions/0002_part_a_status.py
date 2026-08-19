"""Map previous delivery-reconciliation states to the Part A sent state."""

from alembic import op


revision = "0002_part_a_status"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing successful jobs were named "delivered" by the previous Part C
    # implementation. Existing accepted jobs are also treated as sent because
    # the focused Part A worker no longer performs delivery reconciliation.
    op.execute("UPDATE dm_jobs SET status = 'sent' WHERE status IN ('delivered', 'awaiting_delivery')")


def downgrade() -> None:
    # There is no reliable way to distinguish former delivered from awaiting
    # jobs after the upgrade, so the compact Part A state intentionally remains.
    pass

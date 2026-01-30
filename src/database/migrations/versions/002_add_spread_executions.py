"""Add spread_executions table for crash recovery.

Revision ID: 002
Revises: 001
Create Date: 2026-01-27

Creates the spread_executions table for tracking multi-leg trade state,
enabling recovery after crashes.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create spread_executions table."""
    # Create spread execution status enum
    spread_execution_status_enum = postgresql.ENUM(
        "PENDING",
        "LEG1_SUBMITTED",
        "LEG1_FILLED",
        "LEG2_SUBMITTED",
        "COMPLETED",
        "PARTIAL",
        "ROLLBACK_PENDING",
        "ROLLED_BACK",
        "FAILED",
        "RECOVERY_NEEDED",
        name="spreadexecutionstatus",
        create_type=True,
    )

    # Create spread_executions table
    op.create_table(
        "spread_executions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("spread_id", sa.String(50), nullable=False, unique=True),
        sa.Column("opportunity_id", sa.String(255), nullable=False),
        sa.Column(
            "status",
            spread_execution_status_enum,
            nullable=False,
            server_default="PENDING",
        ),
        # Leg 1 details
        sa.Column("leg1_exchange", sa.String(50), nullable=False),
        sa.Column("leg1_ticker", sa.String(100), nullable=False),
        sa.Column("leg1_side", sa.String(10), nullable=False),
        sa.Column("leg1_price", sa.Numeric(10, 4), nullable=False),
        sa.Column("leg1_size", sa.Integer, nullable=False),
        sa.Column("leg1_order_id", sa.String(255), nullable=True),
        sa.Column("leg1_filled_size", sa.Integer, nullable=False, server_default="0"),
        sa.Column("leg1_fill_price", sa.Numeric(10, 4), nullable=True),
        # Leg 2 details
        sa.Column("leg2_exchange", sa.String(50), nullable=False),
        sa.Column("leg2_ticker", sa.String(100), nullable=False),
        sa.Column("leg2_side", sa.String(10), nullable=False),
        sa.Column("leg2_price", sa.Numeric(10, 4), nullable=False),
        sa.Column("leg2_size", sa.Integer, nullable=False),
        sa.Column("leg2_order_id", sa.String(255), nullable=True),
        sa.Column("leg2_filled_size", sa.Integer, nullable=False, server_default="0"),
        sa.Column("leg2_fill_price", sa.Numeric(10, 4), nullable=True),
        # Rollback tracking
        sa.Column("rollback_order_id", sa.String(255), nullable=True),
        sa.Column("rollback_filled_size", sa.Integer, nullable=False, server_default="0"),
        # Profit tracking
        sa.Column("expected_profit", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("actual_profit", sa.Numeric(12, 4), nullable=True),
        sa.Column("total_fees", sa.Numeric(12, 4), nullable=False, server_default="0"),
        # Timing
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        # Error tracking
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("recovery_attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_recovery_at", sa.DateTime(timezone=True), nullable=True),
        # Metadata
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        # Constraints
        sa.CheckConstraint(
            "leg1_side IN ('buy', 'sell')",
            name="ck_spread_exec_leg1_side",
        ),
        sa.CheckConstraint(
            "leg2_side IN ('buy', 'sell')",
            name="ck_spread_exec_leg2_side",
        ),
        sa.CheckConstraint("leg1_size > 0", name="ck_spread_exec_leg1_size"),
        sa.CheckConstraint("leg2_size > 0", name="ck_spread_exec_leg2_size"),
    )

    # Create indexes
    op.create_index(
        "ix_spread_executions_status",
        "spread_executions",
        ["status"],
    )
    op.create_index(
        "ix_spread_executions_spread_id",
        "spread_executions",
        ["spread_id"],
    )
    op.create_index(
        "ix_spread_executions_started_at",
        "spread_executions",
        ["started_at"],
    )
    op.create_index(
        "ix_spread_executions_opportunity",
        "spread_executions",
        ["opportunity_id"],
    )

    # Partial index for incomplete executions (for fast recovery queries)
    op.create_index(
        "ix_spread_executions_incomplete",
        "spread_executions",
        ["status", "started_at"],
        postgresql_where=sa.text(
            "status NOT IN ('COMPLETED', 'FAILED', 'ROLLED_BACK')"
        ),
    )

    # Add trigger for updated_at
    op.execute("""
        CREATE TRIGGER update_spread_executions_updated_at
        BEFORE UPDATE ON spread_executions
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
    """)


def downgrade() -> None:
    """Drop spread_executions table."""
    # Drop trigger
    op.execute(
        "DROP TRIGGER IF EXISTS update_spread_executions_updated_at ON spread_executions"
    )

    # Drop table
    op.drop_table("spread_executions")

    # Drop enum
    op.execute("DROP TYPE IF EXISTS spreadexecutionstatus")

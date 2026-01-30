"""Initial database schema.

Revision ID: 001
Revises:
Create Date: 2026-01-23

Creates all tables for the trading system:
- markets: unified market data from both platforms
- opportunities: detected arbitrage opportunities
- orders: all orders with status tracking
- trades: completed arbitrage trade pairs
- positions: current open positions
- fills: execution records
- balances: capital per platform over time
- system_events: audit log
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create all tables."""
    # Create enums
    platform_enum = postgresql.ENUM(
        "KALSHI", "POLYMARKET", name="platform", create_type=True
    )
    order_status_enum = postgresql.ENUM(
        "PENDING",
        "OPEN",
        "PARTIALLY_FILLED",
        "FILLED",
        "CANCELED",
        "REJECTED",
        "EXPIRED",
        name="orderstatus",
        create_type=True,
    )
    opportunity_status_enum = postgresql.ENUM(
        "OPEN",
        "EXECUTING",
        "COMPLETED",
        "FAILED",
        "EXPIRED",
        name="opportunitystatus",
        create_type=True,
    )
    market_status_enum = postgresql.ENUM(
        "ACTIVE",
        "CLOSED",
        "SETTLED",
        "HALTED",
        name="marketstatus",
        create_type=True,
    )

    # Create markets table
    op.create_table(
        "markets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("platform", platform_enum, nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("ticker", sa.String(100), nullable=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("category", sa.String(100), nullable=True),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", market_status_enum, nullable=True, server_default="ACTIVE"),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
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
        sa.UniqueConstraint("platform", "external_id", name="uq_market_platform_external"),
    )

    op.create_index("ix_markets_platform", "markets", ["platform"])
    op.create_index("ix_markets_ticker", "markets", ["ticker"])
    op.create_index("ix_markets_status", "markets", ["status"])
    op.create_index("ix_markets_close_time", "markets", ["close_time"])

    # Create opportunities table
    op.create_table(
        "opportunities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "kalshi_market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("markets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "polymarket_market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("markets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("kalshi_price", sa.Numeric(10, 4), nullable=False),
        sa.Column("polymarket_price", sa.Numeric(10, 4), nullable=False),
        sa.Column("spread", sa.Numeric(10, 4), nullable=False),
        sa.Column("net_spread", sa.Numeric(10, 4), nullable=False),
        sa.Column("roi", sa.Numeric(10, 4), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False, server_default="1.0"),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            opportunity_status_enum,
            nullable=False,
            server_default="OPEN",
        ),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
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
    )

    op.create_index("ix_opportunities_status", "opportunities", ["status"])
    op.create_index("ix_opportunities_detected_at", "opportunities", ["detected_at"])
    op.create_index("ix_opportunities_roi", "opportunities", ["roi"])
    op.create_index(
        "ix_opportunities_open_roi",
        "opportunities",
        ["status", "roi"],
        postgresql_where=sa.text("status = 'OPEN'"),
    )

    # Create orders table
    op.create_table(
        "orders",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "opportunity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("opportunities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("markets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("platform", platform_enum, nullable=False),
        sa.Column("external_order_id", sa.String(255), nullable=True),
        sa.Column("ticker", sa.String(100), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("price", sa.Numeric(10, 4), nullable=False),
        sa.Column("size", sa.Integer, nullable=False),
        sa.Column("filled_size", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", order_status_enum, nullable=False, server_default="PENDING"),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
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
        sa.CheckConstraint("side IN ('BID', 'ASK', 'buy', 'sell')", name="ck_orders_side"),
        sa.CheckConstraint("size > 0", name="ck_orders_size_positive"),
        sa.CheckConstraint("filled_size >= 0", name="ck_orders_filled_nonnegative"),
        sa.CheckConstraint("filled_size <= size", name="ck_orders_filled_le_size"),
    )

    op.create_index("ix_orders_platform", "orders", ["platform"])
    op.create_index("ix_orders_status", "orders", ["status"])
    op.create_index("ix_orders_ticker", "orders", ["ticker"])
    op.create_index("ix_orders_external", "orders", ["platform", "external_order_id"])

    # Create trades table
    op.create_table(
        "trades",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "opportunity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("opportunities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "kalshi_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "polymarket_order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("gross_profit", sa.Numeric(12, 4), nullable=True),
        sa.Column("fees", sa.Numeric(12, 4), nullable=True),
        sa.Column("net_profit", sa.Numeric(12, 4), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
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
    )

    op.create_index("ix_trades_opened_at", "trades", ["opened_at"])
    op.create_index("ix_trades_closed_at", "trades", ["closed_at"])
    op.create_index("ix_trades_net_profit", "trades", ["net_profit"])

    # Create positions table
    op.create_table(
        "positions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "market_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("markets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("platform", platform_enum, nullable=False),
        sa.Column("ticker", sa.String(100), nullable=False),
        sa.Column("size", sa.Integer, nullable=False, server_default="0"),
        sa.Column("entry_price", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("current_price", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("unrealized_pnl", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("realized_pnl", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("platform", "ticker", name="uq_position_platform_ticker"),
    )

    op.create_index("ix_positions_platform", "positions", ["platform"])
    op.create_index("ix_positions_ticker", "positions", ["ticker"])

    # Create fills table
    op.create_table(
        "fills",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("platform", platform_enum, nullable=False),
        sa.Column("external_fill_id", sa.String(255), nullable=True),
        sa.Column("external_order_id", sa.String(255), nullable=False),
        sa.Column("ticker", sa.String(100), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("price", sa.Numeric(10, 4), nullable=False),
        sa.Column("size", sa.Integer, nullable=False),
        sa.Column("fee", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column(
            "filled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
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
        sa.CheckConstraint("side IN ('BID', 'ASK', 'buy', 'sell')", name="ck_fills_side"),
        sa.CheckConstraint("size > 0", name="ck_fills_size_positive"),
    )

    op.create_index("ix_fills_platform", "fills", ["platform"])
    op.create_index("ix_fills_ticker", "fills", ["ticker"])
    op.create_index("ix_fills_filled_at", "fills", ["filled_at"])
    op.create_index("ix_fills_external", "fills", ["platform", "external_fill_id"])

    # Create balances table
    op.create_table(
        "balances",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("platform", platform_enum, nullable=False),
        sa.Column("available", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("reserved", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(14, 4), nullable=False, server_default="0"),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
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
    )

    op.create_index("ix_balances_platform", "balances", ["platform"])
    op.create_index("ix_balances_recorded_at", "balances", ["recorded_at"])
    op.create_index("ix_balances_platform_time", "balances", ["platform", "recorded_at"])

    # Create system_events table
    op.create_table(
        "system_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="INFO"),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "severity IN ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')",
            name="ck_events_severity",
        ),
    )

    op.create_index("ix_events_type", "system_events", ["event_type"])
    op.create_index("ix_events_severity", "system_events", ["severity"])
    op.create_index("ix_events_created_at", "system_events", ["created_at"])
    op.create_index("ix_events_type_time", "system_events", ["event_type", "created_at"])

    # Create trigger function for updated_at
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ language 'plpgsql';
    """)

    # Apply trigger to all tables with updated_at
    for table in ["markets", "opportunities", "orders", "trades", "positions", "fills", "balances"]:
        op.execute(f"""
            CREATE TRIGGER update_{table}_updated_at
            BEFORE UPDATE ON {table}
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at_column();
        """)


def downgrade() -> None:
    """Drop all tables."""
    # Drop triggers first
    for table in ["markets", "opportunities", "orders", "trades", "positions", "fills", "balances"]:
        op.execute(f"DROP TRIGGER IF EXISTS update_{table}_updated_at ON {table}")

    op.execute("DROP FUNCTION IF EXISTS update_updated_at_column()")

    # Drop tables in reverse order of dependencies
    op.drop_table("system_events")
    op.drop_table("balances")
    op.drop_table("fills")
    op.drop_table("positions")
    op.drop_table("trades")
    op.drop_table("orders")
    op.drop_table("opportunities")
    op.drop_table("markets")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS marketstatus")
    op.execute("DROP TYPE IF EXISTS opportunitystatus")
    op.execute("DROP TYPE IF EXISTS orderstatus")
    op.execute("DROP TYPE IF EXISTS platform")

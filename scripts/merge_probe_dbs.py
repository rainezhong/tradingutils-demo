#!/usr/bin/env python3
"""Merge multiple probe databases into one unified training database.

Handles schema differences (L2 tables may not exist in all DBs).
Deduplicates by timestamp to avoid double-counting trades.
Preserves all data types and indexes.

Usage:
    python3 scripts/merge_probe_dbs.py \\
        --inputs data/btc_ob_48h.db data/btc_probe_20260227.db \\
        --output data/btc_merged_training.db

    python3 scripts/merge_probe_dbs.py \\
        --inputs data/*.db \\
        --output data/btc_all_historical.db \\
        --skip-kalshi  # Skip Kalshi-specific tables (markets/settlements)
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import List

# Tables to merge (order matters - dependencies first)
CORE_TABLES = [
    "kraken_trades",
    "binance_trades",
    "coinbase_trades",
    "kraken_snapshots",
]

# Optional tables (may not exist in all DBs)
OPTIONAL_TABLES = [
    "binance_l2",
    "coinbase_l2",
]

# Kalshi-specific tables (only merge if needed for backtesting)
KALSHI_TABLES = [
    "kalshi_snapshots",
    "kalshi_orderbook",
    "market_settlements",
]


def get_table_count(db_path: str, table: str) -> int:
    """Get row count for a table, or 0 if table doesn't exist."""
    try:
        with sqlite3.connect(db_path) as conn:
            result = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            return result[0] if result else 0
    except sqlite3.OperationalError:
        return 0


def table_exists(db_path: str, table: str) -> bool:
    """Check if table exists in database."""
    with sqlite3.connect(db_path) as conn:
        result = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,)
        ).fetchone()
        return result is not None


def create_merged_database(output_path: str, input_dbs: List[str]) -> None:
    """Create output database with schema from ALL input DBs."""
    if Path(output_path).exists():
        print(f"⚠️  Output database already exists: {output_path}")
        response = input("Delete and recreate? [y/N]: ")
        if response.lower() != 'y':
            print("Aborted.")
            sys.exit(1)
        Path(output_path).unlink()

    # Collect all table schemas from all input DBs
    all_schemas = {}
    for db_path in input_dbs:
        with sqlite3.connect(db_path) as conn:
            schema = conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"
            ).fetchall()
            for name, sql in schema:
                if 'sqlite_sequence' not in sql:
                    all_schemas[name] = sql  # Later DBs override if schema differs

    # Create output DB with all tables
    with sqlite3.connect(output_path) as out_conn:
        for table_name, sql in all_schemas.items():
            out_conn.execute(sql)
        out_conn.commit()

    print(f"✅ Created output database with {len(all_schemas)} tables: {output_path}")


def merge_table(
    input_dbs: List[str],
    output_db: str,
    table: str,
    dedupe_column: str = "ts",
) -> int:
    """Merge a single table from multiple input databases.

    Args:
        input_dbs: List of input database paths
        output_db: Output database path
        table: Table name to merge
        dedupe_column: Column to use for deduplication (default: ts)

    Returns:
        Number of rows merged
    """
    total_rows = 0

    with sqlite3.connect(output_db) as out_conn:
        # Check if table exists in output DB
        if not table_exists(output_db, table):
            return 0

        # Get column names (excluding id which is auto-increment)
        columns = out_conn.execute(f"PRAGMA table_info({table})").fetchall()
        col_names = [col[1] for col in columns if col[1] != 'id']

        if not col_names:
            return 0

        col_list = ', '.join(col_names)
        placeholders = ', '.join(['?' for _ in col_names])

        # Track seen timestamps to dedupe
        seen_ts = set()
        if dedupe_column in col_names:
            existing = out_conn.execute(f"SELECT {dedupe_column} FROM {table}").fetchall()
            seen_ts = {row[0] for row in existing}

        # Merge from each input DB
        for db_path in input_dbs:
            if not table_exists(db_path, table):
                continue

            with sqlite3.connect(db_path) as in_conn:
                rows = in_conn.execute(f"SELECT {col_list} FROM {table}").fetchall()

                # Filter duplicates if dedupe_column exists
                if dedupe_column in col_names:
                    ts_idx = col_names.index(dedupe_column)
                    new_rows = []
                    for row in rows:
                        if row[ts_idx] not in seen_ts:
                            new_rows.append(row)
                            seen_ts.add(row[ts_idx])
                    rows = new_rows

                # Insert in batches
                batch_size = 10000
                for i in range(0, len(rows), batch_size):
                    batch = rows[i:i+batch_size]
                    out_conn.executemany(
                        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                        batch
                    )
                    total_rows += len(batch)

                out_conn.commit()

    return total_rows


def merge_databases(
    input_dbs: List[str],
    output_db: str,
    skip_kalshi: bool = False,
) -> None:
    """Merge multiple probe databases into one.

    Args:
        input_dbs: List of input database paths
        output_db: Output database path
        skip_kalshi: If True, skip Kalshi-specific tables
    """
    print("="*70)
    print("PROBE DATABASE MERGER")
    print("="*70)
    print(f"Input databases: {len(input_dbs)}")
    for db in input_dbs:
        print(f"  - {db}")
    print(f"Output: {output_db}")
    print(f"Skip Kalshi tables: {skip_kalshi}")
    print()

    # Validate inputs
    for db in input_dbs:
        if not Path(db).exists():
            print(f"❌ Input database not found: {db}")
            sys.exit(1)

    # Create output DB with schemas from all input DBs
    create_merged_database(output_db, input_dbs)

    # Merge core tables
    print("="*70)
    print("MERGING CORE TABLES")
    print("="*70)

    tables_to_merge = CORE_TABLES.copy()
    if not skip_kalshi:
        tables_to_merge.extend(KALSHI_TABLES)
    tables_to_merge.extend(OPTIONAL_TABLES)

    for table in tables_to_merge:
        # Check if any input DB has this table
        has_table = any(table_exists(db, table) for db in input_dbs)
        if not has_table:
            print(f"⏭️  Skipping {table} (not found in any input DB)")
            continue

        print(f"📊 Merging {table}...")

        # Show input counts
        for db in input_dbs:
            count = get_table_count(db, table)
            if count > 0:
                print(f"    {Path(db).name}: {count:,} rows")

        # Merge
        total = merge_table(input_dbs, output_db, table)
        print(f"  ✅ Merged {total:,} total rows")
        print()

    # Create indexes
    print("="*70)
    print("CREATING INDEXES")
    print("="*70)

    # Collect all indexes from all input DBs
    all_indexes = set()
    for db_path in input_dbs:
        with sqlite3.connect(db_path) as conn:
            indexes = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
            ).fetchall()
            for (sql,) in indexes:
                if sql:  # Filter out None
                    all_indexes.add(sql)

    with sqlite3.connect(output_db) as conn:
        for sql in all_indexes:
            try:
                conn.execute(sql)
                print(f"  ✅ {sql[:60]}...")
            except sqlite3.OperationalError as e:
                print(f"  ⚠️  Skipped (already exists): {sql[:60]}...")

        conn.commit()

    print()

    # Summary
    print("="*70)
    print("MERGE COMPLETE")
    print("="*70)

    with sqlite3.connect(output_db) as conn:
        for table in tables_to_merge:
            if not table_exists(output_db, table):
                continue
            count = get_table_count(output_db, table)
            print(f"  {table}: {count:,} rows")

    print()
    print(f"✅ Merged database saved to: {output_db}")


def main():
    parser = argparse.ArgumentParser(
        description="Merge multiple probe databases into one training database"
    )
    parser.add_argument(
        "--inputs",
        nargs="+",
        required=True,
        help="Input database paths (space-separated)"
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output database path"
    )
    parser.add_argument(
        "--skip-kalshi",
        action="store_true",
        help="Skip Kalshi-specific tables (markets, settlements, orderbook)"
    )

    args = parser.parse_args()

    try:
        merge_databases(
            input_dbs=args.inputs,
            output_db=args.output,
            skip_kalshi=args.skip_kalshi,
        )
        print("✅ Merge completed successfully!")
    except Exception as e:
        print(f"❌ Merge failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

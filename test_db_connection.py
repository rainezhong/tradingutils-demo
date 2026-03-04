#!/usr/bin/env python3
"""Quick diagnostic to find the correct database"""

import sqlite3
from pathlib import Path

# Find data directory
current = Path.cwd()
print(f"Current directory: {current}")

if (current / 'data').exists():
    data_dir = current / 'data'
elif (current.parent / 'data').exists():
    data_dir = current.parent / 'data'
else:
    print("ERROR: Can't find data directory!")
    exit(1)

print(f"Data directory: {data_dir}")
print(f"\nAvailable databases:")

# List all databases
for db in sorted(data_dir.glob('*.db')):
    size_mb = db.stat().st_size / 1024 / 1024
    print(f"  - {db.name} ({size_mb:.1f} MB)")

print("\nChecking for kalshi_snapshots table...")

# Test each database
candidates = [
    'btc_probe_20260227.db',
    'btc_ob_48h.db',
    'btc_probe_merged.db',
    'btc_latency_probe.db'
]

for candidate in candidates:
    db_path = data_dir / candidate
    if not db_path.exists():
        print(f"  {candidate}: NOT FOUND")
        continue

    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Get all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        if 'kalshi_snapshots' in tables:
            # Get row count
            cursor.execute("SELECT COUNT(*) FROM kalshi_snapshots")
            count = cursor.fetchone()[0]
            print(f"  ✓ {candidate}: HAS kalshi_snapshots ({count:,} rows)")

            # Show first row
            cursor.execute("SELECT * FROM kalshi_snapshots LIMIT 1")
            print(f"    First row: {cursor.fetchone()}")
        else:
            print(f"  ✗ {candidate}: missing kalshi_snapshots (tables: {', '.join(tables[:3])}...)")

        conn.close()
    except Exception as e:
        print(f"  ✗ {candidate}: ERROR - {e}")

print("\n" + "="*60)
print("RECOMMENDED: Use the database marked with ✓")
print("="*60)

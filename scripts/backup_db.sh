#!/bin/bash
# Database backup script for Kalshi Market Data Collector
# Usage: ./scripts/backup_db.sh [backup_dir]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DB_PATH="$PROJECT_ROOT/data/markets.db"
BACKUP_DIR="${1:-$PROJECT_ROOT/backups}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="$BACKUP_DIR/markets_${TIMESTAMP}.db"

# Create backup directory if needed
mkdir -p "$BACKUP_DIR"

echo "==================================="
echo "Database Backup"
echo "==================================="
echo

# Check if database exists
if [ ! -f "$DB_PATH" ]; then
    echo "Error: Database not found at $DB_PATH"
    exit 1
fi

# Get database size
DB_SIZE=$(du -h "$DB_PATH" | cut -f1)
echo "Source: $DB_PATH ($DB_SIZE)"
echo "Target: $BACKUP_FILE"
echo

# Create backup using SQLite online backup
echo "Creating backup..."
sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"

# Verify backup
if [ -f "$BACKUP_FILE" ]; then
    BACKUP_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
    echo "Backup created successfully ($BACKUP_SIZE)"
else
    echo "Error: Backup failed"
    exit 1
fi

# Compress backup
echo "Compressing backup..."
gzip "$BACKUP_FILE"
COMPRESSED_FILE="${BACKUP_FILE}.gz"
COMPRESSED_SIZE=$(du -h "$COMPRESSED_FILE" | cut -f1)
echo "Compressed to $COMPRESSED_FILE ($COMPRESSED_SIZE)"

# Clean up old backups (keep last 7 days)
echo
echo "Cleaning up old backups..."
find "$BACKUP_DIR" -name "markets_*.db.gz" -mtime +7 -delete 2>/dev/null || true
BACKUP_COUNT=$(ls -1 "$BACKUP_DIR"/markets_*.db.gz 2>/dev/null | wc -l)
echo "Keeping $BACKUP_COUNT backup(s)"

echo
echo "==================================="
echo "Backup Complete!"
echo "==================================="

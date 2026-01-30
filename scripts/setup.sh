#!/bin/bash
# Setup script for Kalshi Market Data Collector
# Usage: ./scripts/setup.sh

set -e

echo "==================================="
echo "Kalshi Market Data Collector Setup"
echo "==================================="
echo

# Detect script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# Check Python version
echo "[1/6] Checking Python version..."
PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1,2)
REQUIRED_VERSION="3.9"

if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
    echo "Error: Python $REQUIRED_VERSION or higher is required (found $PYTHON_VERSION)"
    exit 1
fi
echo "    Python $PYTHON_VERSION detected"

# Create virtual environment if it doesn't exist
echo "[2/6] Setting up virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "    Created new virtual environment"
else
    echo "    Using existing virtual environment"
fi

# Activate virtual environment
source venv/bin/activate

# Install dependencies
echo "[3/6] Installing dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "    Dependencies installed"

# Create directories
echo "[4/6] Creating directories..."
mkdir -p data reports logs
echo "    Created data/, reports/, logs/"

# Copy config file if not exists
echo "[5/6] Setting up configuration..."
if [ ! -f "config.yaml" ]; then
    cp config.example.yaml config.yaml
    echo "    Created config.yaml from template"
    echo "    IMPORTANT: Edit config.yaml with your settings"
else
    echo "    config.yaml already exists"
fi

# Initialize database
echo "[6/6] Initializing database..."
python3 -c "
from src.core import MarketDatabase, get_config
db = MarketDatabase()
db.init_db()
db.close()
print('    Database initialized at', get_config().db_path)
"

echo
echo "==================================="
echo "Setup Complete!"
echo "==================================="
echo
echo "Next steps:"
echo "  1. Edit config.yaml with your settings"
echo "  2. Activate the virtual environment: source venv/bin/activate"
echo "  3. Run a test scan: python main.py scan"
echo "  4. View help: python main.py --help"
echo

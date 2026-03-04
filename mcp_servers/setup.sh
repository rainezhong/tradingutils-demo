#!/bin/bash
# Setup the MCP research server venv
# Requires Python 3.10+ (the mcp SDK needs it)
set -e

cd "$(dirname "$0")"

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "Error: Python 3.10+ required for the MCP SDK."
    echo "Install with: pyenv install 3.12 && pyenv shell 3.12"
    exit 1
fi

echo "Using $PYTHON ($($PYTHON --version))"

$PYTHON -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

echo ""
echo "Done. Restart Claude Code to pick up the MCP server."
echo "The server will be available as 'research' with tools:"
echo "  - list_databases, query_db, describe_table"
echo "  - list_notebooks, run_notebook, create_notebook, read_notebook_output"

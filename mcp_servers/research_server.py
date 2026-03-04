#!/usr/bin/env python3
"""
MCP Research Server for TradingUtils

Gives Claude tools to query SQLite databases, execute Jupyter notebooks,
and create new analysis notebooks. Designed for strategy research workflows.

Setup:
    cd mcp_servers && ./setup.sh
    # Then restart Claude Code to pick up .mcp.json
"""

import json
import os
import re
import sqlite3
import subprocess
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import nbformat
from mcp.server.fastmcp import FastMCP

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
NOTEBOOKS_DIR = PROJECT_ROOT / "notebooks"
RESULTS_DIR = NOTEBOOKS_DIR / "results"

# System python that has numpy/pandas/matplotlib/papermill installed
SYSTEM_PYTHON = os.environ.get("RESEARCH_PYTHON", "python3")

# ── Server ────────────────────────────────────────────────────────────────────
mcp = FastMCP(
    "research",
    instructions=(
        "Trading research tools. Use query_db to explore market data in SQLite, "
        "run_notebook to execute Jupyter analyses, create_notebook for new studies."
    ),
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _resolve_db(name: str) -> Path:
    """Resolve a database name to a path in data/."""
    p = DATA_DIR / name
    if not p.suffix:
        p = p.with_suffix(".db")
    if not p.exists():
        available = ", ".join(f.name for f in sorted(DATA_DIR.glob("*.db")))
        raise FileNotFoundError(f"Database not found: {p.name}. Available: {available}")
    return p


def _resolve_notebook(name: str) -> Path:
    """Resolve a notebook name, checking results/ then notebooks/."""
    for base in [RESULTS_DIR, NOTEBOOKS_DIR]:
        p = base / name
        if p.exists():
            return p
        if not name.endswith(".ipynb"):
            p = base / f"{name}.ipynb"
            if p.exists():
                return p
    # Try as absolute/relative path
    p = Path(name)
    if p.exists():
        return p
    raise FileNotFoundError(f"Notebook not found: {name}")


def _format_rows(columns: List[str], rows: list, max_col_width: int = 60) -> str:
    """Format query results as an aligned text table."""
    if not rows:
        return "(no rows)"

    str_rows = []
    for row in rows:
        str_rows.append([str(v) if v is not None else "NULL" for v in row])

    widths = [len(c) for c in columns]
    for row in str_rows:
        for i, val in enumerate(row):
            widths[i] = min(max(widths[i], len(val)), max_col_width)

    header = " | ".join(c.ljust(widths[i]) for i, c in enumerate(columns))
    separator = "-+-".join("-" * widths[i] for i in range(len(columns)))

    lines = [header, separator]
    for row in str_rows:
        vals = []
        for i, val in enumerate(row):
            if len(val) > max_col_width:
                val = val[: max_col_width - 3] + "..."
            vals.append(val.ljust(widths[i]))
        lines.append(" | ".join(vals))

    return "\n".join(lines)


def _extract_notebook_outputs(nb_path: Path) -> str:
    """Extract text outputs from all code cells in a notebook."""
    nb = nbformat.read(str(nb_path), as_version=4)

    parts = [f"## Outputs from {nb_path.name}\n"]

    for i, cell in enumerate(nb.cells):
        if cell.cell_type != "code":
            continue

        outputs = cell.get("outputs", [])
        if not outputs:
            continue

        cell_parts = []
        for output in outputs:
            if output.output_type == "stream":
                cell_parts.append(output.text)
            elif output.output_type in ("execute_result", "display_data"):
                data = output.get("data", {})
                if "text/plain" in data:
                    cell_parts.append(data["text/plain"])
                if "image/png" in data:
                    cell_parts.append("[matplotlib figure]")
            elif output.output_type == "error":
                tb = "\n".join(output.get("traceback", []))
                # Strip ANSI color codes
                tb = re.sub(r"\x1b\[[0-9;]*m", "", tb)
                cell_parts.append(f"ERROR ({output.ename}): {output.evalue}\n{tb}")

        if cell_parts:
            first_line = cell.source.split("\n")[0][:80]
            parts.append(f"### Cell {i + 1}: `{first_line}`")
            parts.append("\n".join(cell_parts))
            parts.append("")

    return "\n".join(parts) if len(parts) > 1 else "No outputs found in notebook."


# ── Database Tools ────────────────────────────────────────────────────────────


@mcp.tool()
def list_databases() -> str:
    """List all SQLite databases in data/ with their tables and row counts."""
    dbs = sorted(DATA_DIR.glob("*.db"))
    if not dbs:
        return "No databases found in data/"

    parts = []
    for db_path in dbs:
        size_mb = db_path.stat().st_size / (1024 * 1024)
        parts.append(f"\n## {db_path.name} ({size_mb:.1f} MB)")

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()

            for (table_name,) in tables:
                try:
                    count = conn.execute(
                        f"SELECT COUNT(*) FROM [{table_name}]"
                    ).fetchone()[0]
                    parts.append(f"  - {table_name}: {count:,} rows")
                except Exception:
                    parts.append(f"  - {table_name}: (error reading)")

            conn.close()
        except Exception as e:
            parts.append(f"  (error: {e})")

    return "\n".join(parts)


@mcp.tool()
def query_db(
    sql: str,
    database: str = "btc_latency_probe.db",
    limit: int = 200,
) -> str:
    """Run a SQL query against a SQLite database.

    Databases are opened read-only. A LIMIT is auto-appended to SELECT queries
    if not already present.

    Args:
        sql: SQL query to execute (SELECT, PRAGMA, EXPLAIN, etc.)
        database: Database filename in data/ (default: btc_latency_probe.db)
        limit: Max rows to return (default: 200, set to 0 for no limit)
    """
    db_path = _resolve_db(database)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    cleaned = sql.strip().rstrip(";")

    # Auto-limit SELECTs that don't already have a LIMIT
    if limit > 0 and cleaned.upper().startswith("SELECT"):
        if "LIMIT" not in cleaned.upper().split(")")[-1]:
            cleaned = f"{cleaned} LIMIT {limit}"

    try:
        cursor = conn.execute(cleaned)
        if cursor.description:
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            result = _format_rows(columns, rows)
            result += f"\n\n({len(rows)} rows"
            if len(rows) == limit and limit > 0:
                result += ", limit hit — increase limit or add your own LIMIT/WHERE"
            result += ")"
            return result
        else:
            return "(query executed, no results returned)"
    except Exception as e:
        return f"SQL error: {e}"
    finally:
        conn.close()


@mcp.tool()
def describe_table(
    table: str,
    database: str = "btc_latency_probe.db",
) -> str:
    """Get schema, indexes, row count, time range, and sample rows for a table.

    Args:
        table: Table name
        database: Database filename in data/
    """
    db_path = _resolve_db(database)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    parts = [f"## {table} in {database}"]

    try:
        # Schema
        schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not schema:
            return f"Table '{table}' not found in {database}"
        parts.append(f"\n### CREATE statement\n```sql\n{schema[0]}\n```")

        # Column info
        cols = conn.execute(f"PRAGMA table_info([{table}])").fetchall()
        parts.append("\n### Columns")
        for col in cols:
            _, name, dtype, notnull, default, pk = col
            flags = []
            if pk:
                flags.append("PK")
            if notnull:
                flags.append("NOT NULL")
            if default is not None:
                flags.append(f"DEFAULT {default}")
            flag_str = f" ({', '.join(flags)})" if flags else ""
            parts.append(f"  - `{name}` {dtype}{flag_str}")

        # Indexes
        indexes = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=?",
            (table,),
        ).fetchall()
        if indexes:
            parts.append("\n### Indexes")
            for _, idx_sql in indexes:
                if idx_sql:
                    parts.append(f"  - `{idx_sql}`")

        # Row count
        count = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
        parts.append("\n### Stats")
        parts.append(f"Rows: {count:,}")

        # Time range if ts column exists
        col_names = [c[1] for c in cols]
        if "ts" in col_names:
            ts_range = conn.execute(
                f"SELECT MIN(ts), MAX(ts) FROM [{table}]"
            ).fetchone()
            if ts_range[0] is not None:
                from_dt = datetime.fromtimestamp(ts_range[0]).isoformat(
                    timespec="seconds"
                )
                to_dt = datetime.fromtimestamp(ts_range[1]).isoformat(
                    timespec="seconds"
                )
                duration_h = (ts_range[1] - ts_range[0]) / 3600
                parts.append(
                    f"Time range: {from_dt} to {to_dt} ({duration_h:.1f} hours)"
                )

        # Sample rows
        cursor = conn.execute(f"SELECT * FROM [{table}] LIMIT 5")
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        parts.append(f"\n### Sample rows (first 5)\n{_format_rows(columns, rows)}")

    except Exception as e:
        parts.append(f"\nError: {e}")
    finally:
        conn.close()

    return "\n".join(parts)


# ── Notebook Tools ────────────────────────────────────────────────────────────


@mcp.tool()
def list_notebooks() -> str:
    """List Jupyter notebooks in notebooks/ and notebooks/results/."""
    parts = []

    for label, directory in [
        ("notebooks/", NOTEBOOKS_DIR),
        ("notebooks/results/", RESULTS_DIR),
    ]:
        if not directory.exists():
            continue
        nbs = sorted(directory.glob("*.ipynb"))
        if not nbs:
            continue

        parts.append(f"\n## {label}")
        for nb in nbs:
            size_kb = nb.stat().st_size / 1024
            mtime = datetime.fromtimestamp(nb.stat().st_mtime).strftime(
                "%Y-%m-%d %H:%M"
            )
            parts.append(f"  - {nb.name} ({size_kb:.0f} KB, {mtime})")

    return "\n".join(parts) if parts else "No notebooks found."


@mcp.tool()
def run_notebook(
    notebook: str,
    parameters: Optional[Dict[str, Any]] = None,
    output_name: Optional[str] = None,
    timeout: int = 300,
) -> str:
    """Execute a Jupyter notebook via papermill and return cell outputs.

    The notebook runs under the system Python environment which has numpy,
    pandas, matplotlib, etc. Output is saved to notebooks/results/.

    Args:
        notebook: Notebook filename (in notebooks/) or path
        parameters: Dict of parameters to inject (papermill -p key value)
        output_name: Output filename stem (default: <input>_executed)
        timeout: Per-cell execution timeout in seconds (default: 300)
    """
    nb_path = _resolve_notebook(notebook)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if output_name is None:
        stem = nb_path.stem.replace("_executed", "")
        output_name = f"{stem}_executed"
    out_path = RESULTS_DIR / f"{output_name}.ipynb"

    cmd = [
        SYSTEM_PYTHON,
        "-m",
        "papermill",
        str(nb_path),
        str(out_path),
        "--cwd",
        str(PROJECT_ROOT),
        "--execution-timeout",
        str(timeout),
        "--no-progress-bar",
    ]

    if parameters:
        for key, value in parameters.items():
            if isinstance(value, (dict, list)):
                cmd.extend(["-p", str(key), json.dumps(value)])
            elif isinstance(value, bool):
                cmd.extend(["-p", str(key), str(value).lower()])
            elif isinstance(value, (int, float)):
                cmd.extend(["-p", str(key), str(value)])
            else:
                cmd.extend(["-p", str(key), str(value)])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout + 60,
            cwd=str(PROJECT_ROOT),
        )

        if result.returncode != 0:
            # Truncate long error output
            stderr = result.stderr
            if len(stderr) > 3000:
                stderr = stderr[:1500] + "\n...\n" + stderr[-1500:]
            return f"Notebook execution failed (exit {result.returncode}):\n{stderr}"

        return _extract_notebook_outputs(out_path)

    except subprocess.TimeoutExpired:
        return f"Notebook execution timed out after {timeout + 60}s"
    except Exception as e:
        return f"Error running notebook: {e}"


@mcp.tool()
def create_notebook(
    name: str,
    cells: List[str],
    cell_types: Optional[List[str]] = None,
    description: str = "",
) -> str:
    """Create a new Jupyter notebook in notebooks/.

    A setup cell with standard imports (sqlite3, numpy, pandas, matplotlib)
    and project root configuration is prepended automatically.

    Args:
        name: Notebook name (without .ipynb)
        cells: List of cell source strings
        cell_types: 'code' or 'markdown' per cell (default: all code)
        description: Markdown description for the header cell
    """
    if cell_types is None:
        cell_types = ["code"] * len(cells)

    if len(cell_types) != len(cells):
        return "Error: cells and cell_types must have the same length"

    nb = nbformat.v4.new_notebook()
    nb.metadata["kernelspec"] = {
        "display_name": "Python 3",
        "language": "python",
        "name": "python3",
    }

    # Header
    if description:
        nb.cells.append(nbformat.v4.new_markdown_cell(f"# {name}\n\n{description}"))

    # Standard setup cell
    setup = textwrap.dedent("""\
        import sqlite3
        import sys
        from pathlib import Path

        import numpy as np
        import pandas as pd
        import matplotlib.pyplot as plt

        PROJECT_ROOT = Path.cwd() if (Path.cwd() / 'data').exists() else Path.cwd().parent
        sys.path.insert(0, str(PROJECT_ROOT))
        DATA_DIR = PROJECT_ROOT / 'data'

        %matplotlib inline
        plt.rcParams['figure.figsize'] = (12, 6)
        plt.rcParams['figure.dpi'] = 100
    """)
    nb.cells.append(nbformat.v4.new_code_cell(setup))

    # User cells
    for source, ctype in zip(cells, cell_types):
        if ctype == "markdown":
            nb.cells.append(nbformat.v4.new_markdown_cell(source))
        else:
            nb.cells.append(nbformat.v4.new_code_cell(source))

    out_path = NOTEBOOKS_DIR / f"{name}.ipynb"
    with open(out_path, "w") as f:
        nbformat.write(nb, f)

    return f"Created: {out_path} ({len(nb.cells)} cells including setup)"


@mcp.tool()
def read_notebook_output(notebook: str) -> str:
    """Read text outputs from an executed notebook.

    Extracts stdout, text results, and error tracebacks from code cells.
    Checks notebooks/results/ first, then notebooks/.

    Args:
        notebook: Notebook filename or path
    """
    nb_path = _resolve_notebook(notebook)
    return _extract_notebook_outputs(nb_path)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()

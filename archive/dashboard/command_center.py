"""
Trading Command Center - Unified Web UI

A 1/3 - 2/3 split layout web interface for:
- Browsing all available scanners, strategies, and executors
- Configuring parameters via form inputs (maps to CLI args)
- Dry run / wet run with step-by-step workflow
- Market selection from scan results before execution

Run with:
    python -m dashboard.command_center

Then open http://localhost:8050
"""

import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

# Add parent to path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# Module Registry - Discovers all available tools
# =============================================================================


class ModuleCategory(str, Enum):
    SCANNER = "scanner"
    STRATEGY = "strategy"
    EXECUTOR = "executor"
    UTILITY = "utility"


@dataclass
class Parameter:
    """A configurable parameter for a module."""

    name: str
    type: str  # "str", "int", "float", "bool", "choice", "multiselect"
    default: Any = None
    description: str = ""
    required: bool = False
    choices: List[str] = field(default_factory=list)  # For choice/multiselect
    min_value: Optional[float] = None
    max_value: Optional[float] = None


@dataclass
class RegisteredModule:
    """A registered module (scanner, strategy, executor)."""

    id: str
    name: str
    category: ModuleCategory
    description: str
    parameters: List[Parameter] = field(default_factory=list)
    supports_dry_run: bool = True
    supports_market_selection: bool = False

    # Callables for execution
    scan_func: Optional[Callable] = None
    execute_func: Optional[Callable] = None


# Global registry
_REGISTRY: Dict[str, RegisteredModule] = {}


def register_module(module: RegisteredModule):
    """Register a module in the global registry."""
    _REGISTRY[module.id] = module
    logger.info(f"Registered module: {module.id} ({module.category.value})")


def get_all_modules() -> List[RegisteredModule]:
    """Get all registered modules."""
    return list(_REGISTRY.values())


def get_module(module_id: str) -> Optional[RegisteredModule]:
    """Get a module by ID."""
    return _REGISTRY.get(module_id)


# =============================================================================
# Auto-discover and register modules
# =============================================================================


def discover_modules():
    """Auto-discover and register available modules from src/."""

    # NBA Points Spread Scanner
    register_module(
        RegisteredModule(
            id="nba_spread_scanner",
            name="NBA Spread Scanner",
            category=ModuleCategory.SCANNER,
            description="Scans Kalshi NBA markets for mispricing via bid/ask spreads. "
            "Finds dutch book opportunities and wide spreads.",
            parameters=[
                Parameter(
                    "series",
                    "multiselect",
                    ["KXNBAGAME", "KXNBATOTAL", "KXNBASPREAD"],
                    "Which NBA series to scan",
                    choices=["KXNBAGAME", "KXNBATOTAL", "KXNBASPREAD"],
                ),
                Parameter("min_edge", "float", 3.0, "Minimum edge in cents to report"),
                Parameter(
                    "verbose",
                    "bool",
                    False,
                    "Show top 10 markets even if no profitable edge",
                ),
                Parameter(
                    "depth",
                    "bool",
                    False,
                    "Fetch orderbook depth (slower but more accurate)",
                ),
                Parameter(
                    "maker", "bool", False, "Use maker fee assumptions (optimistic)"
                ),
                Parameter("demo", "bool", False, "Use Kalshi demo API"),
            ],
            supports_dry_run=True,
            supports_market_selection=True,
        )
    )

    # Cross-Market Pairs Scanner
    register_module(
        RegisteredModule(
            id="nba_cross_market",
            name="NBA Cross-Market Pairs",
            category=ModuleCategory.SCANNER,
            description="Finds complementary market pairs (Team A vs Team B) for cross-market analysis. "
            "Key insight: YES on Team A = NO on Team B.",
            parameters=[
                Parameter(
                    "series",
                    "multiselect",
                    ["KXNBAGAME"],
                    "Which NBA series to scan",
                    choices=["KXNBAGAME", "KXNBATOTAL", "KXNBASPREAD"],
                ),
                Parameter("top", "int", 10, "Show top N pairs"),
                Parameter("demo", "bool", False, "Use Kalshi demo API"),
            ],
            supports_dry_run=True,
            supports_market_selection=True,
        )
    )

    # Spread Hunter
    register_module(
        RegisteredModule(
            id="spread_hunter",
            name="Spread Hunter",
            category=ModuleCategory.STRATEGY,
            description="Posts maker orders at bid, takes other side on fill. "
            "NOT arbitrage - requires fills and stable quotes.",
            parameters=[
                Parameter(
                    "series",
                    "multiselect",
                    ["KXNBAGAME", "KXNBATOTAL", "KXNBASPREAD"],
                    "Which NBA series to scan",
                    choices=["KXNBAGAME", "KXNBATOTAL", "KXNBASPREAD"],
                ),
                Parameter(
                    "slippage",
                    "float",
                    2.0,
                    "Target profit in cents (higher = greedier, harder to fill)",
                ),
                Parameter("top", "int", 5, "Show top N huntable markets"),
                Parameter("size", "int", 10, "Contracts per hunt"),
                Parameter(
                    "timeout", "float", 60.0, "Max seconds to wait for maker fill"
                ),
                Parameter("max_concurrent", "int", 3, "Max simultaneous hunts"),
                Parameter("demo", "bool", False, "Use Kalshi demo API"),
            ],
            supports_dry_run=True,
            supports_market_selection=True,
        )
    )

    # Spread Executor
    register_module(
        RegisteredModule(
            id="spread_executor",
            name="Spread Executor",
            category=ModuleCategory.EXECUTOR,
            description="Executes a spread trade by buying YES and NO on a specific market. "
            "Better side fills first, then takes worse side.",
            parameters=[
                Parameter(
                    "ticker", "str", "", "Market ticker (required)", required=True
                ),
                Parameter("size", "int", 50, "Number of contracts"),
                Parameter(
                    "force", "bool", False, "Execute even without profitable edge"
                ),
                Parameter("demo", "bool", False, "Use Kalshi demo API"),
            ],
            supports_dry_run=True,
            supports_market_selection=False,
        )
    )

    # Monitor
    register_module(
        RegisteredModule(
            id="nba_monitor",
            name="NBA Spread Monitor",
            category=ModuleCategory.UTILITY,
            description="Continuously monitors NBA markets for spread opportunities. "
            "Alerts when opportunities persist for minimum duration.",
            parameters=[
                Parameter("min_edge", "float", 3.0, "Minimum edge in cents"),
                Parameter(
                    "min_duration", "float", 5.0, "Minimum duration in seconds to alert"
                ),
                Parameter("interval", "float", 2.0, "Scan interval in seconds"),
                Parameter("maker", "bool", False, "Use maker fee assumptions"),
                Parameter("demo", "bool", False, "Use Kalshi demo API"),
            ],
            supports_dry_run=True,
            supports_market_selection=False,
        )
    )

    # Try to discover more from src/strategies
    try:
        from src.strategies.nba_mispricing import NBAMispricingStrategy

        register_module(
            RegisteredModule(
                id="nba_mispricing_strategy",
                name="NBA Mispricing Strategy",
                category=ModuleCategory.STRATEGY,
                description="Detects and trades NBA mispricing opportunities.",
                parameters=[
                    Parameter("min_edge_cents", "float", 2.0, "Minimum edge to trade"),
                    Parameter("max_position", "int", 100, "Maximum position size"),
                ],
                supports_dry_run=True,
            )
        )
    except ImportError:
        pass

    # Try to discover arbitrage detector
    try:
        from src.arbitrage.detector import ArbitrageDetector

        register_module(
            RegisteredModule(
                id="arbitrage_detector",
                name="Cross-Exchange Arbitrage Detector",
                category=ModuleCategory.SCANNER,
                description="Detects arbitrage opportunities across exchanges (Kalshi, Polymarket).",
                parameters=[
                    Parameter("min_edge", "float", 1.0, "Minimum edge in cents"),
                    Parameter("markets", "str", "", "Comma-separated market filters"),
                ],
                supports_dry_run=True,
                supports_market_selection=True,
            )
        )
    except ImportError:
        pass


# =============================================================================
# Execution Engine
# =============================================================================


class RunMode(str, Enum):
    DRY = "dry"
    WET = "wet"


@dataclass
class ScanResult:
    """Result from a scanner."""

    ticker: str
    title: str = ""
    edge_cents: float = 0.0
    spread_cents: float = 0.0
    yes_bid: float = 0.0
    yes_ask: float = 0.0
    no_bid: float = 0.0
    no_ask: float = 0.0
    close_time: Optional[str] = None  # ISO format timestamp
    game_status: Optional[str] = None  # Live game status like "Q3 4:21"
    selected: bool = False  # User selection for execution
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionStep:
    """A step in the execution workflow."""

    step_number: int
    description: str
    status: str = "pending"  # pending, running, completed, failed
    result: Optional[str] = None
    error: Optional[str] = None
    timestamp: Optional[datetime] = None


@dataclass
class ExecutionSession:
    """Tracks an execution session."""

    session_id: str
    module_id: str
    mode: RunMode
    parameters: Dict[str, Any]
    steps: List[ExecutionStep] = field(default_factory=list)
    scan_results: List[ScanResult] = field(default_factory=list)
    selected_markets: List[str] = field(default_factory=list)
    status: str = (
        "initialized"  # initialized, scanning, selecting, executing, completed, failed
    )
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    final_result: Optional[Dict[str, Any]] = None


# Active sessions
_SESSIONS: Dict[str, ExecutionSession] = {}


async def run_scanner(
    module_id: str, params: Dict[str, Any], dry_run: bool = True
) -> List[ScanResult]:
    """Run a scanner and return results."""
    from src.kalshi.client import KalshiClient

    results = []

    if module_id == "nba_spread_scanner":
        # Import and run NBA scanner
        from arb.nba_points_arb import NBAPointsArbScanner

        client = KalshiClient.from_env(demo=params.get("demo", True))
        async with client:
            scanner = NBAPointsArbScanner(
                client,
                min_edge_cents=params.get("min_edge", -100.0),
                use_maker_fees=params.get("maker", False),
                series_tickers=params.get("series"),  # Pass selected series
            )

            markets = await scanner.fetch_nba_markets()

            for m in markets:
                if m.yes_bid and m.yes_ask and m.no_bid and m.no_ask:
                    opp = scanner.calculate_dutch_edge(m)
                    results.append(
                        ScanResult(
                            ticker=m.ticker,
                            title=m.title,
                            edge_cents=(opp.net_edge * 100) if opp else 0,
                            spread_cents=(
                                (m.yes_ask - m.yes_bid) + (m.no_ask - m.no_bid)
                            )
                            * 100,
                            yes_bid=m.yes_bid,
                            yes_ask=m.yes_ask,
                            no_bid=m.no_bid,
                            no_ask=m.no_ask,
                            close_time=m.close_time.isoformat()
                            if m.close_time
                            else None,
                            game_status=m.game_status,
                        )
                    )

    elif module_id == "nba_cross_market":
        from arb.nba_points_arb import NBAPointsArbScanner

        client = KalshiClient.from_env(demo=params.get("demo", True))
        async with client:
            scanner = NBAPointsArbScanner(
                client,
                min_edge_cents=-100.0,
                series_tickers=params.get("series"),
            )
            pairs = await scanner.scan_cross_market()

            for pair in pairs[: params.get("top", 10)]:
                results.append(
                    ScanResult(
                        ticker=pair.event_ticker,
                        title=f"{pair.team_a_name} vs {pair.team_b_name}",
                        edge_cents=pair.dutch_book_edge * 100,
                        extra={
                            "market_a": pair.market_a.ticker,
                            "market_b": pair.market_b.ticker,
                            "prob_diff": pair.implied_prob_diff * 100,
                        },
                    )
                )

    elif module_id == "spread_hunter":
        from arb.nba_points_arb import NBAPointsArbScanner
        from src.kalshi.spread_hunter import MarketQuote

        client = KalshiClient.from_env(demo=params.get("demo", True))
        async with client:
            scanner = NBAPointsArbScanner(
                client,
                min_edge_cents=-100.0,
                series_tickers=params.get("series"),
            )
            markets = await scanner.fetch_nba_markets()

            slippage = params.get("slippage", 2.0) / 100.0

            for m in markets:
                if m.yes_bid and m.yes_ask and m.no_bid and m.no_ask:
                    quote = MarketQuote(
                        ticker=m.ticker,
                        yes_bid=m.yes_bid,
                        yes_ask=m.yes_ask,
                        no_bid=m.no_bid,
                        no_ask=m.no_ask,
                    )
                    edge = quote.huntable_edge(slippage)
                    results.append(
                        ScanResult(
                            ticker=m.ticker,
                            title=m.title,
                            edge_cents=edge * 100,
                            spread_cents=(quote.yes_spread + quote.no_spread) * 100,
                            yes_bid=m.yes_bid,
                            yes_ask=m.yes_ask,
                            no_bid=m.no_bid,
                            no_ask=m.no_ask,
                            close_time=m.close_time.isoformat()
                            if m.close_time
                            else None,
                            game_status=m.game_status,
                            extra={"huntable": edge > 0},
                        )
                    )

    # Sort by edge
    results.sort(key=lambda x: x.edge_cents, reverse=True)
    return results


async def execute_on_markets(
    module_id: str,
    params: Dict[str, Any],
    markets: List[str],
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Execute strategy on selected markets."""
    from src.kalshi.client import KalshiClient
    from src.kalshi.execution import OrderExecutor

    results = {"success": [], "failed": [], "dry_run": dry_run}

    if module_id == "spread_hunter":
        from src.kalshi.spread_hunter import SpreadHunter

        client = KalshiClient.from_env(demo=params.get("demo", True))
        async with client:
            hunter = SpreadHunter(
                client,
                slippage=params.get("slippage", 2.0) / 100.0,
                max_wait_seconds=params.get("timeout", 60.0),
                paper_mode=dry_run,
            )

            hunt_results = await hunter.hunt_multiple(
                markets,
                size=params.get("size", 10),
            )

            for r in hunt_results:
                if r.success:
                    results["success"].append(
                        {
                            "ticker": r.ticker,
                            "net_profit": r.net_profit,
                            "maker_price": r.maker_price,
                            "taker_price": r.taker_price,
                        }
                    )
                else:
                    results["failed"].append(
                        {
                            "ticker": r.ticker,
                            "error": r.error,
                        }
                    )

    elif module_id == "spread_executor":
        client = KalshiClient.from_env(demo=params.get("demo", True))
        async with client:
            executor = OrderExecutor(client, paper_mode=dry_run)

            for ticker in markets:
                # Get current prices
                market = await client.get_market(ticker)
                yes_ask = market.get("yes_ask", 0) / 100.0
                yes_bid = market.get("yes_bid", 0) / 100.0
                no_ask = 1.0 - yes_bid
                no_bid = 1.0 - yes_ask

                result = await executor.execute_spread(
                    ticker=ticker,
                    yes_price=yes_ask,
                    no_price=no_ask,
                    size=params.get("size", 50),
                    yes_spread=yes_ask - yes_bid,
                    no_spread=no_ask - no_bid,
                )

                if result.is_complete:
                    results["success"].append(
                        {
                            "ticker": ticker,
                            "yes_filled": result.yes_result.filled,
                            "no_filled": result.no_result.filled,
                        }
                    )
                else:
                    results["failed"].append(
                        {
                            "ticker": ticker,
                            "aborted": result.aborted,
                        }
                    )

    return results


# =============================================================================
# FastAPI Application
# =============================================================================

app = FastAPI(title="Trading Command Center", version="1.0.0")


# Pydantic models for API
class StartSessionRequest(BaseModel):
    module_id: str
    parameters: Dict[str, Any] = {}
    mode: str = "dry"


class SelectMarketsRequest(BaseModel):
    session_id: str
    selected_tickers: List[str]


class ExecuteRequest(BaseModel):
    session_id: str


# Serve static files
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.on_event("startup")
async def startup():
    """Initialize module registry on startup."""
    discover_modules()
    logger.info(f"Registered {len(_REGISTRY)} modules")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the command center HTML."""
    return HTMLResponse(content=COMMAND_CENTER_HTML)


@app.get("/api/modules")
async def list_modules():
    """List all available modules."""
    modules = []
    for m in get_all_modules():
        modules.append(
            {
                "id": m.id,
                "name": m.name,
                "category": m.category.value,
                "description": m.description,
                "parameters": [asdict(p) for p in m.parameters],
                "supports_dry_run": m.supports_dry_run,
                "supports_market_selection": m.supports_market_selection,
            }
        )
    return {"modules": modules}


@app.get("/api/modules/{module_id}")
async def get_module_details(module_id: str):
    """Get details for a specific module."""
    module = get_module(module_id)
    if not module:
        raise HTTPException(404, f"Module not found: {module_id}")

    return {
        "id": module.id,
        "name": module.name,
        "category": module.category.value,
        "description": module.description,
        "parameters": [asdict(p) for p in module.parameters],
        "supports_dry_run": module.supports_dry_run,
        "supports_market_selection": module.supports_market_selection,
    }


@app.post("/api/sessions/start")
async def start_session(req: StartSessionRequest):
    """Start a new execution session."""
    import uuid

    module = get_module(req.module_id)
    if not module:
        raise HTTPException(404, f"Module not found: {req.module_id}")

    session_id = f"sess_{uuid.uuid4().hex[:8]}"
    session = ExecutionSession(
        session_id=session_id,
        module_id=req.module_id,
        mode=RunMode(req.mode),
        parameters=req.parameters,
    )

    # Add steps based on module capabilities
    step_num = 1

    if module.supports_market_selection:
        session.steps.append(ExecutionStep(step_num, "Scan markets"))
        step_num += 1
        session.steps.append(ExecutionStep(step_num, "Select markets"))
        step_num += 1

    session.steps.append(ExecutionStep(step_num, f"Execute ({req.mode} run)"))

    _SESSIONS[session_id] = session

    return {
        "session_id": session_id,
        "steps": [asdict(s) for s in session.steps],
        "status": session.status,
    }


@app.post("/api/sessions/{session_id}/scan")
async def run_session_scan(session_id: str):
    """Run the scan step for a session."""
    session = _SESSIONS.get(session_id)
    if not session:
        raise HTTPException(404, f"Session not found: {session_id}")

    session.status = "scanning"

    # Update step status
    for step in session.steps:
        if "Scan" in step.description:
            step.status = "running"
            step.timestamp = datetime.now()

    try:
        results = await run_scanner(
            session.module_id,
            session.parameters,
            dry_run=session.mode == RunMode.DRY,
        )

        session.scan_results = results

        for step in session.steps:
            if "Scan" in step.description:
                step.status = "completed"
                step.result = f"Found {len(results)} markets"

        session.status = "selecting"

        return {
            "status": "completed",
            "results": [asdict(r) for r in results],
        }

    except Exception as e:
        import traceback

        logger.error(f"Scan failed: {e}\n{traceback.format_exc()}")
        for step in session.steps:
            if "Scan" in step.description:
                step.status = "failed"
                step.error = str(e)
        session.status = "failed"
        raise HTTPException(500, str(e))


@app.post("/api/sessions/{session_id}/select")
async def select_markets(session_id: str, req: SelectMarketsRequest):
    """Select markets for execution."""
    session = _SESSIONS.get(session_id)
    if not session:
        raise HTTPException(404, f"Session not found: {session_id}")

    session.selected_markets = req.selected_tickers

    for step in session.steps:
        if "Select" in step.description:
            step.status = "completed"
            step.result = f"Selected {len(req.selected_tickers)} markets"
            step.timestamp = datetime.now()

    return {
        "status": "selected",
        "selected_count": len(req.selected_tickers),
    }


@app.post("/api/sessions/{session_id}/execute")
async def execute_session(session_id: str):
    """Execute the strategy on selected markets."""
    session = _SESSIONS.get(session_id)
    if not session:
        raise HTTPException(404, f"Session not found: {session_id}")

    session.status = "executing"

    for step in session.steps:
        if "Execute" in step.description:
            step.status = "running"
            step.timestamp = datetime.now()

    try:
        # Determine markets to execute on
        markets = session.selected_markets
        if not markets and session.parameters.get("ticker"):
            markets = [session.parameters["ticker"]]

        if not markets:
            raise HTTPException(400, "No markets selected")

        results = await execute_on_markets(
            session.module_id,
            session.parameters,
            markets,
            dry_run=session.mode == RunMode.DRY,
        )

        session.final_result = results

        for step in session.steps:
            if "Execute" in step.description:
                step.status = "completed"
                step.result = f"Success: {len(results['success'])}, Failed: {len(results['failed'])}"

        session.status = "completed"
        session.completed_at = datetime.now()

        return {
            "status": "completed",
            "results": results,
        }

    except Exception as e:
        for step in session.steps:
            if "Execute" in step.description:
                step.status = "failed"
                step.error = str(e)
        session.status = "failed"
        raise HTTPException(500, str(e))


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """Get session status."""
    session = _SESSIONS.get(session_id)
    if not session:
        raise HTTPException(404, f"Session not found: {session_id}")

    return {
        "session_id": session.session_id,
        "module_id": session.module_id,
        "mode": session.mode.value,
        "status": session.status,
        "parameters": session.parameters,
        "steps": [asdict(s) for s in session.steps],
        "scan_results": [asdict(r) for r in session.scan_results],
        "selected_markets": session.selected_markets,
        "final_result": session.final_result,
        "started_at": session.started_at.isoformat(),
        "completed_at": session.completed_at.isoformat()
        if session.completed_at
        else None,
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """WebSocket for real-time updates."""
    await ws.accept()
    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "subscribe_session":
                session_id = msg.get("session_id")
                while True:
                    session = _SESSIONS.get(session_id)
                    if session:
                        await ws.send_json(
                            {
                                "type": "session_update",
                                "session": {
                                    "status": session.status,
                                    "steps": [asdict(s) for s in session.steps],
                                },
                            }
                        )
                    await asyncio.sleep(0.5)

    except WebSocketDisconnect:
        pass


# =============================================================================
# HTML Template
# =============================================================================

COMMAND_CENTER_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Trading Command Center</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0a0f;
            color: #e0e0e0;
            height: 100vh;
            overflow: hidden;
        }
        
        .container {
            display: flex;
            height: 100vh;
        }
        
        /* Left Panel - Module List */
        .left-panel {
            width: 33.33%;
            background: #12121a;
            border-right: 1px solid #2a2a3a;
            display: flex;
            flex-direction: column;
        }
        
        .panel-header {
            padding: 16px 20px;
            background: #1a1a25;
            border-bottom: 1px solid #2a2a3a;
        }
        
        .panel-header h2 {
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #888;
        }
        
        .module-list {
            flex: 1;
            overflow-y: auto;
            padding: 8px;
        }
        
        .category-section {
            margin-bottom: 16px;
        }
        
        .category-label {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #666;
            padding: 8px 12px;
        }
        
        .module-item {
            padding: 12px 16px;
            border-radius: 8px;
            cursor: pointer;
            margin: 4px 0;
            transition: all 0.15s;
            border: 1px solid transparent;
        }
        
        .module-item:hover {
            background: #1a1a25;
            border-color: #3a3a4a;
        }
        
        .module-item.selected {
            background: #1e3a5f;
            border-color: #2d5a8f;
        }
        
        .module-item.checked {
            background: #1a2a1a;
            border-color: #2d5a2d;
        }
        
        .module-row {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .module-checkbox {
            width: 16px;
            height: 16px;
            cursor: pointer;
            flex-shrink: 0;
        }
        
        .module-info {
            flex: 1;
            cursor: pointer;
        }
        
        .module-name {
            font-weight: 500;
            margin-bottom: 4px;
        }
        
        .selected-modules-bar {
            padding: 12px 16px;
            background: #1a2a1a;
            border-bottom: 1px solid #2d5a2d;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        
        .selected-count {
            color: #4ade80;
            font-weight: 500;
        }
        
        .module-category {
            font-size: 11px;
            color: #666;
            text-transform: uppercase;
        }
        
        /* Right Panel - Details & Execution */
        .right-panel {
            width: 66.67%;
            display: flex;
            flex-direction: column;
            background: #0f0f15;
        }
        
        .detail-header {
            padding: 20px 24px;
            background: #1a1a25;
            border-bottom: 1px solid #2a2a3a;
        }
        
        .detail-header h1 {
            font-size: 20px;
            margin-bottom: 8px;
        }
        
        .detail-header p {
            color: #888;
            line-height: 1.5;
        }
        
        .detail-content {
            flex: 1;
            overflow-y: auto;
            padding: 24px;
        }
        
        /* Parameters Form */
        .params-section {
            margin-bottom: 32px;
        }
        
        .section-title {
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #666;
            margin-bottom: 16px;
            padding-bottom: 8px;
            border-bottom: 1px solid #2a2a3a;
        }
        
        .param-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 16px;
        }
        
        .param-item {
            display: flex;
            flex-direction: column;
        }
        
        .param-item label {
            font-size: 12px;
            color: #888;
            margin-bottom: 6px;
        }
        
        .param-item input, .param-item select {
            padding: 10px 12px;
            background: #1a1a25;
            border: 1px solid #2a2a3a;
            border-radius: 6px;
            color: #e0e0e0;
            font-size: 14px;
        }
        
        .param-item input:focus, .param-item select:focus {
            outline: none;
            border-color: #4a9eff;
        }
        
        .param-item.checkbox {
            flex-direction: column;
            align-items: flex-start;
            gap: 4px;
        }
        
        .param-item.checkbox .param-header {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .param-item.checkbox input {
            width: 18px;
            height: 18px;
        }
        
        .param-desc {
            font-size: 11px;
            color: #666;
            margin-top: 2px;
            margin-bottom: 6px;
            line-height: 1.4;
        }
        
        /* Multiselect Group */
        .multiselect-group {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            padding: 8px;
            background: #1a1a25;
            border: 1px solid #2a2a3a;
            border-radius: 6px;
        }
        
        .multiselect-option {
            display: flex;
            align-items: center;
            gap: 6px;
            padding: 6px 12px;
            background: #252530;
            border: 1px solid #3a3a4a;
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.15s;
        }
        
        .multiselect-option:hover {
            background: #2a2a3a;
            border-color: #4a4a5a;
        }
        
        .multiselect-option:has(input:checked) {
            background: #1e3a5f;
            border-color: #4a9eff;
        }
        
        .multiselect-option input {
            width: 14px;
            height: 14px;
            cursor: pointer;
        }
        
        .multiselect-option span {
            font-size: 12px;
            color: #e0e0e0;
        }
        
        /* Action Buttons */
        .action-bar {
            display: flex;
            gap: 12px;
            margin-top: 24px;
        }
        
        .btn {
            padding: 12px 24px;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.15s;
        }
        
        .btn-primary {
            background: #2d5a8f;
            color: white;
        }
        
        .btn-primary:hover {
            background: #3d6a9f;
        }
        
        .btn-danger {
            background: #8f2d2d;
            color: white;
        }
        
        .btn-danger:hover {
            background: #9f3d3d;
        }
        
        .btn-secondary {
            background: #2a2a3a;
            color: #e0e0e0;
        }
        
        .btn-secondary:hover {
            background: #3a3a4a;
        }
        
        .btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
        }
        
        /* Workflow Steps */
        .workflow-section {
            margin-top: 32px;
        }
        
        .step-list {
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        
        .step-item {
            display: flex;
            align-items: center;
            padding: 12px 16px;
            background: #1a1a25;
            border-radius: 8px;
            border: 1px solid #2a2a3a;
        }
        
        .step-number {
            width: 28px;
            height: 28px;
            border-radius: 50%;
            background: #2a2a3a;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            font-weight: 600;
            margin-right: 12px;
        }
        
        .step-item.completed .step-number {
            background: #2d8f5a;
        }
        
        .step-item.running .step-number {
            background: #4a9eff;
            animation: pulse 1s infinite;
        }
        
        .step-item.failed .step-number {
            background: #8f2d2d;
        }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        
        .step-info {
            flex: 1;
        }
        
        .step-description {
            font-weight: 500;
        }
        
        .step-result {
            font-size: 12px;
            color: #888;
            margin-top: 2px;
        }
        
        /* Market Selection Table */
        .market-table-container {
            max-height: 400px;
            overflow-y: auto;
            margin-top: 16px;
            border: 1px solid #2a2a3a;
            border-radius: 8px;
        }
        
        .market-table {
            width: 100%;
            border-collapse: collapse;
        }
        
        .market-table th, .market-table td {
            padding: 10px 12px;
            text-align: left;
            border-bottom: 1px solid #2a2a3a;
        }
        
        .market-table th {
            font-size: 11px;
            text-transform: uppercase;
            color: #666;
            background: #1a1a25;
            position: sticky;
            top: 0;
            z-index: 1;
            cursor: pointer;
            user-select: none;
        }
        
        .market-table th:hover {
            color: #e0e0e0;
            background: #252530;
        }
        
        .market-table th.sortable {
            padding-right: 20px;
            position: relative;
        }
        
        .market-table th .sort-arrow {
            position: absolute;
            right: 6px;
            top: 50%;
            transform: translateY(-50%);
            opacity: 0.3;
            font-size: 10px;
        }
        
        .market-table th.sorted .sort-arrow {
            opacity: 1;
            color: #4a9eff;
        }
        
        .market-table tr:hover {
            background: #1a1a25;
        }
        
        .market-table input[type="checkbox"] {
            width: 16px;
            height: 16px;
        }
        
        .edge-positive { color: #4ade80; }
        .edge-negative { color: #f87171; }
        
        /* Empty State */
        .empty-state {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 100%;
            color: #666;
        }
        
        .empty-state h3 {
            margin-bottom: 8px;
        }
        
        /* Results Section */
        .results-section {
            margin-top: 24px;
            padding: 16px;
            background: #1a1a25;
            border-radius: 8px;
        }
        
        .result-item {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #2a2a3a;
        }
        
        .result-item:last-child {
            border-bottom: none;
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Left Panel: Module List -->
        <div class="left-panel">
            <div class="panel-header">
                <h2>🎛️ Command Center</h2>
            </div>
            <div class="module-list" id="moduleList">
                <!-- Populated by JS -->
            </div>
        </div>
        
        <!-- Right Panel: Details & Execution -->
        <div class="right-panel">
            <div id="detailView">
                <div class="empty-state">
                    <h3>Select a module</h3>
                    <p>Choose a scanner, strategy, or executor from the left panel</p>
                </div>
            </div>
        </div>
    </div>

    <script>
        // State
        let modules = [];
        let selectedModule = null;
        let selectedModules = new Set();  // For multiselect
        let currentSession = null;
        let scanResults = [];
        let sortColumn = 'edge_cents';  // Default sort column
        let sortDirection = 'desc';     // 'asc' or 'desc'
        
        // API helpers
        async function api(path, options = {}) {
            const res = await fetch(`/api${path}`, {
                headers: { 'Content-Type': 'application/json' },
                ...options,
                body: options.body ? JSON.stringify(options.body) : undefined,
            });
            if (!res.ok) throw new Error(await res.text());
            return res.json();
        }
        
        // Load modules
        async function loadModules() {
            const data = await api('/modules');
            modules = data.modules;
            renderModuleList();
        }
        
        // Render module list grouped by category
        function renderModuleList() {
            const list = document.getElementById('moduleList');
            const categories = {};
            
            modules.forEach(m => {
                if (!categories[m.category]) categories[m.category] = [];
                categories[m.category].push(m);
            });
            
            const categoryLabels = {
                scanner: '🔍 Scanners',
                strategy: '📈 Strategies',
                executor: '⚡ Executors',
                utility: '🔧 Utilities',
            };
            
            list.innerHTML = Object.entries(categories).map(([cat, mods]) => `
                <div class="category-section">
                    <div class="category-label">${categoryLabels[cat] || cat}</div>
                    ${mods.map(m => `
                        <div class="module-item ${selectedModule?.id === m.id ? 'selected' : ''} ${selectedModules.has(m.id) ? 'checked' : ''}">
                            <div class="module-row">
                                <input type="checkbox" class="module-checkbox" 
                                       ${selectedModules.has(m.id) ? 'checked' : ''}
                                       onclick="event.stopPropagation(); toggleModuleSelect('${m.id}')">
                                <div class="module-info" onclick="selectModule('${m.id}')">
                                    <div class="module-name">${m.name}</div>
                                </div>
                            </div>
                        </div>
                    `).join('')}
                </div>
            `).join('');
            
            // Show selected count if any
            updateSelectedBar();
        }
        
        // Toggle module selection (checkbox)
        function toggleModuleSelect(moduleId) {
            if (selectedModules.has(moduleId)) {
                selectedModules.delete(moduleId);
            } else {
                selectedModules.add(moduleId);
            }
            renderModuleList();
        }
        
        // Update selected modules bar
        function updateSelectedBar() {
            let bar = document.getElementById('selectedBar');
            if (selectedModules.size > 0) {
                if (!bar) {
                    bar = document.createElement('div');
                    bar.id = 'selectedBar';
                    bar.className = 'selected-modules-bar';
                    document.querySelector('.left-panel').insertBefore(bar, document.getElementById('moduleList'));
                }
                const names = Array.from(selectedModules).map(id => modules.find(m => m.id === id)?.name || id);
                bar.innerHTML = `
                    <span class="selected-count">✓ ${selectedModules.size} selected</span>
                    <button class="btn btn-secondary" style="padding: 4px 12px; font-size: 12px;" onclick="runSelectedModules()">
                        Run All
                    </button>
                `;
            } else if (bar) {
                bar.remove();
            }
        }
        
        // Run all selected modules
        async function runSelectedModules() {
            if (selectedModules.size === 0) {
                alert('No modules selected');
                return;
            }
            
            const results = [];
            for (const moduleId of selectedModules) {
                const mod = modules.find(m => m.id === moduleId);
                if (mod) {
                    selectedModule = mod;
                    renderModuleList();
                    renderDetailView();
                    
                    // Start dry run for each
                    try {
                        await startSession('dry');
                        results.push({ module: mod.name, status: 'started' });
                    } catch (e) {
                        results.push({ module: mod.name, status: 'error', error: e.message });
                    }
                }
            }
            console.log('Batch run results:', results);
        }
        
        // Select a module (click on name)
        function selectModule(moduleId) {
            selectedModule = modules.find(m => m.id === moduleId);
            currentSession = null;
            scanResults = [];
            renderModuleList();
            renderDetailView();
        }
        
        // Render detail view
        function renderDetailView() {
            const view = document.getElementById('detailView');
            
            if (!selectedModule) {
                view.innerHTML = `
                    <div class="empty-state">
                        <h3>Select a module</h3>
                        <p>Choose a scanner, strategy, or executor from the left panel</p>
                    </div>
                `;
                return;
            }
            
            view.innerHTML = `
                <div class="detail-header">
                    <h1>${selectedModule.name}</h1>
                    <p>${selectedModule.description}</p>
                </div>
                <div class="detail-content">
                    <div class="params-section">
                        <div class="section-title">Parameters</div>
                        <div class="param-grid">
                            ${selectedModule.parameters.map(p => renderParam(p)).join('')}
                        </div>
                    </div>
                    
                    <div class="action-bar">
                        <button class="btn btn-secondary" onclick="startSession('dry')">
                            🔍 Dry Run
                        </button>
                        <button class="btn btn-danger" onclick="startSession('wet')">
                            ⚡ Live Run
                        </button>
                    </div>
                    
                    <div id="workflowSection"></div>
                    <div id="marketSection"></div>
                    <div id="resultsSection"></div>
                </div>
            `;
        }
        
        // Render a parameter input
        function renderParam(param) {
            const id = `param_${param.name}`;
            const descHtml = param.description ? `<div class="param-desc">${param.description}</div>` : '';
            
            if (param.type === 'bool') {
                return `
                    <div class="param-item checkbox">
                        <div class="param-header">
                            <input type="checkbox" id="${id}" ${param.default ? 'checked' : ''}>
                            <label for="${id}">${param.name}</label>
                        </div>
                        ${descHtml}
                    </div>
                `;
            }
            
            if (param.type === 'multiselect' && param.choices.length) {
                const defaults = Array.isArray(param.default) ? param.default : [];
                return `
                    <div class="param-item" style="grid-column: span 2;">
                        <label>${param.name}</label>
                        ${descHtml}
                        <div class="multiselect-group" id="${id}">
                            ${param.choices.map(c => `
                                <label class="multiselect-option">
                                    <input type="checkbox" value="${c}" ${defaults.includes(c) ? 'checked' : ''}>
                                    <span>${c}</span>
                                </label>
                            `).join('')}
                        </div>
                    </div>
                `;
            }
            
            if (param.type === 'choice' && param.choices.length) {
                return `
                    <div class="param-item">
                        <label for="${id}">${param.name}</label>
                        ${descHtml}
                        <select id="${id}">
                            ${param.choices.map(c => `<option value="${c}" ${c === param.default ? 'selected' : ''}>${c}</option>`).join('')}
                        </select>
                    </div>
                `;
            }
            
            const inputType = param.type === 'int' || param.type === 'float' ? 'number' : 'text';
            const step = param.type === 'float' ? '0.1' : '1';
            
            return `
                <div class="param-item">
                    <label for="${id}">${param.name}${param.required ? ' *' : ''}</label>
                    ${descHtml}
                    <input type="${inputType}" id="${id}" value="${param.default ?? ''}"
                           step="${step}">
                </div>
            `;
        }
        
        // Collect parameters from form
        function collectParams() {
            const params = {};
            selectedModule.parameters.forEach(p => {
                const el = document.getElementById(`param_${p.name}`);
                if (!el) return;
                
                if (p.type === 'bool') {
                    params[p.name] = el.checked;
                } else if (p.type === 'multiselect') {
                    // Collect all checked values from multiselect group
                    const checkboxes = el.querySelectorAll('input[type="checkbox"]:checked');
                    params[p.name] = Array.from(checkboxes).map(cb => cb.value);
                } else if (p.type === 'int') {
                    params[p.name] = parseInt(el.value) || p.default;
                } else if (p.type === 'float') {
                    params[p.name] = parseFloat(el.value) || p.default;
                } else {
                    params[p.name] = el.value || p.default;
                }
            });
            return params;
        }
        
        // Start a session
        async function startSession(mode) {
            const params = collectParams();
            
            try {
                const data = await api('/sessions/start', {
                    method: 'POST',
                    body: { module_id: selectedModule.id, parameters: params, mode },
                });
                
                currentSession = data;
                renderWorkflow();
                
                // Auto-run scan if supported
                if (selectedModule.supports_market_selection) {
                    await runScan();
                } else {
                    await runExecute();
                }
            } catch (e) {
                alert('Failed to start session: ' + e.message);
            }
        }
        
        // Render workflow steps
        function renderWorkflow() {
            const section = document.getElementById('workflowSection');
            
            section.innerHTML = `
                <div class="workflow-section">
                    <div class="section-title">Workflow</div>
                    <div class="step-list">
                        ${currentSession.steps.map(s => `
                            <div class="step-item ${s.status}">
                                <div class="step-number">${s.step_number}</div>
                                <div class="step-info">
                                    <div class="step-description">${s.description}</div>
                                    ${s.result ? `<div class="step-result">${s.result}</div>` : ''}
                                    ${s.error ? `<div class="step-result" style="color: #f87171;">${s.error}</div>` : ''}
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
        }
        
        // Run scan
        async function runScan() {
            try {
                const data = await api(`/sessions/${currentSession.session_id}/scan`, { method: 'POST' });
                scanResults = data.results;
                
                // Refresh session
                currentSession = await api(`/sessions/${currentSession.session_id}`);
                renderWorkflow();
                renderMarketSelection();
            } catch (e) {
                alert('Scan failed: ' + e.message);
            }
        }
        
        // Refresh scan (re-run scan to get updated market data)
        async function refreshScan() {
            const btn = event.target;
            const originalText = btn.innerHTML;
            btn.innerHTML = '⏳ Scanning...';
            btn.disabled = true;
            
            try {
                await runScan();
            } finally {
                btn.innerHTML = originalText;
                btn.disabled = false;
            }
        }
        
        // Render market selection
        function renderMarketSelection() {
            const section = document.getElementById('marketSection');
            
            if (!scanResults.length) {
                section.innerHTML = '<p style="color: #666; padding: 16px;">No markets found</p>';
                return;
            }
            
            // Sort results
            const sorted = [...scanResults].sort((a, b) => {
                let aVal, bVal;
                
                // Handle computed columns
                if (sortColumn === 'yn_bid') {
                    aVal = (a.yes_bid || 0) + (a.no_bid || 0);
                    bVal = (b.yes_bid || 0) + (b.no_bid || 0);
                } else {
                    aVal = a[sortColumn];
                    bVal = b[sortColumn];
                }
                
                // Handle string vs number
                if (typeof aVal === 'string') {
                    aVal = aVal.toLowerCase();
                    bVal = (bVal || '').toLowerCase();
                    return sortDirection === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
                }
                
                return sortDirection === 'asc' ? aVal - bVal : bVal - aVal;
            });
            
            const arrow = (col) => {
                const isActive = sortColumn === col;
                const icon = sortDirection === 'asc' ? '▲' : '▼';
                return `<span class="sort-arrow">${isActive ? icon : '⇅'}</span>`;
            };
            
            // Format game status with styling
            const formatStatus = (status) => {
                if (!status) return '<span style="color: #9ca3af;">-</span>';
                if (status === 'Final') return '<span style="color: #9ca3af;">Final</span>';
                if (status.startsWith('Pre:')) return `<span style="color: #60a5fa;">${status}</span>`;
                // Live game - highlight quarter
                const match = status.match(/^(Q[1-4]|OT)/);
                if (match) {
                    return `<span style="color: #4ade80; font-weight: 600;">${status}</span>`;
                }
                return status;
            };
            
            section.innerHTML = `
                <div class="params-section" style="margin-top: 24px;">
                    <div class="section-title">
                        Select Markets (${scanResults.length} found)
                        <div style="float: right; display: flex; gap: 8px;">
                            <button class="btn btn-secondary" style="padding: 4px 12px; font-size: 12px;" onclick="refreshScan()" title="Refresh market data">🔄 Refresh</button>
                            <button class="btn btn-secondary" style="padding: 4px 12px; font-size: 12px;" onclick="selectAllMarkets()">Select All</button>
                        </div>
                    </div>
                    <div class="market-table-container">
                    <table class="market-table">
                        <thead>
                            <tr>
                                <th></th>
                                <th class="sortable ${sortColumn === 'ticker' ? 'sorted' : ''}" onclick="sortBy('ticker')">Ticker ${arrow('ticker')}</th>
                                <th class="sortable ${sortColumn === 'game_status' ? 'sorted' : ''}" onclick="sortBy('game_status')">Game ${arrow('game_status')}</th>
                                <th class="sortable ${sortColumn === 'yes_bid' ? 'sorted' : ''}" onclick="sortBy('yes_bid')">Y Bid ${arrow('yes_bid')}</th>
                                <th class="sortable ${sortColumn === 'yes_ask' ? 'sorted' : ''}" onclick="sortBy('yes_ask')">Y Ask ${arrow('yes_ask')}</th>
                                <th class="sortable ${sortColumn === 'no_bid' ? 'sorted' : ''}" onclick="sortBy('no_bid')">N Bid ${arrow('no_bid')}</th>
                                <th class="sortable ${sortColumn === 'no_ask' ? 'sorted' : ''}" onclick="sortBy('no_ask')">N Ask ${arrow('no_ask')}</th>
                                <th class="sortable ${sortColumn === 'yn_bid' ? 'sorted' : ''}" onclick="sortBy('yn_bid')">Y+N Bid ${arrow('yn_bid')}</th>
                                <th class="sortable ${sortColumn === 'edge_cents' ? 'sorted' : ''}" onclick="sortBy('edge_cents')">Edge ${arrow('edge_cents')}</th>
                                <th class="sortable ${sortColumn === 'spread_cents' ? 'sorted' : ''}" onclick="sortBy('spread_cents')">Spread ${arrow('spread_cents')}</th>
                                <th>Strong</th>
                                <th class="sortable ${sortColumn === 'close_time' ? 'sorted' : ''}" onclick="sortBy('close_time')">Time Left ${arrow('close_time')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${sorted.slice(0, 100).map((r, i) => {
                                // Determine strong side: YES if yes_spread < no_spread, else NO
                                const yesSpread = (r.yes_ask || 0) - (r.yes_bid || 0);
                                const noSpread = ((1 - (r.yes_bid || 0)) - (1 - (r.yes_ask || 0)));
                                const strongSide = yesSpread <= noSpread ? 'YES' : 'NO';
                                const strongClass = strongSide === 'YES' ? 'edge-positive' : 'edge-negative';
                                return `
                                <tr>
                                    <td><input type="checkbox" id="market_${i}" value="${r.ticker}"></td>
                                    <td><code style="font-size: 11px;">${r.ticker}</code></td>
                                    <td>${formatStatus(r.game_status)}</td>
                                    <td>${r.yes_bid ? (r.yes_bid * 100).toFixed(0) + '¢' : '-'}</td>
                                    <td>${r.yes_ask ? (r.yes_ask * 100).toFixed(0) + '¢' : '-'}</td>
                                    <td>${r.no_bid ? (r.no_bid * 100).toFixed(0) + '¢' : '-'}</td>
                                    <td>${r.no_ask ? (r.no_ask * 100).toFixed(0) + '¢' : '-'}</td>
                                    <td class="${((r.yes_bid || 0) + (r.no_bid || 0)) >= 1 ? 'edge-positive' : 'edge-negative'}">${(((r.yes_bid || 0) + (r.no_bid || 0)) * 100).toFixed(0)}¢</td>
                                    <td class="${r.edge_cents > 0 ? 'edge-positive' : 'edge-negative'}">${r.edge_cents.toFixed(1)}¢</td>
                                    <td>${r.spread_cents.toFixed(1)}¢</td>
                                    <td><span class="${strongClass}" style="font-weight: 600;">${strongSide}</span></td>
                                    <td class="time-left" data-close="${r.close_time || ''}">${formatTimeLeft(r.close_time)}</td>
                                </tr>
                            `}).join('')}
                        </tbody>
                    </table>
                    </div>
                    <div class="action-bar" style="margin-top: 16px;">
                        <button class="btn btn-primary" onclick="confirmSelection()">
                            Proceed with Selected →
                        </button>
                    </div>
                </div>
            `;
            
            // Start countdown timer
            startCountdownTimer();
        }
        
        // Format time left as countdown
        function formatTimeLeft(closeTimeStr) {
            if (!closeTimeStr) return '-';
            
            const closeTime = new Date(closeTimeStr);
            const now = new Date();
            const diffMs = closeTime - now;
            
            if (diffMs <= 0) return '<span style="color: #f87171;">Closed</span>';
            
            const diffSec = Math.floor(diffMs / 1000);
            const hours = Math.floor(diffSec / 3600);
            const mins = Math.floor((diffSec % 3600) / 60);
            const secs = diffSec % 60;
            
            if (hours > 24) {
                const days = Math.floor(hours / 24);
                return `${days}d ${hours % 24}h`;
            } else if (hours > 0) {
                return `${hours}h ${mins}m`;
            } else if (mins > 0) {
                return `<span style="color: #fbbf24;">${mins}m ${secs}s</span>`;
            } else {
                return `<span style="color: #f87171;">${secs}s</span>`;
            }
        }
        
        // Countdown timer - updates every second
        let countdownInterval = null;
        function startCountdownTimer() {
            if (countdownInterval) clearInterval(countdownInterval);
            
            countdownInterval = setInterval(() => {
                document.querySelectorAll('.time-left').forEach(el => {
                    const closeTime = el.dataset.close;
                    if (closeTime) {
                        el.innerHTML = formatTimeLeft(closeTime);
                    }
                });
            }, 1000);
        }
        
        // Sort by column
        function sortBy(column) {
            if (sortColumn === column) {
                // Toggle direction
                sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
            } else {
                sortColumn = column;
                // Default direction based on column type
                sortDirection = (column === 'ticker' || column === 'title') ? 'asc' : 'desc';
            }
            renderMarketSelection();
        }
        
        // Select all markets
        function selectAllMarkets() {
            document.querySelectorAll('.market-table input[type="checkbox"]').forEach(cb => cb.checked = true);
        }
        
        // Confirm market selection
        async function confirmSelection() {
            const selected = [];
            document.querySelectorAll('.market-table input[type="checkbox"]:checked').forEach(cb => {
                selected.push(cb.value);
            });
            
            if (!selected.length) {
                alert('Please select at least one market');
                return;
            }
            
            try {
                await api(`/sessions/${currentSession.session_id}/select`, {
                    method: 'POST',
                    body: { session_id: currentSession.session_id, selected_tickers: selected },
                });
                
                currentSession = await api(`/sessions/${currentSession.session_id}`);
                renderWorkflow();
                
                await runExecute();
            } catch (e) {
                alert('Selection failed: ' + e.message);
            }
        }
        
        // Run execution
        async function runExecute() {
            try {
                const data = await api(`/sessions/${currentSession.session_id}/execute`, { method: 'POST' });
                
                currentSession = await api(`/sessions/${currentSession.session_id}`);
                renderWorkflow();
                renderResults(data.results);
            } catch (e) {
                alert('Execution failed: ' + e.message);
            }
        }
        
        // Render results
        function renderResults(results) {
            const section = document.getElementById('resultsSection');
            
            const modeLabel = currentSession.mode === 'dry' ? '(DRY RUN - No real trades)' : '(LIVE)';
            
            section.innerHTML = `
                <div class="results-section">
                    <div class="section-title">Results ${modeLabel}</div>
                    
                    ${results.success.length ? `
                        <h4 style="color: #4ade80; margin-bottom: 8px;">✅ Successful (${results.success.length})</h4>
                        ${results.success.map(r => `
                            <div class="result-item">
                                <span>${r.ticker}</span>
                                <span>${r.net_profit !== undefined ? '$' + r.net_profit.toFixed(4) : 'OK'}</span>
                            </div>
                        `).join('')}
                    ` : ''}
                    
                    ${results.failed.length ? `
                        <h4 style="color: #f87171; margin: 16px 0 8px;">❌ Failed (${results.failed.length})</h4>
                        ${results.failed.map(r => `
                            <div class="result-item">
                                <span>${r.ticker}</span>
                                <span style="color: #888;">${r.error || 'Aborted'}</span>
                            </div>
                        `).join('')}
                    ` : ''}
                </div>
            `;
        }
        
        // Init
        loadModules();
    </script>
</body>
</html>
"""


# =============================================================================
# Main
# =============================================================================


def main():
    """Run the command center server."""
    import argparse

    parser = argparse.ArgumentParser(description="Trading Command Center")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8050, help="Port to bind to")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")

    args = parser.parse_args()

    print("\n🎛️  Trading Command Center")
    print(f"   Open http://{args.host}:{args.port} in your browser\n")

    uvicorn.run(
        "dashboard.command_center:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()

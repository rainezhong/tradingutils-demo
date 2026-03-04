"""
Hybrid Data Scout Agent - Pattern Detection with LLM Reasoning

This agent combines statistical pattern detection with LLM-powered reasoning to:
1. Discover which data sources to analyze (LLM reasoning)
2. Execute statistical analysis (pure Python code)
3. Generate hypotheses about trading edges

Uses file-based interface to communicate with Claude Code for LLM reasoning,
avoiding direct API calls and using your Claude subscription instead.
"""

import json
import sqlite3
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime
from dataclasses import dataclass, field, asdict

# Import the original DataScoutAgent for statistical methods
from agents.data_scout import DataScoutAgent, Hypothesis


@dataclass
class DataSourceInfo:
    """Information about an available data source."""
    path: str
    name: str
    description: str
    tables: List[str]
    row_count: int
    date_range: Optional[Dict[str, str]] = None
    sample_data: Optional[Dict[str, Any]] = None


@dataclass
class AnalysisPlan:
    """LLM-generated plan for data analysis."""
    name: str
    data_sources: List[str]
    comparison_type: str
    edge_definition: str
    reasoning: str
    expected_pattern: str
    statistical_test: str
    join_conditions: Optional[Dict[str, Any]] = None


class HybridDataScout:
    """
    Hybrid Data Scout with LLM reasoning capabilities.

    Architecture:
    - Phase 1 (LLM): Reason about which data sources to analyze and how
    - Phase 2 (Code): Execute statistical analysis on the plan
    - Phase 3 (Code): Generate hypotheses from findings

    Uses file-based interface to request LLM reasoning from Claude Code:
    - Writes requests to tmp/llm_requests/
    - Reads responses from tmp/llm_responses/
    - Caches analysis plans to tmp/llm_cache/
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.request_dir = Path("tmp/llm_requests")
        self.response_dir = Path("tmp/llm_responses")
        self.cache_dir = Path("tmp/llm_cache")

        # Create directories
        for d in [self.request_dir, self.response_dir, self.cache_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self.data_scout = None  # Will be initialized per data source

    def discover_data_sources(self) -> List[DataSourceInfo]:
        """
        Discover all available SQLite databases in data directory.

        Returns:
            List of DataSourceInfo describing each database
        """
        data_sources = []

        for db_file in self.data_dir.glob("*.db"):
            try:
                info = self._inspect_database(db_file)
                data_sources.append(info)
            except Exception as e:
                print(f"Warning: Could not inspect {db_file}: {e}")

        return data_sources

    def _inspect_database(self, db_path: Path) -> DataSourceInfo:
        """Inspect a database and return metadata."""
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        # Get total row count
        total_rows = 0
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            total_rows += cursor.fetchone()[0]

        # Get date range if timestamp column exists
        date_range = None
        for table in tables:
            cursor.execute(f"PRAGMA table_info({table})")
            columns = [row[1] for row in cursor.fetchall()]
            if 'ts' in columns or 'timestamp' in columns:
                ts_col = 'ts' if 'ts' in columns else 'timestamp'
                cursor.execute(f"SELECT MIN({ts_col}), MAX({ts_col}) FROM {table}")
                min_ts, max_ts = cursor.fetchone()
                date_range = {"start": str(min_ts), "end": str(max_ts)}
                break

        # Get sample data from first table
        sample_data = None
        if tables:
            cursor.execute(f"SELECT * FROM {tables[0]} LIMIT 3")
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            sample_data = {
                "table": tables[0],
                "columns": columns,
                "sample_rows": [dict(zip(columns, row)) for row in rows]
            }

        conn.close()

        # Infer description from filename
        name = db_path.stem
        description = self._infer_description(name, tables)

        return DataSourceInfo(
            path=str(db_path),
            name=name,
            description=description,
            tables=tables,
            row_count=total_rows,
            date_range=date_range,
            sample_data=sample_data
        )

    def _infer_description(self, db_name: str, tables: List[str]) -> str:
        """Infer what a database contains based on name and tables."""
        descriptions = {
            "btc_latency_probe": "Bitcoin latency probe data with Kalshi snapshots and Kraken prices",
            "weather_markets": "Kalshi weather/temperature market data",
            "noaa_forecasts": "NOAA weather forecast data with confidence intervals",
            "portfolio_trades": "Trading history and performance tracking",
            "research": "Research hypotheses, backtests, and reports",
        }

        if db_name in descriptions:
            return descriptions[db_name]

        # Generic description
        return f"Database with tables: {', '.join(tables[:3])}"

    def request_analysis_plans(self, data_sources: List[DataSourceInfo]) -> str:
        """
        Request LLM to generate analysis plans for available data sources.

        Writes request to file and returns status signal for orchestrator.

        Args:
            data_sources: List of available data sources

        Returns:
            Status string: "NEEDS_LLM" to signal orchestrator
        """
        # Check cache first
        cache_key = self._generate_cache_key(data_sources)
        cache_file = self.cache_dir / f"analysis_plans_{cache_key}.json"

        if cache_file.exists():
            print(f"✓ Using cached analysis plans from {cache_file}")
            return "CACHE_HIT"

        # Prepare request
        request = {
            "task": "generate_analysis_plans",
            "timestamp": datetime.utcnow().isoformat(),
            "data_sources": [asdict(ds) for ds in data_sources],
            "instructions": """
            Analyze these data sources and suggest comparisons that could reveal trading edges.

            For each potential edge:
            1. Which data sources should be compared?
            2. What metric defines an "edge"?
            3. Why might this edge exist? (information asymmetry, behavioral bias, latency, etc.)
            4. What statistical test should be used?
            5. What pattern would indicate opportunity?

            Focus on:
            - Cross-referencing forecast/prediction data with market prices
            - Latency opportunities (comparing real-time feeds)
            - Mispricing due to information gaps
            - Mean reversion vs momentum patterns

            Return JSON array of analysis plans with structure:
            {
                "name": "Strategy Name",
                "data_sources": ["db1", "db2"],
                "comparison_type": "forecast_vs_market_price",
                "edge_definition": "forecast_confidence > market_implied_probability",
                "reasoning": "Why this edge exists...",
                "expected_pattern": "What we're looking for...",
                "statistical_test": "How to validate...",
                "join_conditions": {"on": ["timestamp", "ticker"]}
            }
            """
        }

        # Write request file
        request_file = self.request_dir / "data_scout_request.json"
        with open(request_file, "w") as f:
            json.dump(request, f, indent=2)

        print(f"📝 LLM reasoning request written to: {request_file}")
        print("   Waiting for Claude Code to process...")

        return "NEEDS_LLM"

    def get_analysis_plans(self) -> Optional[List[AnalysisPlan]]:
        """
        Read analysis plans from LLM response file.

        Returns:
            List of AnalysisPlan objects, or None if not ready
        """
        response_file = self.response_dir / "data_scout_response.json"

        if not response_file.exists():
            return None

        with open(response_file) as f:
            response_data = json.load(f)

        # Parse into AnalysisPlan objects
        plans = []
        for plan_data in response_data.get("analysis_plans", []):
            plan = AnalysisPlan(**plan_data)
            plans.append(plan)

        # Cache the plans
        cache_key = datetime.utcnow().strftime("%Y%m%d")
        cache_file = self.cache_dir / f"analysis_plans_{cache_key}.json"
        with open(cache_file, "w") as f:
            json.dump(response_data, f, indent=2)

        # Remove response file (processed)
        response_file.unlink()

        return plans

    def execute_analysis_plan(self, plan: AnalysisPlan) -> List[Hypothesis]:
        """
        Execute an analysis plan using pure Python statistical methods.

        Args:
            plan: Analysis plan from LLM

        Returns:
            List of hypotheses/findings
        """
        print(f"\n🔬 Executing analysis plan: {plan.name}")
        print(f"   Comparing: {' + '.join(plan.data_sources)}")

        # Route to appropriate analysis method based on comparison type
        if plan.comparison_type == "forecast_vs_market_price":
            return self._analyze_forecast_arbitrage(plan)
        elif plan.comparison_type == "latency_arbitrage":
            return self._analyze_latency_arbitrage(plan)
        elif plan.comparison_type == "spread_analysis":
            return self._analyze_spread_patterns(plan)
        else:
            # Generic statistical analysis
            return self._analyze_generic_pattern(plan)

    def _analyze_forecast_arbitrage(self, plan: AnalysisPlan) -> List[Hypothesis]:
        """Analyze forecast vs market price discrepancies."""
        hypotheses = []

        # This would load both databases, join on specified conditions,
        # and calculate edge (forecast_prob - market_prob)
        # For now, return placeholder

        print(f"   Analysis: {plan.reasoning}")
        print(f"   Edge: {plan.edge_definition}")

        # Placeholder hypothesis
        hypotheses.append(Hypothesis(
            pattern_type="forecast_arbitrage",
            ticker="PLACEHOLDER",
            description=f"{plan.name}: {plan.expected_pattern}",
            confidence=0.85,
            statistical_significance=3.2,
            data_points=100,
            metadata={
                "plan_name": plan.name,
                "reasoning": plan.reasoning,
                "comparison_type": plan.comparison_type
            }
        ))

        return hypotheses

    def _analyze_latency_arbitrage(self, plan: AnalysisPlan) -> List[Hypothesis]:
        """Analyze latency opportunities between data feeds."""
        # Similar to forecast arbitrage but focusing on timing differences
        return []

    def _analyze_spread_patterns(self, plan: AnalysisPlan) -> List[Hypothesis]:
        """Analyze spread anomalies in market data."""
        # Use original DataScoutAgent for this
        if not plan.data_sources:
            return []

        db_path = plan.data_sources[0]
        scout = DataScoutAgent(db_path)

        with scout:
            # Get all tickers
            tickers = scout._get_active_tickers(min_snapshots=50)
            all_hyps = []

            for ticker in tickers[:10]:  # Limit for demo
                hyps = scout.find_spread_anomalies(ticker)
                all_hyps.extend(hyps)

            return all_hyps

    def _analyze_generic_pattern(self, plan: AnalysisPlan) -> List[Hypothesis]:
        """Generic pattern analysis for unknown comparison types."""
        return []

    def scan_for_patterns(self) -> Dict[str, Any]:
        """
        Main entry point for hybrid pattern scanning.

        Returns:
            Dictionary with status and either hypotheses or LLM request signal
        """
        # Step 1: Discover data sources
        print("🔍 Discovering data sources...")
        data_sources = self.discover_data_sources()
        print(f"   Found {len(data_sources)} databases:")
        for ds in data_sources:
            print(f"   - {ds.name}: {ds.description} ({ds.row_count} rows)")

        # Step 2: Check if we need LLM reasoning
        status = self.request_analysis_plans(data_sources)

        if status == "NEEDS_LLM":
            return {
                "status": "NEEDS_LLM",
                "request_file": str(self.request_dir / "data_scout_request.json"),
                "response_file": str(self.response_dir / "data_scout_response.json"),
                "data_sources": [asdict(ds) for ds in data_sources]
            }

        # Step 3: Get analysis plans (either from cache or LLM response)
        plans = self.get_analysis_plans()

        if plans is None:
            return {
                "status": "WAITING_FOR_LLM",
                "message": "Waiting for LLM response file to appear"
            }

        # Step 4: Execute plans
        print(f"\n✓ Received {len(plans)} analysis plans")
        all_hypotheses = []

        for plan in plans:
            try:
                hyps = self.execute_analysis_plan(plan)
                all_hypotheses.extend(hyps)
            except Exception as e:
                print(f"   ⚠️  Error executing {plan.name}: {e}")

        return {
            "status": "COMPLETE",
            "hypotheses": all_hypotheses,
            "plans_executed": len(plans)
        }

    def _generate_cache_key(self, data_sources: List[DataSourceInfo]) -> str:
        """Generate cache key from data sources."""
        # Use date + hash of data source names (avoid filename too long error)
        import hashlib
        names = sorted([ds.name for ds in data_sources])
        names_hash = hashlib.md5('_'.join(names).encode()).hexdigest()[:8]
        date_str = datetime.utcnow().strftime("%Y%m%d")
        return f"{date_str}_{names_hash}"


if __name__ == "__main__":
    # Demo usage
    scout = HybridDataScout()
    result = scout.scan_for_patterns()

    if result["status"] == "NEEDS_LLM":
        print("\n" + "="*60)
        print("⏸️  Paused: LLM reasoning required")
        print("="*60)
        print(f"\nRequest file: {result['request_file']}")
        print(f"Response file: {result['response_file']}")
        print("\nNext step:")
        print("  Run: claude /research-cycle")
        print("  (This will process the LLM request and continue)")
    elif result["status"] == "COMPLETE":
        print(f"\n✓ Scan complete!")
        print(f"  Hypotheses found: {len(result['hypotheses'])}")
        for hyp in result['hypotheses'][:5]:
            print(f"\n{hyp}")

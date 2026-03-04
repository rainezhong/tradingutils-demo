"""Research report generator agent.

Generates comprehensive Jupyter notebook reports from backtest results,
including executive summary, visualizations, statistical validation, and
deployment recommendations.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from scipy import stats

from src.backtesting.metrics import BacktestResult

logger = logging.getLogger(__name__)


@dataclass
class HypothesisInfo:
    """Hypothesis metadata for report generation."""

    name: str
    description: str
    market_type: str
    strategy_family: str
    parameters: Dict[str, Any]
    data_source: str
    time_period: Optional[str] = None


class ReportGeneratorAgent:
    """Generates comprehensive research reports from backtest results.

    Creates Jupyter notebooks with:
    - Executive Summary (LLM-generated)
    - Hypothesis description
    - Backtest results (tables)
    - Visualizations (equity curve, drawdown, returns distribution)
    - Statistical validation (Sharpe, p-value, etc.)
    - Deployment recommendation (deploy/paper/reject)

    Usage:
        agent = ReportGeneratorAgent()
        report_path = agent.generate(hypothesis_info, backtest_results)
    """

    def __init__(
        self,
        reports_dir: Optional[Path] = None,
        min_sharpe_deploy: float = 1.0,
        min_sharpe_paper: float = 0.5,
        min_trades_deploy: int = 50,
        min_trades_paper: int = 20,
    ):
        """Initialize report generator.

        Args:
            reports_dir: Directory to save reports (default: research/reports/)
            min_sharpe_deploy: Minimum Sharpe ratio for production deployment
            min_sharpe_paper: Minimum Sharpe ratio for paper trading
            min_trades_deploy: Minimum trade count for production deployment
            min_trades_paper: Minimum trade count for paper trading
        """
        if reports_dir is None:
            project_root = Path(__file__).parent.parent
            reports_dir = project_root / "research" / "reports"

        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        self.min_sharpe_deploy = min_sharpe_deploy
        self.min_sharpe_paper = min_sharpe_paper
        self.min_trades_deploy = min_trades_deploy
        self.min_trades_paper = min_trades_paper

    def generate(
        self,
        hypothesis: HypothesisInfo,
        results: BacktestResult,
    ) -> str:
        """Generate a comprehensive research report.

        Args:
            hypothesis: Hypothesis metadata
            results: Backtest results

        Returns:
            Path to generated notebook
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        notebook_name = f"{hypothesis.name}_{timestamp}"

        logger.info(f"Generating research report: {notebook_name}")

        # Calculate advanced metrics
        advanced_metrics = self._calculate_advanced_metrics(results)

        # Generate recommendation
        recommendation = self._generate_recommendation(results, advanced_metrics)

        # Create notebook cells
        cells = []
        cell_types = []

        # Executive summary (markdown)
        cells.append(self._create_executive_summary(hypothesis, results, advanced_metrics, recommendation))
        cell_types.append("markdown")

        # Hypothesis description (markdown)
        cells.append(self._create_hypothesis_section(hypothesis))
        cell_types.append("markdown")

        # Results summary (code + table)
        cells.append(self._create_results_summary_cell(results, advanced_metrics))
        cell_types.append("code")

        # Equity curve visualization
        cells.extend(self._create_visualization_cells(results))
        cell_types.extend(["code"] * len(self._create_visualization_cells(results)))

        # Statistical validation
        cells.append(self._create_statistical_validation_cell(results, advanced_metrics))
        cell_types.append("code")

        # Trade analysis
        cells.append(self._create_trade_analysis_cell(results))
        cell_types.append("code")

        # Recommendation (markdown)
        cells.append(self._create_recommendation_section(recommendation, advanced_metrics))
        cell_types.append("markdown")

        # Use MCP research server to create notebook
        try:
            from mcp__research__create_notebook import create_notebook

            description = f"Research report for hypothesis: {hypothesis.description}"

            result = create_notebook(
                name=notebook_name,
                cells=cells,
                cell_types=cell_types,
                description=description,
            )

            notebook_path = self.reports_dir.parent.parent / "notebooks" / f"{notebook_name}.ipynb"

            logger.info(f"Created research report: {notebook_path}")
            return str(notebook_path)

        except ImportError:
            # Fallback: create notebook using nbformat directly
            logger.warning("MCP research server not available, using fallback method")
            return self._create_notebook_fallback(notebook_name, cells, cell_types, hypothesis)

    def _calculate_advanced_metrics(self, results: BacktestResult) -> Dict[str, float]:
        """Calculate advanced performance metrics."""
        m = results.metrics

        # Extract bankroll curve
        if not results.bankroll_curve:
            return {
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "calmar_ratio": 0.0,
                "profit_factor": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "max_consecutive_losses": 0,
                "p_value": 1.0,
            }

        # Calculate returns from bankroll curve
        bankroll_values = [point[1] for point in results.bankroll_curve]
        returns = np.diff(bankroll_values) / bankroll_values[:-1] if len(bankroll_values) > 1 else []

        if len(returns) == 0:
            returns = [0.0]

        # Sharpe ratio (annualized, assuming daily returns)
        mean_return = np.mean(returns)
        std_return = np.std(returns, ddof=1) if len(returns) > 1 else 0.0
        sharpe = (mean_return / std_return * np.sqrt(252)) if std_return > 0 else 0.0

        # Sortino ratio (downside deviation)
        downside_returns = [r for r in returns if r < 0]
        downside_std = np.std(downside_returns, ddof=1) if len(downside_returns) > 1 else std_return
        sortino = (mean_return / downside_std * np.sqrt(252)) if downside_std > 0 else 0.0

        # Calmar ratio (return / max drawdown)
        calmar = (m.return_pct / m.max_drawdown_pct) if m.max_drawdown_pct > 0 else 0.0

        # Profit factor
        winning_total = sum((s - f.price) * f.size if f.side == "BID" else (f.price - s) * f.size
                           for f in results.fills
                           for s in [results.settlements.get(f.ticker)]
                           if s is not None and ((f.side == "BID" and s > f.price) or (f.side == "ASK" and s < f.price)))
        losing_total = abs(sum((s - f.price) * f.size if f.side == "BID" else (f.price - s) * f.size
                              for f in results.fills
                              for s in [results.settlements.get(f.ticker)]
                              if s is not None and ((f.side == "BID" and s < f.price) or (f.side == "ASK" and s > f.price))))
        profit_factor = (winning_total / losing_total) if losing_total > 0 else 0.0

        # Average win/loss
        avg_win = winning_total / m.winning_fills if m.winning_fills > 0 else 0.0
        avg_loss = losing_total / m.losing_fills if m.losing_fills > 0 else 0.0

        # Max consecutive losses
        consecutive_losses = 0
        max_consecutive_losses = 0
        for f in results.fills:
            settle = results.settlements.get(f.ticker)
            if settle is None:
                continue
            pnl = (settle - f.price) * f.size if f.side == "BID" else (f.price - settle) * f.size
            if pnl < 0:
                consecutive_losses += 1
                max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
            else:
                consecutive_losses = 0

        # Statistical significance (t-test against zero)
        if len(returns) > 1:
            t_stat, p_value = stats.ttest_1samp(returns, 0)
            p_value = p_value / 2 if t_stat > 0 else 1 - (p_value / 2)  # One-tailed test
        else:
            p_value = 1.0

        return {
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "calmar_ratio": calmar,
            "profit_factor": profit_factor,
            "avg_win": avg_win / 100.0,  # Convert to dollars
            "avg_loss": avg_loss / 100.0,  # Convert to dollars
            "max_consecutive_losses": max_consecutive_losses,
            "p_value": p_value,
        }

    def _generate_recommendation(
        self,
        results: BacktestResult,
        advanced_metrics: Dict[str, float],
    ) -> str:
        """Generate deployment recommendation (deploy/paper/reject)."""
        m = results.metrics
        sharpe = advanced_metrics["sharpe_ratio"]

        # Check basic profitability
        if m.net_pnl <= 0:
            return "reject"

        # Check statistical significance
        if advanced_metrics["p_value"] > 0.05:
            return "reject"

        # Check trade count and Sharpe for deployment tiers
        if m.total_fills >= self.min_trades_deploy and sharpe >= self.min_sharpe_deploy:
            return "deploy"
        elif m.total_fills >= self.min_trades_paper and sharpe >= self.min_sharpe_paper:
            return "paper"
        else:
            return "reject"

    def _create_executive_summary(
        self,
        hypothesis: HypothesisInfo,
        results: BacktestResult,
        metrics: Dict[str, float],
        recommendation: str,
    ) -> str:
        """Create executive summary markdown."""
        m = results.metrics

        # Determine verdict emoji and text
        verdict_map = {
            "deploy": ("✅", "APPROVED FOR PRODUCTION"),
            "paper": ("📝", "APPROVED FOR PAPER TRADING"),
            "reject": ("❌", "REJECTED"),
        }
        emoji, verdict_text = verdict_map.get(recommendation, ("❓", "UNKNOWN"))

        return f"""## Executive Summary

### {emoji} {verdict_text}

**Strategy:** {hypothesis.name}
**Market Type:** {hypothesis.market_type}
**Test Period:** {hypothesis.time_period or "N/A"}
**Generated:** {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

#### Key Results

- **Total Trades:** {m.total_fills:,}
- **Win Rate:** {m.win_rate_pct:.1f}%
- **Net P&L:** ${m.net_pnl:,.2f} ({m.return_pct:+.1f}%)
- **Sharpe Ratio:** {metrics["sharpe_ratio"]:.2f}
- **Max Drawdown:** {m.max_drawdown_pct:.1f}%

#### Performance Assessment

The strategy generated **{m.total_fills} trades** with a **{m.win_rate_pct:.1f}% win rate**,
producing a net P&L of **${m.net_pnl:,.2f}** ({m.return_pct:+.1f}% return).

Risk-adjusted performance shows a **Sharpe ratio of {metrics["sharpe_ratio"]:.2f}** and
**Sortino ratio of {metrics["sortino_ratio"]:.2f}**. The profit factor of
**{metrics["profit_factor"]:.2f}** indicates {'strong' if metrics["profit_factor"] > 2 else 'moderate' if metrics["profit_factor"] > 1 else 'weak'}
edge per unit of risk.

Statistical validation shows {'**statistical significance**' if metrics["p_value"] < 0.05 else '**no statistical significance**'}
(p={metrics["p_value"]:.4f}), with maximum consecutive losses of **{metrics["max_consecutive_losses"]}**.

**Recommendation:** {verdict_text}
"""

    def _create_hypothesis_section(self, hypothesis: HypothesisInfo) -> str:
        """Create hypothesis description markdown."""
        params_str = "\n".join(f"- **{k}**: {v}" for k, v in hypothesis.parameters.items())

        return f"""## Hypothesis

### Description

{hypothesis.description}

### Strategy Details

- **Family:** {hypothesis.strategy_family}
- **Market Type:** {hypothesis.market_type}
- **Data Source:** {hypothesis.data_source}

### Parameters

{params_str}
"""

    def _create_results_summary_cell(
        self,
        results: BacktestResult,
        metrics: Dict[str, float],
    ) -> str:
        """Create results summary table code."""
        return f"""# Results Summary

import pandas as pd

# Core metrics
core_metrics = {{
    'Total Frames': {results.metrics.total_frames},
    'Total Signals': {results.metrics.total_signals},
    'Total Fills': {results.metrics.total_fills},
    'Initial Bankroll': f"${results.metrics.initial_bankroll:,.2f}",
    'Final Bankroll': f"${results.metrics.final_bankroll:,.2f}",
    'Net P&L': f"${results.metrics.net_pnl:,.2f}",
    'Return %': f"{results.metrics.return_pct:+.2f}%",
    'Total Fees': f"${results.metrics.total_fees:,.2f}",
}}

# Risk metrics
risk_metrics = {{
    'Max Drawdown %': f"{results.metrics.max_drawdown_pct:.2f}%",
    'Peak Bankroll': f"${results.metrics.peak_bankroll:,.2f}",
    'Sharpe Ratio': {metrics['sharpe_ratio']:.3f},
    'Sortino Ratio': {metrics['sortino_ratio']:.3f},
    'Calmar Ratio': {metrics['calmar_ratio']:.3f},
}}

# Trade metrics
trade_metrics = {{
    'Winners': {results.metrics.winning_fills},
    'Losers': {results.metrics.losing_fills},
    'Win Rate %': f"{results.metrics.win_rate_pct:.2f}%",
    'Profit Factor': {metrics['profit_factor']:.3f},
    'Avg Win': f"${metrics['avg_win']:.2f}",
    'Avg Loss': f"${metrics['avg_loss']:.2f}",
    'Max Consecutive Losses': {metrics['max_consecutive_losses']},
}}

# Statistical validation
stat_metrics = {{
    'P-Value': {metrics['p_value']:.6f},
    'Significant?': '✅ Yes (p < 0.05)' if {metrics['p_value']} < 0.05 else '❌ No (p >= 0.05)',
}}

# Display tables
print("=" * 60)
print("CORE METRICS")
print("=" * 60)
for k, v in core_metrics.items():
    print(f"{{{{k:.<40}}}} {{{{v:>15}}}}")

print("\\n" + "=" * 60)
print("RISK METRICS")
print("=" * 60)
for k, v in risk_metrics.items():
    print(f"{{{{k:.<40}}}} {{{{v:>15}}}}")

print("\\n" + "=" * 60)
print("TRADE METRICS")
print("=" * 60)
for k, v in trade_metrics.items():
    print(f"{{{{k:.<40}}}} {{{{v:>15}}}}")

print("\\n" + "=" * 60)
print("STATISTICAL VALIDATION")
print("=" * 60)
for k, v in stat_metrics.items():
    print(f"{{{{k:.<40}}}} {{{{v:>15}}}}")
"""

    def _create_visualization_cells(self, results: BacktestResult) -> List[str]:
        """Create visualization code cells."""
        cells = []

        # Equity curve
        bankroll_data = json.dumps(results.bankroll_curve)
        cells.append(f"""# Equity Curve

import matplotlib.pyplot as plt
import numpy as np

# Extract bankroll curve
bankroll_curve = {bankroll_data}
if bankroll_curve:
    timestamps = [point[0] for point in bankroll_curve]
    values = [point[1] for point in bankroll_curve]

    fig, ax = plt.subplots(figsize=(14, 6))
    ax.plot(values, linewidth=2, color='#2E86AB')
    ax.axhline(y={results.metrics.initial_bankroll}, color='gray', linestyle='--',
               label=f'Initial: ${results.metrics.initial_bankroll:,.0f}', alpha=0.7)
    ax.fill_between(range(len(values)), {results.metrics.initial_bankroll}, values,
                     where=np.array(values) >= {results.metrics.initial_bankroll},
                     alpha=0.2, color='green', label='Profit')
    ax.fill_between(range(len(values)), {results.metrics.initial_bankroll}, values,
                     where=np.array(values) < {results.metrics.initial_bankroll},
                     alpha=0.2, color='red', label='Loss')

    ax.set_xlabel('Trade Number', fontsize=12)
    ax.set_ylabel('Bankroll ($)', fontsize=12)
    ax.set_title('Equity Curve Over Time', fontsize=14, fontweight='bold')
    ax.legend(loc='best')
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()

    print(f"Final bankroll: ${{values[-1]:,.2f}}")
    print(f"Peak bankroll: ${{max(values):,.2f}}")
    print(f"Total return: ${{values[-1] - values[0]:+,.2f}} ({{(values[-1] / values[0] - 1) * 100:+.2f}}%)")
else:
    print("No bankroll curve data available")
""")

        # Drawdown analysis
        cells.append(f"""# Drawdown Analysis

if bankroll_curve:
    values = [point[1] for point in bankroll_curve]

    # Calculate running maximum and drawdown
    running_max = np.maximum.accumulate(values)
    drawdown = (values - running_max) / running_max * 100

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True)

    # Equity with peaks
    ax1.plot(values, linewidth=2, color='#2E86AB', label='Equity')
    ax1.plot(running_max, linewidth=1, color='green', linestyle='--',
             alpha=0.7, label='Peak Equity')
    ax1.fill_between(range(len(values)), running_max, values,
                      alpha=0.3, color='red')
    ax1.set_ylabel('Bankroll ($)', fontsize=12)
    ax1.set_title('Equity and Peak Equity', fontsize=14, fontweight='bold')
    ax1.legend(loc='best')
    ax1.grid(True, alpha=0.3)

    # Drawdown %
    ax2.fill_between(range(len(drawdown)), 0, drawdown,
                      alpha=0.5, color='red')
    ax2.plot(drawdown, linewidth=2, color='darkred')
    ax2.set_xlabel('Trade Number', fontsize=12)
    ax2.set_ylabel('Drawdown (%)', fontsize=12)
    ax2.set_title('Drawdown Percentage', fontsize=14, fontweight='bold')
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.show()

    print(f"Max drawdown: {{min(drawdown):.2f}}%")
    print(f"Current drawdown: {{drawdown[-1]:.2f}}%")
""")

        # Returns distribution
        cells.append(f"""# Returns Distribution

if bankroll_curve and len(bankroll_curve) > 1:
    values = [point[1] for point in bankroll_curve]
    returns = np.diff(values) / values[:-1] * 100  # Percentage returns

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    # Histogram
    ax1.hist(returns, bins=30, edgecolor='black', alpha=0.7, color='#2E86AB')
    ax1.axvline(x=0, color='red', linestyle='--', linewidth=2, label='Break-even')
    ax1.axvline(x=np.mean(returns), color='green', linestyle='--',
                linewidth=2, label=f'Mean: {{np.mean(returns):.3f}}%')
    ax1.set_xlabel('Return (%)', fontsize=12)
    ax1.set_ylabel('Frequency', fontsize=12)
    ax1.set_title('Distribution of Returns', fontsize=14, fontweight='bold')
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    # Q-Q plot for normality check
    from scipy import stats
    stats.probplot(returns, dist="norm", plot=ax2)
    ax2.set_title('Q-Q Plot (Normality Check)', fontsize=14, fontweight='bold')
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.show()

    print(f"Mean return: {{np.mean(returns):.4f}}%")
    print(f"Std dev: {{np.std(returns, ddof=1):.4f}}%")
    print(f"Skewness: {{stats.skew(returns):.4f}}")
    print(f"Kurtosis: {{stats.kurtosis(returns):.4f}}")
""")

        return cells

    def _create_statistical_validation_cell(
        self,
        results: BacktestResult,
        metrics: Dict[str, float],
    ) -> str:
        """Create statistical validation code."""
        return f"""# Statistical Validation

from scipy import stats
import numpy as np

# Calculate statistics
p_value = {metrics['p_value']}
sharpe = {metrics['sharpe_ratio']}
sortino = {metrics['sortino_ratio']}
win_rate = {results.metrics.win_rate_pct}
total_trades = {results.metrics.total_fills}

print("=" * 60)
print("STATISTICAL VALIDATION")
print("=" * 60)

# Significance test
print(f"\\nP-Value: {{{{p_value:.6f}}}}")
if p_value < 0.001:
    sig_level = "*** (p < 0.001)"
elif p_value < 0.01:
    sig_level = "** (p < 0.01)"
elif p_value < 0.05:
    sig_level = "* (p < 0.05)"
else:
    sig_level = "Not significant (p >= 0.05)"
print(f"Significance: {{{{sig_level}}}}")

# Sharpe ratio interpretation
print(f"\\nSharpe Ratio: {{{{sharpe:.3f}}}}")
if sharpe > 3:
    sharpe_qual = "Excellent"
elif sharpe > 2:
    sharpe_qual = "Very Good"
elif sharpe > 1:
    sharpe_qual = "Good"
elif sharpe > 0.5:
    sharpe_qual = "Acceptable"
else:
    sharpe_qual = "Poor"
print(f"Quality: {{{{sharpe_qual}}}}")

# Sortino ratio
print(f"\\nSortino Ratio: {{{{sortino:.3f}}}}")
print(f"(Sharpe-like metric focusing on downside risk)")

# Sample size assessment
print(f"\\nSample Size: {{{{total_trades}}}} trades")
if total_trades >= 100:
    sample_qual = "Large (high confidence)"
elif total_trades >= 50:
    sample_qual = "Adequate (moderate confidence)"
elif total_trades >= 20:
    sample_qual = "Small (low confidence)"
else:
    sample_qual = "Very small (very low confidence)"
print(f"Assessment: {{{{sample_qual}}}}")

# Win rate confidence interval (binomial proportion)
if total_trades > 0:
    from scipy.stats import binom
    alpha = 0.05
    wins = {results.metrics.winning_fills}
    ci_low = binom.ppf(alpha/2, total_trades, wins/total_trades) / total_trades * 100
    ci_high = binom.ppf(1-alpha/2, total_trades, wins/total_trades) / total_trades * 100
    print(f"\\nWin Rate: {{{{win_rate:.2f}}}}%")
    print(f"95% CI: [{{{{ci_low:.2f}}}}%, {{{{ci_high:.2f}}}}%]")

print("\\n" + "=" * 60)
"""

    def _create_trade_analysis_cell(self, results: BacktestResult) -> str:
        """Create trade-by-trade analysis code."""
        fills_data = []
        for f in results.fills[:100]:  # Limit to first 100 trades for report size
            settle = results.settlements.get(f.ticker)
            if settle is not None:
                pnl = (settle - f.price) * f.size if f.side == "BID" else (f.price - settle) * f.size
                fills_data.append({
                    "ticker": f.ticker,
                    "timestamp": f.timestamp.isoformat() if f.timestamp else "N/A",
                    "side": f.side,
                    "price": f.price / 100.0,
                    "size": f.size,
                    "settlement": settle / 100.0,
                    "pnl": pnl / 100.0,
                })

        fills_json = json.dumps(fills_data, indent=2)

        return f"""# Trade-by-Trade Analysis

import pandas as pd

# Load trade data (first 100 trades)
trades_data = {fills_json}

if trades_data:
    df = pd.DataFrame(trades_data)

    print("=" * 80)
    print("FIRST 20 TRADES")
    print("=" * 80)
    print(df.head(20).to_string(index=False))

    print("\\n" + "=" * 80)
    print("LAST 20 TRADES")
    print("=" * 80)
    print(df.tail(20).to_string(index=False))

    # P&L by side
    print("\\n" + "=" * 80)
    print("P&L BY SIDE")
    print("=" * 80)
    side_pnl = df.groupby('side').agg({{
        'pnl': ['count', 'sum', 'mean', 'std'],
        'size': 'sum'
    }})
    print(side_pnl)

    # Top winners and losers
    print("\\n" + "=" * 80)
    print("TOP 5 WINNERS")
    print("=" * 80)
    print(df.nlargest(5, 'pnl')[['ticker', 'side', 'price', 'pnl']].to_string(index=False))

    print("\\n" + "=" * 80)
    print("TOP 5 LOSERS")
    print("=" * 80)
    print(df.nsmallest(5, 'pnl')[['ticker', 'side', 'price', 'pnl']].to_string(index=False))
else:
    print("No trade data available")
"""

    def _create_recommendation_section(
        self,
        recommendation: str,
        metrics: Dict[str, float],
    ) -> str:
        """Create deployment recommendation markdown."""
        rec_map = {
            "deploy": {
                "title": "✅ APPROVED FOR PRODUCTION DEPLOYMENT",
                "description": "This strategy meets all criteria for production deployment.",
                "next_steps": [
                    "Configure production parameters in strategy config",
                    "Set up monitoring and alerting",
                    "Start with reduced position sizing (50% of backtest)",
                    "Monitor performance for 1-2 weeks before full deployment",
                    "Document entry/exit rules and edge conditions",
                ],
            },
            "paper": {
                "title": "📝 APPROVED FOR PAPER TRADING",
                "description": "This strategy shows promise but requires validation in paper trading.",
                "next_steps": [
                    "Deploy to paper trading environment",
                    "Monitor for 2-4 weeks minimum",
                    "Validate edge persists in live market conditions",
                    "Check for execution slippage and fill rates",
                    "Re-evaluate after collecting live performance data",
                ],
            },
            "reject": {
                "title": "❌ REJECTED",
                "description": "This strategy does not meet minimum criteria for deployment.",
                "next_steps": [
                    "Review hypothesis and parameter assumptions",
                    "Check for data quality issues or look-ahead bias",
                    "Consider alternative market types or timeframes",
                    "Investigate if edge exists in subsets of data",
                    "Document findings for future reference",
                ],
            },
        }

        rec_info = rec_map.get(recommendation, rec_map["reject"])
        next_steps_str = "\n".join(f"{i+1}. {step}" for i, step in enumerate(rec_info["next_steps"]))

        return f"""## Recommendation

### {rec_info["title"]}

{rec_info["description"]}

### Rationale

- **Sharpe Ratio:** {metrics["sharpe_ratio"]:.2f} ({'✅' if metrics["sharpe_ratio"] >= self.min_sharpe_deploy else '⚠️' if metrics["sharpe_ratio"] >= self.min_sharpe_paper else '❌'})
- **Statistical Significance:** p={metrics["p_value"]:.4f} ({'✅' if metrics["p_value"] < 0.05 else '❌'})
- **Profit Factor:** {metrics["profit_factor"]:.2f} ({'✅' if metrics["profit_factor"] > 1.5 else '⚠️' if metrics["profit_factor"] > 1.0 else '❌'})

### Next Steps

{next_steps_str}

---

*Report generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} by ReportGeneratorAgent*
"""

    def _create_notebook_fallback(
        self,
        name: str,
        cells: List[str],
        cell_types: List[str],
        hypothesis: HypothesisInfo,
    ) -> str:
        """Create notebook using nbformat directly (fallback method)."""
        import nbformat

        nb = nbformat.v4.new_notebook()
        nb.metadata["kernelspec"] = {
            "display_name": "Python 3",
            "language": "python",
            "name": "python3",
        }

        # Add title
        nb.cells.append(nbformat.v4.new_markdown_cell(f"# {name}\n\n{hypothesis.description}"))

        # Add setup cell
        setup = """import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats

PROJECT_ROOT = Path.cwd() if (Path.cwd() / 'data').exists() else Path.cwd().parent
sys.path.insert(0, str(PROJECT_ROOT))
DATA_DIR = PROJECT_ROOT / 'data'

%matplotlib inline
plt.rcParams['figure.figsize'] = (12, 6)
plt.rcParams['figure.dpi'] = 100
"""
        nb.cells.append(nbformat.v4.new_code_cell(setup))

        # Add user cells
        for source, ctype in zip(cells, cell_types):
            if ctype == "markdown":
                nb.cells.append(nbformat.v4.new_markdown_cell(source))
            else:
                nb.cells.append(nbformat.v4.new_code_cell(source))

        # Save to reports directory
        output_path = self.reports_dir / f"{name}.ipynb"
        with open(output_path, "w") as f:
            nbformat.write(nb, f)

        logger.info(f"Created notebook (fallback): {output_path}")
        return str(output_path)

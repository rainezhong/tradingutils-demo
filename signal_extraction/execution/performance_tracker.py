"""
Performance tracking and analysis for trading sessions.
Tracks all metrics needed to evaluate profitability.
"""

import json
import time
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional
from datetime import datetime
import numpy as np
import pandas as pd
from pathlib import Path


@dataclass
class Trade:
    """Record of a single trade."""
    trade_id: int
    timestamp: float
    ticker: str
    side: str  # 'buy' or 'sell'
    quantity: int
    entry_price: float
    exit_price: Optional[float] = None
    exit_timestamp: Optional[float] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    
    # Context at entry
    fair_value: Optional[float] = None
    mispricing: Optional[float] = None
    signal_strength: Optional[float] = None
    orderbook_imbalance: Optional[float] = None
    score_differential: Optional[int] = None
    win_probability: Optional[float] = None
    
    @property
    def is_closed(self) -> bool:
        return self.exit_price is not None
    
    @property
    def duration_seconds(self) -> float:
        if self.exit_timestamp:
            return self.exit_timestamp - self.timestamp
        return time.time() - self.timestamp
    
    @property
    def is_winner(self) -> bool:
        return self.pnl is not None and self.pnl > 0


class PerformanceTracker:
    """
    Tracks trading performance and calculates profitability metrics.
    """
    
    def __init__(self, initial_capital: float, session_name: str = None):
        """
        Initialize performance tracker.
        
        Args:
            initial_capital: Starting capital
            session_name: Name for this session (e.g., "LAL_vs_BOS_2024-01-21")
        """
        self.initial_capital = initial_capital
        self.session_name = session_name or f"session_{int(time.time())}"
        
        # Trade tracking
        self.trades: List[Trade] = []
        self.trade_counter = 0
        
        # Equity curve (timestamp, equity)
        self.equity_curve: List[tuple] = [(time.time(), initial_capital)]
        
        # Session metadata
        self.session_start = time.time()
        self.session_end: Optional[float] = None
        
        print(f"[PerformanceTracker] Initialized session: {self.session_name}")
        print(f"  Initial Capital: ${initial_capital:,.2f}")
    
    def record_trade_entry(
        self,
        ticker: str,
        side: str,
        quantity: int,
        entry_price: float,
        context: Dict = None
    ) -> int:
        """
        Record a new trade entry.
        
        Args:
            ticker: Market ticker
            side: 'buy' or 'sell'
            quantity: Number of contracts
            entry_price: Entry price
            context: Optional dict with strategy context
            
        Returns:
            trade_id for tracking
        """
        self.trade_counter += 1
        
        trade = Trade(
            trade_id=self.trade_counter,
            timestamp=time.time(),
            ticker=ticker,
            side=side,
            quantity=quantity,
            entry_price=entry_price
        )
        
        # Add context if provided
        if context:
            trade.fair_value = context.get('fair_value')
            trade.mispricing = context.get('mispricing')
            trade.signal_strength = context.get('signal_strength')
            trade.orderbook_imbalance = context.get('orderbook_imbalance')
            trade.score_differential = context.get('score_differential')
            trade.win_probability = context.get('win_probability')
        
        self.trades.append(trade)
        
        print(f"\n[Trade #{self.trade_counter}] ENTRY")
        print(f"  {side.upper()} {quantity} {ticker} @ ${entry_price:.4f}")
        if context:
            print(f"  Fair Value: ${context.get('fair_value', 0):.4f}")
            print(f"  Mispricing: ${context.get('mispricing', 0):+.4f}")
        
        return self.trade_counter
    
    def record_trade_exit(
        self,
        trade_id: int,
        exit_price: float
    ):
        """
        Record trade exit and calculate P&L.
        
        Args:
            trade_id: ID of trade to close
            exit_price: Exit price
        """
        # Find trade
        trade = next((t for t in self.trades if t.trade_id == trade_id), None)
        
        if not trade:
            print(f"[PerformanceTracker] Warning: Trade {trade_id} not found")
            return
        
        if trade.is_closed:
            print(f"[PerformanceTracker] Warning: Trade {trade_id} already closed")
            return
        
        # Calculate P&L
        trade.exit_timestamp = time.time()
        trade.exit_price = exit_price
        
        if trade.side == 'buy':
            trade.pnl = (exit_price - trade.entry_price) * trade.quantity
        else:  # sell
            trade.pnl = (trade.entry_price - exit_price) * trade.quantity
        
        trade.pnl_pct = (trade.pnl / (trade.entry_price * trade.quantity)) * 100
        
        # Update equity curve
        current_equity = self.equity_curve[-1][1] + trade.pnl
        self.equity_curve.append((time.time(), current_equity))
        
        # Print trade result
        result = "WIN" if trade.is_winner else "LOSS"
        print(f"\n[Trade #{trade_id}] EXIT - {result}")
        print(f"  Entry:  ${trade.entry_price:.4f}")
        print(f"  Exit:   ${exit_price:.4f}")
        print(f"  P&L:    ${trade.pnl:+,.2f} ({trade.pnl_pct:+.2f}%)")
        print(f"  Duration: {trade.duration_seconds:.0f}s")
        print(f"  New Equity: ${current_equity:,.2f}")
    
    def update_equity(self, current_equity: float):
        """
        Update equity curve with current portfolio value.
        
        Args:
            current_equity: Current total equity
        """
        self.equity_curve.append((time.time(), current_equity))
    
    def end_session(self):
        """Mark session as ended."""
        self.session_end = time.time()
    
    def get_statistics(self) -> Dict:
        """
        Calculate comprehensive performance statistics.
        
        Returns:
            Dict with all performance metrics
        """
        closed_trades = [t for t in self.trades if t.is_closed]
        
        if not closed_trades:
            return {
                'total_trades': 0,
                'profitability': 'N/A - No closed trades',
                'note': 'No trades completed yet'
            }
        
        # Basic counts
        total_trades = len(closed_trades)
        winning_trades = [t for t in closed_trades if t.is_winner]
        losing_trades = [t for t in closed_trades if not t.is_winner]
        
        num_wins = len(winning_trades)
        num_losses = len(losing_trades)
        
        # P&L metrics
        total_pnl = sum(t.pnl for t in closed_trades)
        avg_pnl = total_pnl / total_trades
        
        gross_profit = sum(t.pnl for t in winning_trades) if winning_trades else 0
        gross_loss = abs(sum(t.pnl for t in losing_trades)) if losing_trades else 0
        
        avg_win = gross_profit / num_wins if num_wins > 0 else 0
        avg_loss = gross_loss / num_losses if num_losses > 0 else 0
        
        # Win rate
        win_rate = (num_wins / total_trades * 100) if total_trades > 0 else 0
        
        # Profit factor
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        # Returns
        final_equity = self.equity_curve[-1][1]
        total_return = ((final_equity - self.initial_capital) / self.initial_capital) * 100
        
        # Sharpe ratio (simplified)
        if len(self.equity_curve) > 1:
            equity_series = pd.Series([e[1] for e in self.equity_curve])
            returns = equity_series.pct_change().dropna()
            
            if len(returns) > 0 and returns.std() > 0:
                sharpe = returns.mean() / returns.std() * np.sqrt(len(returns))
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0
        
        # Max drawdown
        equity_values = [e[1] for e in self.equity_curve]
        peak = equity_values[0]
        max_dd = 0
        
        for equity in equity_values:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100
            if dd > max_dd:
                max_dd = dd
        
        # Session duration
        if self.session_end:
            duration = self.session_end - self.session_start
        else:
            duration = time.time() - self.session_start
        
        duration_minutes = duration / 60
        
        return {
            # Overview
            'session_name': self.session_name,
            'duration_minutes': duration_minutes,
            
            # Capital
            'initial_capital': self.initial_capital,
            'final_equity': final_equity,
            'total_return': total_return,
            'total_pnl': total_pnl,
            
            # Trade metrics
            'total_trades': total_trades,
            'winning_trades': num_wins,
            'losing_trades': num_losses,
            'win_rate': win_rate,
            
            # P&L breakdown
            'gross_profit': gross_profit,
            'gross_loss': gross_loss,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'avg_pnl_per_trade': avg_pnl,
            'profit_factor': profit_factor,
            
            # Risk metrics
            'sharpe_ratio': sharpe,
            'max_drawdown': max_dd,
            
            # Profitability assessment
            'is_profitable': total_pnl > 0,
            'beats_breakeven': win_rate > 52,  # After fees
            'profitability': self._assess_profitability(
                total_return, win_rate, profit_factor, sharpe
            )
        }
    
    def _assess_profitability(
        self,
        total_return: float,
        win_rate: float,
        profit_factor: float,
        sharpe: float
    ) -> str:
        """
        Assess overall profitability with qualitative rating.
        """
        if total_return <= 0:
            return "UNPROFITABLE ❌"
        
        if total_return > 0 and total_return < 1:
            return "BARELY PROFITABLE ⚠️"
        
        # Check quality of profitability
        if win_rate >= 55 and profit_factor >= 1.5 and sharpe >= 1.0:
            return "HIGHLY PROFITABLE ✅✅"
        
        if win_rate >= 52 and profit_factor >= 1.2:
            return "PROFITABLE ✅"
        
        return "MARGINALLY PROFITABLE ⚠️"
    
    def print_summary(self):
        """Print formatted performance summary."""
        stats = self.get_statistics()
        
        print("\n" + "="*70)
        print("SESSION PERFORMANCE SUMMARY")
        print("="*70)
        
        if stats['total_trades'] == 0:
            print("No trades completed yet.")
            print("="*70 + "\n")
            return
        
        # Header
        print(f"\nSession: {stats['session_name']}")
        print(f"Duration: {stats['duration_minutes']:.1f} minutes")
        print(f"\n{'-'*70}")
        
        # Capital
        print("CAPITAL:")
        print(f"  Starting: ${stats['initial_capital']:,.2f}")
        print(f"  Ending:   ${stats['final_equity']:,.2f}")
        print(f"  P&L:      ${stats['total_pnl']:+,.2f} ({stats['total_return']:+.2f}%)")
        
        # Verdict
        print(f"\n  ► {stats['profitability']}")
        
        print(f"\n{'-'*70}")
        
        # Trade statistics
        print("TRADE STATISTICS:")
        print(f"  Total Trades:    {stats['total_trades']}")
        print(f"  Winners:         {stats['winning_trades']} ({stats['win_rate']:.1f}%)")
        print(f"  Losers:          {stats['losing_trades']}")
        print(f"  Avg P&L/Trade:   ${stats['avg_pnl_per_trade']:+,.2f}")
        
        print(f"\n{'-'*70}")
        
        # P&L breakdown
        print("P&L BREAKDOWN:")
        print(f"  Gross Profit:    ${stats['gross_profit']:,.2f}")
        print(f"  Gross Loss:      ${stats['gross_loss']:,.2f}")
        print(f"  Avg Win:         ${stats['avg_win']:,.2f}")
        print(f"  Avg Loss:        ${stats['avg_loss']:,.2f}")
        print(f"  Profit Factor:   {stats['profit_factor']:.2f}")
        
        print(f"\n{'-'*70}")
        
        # Risk metrics
        print("RISK METRICS:")
        print(f"  Sharpe Ratio:    {stats['sharpe_ratio']:.2f}")
        print(f"  Max Drawdown:    {stats['max_drawdown']:.2f}%")
        
        # Interpretation guide
        print(f"\n{'-'*70}")
        print("INTERPRETATION:")
        print(f"  {'✅' if stats['is_profitable'] else '❌'} Profitable: {stats['is_profitable']}")
        print(f"  {'✅' if stats['beats_breakeven'] else '❌'} Win Rate > 52%: {stats['beats_breakeven']}")
        print(f"  {'✅' if stats['profit_factor'] > 1.5 else '⚠️' if stats['profit_factor'] > 1.0 else '❌'} "
              f"Profit Factor > 1.5: {stats['profit_factor'] > 1.5}")
        print(f"  {'✅' if stats['sharpe_ratio'] > 1.0 else '⚠️' if stats['sharpe_ratio'] > 0.5 else '❌'} "
              f"Sharpe > 1.0: {stats['sharpe_ratio'] > 1.0}")
        
        print("="*70 + "\n")
    
    def save_results(self, directory: str = "backtest_results"):
        """
        Save session results to files.
        
        Args:
            directory: Directory to save results
        """
        # Create directory
        Path(directory).mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"{self.session_name}_{timestamp}"
        
        # Save statistics as JSON
        stats = self.get_statistics()
        stats_file = Path(directory) / f"{base_name}_stats.json"
        
        with open(stats_file, 'w') as f:
            json.dump(stats, f, indent=2, default=str)
        
        print(f"[PerformanceTracker] Saved stats to {stats_file}")
        
        # Save trades as CSV
        if self.trades:
            trades_data = [asdict(t) for t in self.trades]
            df_trades = pd.DataFrame(trades_data)
            trades_file = Path(directory) / f"{base_name}_trades.csv"
            df_trades.to_csv(trades_file, index=False)
            print(f"[PerformanceTracker] Saved trades to {trades_file}")
        
        # Save equity curve as CSV
        if self.equity_curve:
            df_equity = pd.DataFrame(
                self.equity_curve,
                columns=['timestamp', 'equity']
            )
            df_equity['datetime'] = pd.to_datetime(df_equity['timestamp'], unit='s')
            equity_file = Path(directory) / f"{base_name}_equity.csv"
            df_equity.to_csv(equity_file, index=False)
            print(f"[PerformanceTracker] Saved equity curve to {equity_file}")
        
        return base_name
    
    def plot_results(self):
        """Plot equity curve and trade distribution."""
        try:
            import matplotlib.pyplot as plt
            
            fig, axes = plt.subplots(2, 2, figsize=(14, 10))
            fig.suptitle(f"Performance Analysis: {self.session_name}", fontsize=14, fontweight='bold')
            
            # 1. Equity Curve
            ax1 = axes[0, 0]
            timestamps = [e[0] for e in self.equity_curve]
            equities = [e[1] for e in self.equity_curve]
            times = [(t - timestamps[0]) / 60 for t in timestamps]  # Minutes since start
            
            ax1.plot(times, equities, 'b-', linewidth=2)
            ax1.axhline(y=self.initial_capital, color='gray', linestyle='--', label='Starting Capital')
            ax1.set_xlabel('Time (minutes)')
            ax1.set_ylabel('Equity ($)')
            ax1.set_title('Equity Curve')
            ax1.legend()
            ax1.grid(True, alpha=0.3)
            
            # 2. P&L Distribution
            ax2 = axes[0, 1]
            closed_trades = [t for t in self.trades if t.is_closed]
            if closed_trades:
                pnls = [t.pnl for t in closed_trades]
                ax2.hist(pnls, bins=20, alpha=0.7, edgecolor='black')
                ax2.axvline(x=0, color='red', linestyle='--', linewidth=2)
                ax2.set_xlabel('P&L ($)')
                ax2.set_ylabel('Frequency')
                ax2.set_title('P&L Distribution')
                ax2.grid(True, alpha=0.3)
            
            # 3. Cumulative P&L
            ax3 = axes[1, 0]
            if closed_trades:
                cumulative_pnl = np.cumsum([t.pnl for t in closed_trades])
                ax3.plot(range(1, len(cumulative_pnl) + 1), cumulative_pnl, 'g-', linewidth=2)
                ax3.axhline(y=0, color='red', linestyle='--')
                ax3.set_xlabel('Trade Number')
                ax3.set_ylabel('Cumulative P&L ($)')
                ax3.set_title('Cumulative P&L by Trade')
                ax3.grid(True, alpha=0.3)
            
            # 4. Win/Loss breakdown
            ax4 = axes[1, 1]
            stats = self.get_statistics()
            if stats['total_trades'] > 0:
                labels = ['Wins', 'Losses']
                sizes = [stats['winning_trades'], stats['losing_trades']]
                colors = ['green', 'red']
                ax4.pie(sizes, labels=labels, colors=colors, autopct='%1.1f%%', startangle=90)
                ax4.set_title(f"Win Rate: {stats['win_rate']:.1f}%")
            
            plt.tight_layout()
            plt.show()
            
        except ImportError:
            print("[PerformanceTracker] Matplotlib not available for plotting")
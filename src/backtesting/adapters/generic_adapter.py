"""Generic adapter that wraps any TradingStrategy subclass.

This allows plugging an existing TradingStrategy into the unified backtest
engine without writing a bespoke adapter.
"""

from typing import List

from src.core.models import Fill
from strategies.base import Signal, TradingStrategy

from ..data_feed import BacktestFrame
from ..engine import BacktestAdapter


class TradingStrategyAdapter(BacktestAdapter):
    """Wraps a TradingStrategy subclass for the unified backtest engine.

    Calls strategy.evaluate(market) for each ticker in the frame and
    aggregates the returned signals.
    """

    def __init__(self, strategy: TradingStrategy):
        self._strategy = strategy

    def evaluate(self, frame: BacktestFrame) -> List[Signal]:
        signals: List[Signal] = []
        for market in frame.markets.values():
            signals.extend(self._strategy.evaluate(market))
        return signals

    def on_fill(self, fill: Fill) -> None:
        self._strategy.on_fill(fill)

    def on_start(self) -> None:
        self._strategy.start()

    def on_end(self) -> None:
        self._strategy.stop()

    @property
    def name(self) -> str:
        return self._strategy._config.name

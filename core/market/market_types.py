from dataclasses import dataclass


@dataclass
class OrderBook:
    best_bid_yes: int
    best_ask_yes: int
    current_volume: int
    last_traded_at_yes: int
    depth_yes: dict[int, int]
    spread: int
    timestamp_ns: int

    @property
    def mid_price(self) -> int:
        """Mid price in cents."""
        return (self.best_bid_yes + self.best_ask_yes) // 2

    @property
    def mid_price_float(self) -> float:
        """Mid price as float for precision."""
        return (self.best_bid_yes + self.best_ask_yes) / 2

"""Cross-Platform Spread Detection Engine.

Real-time detection of arbitrage opportunities across prediction market platforms
(e.g., Kalshi vs Polymarket). Handles multi-platform fees, liquidity filtering,
quote freshness validation, and alert generation with urgency scoring.

SPEED IS CRITICAL: Alerts fire immediately when opportunities are detected.
No artificial delays - arb opportunities can disappear in milliseconds.

Usage:
------
from arb.spread_detector import SpreadDetector, SpreadAlert

# You provide the market matcher (maps equivalent markets across platforms)
detector = SpreadDetector(
    market_matcher=your_matcher,  # You're building this
    min_edge_cents=2.0,           # Minimum 2 cent edge to alert
    min_liquidity_usd=500,        # Minimum $500 available
    max_quote_age_ms=2000,        # Reject quotes older than 2 seconds
)

# Start monitoring
detector.start()

# Get current alerts (or use on_alert callback for immediate notification)
alerts = detector.get_alerts()
for alert in alerts:
    print(f"{alert.urgency_score:.1f} | {alert.estimated_profit_usd:.2f} | {alert.summary}")

# Stop monitoring
detector.stop()
"""

import math
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, List, Optional, Protocol, Tuple


# =============================================================================
# Fee Calculations (Multi-Platform)
# =============================================================================


class Platform(Enum):
    """Supported prediction market platforms."""

    KALSHI = "kalshi"
    POLYMARKET = "polymarket"


@dataclass
class FeeStructure:
    """Fee structure for a platform.

    Attributes:
        taker_rate: Fee rate for taker orders (crossing the spread)
        maker_rate: Fee rate for maker orders (providing liquidity)
        min_fee: Minimum fee per order in dollars
        max_fee_per_contract: Maximum fee per contract (cap)
    """

    taker_rate: float
    maker_rate: float
    min_fee: float = 0.0
    max_fee_per_contract: Optional[float] = None


# Platform-specific fee structures
PLATFORM_FEES: Dict[Platform, FeeStructure] = {
    Platform.KALSHI: FeeStructure(
        taker_rate=0.07,  # 7% of P*(1-P)
        maker_rate=0.0175,  # 1.75% of P*(1-P)
        min_fee=0.0,
    ),
    Platform.POLYMARKET: FeeStructure(
        taker_rate=0.02,  # ~2% flat on notional
        maker_rate=0.00,  # Makers often free
        min_fee=0.0,
    ),
}


def _round_up_cent(x: float) -> float:
    """Round up to nearest cent."""
    return math.ceil(x * 100.0) / 100.0


def calculate_fee(
    platform: Platform,
    price: float,
    contracts: int,
    maker: bool = False,
) -> float:
    """Calculate trading fee for a platform.

    Args:
        platform: The trading platform
        price: Contract price (0.0 to 1.0)
        contracts: Number of contracts
        maker: Whether this is a maker order

    Returns:
        Total fee in dollars
    """
    fee_struct = PLATFORM_FEES[platform]
    rate = fee_struct.maker_rate if maker else fee_struct.taker_rate

    if platform == Platform.KALSHI:
        # Kalshi: fee = rate * C * P * (1-P), rounded up to cent
        raw_fee = rate * contracts * price * (1.0 - price)
        fee = _round_up_cent(raw_fee)
    elif platform == Platform.POLYMARKET:
        # Polymarket: fee = rate * notional value
        notional = contracts * price
        fee = _round_up_cent(rate * notional)
    else:
        fee = 0.0

    return max(fee, fee_struct.min_fee)


def fee_per_contract(
    platform: Platform,
    price: float,
    contracts: int,
    maker: bool = False,
) -> float:
    """Calculate fee per contract for a platform."""
    total = calculate_fee(platform, price, contracts, maker)
    return total / contracts if contracts > 0 else 0.0


def all_in_buy_cost(
    platform: Platform,
    price: float,
    contracts: int,
    maker: bool = False,
) -> float:
    """Calculate all-in cost per contract to buy (price + fee)."""
    return price + fee_per_contract(platform, price, contracts, maker)


def all_in_sell_proceeds(
    platform: Platform,
    price: float,
    contracts: int,
    maker: bool = False,
) -> float:
    """Calculate net proceeds per contract from selling (price - fee)."""
    return price - fee_per_contract(platform, price, contracts, maker)


# =============================================================================
# Placeholder Interfaces (You're Building These)
# =============================================================================


@dataclass
class MarketQuote:
    """Quote data for a single market/outcome.

    This is what your cross-platform detection should provide.
    """

    platform: Platform
    market_id: str
    market_name: str
    outcome: str  # "yes" or "no"

    # Prices
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None

    # Liquidity (size available at best prices)
    bid_size: int = 0  # Contracts available at best bid
    ask_size: int = 0  # Contracts available at best ask

    # Depth (total available within N cents of best)
    bid_depth_usd: float = 0.0  # Total USD available on bid side
    ask_depth_usd: float = 0.0  # Total USD available on ask side

    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2
        return self.best_ask or self.best_bid


@dataclass
class MatchedMarketPair:
    """A pair of markets across platforms representing the same event.

    This is what your market matcher should produce.

    Example:
        Kalshi "Will Bitcoin exceed $100k by Dec 31?"
        matched with
        Polymarket "BTC above $100k EOY"
    """

    pair_id: str
    event_description: str

    # Platform 1 (e.g., Kalshi)
    platform_1: Platform
    market_1_id: str
    market_1_name: str

    # Platform 2 (e.g., Polymarket)
    platform_2: Platform
    market_2_id: str
    market_2_name: str

    # Confidence in the match (0-1)
    match_confidence: float = 1.0

    # Optional metadata
    category: Optional[str] = None
    close_time: Optional[datetime] = None


class MarketMatcher(Protocol):
    """Protocol for market matching across platforms.

    YOU ARE BUILDING THIS. The spread detector will call these methods.
    """

    def get_matched_pairs(self) -> List[MatchedMarketPair]:
        """Get all currently matched market pairs.

        Returns:
            List of matched pairs across platforms
        """
        ...

    def get_quotes(
        self, pair: MatchedMarketPair
    ) -> Tuple[MarketQuote, MarketQuote, MarketQuote, MarketQuote]:
        """Get current quotes for a matched pair.

        Args:
            pair: The matched market pair

        Returns:
            Tuple of (p1_yes, p1_no, p2_yes, p2_no) quotes
        """
        ...


class PlaceholderMatcher:
    """Placeholder implementation - replace with your real matcher."""

    def get_matched_pairs(self) -> List[MatchedMarketPair]:
        """Returns empty list - implement your matcher."""
        return []

    def get_quotes(
        self, pair: MatchedMarketPair
    ) -> Tuple[MarketQuote, MarketQuote, MarketQuote, MarketQuote]:
        """Returns dummy quotes - implement your matcher."""
        raise NotImplementedError("Implement your market matcher")


# =============================================================================
# Spread Detection Core
# =============================================================================


@dataclass
class SpreadOpportunity:
    """A detected spread/arbitrage opportunity."""

    # Identification
    pair: MatchedMarketPair
    opportunity_type: str  # "cross_platform_arb", "dutch_book"

    # The trade
    buy_platform: Platform
    buy_market_id: str
    buy_outcome: str  # "yes" or "no"
    buy_price: float

    sell_platform: Platform
    sell_market_id: str
    sell_outcome: str
    sell_price: float

    # Profit calculation
    gross_edge_per_contract: float  # Before fees
    net_edge_per_contract: float  # After fees
    total_fees_per_contract: float

    # Liquidity
    max_contracts: int  # Limited by available liquidity
    available_liquidity_usd: float
    estimated_profit_usd: float

    # Timing
    first_seen: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)

    @property
    def age_seconds(self) -> float:
        return (self.last_seen - self.first_seen).total_seconds()


@dataclass
class SpreadAlert:
    """An alert for a confirmed spread opportunity."""

    # Core opportunity
    opportunity: SpreadOpportunity

    # Alert metadata
    alert_id: str
    created_at: datetime
    urgency_score: float  # 0-100, higher = more urgent

    # Status
    is_active: bool = True
    times_confirmed: int = 1

    @property
    def summary(self) -> str:
        opp = self.opportunity
        return (
            f"{opp.opportunity_type.upper()}: "
            f"BUY {opp.buy_outcome.upper()} on {opp.buy_platform.value} @ {opp.buy_price:.3f} | "
            f"SELL {opp.sell_outcome.upper()} on {opp.sell_platform.value} @ {opp.sell_price:.3f} | "
            f"Edge: {opp.net_edge_per_contract:.4f}/contract | "
            f"Est. profit: ${opp.estimated_profit_usd:.2f}"
        )

    @property
    def estimated_profit_usd(self) -> float:
        return self.opportunity.estimated_profit_usd

    @property
    def platforms(self) -> Tuple[str, str]:
        return (
            self.opportunity.buy_platform.value,
            self.opportunity.sell_platform.value,
        )

    @property
    def market_names(self) -> Tuple[str, str]:
        return (
            self.opportunity.pair.market_1_name,
            self.opportunity.pair.market_2_name,
        )


class SpreadDetector:
    """Real-time cross-platform spread detection engine.

    Monitors matched market pairs across platforms and detects arbitrage
    opportunities with configurable thresholds for edge, liquidity, and
    persistence.
    """

    def __init__(
        self,
        market_matcher: Optional[MarketMatcher] = None,
        min_edge_cents: float = 2.0,
        min_liquidity_usd: float = 500.0,
        max_quote_age_ms: float = 2000.0,
        poll_interval_ms: int = 500,
        max_alerts: int = 100,
        on_alert: Optional[Callable[[SpreadAlert], None]] = None,
    ):
        """Initialize the spread detector.

        Args:
            market_matcher: Your market matcher implementation (or None for placeholder)
            min_edge_cents: Minimum edge in cents to consider (default 2 cents)
            min_liquidity_usd: Minimum available liquidity in USD (default $500)
            max_quote_age_ms: Maximum quote age in milliseconds to trust (default 2000ms)
            poll_interval_ms: How often to poll for updates in milliseconds
            max_alerts: Maximum number of alerts to keep in history
            on_alert: Optional callback when new alert is generated
        """
        self.matcher = market_matcher or PlaceholderMatcher()
        self.min_edge = min_edge_cents / 100.0  # Convert to dollars
        self.min_liquidity_usd = min_liquidity_usd
        self.max_quote_age_s = max_quote_age_ms / 1000.0
        self.poll_interval_s = poll_interval_ms / 1000.0
        self.max_alerts = max_alerts
        self.on_alert = on_alert

        # State
        self._active_alerts: Dict[str, SpreadAlert] = {}
        self._alert_history: List[SpreadAlert] = []
        self._alert_counter = 0

        # Threading
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "SpreadDetector":
        """Start the detection loop in a background thread."""
        if self._thread is not None and self._thread.is_alive():
            return self

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop the detection loop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def get_alerts(self, active_only: bool = True) -> List[SpreadAlert]:
        """Get current alerts.

        Args:
            active_only: If True, only return active alerts

        Returns:
            List of alerts sorted by urgency (highest first)
        """
        with self._lock:
            if active_only:
                alerts = list(self._active_alerts.values())
            else:
                alerts = self._alert_history.copy()

        return sorted(alerts, key=lambda a: a.urgency_score, reverse=True)

    def check_once(self) -> List[SpreadOpportunity]:
        """Run one detection cycle and return found opportunities.

        Useful for testing or manual invocation.
        """
        opportunities = []

        try:
            pairs = self.matcher.get_matched_pairs()

            for pair in pairs:
                try:
                    opps = self._analyze_pair(pair)
                    opportunities.extend(opps)
                except Exception:
                    # Log but continue with other pairs
                    pass

        except Exception:
            pass

        return opportunities

    def _run_loop(self) -> None:
        """Main detection loop."""
        while not self._stop_event.is_set():
            try:
                self._detection_cycle()
            except Exception:
                # Log error but keep running
                pass

            self._stop_event.wait(self.poll_interval_s)

    def _detection_cycle(self) -> None:
        """Run one detection cycle.

        Alerts IMMEDIATELY when opportunities are found - speed is critical for arb.
        """
        # Get current opportunities
        current_opps = self.check_once()
        current_opp_keys = set()

        for opp in current_opps:
            key = self._opportunity_key(opp)
            current_opp_keys.add(key)

            with self._lock:
                if key not in self._active_alerts:
                    # NEW opportunity - alert immediately!
                    self._create_alert(opp)
                else:
                    # Update existing alert with latest prices
                    alert = self._active_alerts[key]
                    alert.opportunity.last_seen = datetime.now()
                    alert.opportunity.buy_price = opp.buy_price
                    alert.opportunity.sell_price = opp.sell_price
                    alert.opportunity.net_edge_per_contract = opp.net_edge_per_contract
                    alert.opportunity.estimated_profit_usd = opp.estimated_profit_usd
                    alert.times_confirmed += 1

        # Mark alerts as inactive if opportunity disappeared
        with self._lock:
            for alert_id, alert in list(self._active_alerts.items()):
                key = self._opportunity_key(alert.opportunity)
                if key not in current_opp_keys:
                    alert.is_active = False
                    del self._active_alerts[alert_id]

    def _is_quote_fresh(self, quote: MarketQuote) -> bool:
        """Check if a quote is fresh enough to trust."""
        age = (datetime.now() - quote.timestamp).total_seconds()
        return age <= self.max_quote_age_s

    def _analyze_pair(self, pair: MatchedMarketPair) -> List[SpreadOpportunity]:
        """Analyze a matched pair for spread opportunities."""
        opportunities = []

        try:
            p1_yes, p1_no, p2_yes, p2_no = self.matcher.get_quotes(pair)
        except NotImplementedError:
            return []
        except Exception:
            return []

        # Validate quote freshness - stale quotes cause false positives
        quotes = [p1_yes, p1_no, p2_yes, p2_no]
        if not all(self._is_quote_fresh(q) for q in quotes):
            return []  # Skip stale quotes

        # Check cross-platform arbitrage opportunities
        # Strategy 1: Buy P1 YES + Buy P2 NO (if combined < 1.0 - fees)
        # Strategy 2: Buy P1 NO + Buy P2 YES (if combined < 1.0 - fees)
        # Strategy 3: Buy cheap, sell expensive cross-platform

        opportunities.extend(
            self._check_dutch_book(pair, p1_yes, p2_no, "p1_yes_p2_no")
        )
        opportunities.extend(
            self._check_dutch_book(pair, p1_no, p2_yes, "p1_no_p2_yes")
        )
        opportunities.extend(
            self._check_cross_platform_arb(pair, p1_yes, p1_no, p2_yes, p2_no)
        )

        return opportunities

    def _check_dutch_book(
        self,
        pair: MatchedMarketPair,
        quote_a: MarketQuote,
        quote_b: MarketQuote,
        combo_name: str,
    ) -> List[SpreadOpportunity]:
        """Check for dutch book opportunity (buy both sides < $1)."""
        opportunities = []

        if quote_a.best_ask is None or quote_b.best_ask is None:
            return []

        # Calculate all-in costs
        # Use 100 contracts as reference for fee calculation
        ref_contracts = 100

        cost_a = all_in_buy_cost(quote_a.platform, quote_a.best_ask, ref_contracts)
        cost_b = all_in_buy_cost(quote_b.platform, quote_b.best_ask, ref_contracts)

        combined_cost = cost_a + cost_b
        gross_edge = 1.0 - (quote_a.best_ask + quote_b.best_ask)
        net_edge = 1.0 - combined_cost

        # Check thresholds
        if net_edge < self.min_edge:
            return []

        # Calculate available liquidity
        available_contracts = min(quote_a.ask_size, quote_b.ask_size)
        available_usd = min(quote_a.ask_depth_usd, quote_b.ask_depth_usd)

        if available_usd < self.min_liquidity_usd:
            return []

        total_fees = (cost_a - quote_a.best_ask) + (cost_b - quote_b.best_ask)
        estimated_profit = net_edge * available_contracts

        opp = SpreadOpportunity(
            pair=pair,
            opportunity_type="dutch_book",
            buy_platform=quote_a.platform,
            buy_market_id=quote_a.market_id,
            buy_outcome=quote_a.outcome,
            buy_price=quote_a.best_ask,
            sell_platform=quote_b.platform,
            sell_market_id=quote_b.market_id,
            sell_outcome=quote_b.outcome,
            sell_price=quote_b.best_ask,
            gross_edge_per_contract=gross_edge,
            net_edge_per_contract=net_edge,
            total_fees_per_contract=total_fees,
            max_contracts=available_contracts,
            available_liquidity_usd=available_usd,
            estimated_profit_usd=estimated_profit,
        )
        opportunities.append(opp)

        return opportunities

    def _check_cross_platform_arb(
        self,
        pair: MatchedMarketPair,
        p1_yes: MarketQuote,
        p1_no: MarketQuote,
        p2_yes: MarketQuote,
        p2_no: MarketQuote,
    ) -> List[SpreadOpportunity]:
        """Check for cross-platform arb (buy on one, sell on other)."""
        opportunities = []
        ref_contracts = 100

        # Check if we can buy YES on P1 and sell YES on P2 (or vice versa)
        arb_combos = [
            (p1_yes, p2_yes, "yes"),  # Buy P1 YES, sell P2 YES
            (p2_yes, p1_yes, "yes"),  # Buy P2 YES, sell P1 YES
            (p1_no, p2_no, "no"),  # Buy P1 NO, sell P2 NO
            (p2_no, p1_no, "no"),  # Buy P2 NO, sell P1 NO
        ]

        for buy_quote, sell_quote, outcome in arb_combos:
            if buy_quote.best_ask is None or sell_quote.best_bid is None:
                continue

            buy_cost = all_in_buy_cost(
                buy_quote.platform, buy_quote.best_ask, ref_contracts
            )
            sell_proceeds = all_in_sell_proceeds(
                sell_quote.platform, sell_quote.best_bid, ref_contracts
            )

            gross_edge = sell_quote.best_bid - buy_quote.best_ask
            net_edge = sell_proceeds - buy_cost

            if net_edge < self.min_edge:
                continue

            # Liquidity check
            available_contracts = min(buy_quote.ask_size, sell_quote.bid_size)
            available_usd = min(buy_quote.ask_depth_usd, sell_quote.bid_depth_usd)

            if available_usd < self.min_liquidity_usd:
                continue

            total_fees = (buy_cost - buy_quote.best_ask) + (
                sell_quote.best_bid - sell_proceeds
            )
            estimated_profit = net_edge * available_contracts

            opp = SpreadOpportunity(
                pair=pair,
                opportunity_type="cross_platform_arb",
                buy_platform=buy_quote.platform,
                buy_market_id=buy_quote.market_id,
                buy_outcome=outcome,
                buy_price=buy_quote.best_ask,
                sell_platform=sell_quote.platform,
                sell_market_id=sell_quote.market_id,
                sell_outcome=outcome,
                sell_price=sell_quote.best_bid,
                gross_edge_per_contract=gross_edge,
                net_edge_per_contract=net_edge,
                total_fees_per_contract=total_fees,
                max_contracts=available_contracts,
                available_liquidity_usd=available_usd,
                estimated_profit_usd=estimated_profit,
            )
            opportunities.append(opp)

        return opportunities

    def _create_alert(self, opp: SpreadOpportunity) -> None:
        """Create an alert for an opportunity - called immediately when found."""
        key = self._opportunity_key(opp)

        # Create new alert
        self._alert_counter += 1
        alert_id = f"SPREAD-{self._alert_counter:06d}"

        urgency = self._calculate_urgency(opp)

        alert = SpreadAlert(
            opportunity=opp,
            alert_id=alert_id,
            created_at=datetime.now(),
            urgency_score=urgency,
        )

        self._active_alerts[key] = alert
        self._alert_history.append(alert)

        # Trim history
        if len(self._alert_history) > self.max_alerts:
            self._alert_history = self._alert_history[-self.max_alerts :]

        # Callback - fire immediately!
        if self.on_alert:
            try:
                self.on_alert(alert)
            except Exception:
                pass

    def _calculate_urgency(self, opp: SpreadOpportunity) -> float:
        """Calculate urgency score (0-100) for an opportunity.

        Factors:
        - Edge size (larger = more urgent, more profit per contract)
        - Estimated profit (larger = more urgent, worth the execution risk)
        - Liquidity (more = higher confidence the opportunity is real)
        """
        score = 0.0

        # Edge contribution (0-40 points)
        # 2 cent edge = 16 points, 5+ cent edge = 40 points
        edge_cents = opp.net_edge_per_contract * 100
        edge_score = min(40, edge_cents * 8)
        score += edge_score

        # Profit contribution (0-35 points)
        # $10 profit = 7 points, $50+ profit = 35 points
        profit_score = min(35, opp.estimated_profit_usd * 0.7)
        score += profit_score

        # Liquidity contribution (0-25 points)
        # Higher liquidity = more confidence it's a real, executable opportunity
        # $500 = 6.25 points, $2000+ = 25 points
        liq_score = min(25, opp.available_liquidity_usd / 80)
        score += liq_score

        return max(0, min(100, score))

    def _opportunity_key(self, opp: SpreadOpportunity) -> str:
        """Generate unique key for an opportunity."""
        return (
            f"{opp.pair.pair_id}:"
            f"{opp.opportunity_type}:"
            f"{opp.buy_platform.value}:{opp.buy_outcome}:"
            f"{opp.sell_platform.value}:{opp.sell_outcome}"
        )


# =============================================================================
# Convenience Functions
# =============================================================================


def create_detector(
    market_matcher: Optional[MarketMatcher] = None,
    aggressive: bool = False,
    conservative: bool = False,
) -> SpreadDetector:
    """Create a spread detector with preset configurations.

    Args:
        market_matcher: Your market matcher implementation
        aggressive: Lower thresholds for more alerts (may include noise)
        conservative: Higher thresholds for fewer, higher-confidence alerts

    Returns:
        Configured SpreadDetector instance
    """
    if aggressive:
        return SpreadDetector(
            market_matcher=market_matcher,
            min_edge_cents=1.0,
            min_liquidity_usd=200.0,
            max_quote_age_ms=5000.0,  # Accept slightly older quotes
        )
    elif conservative:
        return SpreadDetector(
            market_matcher=market_matcher,
            min_edge_cents=3.0,
            min_liquidity_usd=1000.0,
            max_quote_age_ms=1000.0,  # Require very fresh quotes
        )
    else:
        return SpreadDetector(market_matcher=market_matcher)

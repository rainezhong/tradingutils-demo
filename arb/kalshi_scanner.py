"""
Kalshi Single-Exchange Spread Scanner

Automatically discovers complementary market pairs on Kalshi (e.g., sports games
where "Team A wins" and "Team B wins" are separate markets) and monitors them
for spread trading opportunities.

Usage:
------
from arb.kalshi_scanner import KalshiSpreadScanner

# Create scanner
scanner = KalshiSpreadScanner(kalshi_client)

# Find complementary pairs
pairs = scanner.find_complementary_pairs()
for pair in pairs:
    print(f"{pair['market_a']['ticker']} <-> {pair['market_b']['ticker']}")
    print(f"  Event: {pair['event_title']}")
    print(f"  Combined ask: ${pair['combined_yes_ask']:.2f}")

# Monitor a specific pair
from arb.live_arb import live_plot_kalshi_pair
monitor, fig, ani = live_plot_kalshi_pair(
    client=kalshi_client,
    ticker_1=pairs[0]['market_a']['ticker'],
    ticker_2=pairs[0]['market_b']['ticker'],
)

# Or use the scanner's built-in monitor
scanner.monitor_pair(pairs[0], plot=True)
"""

import re
import time
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Callable

from .live_arb import (
    LiveArbMonitor,
    live_plot_monitor,
    create_kalshi_poll_func,
    all_in_buy_cost,
    all_in_sell_proceeds,
)


@dataclass
class KalshiMarketInfo:
    """Parsed Kalshi market information."""
    ticker: str
    title: str
    event_ticker: str
    subtitle: str  # e.g., "Lakers" from "Will the Lakers win?"
    yes_bid: Optional[float] = None
    yes_ask: Optional[float] = None
    no_bid: Optional[float] = None
    no_ask: Optional[float] = None
    volume_24h: int = 0
    open_interest: int = 0
    status: str = "open"
    category: Optional[str] = None

    @property
    def has_quotes(self) -> bool:
        return self.yes_ask is not None and self.no_ask is not None


@dataclass
class ComplementaryPair:
    """A pair of Kalshi markets that are complementary (mutually exclusive outcomes)."""
    market_a: KalshiMarketInfo
    market_b: KalshiMarketInfo
    event_ticker: str
    event_title: str
    match_type: str  # 'sports', 'election', 'binary', etc.
    confidence: float = 1.0

    @property
    def combined_yes_ask(self) -> Optional[float]:
        """Cost to buy YES on both markets (should be ~$1 for perfect complements)."""
        if self.market_a.yes_ask is not None and self.market_b.yes_ask is not None:
            return self.market_a.yes_ask + self.market_b.yes_ask
        return None

    @property
    def combined_yes_bid(self) -> Optional[float]:
        """Value if selling YES on both markets."""
        if self.market_a.yes_bid is not None and self.market_b.yes_bid is not None:
            return self.market_a.yes_bid + self.market_b.yes_bid
        return None

    @property
    def dutch_book_edge(self) -> Optional[float]:
        """Edge from buying both YES positions (positive = profitable)."""
        combined = self.combined_yes_ask
        if combined is not None:
            return 1.0 - combined
        return None

    @property
    def overround(self) -> Optional[float]:
        """Overround/vig (combined ask > 1.0 means market maker edge)."""
        combined = self.combined_yes_ask
        if combined is not None:
            return combined - 1.0
        return None

    def to_dict(self) -> dict:
        return {
            "market_a": {
                "ticker": self.market_a.ticker,
                "title": self.market_a.title,
                "subtitle": self.market_a.subtitle,
                "yes_bid": self.market_a.yes_bid,
                "yes_ask": self.market_a.yes_ask,
            },
            "market_b": {
                "ticker": self.market_b.ticker,
                "title": self.market_b.title,
                "subtitle": self.market_b.subtitle,
                "yes_bid": self.market_b.yes_bid,
                "yes_ask": self.market_b.yes_ask,
            },
            "event_ticker": self.event_ticker,
            "event_title": self.event_title,
            "match_type": self.match_type,
            "combined_yes_ask": self.combined_yes_ask,
            "dutch_book_edge": self.dutch_book_edge,
            "confidence": self.confidence,
        }


class KalshiSpreadScanner:
    """
    Scanner for Kalshi single-exchange spread opportunities.

    Automatically discovers complementary market pairs (e.g., sports games)
    and monitors them for arbitrage opportunities.
    """

    # Known sports team patterns
    SPORTS_PATTERNS = [
        # NBA
        r"(Lakers|Celtics|Warriors|Nets|Knicks|Bulls|Heat|Bucks|Suns|76ers|Sixers|"
        r"Nuggets|Grizzlies|Cavaliers|Cavs|Kings|Clippers|Mavericks|Mavs|Hawks|"
        r"Timberwolves|Wolves|Pelicans|Thunder|Trail Blazers|Blazers|Raptors|"
        r"Pacers|Magic|Hornets|Wizards|Pistons|Spurs|Jazz|Rockets)",
        # NFL
        r"(Chiefs|49ers|Eagles|Bills|Cowboys|Ravens|Lions|Dolphins|Bengals|"
        r"Jaguars|Chargers|Texans|Browns|Steelers|Jets|Raiders|Broncos|Seahawks|"
        r"Vikings|Packers|Saints|Giants|Commanders|Panthers|Bears|Falcons|"
        r"Buccaneers|Bucs|Cardinals|Rams|Titans|Colts)",
        # MLB
        r"(Yankees|Dodgers|Braves|Astros|Phillies|Padres|Mariners|Cardinals|"
        r"Mets|Blue Jays|Guardians|Orioles|Rangers|Twins|Rays|Red Sox|"
        r"Brewers|Cubs|Giants|Diamondbacks|D-backs|Rockies|Angels|White Sox|"
        r"Tigers|Royals|Pirates|Reds|Marlins|Nationals|Athletics|A's)",
        # NHL
        r"(Bruins|Panthers|Oilers|Rangers|Avalanche|Stars|Lightning|Hurricanes|"
        r"Jets|Kings|Canucks|Golden Knights|Knights|Kraken|Maple Leafs|Leafs|"
        r"Devils|Wild|Flames|Capitals|Caps|Islanders|Penguins|Pens|Senators|"
        r"Red Wings|Blues|Predators|Ducks|Blackhawks|Sharks|Flyers|Sabres|Coyotes)",
        # Soccer / MLS
        r"(Inter Miami|LAFC|LA Galaxy|Atlanta United|Seattle Sounders|"
        r"Philadelphia Union|Cincinnati|Columbus Crew|Austin FC|Nashville SC)",
        # College
        r"(Alabama|Georgia|Michigan|Ohio State|Texas|USC|LSU|Clemson|Florida|"
        r"Penn State|Oklahoma|Oregon|Notre Dame|Tennessee|Miami|Auburn)",
    ]

    def __init__(
        self,
        kalshi_client,
        min_volume: int = 0,
        min_open_interest: int = 0,
        categories: Optional[List[str]] = None,
    ):
        """
        Initialize the scanner.

        Args:
            kalshi_client: Kalshi API client (KalshiClient or KalshiExchange)
            min_volume: Minimum 24h volume filter
            min_open_interest: Minimum open interest filter
            categories: Filter to specific categories (e.g., ['Sports', 'Politics'])
        """
        self._client = kalshi_client
        self._min_volume = min_volume
        self._min_open_interest = min_open_interest
        self._categories = set(categories) if categories else None

        # Cache
        self._markets_by_event: Dict[str, List[KalshiMarketInfo]] = {}
        self._last_refresh: float = 0
        self._cache_ttl: float = 60.0  # 1 minute cache

        # Compiled patterns
        self._sports_pattern = re.compile(
            "|".join(self.SPORTS_PATTERNS),
            re.IGNORECASE
        )

    def _get_api_client(self):
        """Get the underlying API client."""
        # Handle both KalshiClient and KalshiExchange
        if hasattr(self._client, '_api'):
            return self._client._api
        return self._client

    def refresh_markets(self, force: bool = False) -> int:
        """
        Fetch fresh market data from Kalshi.

        Args:
            force: Force refresh even if cache is valid

        Returns:
            Number of markets fetched
        """
        now = time.time()
        if not force and (now - self._last_refresh) < self._cache_ttl:
            return sum(len(m) for m in self._markets_by_event.values())

        api = self._get_api_client()
        self._markets_by_event.clear()

        # First, fetch open markets to discover simple game tickers from parlay legs
        cursor = None
        simple_game_tickers = set()  # Tickers like KXNBAGAME-26JAN21TORSAC-TOR
        simple_game_events = {}  # Map event -> list of tickers

        while True:
            response = api.get_markets(status="open", limit=100, cursor=cursor)
            markets = response.get("markets", [])

            if not markets:
                break

            for m in markets:
                # Extract simple game tickers from parlay legs
                legs = m.get("mve_selected_legs", [])
                for leg in legs:
                    ticker = leg.get("market_ticker", "")
                    event = leg.get("event_ticker", "")

                    # Look for game winner markets (KXNBAGAME, KXNFLGAME, etc.)
                    if "GAME" in ticker and event:
                        simple_game_tickers.add(ticker)
                        if event not in simple_game_events:
                            simple_game_events[event] = set()
                        simple_game_events[event].add(ticker)

            cursor = response.get("cursor")
            if not cursor:
                break

        # Now fetch the actual simple game markets
        total = 0
        fetched_events = set()

        for event, tickers in simple_game_events.items():
            # Only process events with exactly 2 tickers (team vs team)
            if len(tickers) != 2:
                continue

            if event in fetched_events:
                continue
            fetched_events.add(event)

            for ticker in tickers:
                try:
                    market_data = api.get_market(ticker)
                    m = market_data.get("market", {})
                    market = self._parse_market(m)

                    if market and self._passes_filters(market):
                        if event not in self._markets_by_event:
                            self._markets_by_event[event] = []
                        self._markets_by_event[event].append(market)
                        total += 1
                except Exception:
                    continue

        self._last_refresh = now
        return total

    def _parse_market(self, data: dict) -> Optional[KalshiMarketInfo]:
        """Parse raw API response into KalshiMarketInfo."""
        try:
            # Extract subtitle (team name, candidate name, etc.)
            title = data.get("title", "")
            subtitle = data.get("yes_sub_title") or data.get("subtitle", "")
            if not subtitle:
                # Try to extract from title
                subtitle = self._extract_subtitle(title)

            # Price conversion (Kalshi returns cents, we want dollars)
            yes_bid = data.get("yes_bid")
            yes_ask = data.get("yes_ask")
            no_bid = data.get("no_bid")
            no_ask = data.get("no_ask")

            # Convert cents to dollars if needed (Kalshi API returns cents)
            if yes_bid is not None and yes_bid > 1:
                yes_bid = yes_bid / 100.0
            if yes_ask is not None and yes_ask > 1:
                yes_ask = yes_ask / 100.0
            if no_bid is not None and no_bid > 1:
                no_bid = no_bid / 100.0
            if no_ask is not None and no_ask > 1:
                no_ask = no_ask / 100.0

            return KalshiMarketInfo(
                ticker=data.get("ticker", ""),
                title=title,
                event_ticker=data.get("event_ticker", ""),
                subtitle=subtitle,
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                no_bid=no_bid,
                no_ask=no_ask,
                volume_24h=data.get("volume_24h", 0) or 0,
                open_interest=data.get("open_interest", 0) or 0,
                status=data.get("status", "open"),
                category=data.get("category"),
            )
        except Exception:
            return None

    def _extract_subtitle(self, title: str) -> str:
        """Extract meaningful subtitle from title."""
        # Look for team names
        match = self._sports_pattern.search(title)
        if match:
            return match.group(0)

        # Look for "Will X win/beat/defeat..."
        patterns = [
            r"Will (?:the )?(.+?) (?:win|beat|defeat)",
            r"(.+?) to win",
            r"(.+?) vs\.? ",
        ]
        for pattern in patterns:
            match = re.search(pattern, title, re.IGNORECASE)
            if match:
                return match.group(1).strip()

        return title[:30]

    def _passes_filters(self, market: KalshiMarketInfo) -> bool:
        """Check if market passes configured filters."""
        if market.volume_24h < self._min_volume:
            return False
        if market.open_interest < self._min_open_interest:
            return False
        if self._categories and market.category not in self._categories:
            return False
        if market.status != "open":
            return False
        return True

    def find_complementary_pairs(
        self,
        refresh: bool = True,
        min_confidence: float = 0.5,
    ) -> List[ComplementaryPair]:
        """
        Find all complementary market pairs on Kalshi.

        Args:
            refresh: Whether to refresh market data first
            min_confidence: Minimum match confidence (0.0-1.0)

        Returns:
            List of ComplementaryPair objects
        """
        if refresh:
            self.refresh_markets()

        pairs = []

        for event_ticker, markets in self._markets_by_event.items():
            if len(markets) < 2:
                continue

            # Try to find complementary pairs within this event
            event_pairs = self._find_pairs_in_event(markets, event_ticker)
            pairs.extend([p for p in event_pairs if p.confidence >= min_confidence])

        # Sort by confidence and dutch book edge
        pairs.sort(key=lambda p: (p.confidence, p.dutch_book_edge or 0), reverse=True)

        return pairs

    def _find_pairs_in_event(
        self,
        markets: List[KalshiMarketInfo],
        event_ticker: str,
    ) -> List[ComplementaryPair]:
        """Find complementary pairs within a single event."""
        pairs = []

        # For events with exactly 2 markets, they're likely complements
        if len(markets) == 2:
            m1, m2 = markets
            if self._are_complements(m1, m2):
                pairs.append(ComplementaryPair(
                    market_a=m1,
                    market_b=m2,
                    event_ticker=event_ticker,
                    event_title=self._get_event_title(m1, m2),
                    match_type=self._classify_match_type(m1, m2),
                    confidence=0.95,
                ))
        else:
            # For events with more markets, try to find pairs
            # This handles multi-way events (e.g., "Who will win?" with 3+ teams)
            used = set()
            for i, m1 in enumerate(markets):
                if m1.ticker in used:
                    continue
                for m2 in markets[i+1:]:
                    if m2.ticker in used:
                        continue
                    if self._are_complements(m1, m2):
                        conf = self._calculate_complement_confidence(m1, m2, markets)
                        pairs.append(ComplementaryPair(
                            market_a=m1,
                            market_b=m2,
                            event_ticker=event_ticker,
                            event_title=self._get_event_title(m1, m2),
                            match_type=self._classify_match_type(m1, m2),
                            confidence=conf,
                        ))
                        used.add(m1.ticker)
                        used.add(m2.ticker)
                        break

        return pairs

    def _are_complements(self, m1: KalshiMarketInfo, m2: KalshiMarketInfo) -> bool:
        """Check if two markets are complementary (mutually exclusive)."""
        # Same event is a good indicator
        if m1.event_ticker != m2.event_ticker:
            return False

        # Different subtitles (different teams/outcomes)
        if m1.subtitle.lower() == m2.subtitle.lower():
            return False

        # Check if combined price is reasonable (0.85 - 1.15 range)
        if m1.yes_ask is not None and m2.yes_ask is not None:
            combined = m1.yes_ask + m2.yes_ask
            if combined < 0.70 or combined > 1.30:
                # Prices don't suggest complements
                return False

        return True

    def _calculate_complement_confidence(
        self,
        m1: KalshiMarketInfo,
        m2: KalshiMarketInfo,
        all_markets: List[KalshiMarketInfo],
    ) -> float:
        """Calculate confidence that two markets are complements."""
        confidence = 0.5

        # Combined price close to $1 increases confidence
        if m1.yes_ask is not None and m2.yes_ask is not None:
            combined = m1.yes_ask + m2.yes_ask
            if 0.95 <= combined <= 1.05:
                confidence += 0.3
            elif 0.90 <= combined <= 1.10:
                confidence += 0.2
            elif 0.85 <= combined <= 1.15:
                confidence += 0.1

        # Only 2 markets in event = high confidence
        if len(all_markets) == 2:
            confidence += 0.2

        # Both have similar volume = good sign
        if m1.volume_24h > 0 and m2.volume_24h > 0:
            ratio = min(m1.volume_24h, m2.volume_24h) / max(m1.volume_24h, m2.volume_24h)
            if ratio > 0.5:
                confidence += 0.1

        return min(confidence, 1.0)

    def _classify_match_type(self, m1: KalshiMarketInfo, m2: KalshiMarketInfo) -> str:
        """Classify the type of complementary pair."""
        combined_title = (m1.title + " " + m2.title).lower()

        if any(sport in combined_title for sport in ["nba", "nfl", "mlb", "nhl", "ncaa", "win", "beat"]):
            return "sports"
        if any(word in combined_title for word in ["elect", "president", "senate", "congress", "vote"]):
            return "election"
        if any(word in combined_title for word in ["price", "btc", "eth", "stock", "above", "below"]):
            return "financial"

        return "binary"

    def _get_event_title(self, m1: KalshiMarketInfo, m2: KalshiMarketInfo) -> str:
        """Generate a human-readable event title."""
        if m1.subtitle and m2.subtitle:
            return f"{m1.subtitle} vs {m2.subtitle}"
        return m1.event_ticker

    def find_sports_pairs(self, refresh: bool = True) -> List[ComplementaryPair]:
        """
        Find complementary pairs for sports events specifically.

        Args:
            refresh: Whether to refresh market data first

        Returns:
            List of sports ComplementaryPair objects
        """
        all_pairs = self.find_complementary_pairs(refresh=refresh)
        return [p for p in all_pairs if p.match_type == "sports"]

    def scan_opportunities(
        self,
        pairs: Optional[List[ComplementaryPair]] = None,
        min_edge_cents: float = 0.0,
        contract_size: int = 100,
        entry_maker: bool = False,
    ) -> List[dict]:
        """
        Scan pairs for current spread opportunities.

        Args:
            pairs: Pairs to scan (uses find_complementary_pairs if None)
            min_edge_cents: Minimum edge in cents to report
            contract_size: Contract size for fee calculations
            entry_maker: Whether entry would be maker orders

        Returns:
            List of opportunity dicts sorted by edge
        """
        if pairs is None:
            pairs = self.find_complementary_pairs()

        opportunities = []

        for pair in pairs:
            if not pair.market_a.has_quotes or not pair.market_b.has_quotes:
                continue

            opp = self._analyze_pair(pair, contract_size, entry_maker)
            if opp and opp.get("best_edge_cents", 0) >= min_edge_cents:
                opportunities.append(opp)

        # Sort by edge (highest first)
        opportunities.sort(key=lambda x: x.get("best_edge_cents", 0), reverse=True)

        return opportunities

    def _analyze_pair(
        self,
        pair: ComplementaryPair,
        contract_size: int,
        entry_maker: bool,
    ) -> Optional[dict]:
        """Analyze a single pair for opportunities."""
        m1, m2 = pair.market_a, pair.market_b

        # Calculate all-in costs with fees
        c1_yes = all_in_buy_cost(m1.yes_ask, contract_size, maker=entry_maker)
        c1_no = all_in_buy_cost(m1.no_ask, contract_size, maker=entry_maker) if m1.no_ask else None
        c2_yes = all_in_buy_cost(m2.yes_ask, contract_size, maker=entry_maker)
        c2_no = all_in_buy_cost(m2.no_ask, contract_size, maker=entry_maker) if m2.no_ask else None

        # Dutch book: buy cheapest way to get each exposure
        # Team 1 exposure: YES on m1 OR NO on m2
        t1_cost = c1_yes
        t1_via = f"BUY {m1.ticker} YES @ {m1.yes_ask:.3f}"
        if c2_no is not None and c2_no < t1_cost:
            t1_cost = c2_no
            t1_via = f"BUY {m2.ticker} NO @ {m2.no_ask:.3f}"

        # Team 2 exposure: YES on m2 OR NO on m1
        t2_cost = c2_yes
        t2_via = f"BUY {m2.ticker} YES @ {m2.yes_ask:.3f}"
        if c1_no is not None and c1_no < t2_cost:
            t2_cost = c1_no
            t2_via = f"BUY {m1.ticker} NO @ {m1.no_ask:.3f}"

        combined_cost = t1_cost + t2_cost
        dutch_profit = 1.0 - combined_cost

        # Routing edges (same exposure, different instrument)
        routing_edge_t1 = c1_yes - (c2_no if c2_no else c1_yes)
        routing_edge_t2 = c2_yes - (c1_no if c1_no else c2_yes)

        best_edge = max(dutch_profit, abs(routing_edge_t1), abs(routing_edge_t2))

        return {
            "pair": pair.to_dict(),
            "combined_cost": combined_cost,
            "dutch_profit_per_contract": dutch_profit,
            "best_edge_cents": best_edge * 100,
            "t1_cost": t1_cost,
            "t1_via": t1_via,
            "t2_cost": t2_cost,
            "t2_via": t2_via,
            "routing_edge_t1": routing_edge_t1,
            "routing_edge_t2": routing_edge_t2,
            "recommended_action": self._recommend_action(dutch_profit, routing_edge_t1, routing_edge_t2),
        }

    def _recommend_action(
        self,
        dutch_profit: float,
        routing_edge_t1: float,
        routing_edge_t2: float,
    ) -> str:
        """Generate action recommendation."""
        if dutch_profit > 0.01:  # > 1 cent profit
            return f"DUTCH: Buy both legs, hold to settlement (+${dutch_profit:.4f}/contract)"

        if abs(routing_edge_t1) > 0.005:
            cheaper = "m2 NO" if routing_edge_t1 > 0 else "m1 YES"
            return f"ROUTE: Get Team1 exposure via {cheaper} (saves ${abs(routing_edge_t1):.4f})"

        if abs(routing_edge_t2) > 0.005:
            cheaper = "m1 NO" if routing_edge_t2 > 0 else "m2 YES"
            return f"ROUTE: Get Team2 exposure via {cheaper} (saves ${abs(routing_edge_t2):.4f})"

        return "NO_TRADE: No significant edge"

    def monitor_pair(
        self,
        pair: ComplementaryPair,
        poll_period_ms: int = 500,
        contract_size: int = 100,
        entry_maker: bool = False,
        exit_maker: bool = False,
        min_edge: float = 0.01,
        arb_floor: float = 0.002,
        profit_floor: float = 0.002,
        plot: bool = True,
    ):
        """
        Start monitoring a complementary pair for opportunities.

        Args:
            pair: The ComplementaryPair to monitor
            poll_period_ms: How often to poll prices
            contract_size: Contract size for calculations
            entry_maker: Whether entry orders are maker
            exit_maker: Whether exit orders are maker
            min_edge: Minimum routing edge to print
            arb_floor: Minimum arb PnL to print
            profit_floor: Minimum dutch profit to print
            plot: Whether to show live plot

        Returns:
            (monitor, fig, ani) if plot=True, else just monitor
        """
        api = self._get_api_client()

        poll_func_1 = create_kalshi_poll_func(api, pair.market_a.ticker)
        poll_func_2 = create_kalshi_poll_func(api, pair.market_b.ticker)

        monitor = LiveArbMonitor(
            market_1_poll_func=poll_func_1,
            market_2_poll_func=poll_func_2,
            poll_period_ms=poll_period_ms,
            contract_size=contract_size,
            entry_maker=entry_maker,
            exit_maker=exit_maker,
            min_edge=min_edge,
            arb_floor=arb_floor,
            profit_floor=profit_floor,
        ).start()

        if plot:
            fig, ani = live_plot_monitor(
                monitor,
                market_1_label=pair.market_a.subtitle or pair.market_a.ticker,
                market_2_label=pair.market_b.subtitle or pair.market_b.ticker,
            )
            return monitor, fig, ani

        return monitor

    def print_pairs(self, pairs: Optional[List[ComplementaryPair]] = None, limit: int = 20):
        """Print discovered pairs in a readable format."""
        if pairs is None:
            pairs = self.find_complementary_pairs()

        print(f"\nFound {len(pairs)} complementary pairs:\n")

        for i, pair in enumerate(pairs[:limit], 1):
            edge = pair.dutch_book_edge
            edge_str = f"${edge:.4f}" if edge is not None else "N/A"
            combined = pair.combined_yes_ask
            combined_str = f"${combined:.2f}" if combined is not None else "N/A"

            print(f"[{i}] {pair.event_title}")
            print(f"    {pair.market_a.ticker} ({pair.market_a.subtitle})")
            print(f"    {pair.market_b.ticker} ({pair.market_b.subtitle})")
            print(f"    Type: {pair.match_type}, Confidence: {pair.confidence:.2f}")
            print(f"    Combined ask: {combined_str}, Dutch edge: {edge_str}")
            print()

        if len(pairs) > limit:
            print(f"... and {len(pairs) - limit} more pairs")

    def print_opportunities(
        self,
        opportunities: Optional[List[dict]] = None,
        limit: int = 10,
    ):
        """Print opportunities in a readable format."""
        if opportunities is None:
            opportunities = self.scan_opportunities()

        print(f"\n{'='*70}")
        print("  KALSHI SPREAD OPPORTUNITIES")
        print(f"{'='*70}\n")

        if not opportunities:
            print("  No opportunities found above threshold.\n")
            return

        for i, opp in enumerate(opportunities[:limit], 1):
            pair = opp["pair"]
            print(f"[{i}] {pair['event_title']}")
            print(f"    {pair['market_a']['ticker']} vs {pair['market_b']['ticker']}")
            print(f"    Best edge: {opp['best_edge_cents']:.2f} cents/contract")
            print(f"    Dutch profit: ${opp['dutch_profit_per_contract']:.4f}/contract")
            print(f"    Combined cost: ${opp['combined_cost']:.4f}")
            print(f"    Recommendation: {opp['recommended_action']}")
            print()

    def scan_known_pairs(
        self,
        ticker_pairs: List[Tuple[str, str]],
        delay_seconds: float = 2.0,
    ) -> List[ComplementaryPair]:
        """
        Scan a list of known ticker pairs directly (avoids API-heavy discovery).

        Args:
            ticker_pairs: List of (ticker_a, ticker_b) tuples
            delay_seconds: Delay between API calls to avoid rate limiting

        Returns:
            List of ComplementaryPair objects with current quotes
        """
        import time

        api = self._get_api_client()
        pairs = []

        for ticker_a, ticker_b in ticker_pairs:
            try:
                time.sleep(delay_seconds)
                m1_data = api.get_market(ticker_a).get("market", {})
                time.sleep(delay_seconds)
                m2_data = api.get_market(ticker_b).get("market", {})

                m1 = self._parse_market(m1_data)
                m2 = self._parse_market(m2_data)

                if m1 and m2:
                    event = m1.event_ticker or "unknown"
                    pair = ComplementaryPair(
                        market_a=m1,
                        market_b=m2,
                        event_ticker=event,
                        event_title=f"{m1.subtitle} vs {m2.subtitle}",
                        match_type=self._classify_match_type(m1, m2),
                        confidence=0.95,
                    )
                    pairs.append(pair)
            except Exception as e:
                print(f"  Error fetching {ticker_a}/{ticker_b}: {e}")

        return pairs

    def scan_known_pairs_batch(
        self,
        ticker_pairs: List[Tuple[str, str]],
    ) -> List[ComplementaryPair]:
        """
        Batch scan known ticker pairs using bulk market fetches.

        Instead of 2 API calls per pair, fetches all open markets in 1-2 calls
        and filters to the needed tickers. Much more efficient for many pairs.

        Args:
            ticker_pairs: List of (ticker_a, ticker_b) tuples

        Returns:
            List of ComplementaryPair objects with current quotes
        """
        api = self._get_api_client()

        # Collect all needed tickers
        needed_tickers = {t for pair in ticker_pairs for t in pair}

        if not needed_tickers:
            return []

        # Fetch all open markets with pagination (1-2 API calls typically)
        all_markets: Dict[str, KalshiMarketInfo] = {}
        cursor = None

        while True:
            response = api.get_markets(status="open", limit=100, cursor=cursor)
            markets = response.get("markets", [])

            if not markets:
                break

            for m in markets:
                ticker = m.get("ticker", "")
                if ticker in needed_tickers:
                    parsed = self._parse_market(m)
                    if parsed:
                        all_markets[ticker] = parsed

            # Early exit if we found all needed markets
            if len(all_markets) >= len(needed_tickers):
                break

            cursor = response.get("cursor")
            if not cursor:
                break

        # Build pairs from fetched markets
        pairs = []
        for ticker_a, ticker_b in ticker_pairs:
            m1 = all_markets.get(ticker_a)
            m2 = all_markets.get(ticker_b)

            if m1 and m2:
                event = m1.event_ticker or "unknown"
                pair = ComplementaryPair(
                    market_a=m1,
                    market_b=m2,
                    event_ticker=event,
                    event_title=f"{m1.subtitle} vs {m2.subtitle}",
                    match_type=self._classify_match_type(m1, m2),
                    confidence=0.95,
                )
                pairs.append(pair)

        return pairs


def get_todays_nba_games() -> List[Tuple[str, str]]:
    """
    Get ticker pairs for today's NBA games.

    Returns:
        List of (team_a_ticker, team_b_ticker) tuples
    """
    # Pattern: KXNBAGAME-{DD}{MON}{YY}{AWAY}{HOME}-{TEAM}
    return [
        ("KXNBAGAME-26JAN21TORSAC-TOR", "KXNBAGAME-26JAN21TORSAC-SAC"),
        ("KXNBAGAME-26JAN22DENWAS-DEN", "KXNBAGAME-26JAN22DENWAS-WAS"),
        ("KXNBAGAME-26JAN21OKCMIL-OKC", "KXNBAGAME-26JAN21OKCMIL-MIL"),
    ]


def get_todays_nhl_games() -> List[Tuple[str, str]]:
    """Get ticker pairs for today's NHL games."""
    # Pattern: KXNHLGAME-{DD}{MON}{YY}{AWAY}{HOME}-{TEAM}
    return [
        ("KXNHLGAME-26JAN21ANACOL-COL", "KXNHLGAME-26JAN21ANACOL-ANA"),
        ("KXNHLGAME-26JAN21NYISEA-SEA", "KXNHLGAME-26JAN21NYISEA-NYI"),
    ]


def get_todays_college_basketball() -> List[Tuple[str, str]]:
    """Get ticker pairs for today's college basketball games."""
    # Pattern: KXNCAAMBGAME-{DD}{MON}{YY}{AWAY}{HOME}-{TEAM}
    return [
        ("KXNCAAMBGAME-26JAN21CINARIZ-ARIZ", "KXNCAAMBGAME-26JAN21CINARIZ-CIN"),
        ("KXNCAAMBGAME-26JAN21ORSTSMC-ORST", "KXNCAAMBGAME-26JAN21ORSTSMC-SMC"),
        ("KXNCAAMBGAME-26JAN21FRESUNM-UNM", "KXNCAAMBGAME-26JAN21FRESUNM-FRES"),
        ("KXNCAAMBGAME-26JAN21UNIILST-ILST", "KXNCAAMBGAME-26JAN21UNIILST-UNI"),
        ("KXNCAAMBGAME-26JAN21PEPPGONZ-GONZ", "KXNCAAMBGAME-26JAN21PEPPGONZ-PEPP"),
        ("KXNCAAMBGAME-26JAN21WASHNEB-NEB", "KXNCAAMBGAME-26JAN21WASHNEB-WASH"),
    ]


def get_nfl_playoffs() -> List[Tuple[str, str]]:
    """Get ticker pairs for NFL playoff/championship games."""
    return [
        # AFC Championship
        ("KXNFLAFCCHAMP-25-NE", "KXNFLAFCCHAMP-25-DEN"),
        # NFC Championship
        ("KXNFLNFCCHAMP-25-SEA", "KXNFLNFCCHAMP-25-LA"),
    ]


def discover_complementary_pairs(kalshi_client, max_pages: int = 3, delay: float = 0.5) -> List[Tuple[str, str]]:
    """
    Discover complementary pairs by scanning parlay market references.

    This is slower but finds all current pairs automatically.

    Args:
        kalshi_client: Kalshi API client
        max_pages: Maximum pages to fetch (to limit rate limiting)
        delay: Delay between API calls

    Returns:
        List of (ticker_a, ticker_b) tuples for complementary markets
    """
    import time
    from collections import defaultdict

    # Get underlying API client
    api = kalshi_client._api if hasattr(kalshi_client, '_api') else kalshi_client

    all_events = defaultdict(set)
    cursor = None
    pages = 0

    while pages < max_pages:
        response = api.get_markets(status='open', limit=100, cursor=cursor)
        markets = response.get('markets', [])
        if not markets:
            break

        for m in markets:
            legs = m.get('mve_selected_legs', [])
            for leg in legs:
                ticker = leg.get('market_ticker', '')
                event = leg.get('event_ticker', '')
                if ticker and event:
                    all_events[event].add(ticker)

        cursor = response.get('cursor')
        pages += 1
        if not cursor:
            break
        time.sleep(delay)

    # Find events with exactly 2 markets
    pairs = []
    for event, tickers in all_events.items():
        if len(tickers) == 2:
            t = sorted(list(tickers))
            pairs.append((t[0], t[1]))

    return pairs


def get_all_known_pairs() -> List[Tuple[str, str]]:
    """
    Get all known complementary pairs across all market types.

    Returns:
        Combined list of all known ticker pairs
    """
    pairs = []
    pairs.extend(get_todays_nba_games())
    pairs.extend(get_todays_nhl_games())
    pairs.extend(get_todays_college_basketball())
    pairs.extend(get_nfl_playoffs())
    return pairs


def quick_scan(kalshi_client, ticker_pairs: List[Tuple[str, str]] = None) -> List[ComplementaryPair]:
    """
    Quick scan using known ticker pairs (avoids discovery rate limits).

    Args:
        kalshi_client: Kalshi API client
        ticker_pairs: List of ticker pairs to check, or None for all known pairs

    Returns:
        List of ComplementaryPair objects
    """
    if ticker_pairs is None:
        ticker_pairs = get_all_known_pairs()

    scanner = KalshiSpreadScanner(kalshi_client)
    return scanner.scan_known_pairs(ticker_pairs, delay_seconds=2.0)


def full_scan(kalshi_client, max_pages: int = 5) -> List[ComplementaryPair]:
    """
    Full scan that discovers pairs automatically (slower, may hit rate limits).

    Args:
        kalshi_client: Kalshi API client
        max_pages: Max pages to scan for discovery

    Returns:
        List of ComplementaryPair objects
    """
    print("Discovering complementary pairs...")
    pairs = discover_complementary_pairs(kalshi_client, max_pages=max_pages)
    print(f"Found {len(pairs)} potential pairs")

    scanner = KalshiSpreadScanner(kalshi_client)
    return scanner.scan_known_pairs(pairs, delay_seconds=2.5)

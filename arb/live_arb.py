"""
Live Arbitrage Monitor for Prediction Markets

This module provides tools for monitoring and visualizing arbitrage opportunities
across complementary prediction market instruments (e.g., YES/NO on Team A vs Team B).

Usage:
------
# For Kalshi markets (quick start):
from arb.live_arb import live_plot_kalshi_pair

monitor, fig, ani = live_plot_kalshi_pair(
    client=kalshi_client,
    ticker_1="MARKET-TEAMA",
    ticker_2="MARKET-TEAMB",
    poll_period_ms=500,
    contract_size=100,
    entry_maker=False,  # False = taker (crossing spread)
    exit_maker=False,
    min_edge=0.01,      # Print routing edges >= 1 cent
    arb_floor=0.002,    # Print arb opportunities >= 0.2 cents
    profit_floor=0.002, # Print dutch-to-settle >= 0.2 cents
)

# Later, stop monitoring:
monitor.stop()

# For custom exchanges (define your own poll functions):
from arb.live_arb import LiveArbMonitor, live_plot_monitor

def poll_market_1():
    # Fetch data from your exchange/API
    return {
        "name": "Team A",
        "yes_ask": 0.45,  # Best ask price for YES contracts
        "no_ask": 0.56,   # Best ask price for NO contracts
        "yes_bid": 0.43,  # Best bid price for YES contracts (optional)
        "no_bid": 0.54,   # Best bid price for NO contracts (optional)
    }

def poll_market_2():
    return {"name": "Team B", "yes_ask": 0.44, "no_ask": 0.57, ...}

monitor = LiveArbMonitor(
    market_1_poll_func=poll_market_1,
    market_2_poll_func=poll_market_2,
    poll_period_ms=500,
    contract_size=100,
    entry_maker=False,
    exit_maker=False,
).start()

fig, ani = live_plot_monitor(monitor, "Market 1", "Market 2")

Features:
---------
- Real-time monitoring of routing edges (cheaper way to get same exposure)
- Cross-market arbitrage detection (buy cheap + sell expensive simultaneously)
- Dutch book / hold-to-settlement profit calculations
- Live matplotlib visualization with dual y-axes (PnL + fees)
- Kalshi fee calculations (maker/taker aware)
- Thread-safe data collection with configurable history
"""

import time, math, threading
from collections import deque
from datetime import datetime

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation

class LiveArbMonitor:
    """
    Docstring for LiveArbMonitor

    :param market_1_poll_func: Function to poll the first market; must return (Team 1 price, Team 2 price)
    :param market_2_poll_func: Function to poll the second market; must return (Team 1 price, Team 2 price)
    """
    def __init__(
        self,
        market_1_poll_func,
        market_2_poll_func,
        poll_period_ms: int,
        contract_size: float,
        entry_maker: bool = False,
        exit_maker: bool = False, 
        min_edge: float = 0.01,
        arb_floor: float = 0.002,    # only print arb if pnl >= this ($/contract)
        profit_floor: float = 0.002, # only print dutch-to-settle if profit >= this
        history: int = 600,
    ):
        self.m1_poll_func = market_1_poll_func
        self.m2_poll_func = market_2_poll_func

        self.poll_period_s = poll_period_ms / 1000.0
        self.C = contract_size
        self.entry_maker = entry_maker
        self.exit_maker = exit_maker
        self.min_edge = min_edge
        self.arb_floor = arb_floor
        self.profit_floor = profit_floor

        self.lock = threading.Lock()
        self.stop_evt = threading.Event()
        self.t0 = time.time()
 
        self.x = deque(maxlen=history)

        # routing edges (entry-cost only; still useful)
        self.edge_t1 = deque(maxlen=history)
        self.edge_t2 = deque(maxlen=history)

        # “dutch to settle” profit (entry only)
        self.profit_to_settle = deque(maxlen=history)

        # cross-market arb pnl per contract (buy cheap exposure, sell expensive exposure)
        self.arb_pnl_t1 = deque(maxlen=history)
        self.arb_pnl_t2 = deque(maxlen=history)

        # entry fees per contract (at asks)
        self.f_t1_yes = deque(maxlen=history)
        self.f_t1_no  = deque(maxlen=history)
        self.f_t2_yes = deque(maxlen=history)
        self.f_t2_no  = deque(maxlen=history)

        # best-action tracking
        self.best_kind = "NO_TRADE"
        self.best_since = time.time()

        self.last_meta = {
            "t1_name": "Team 1", "t2_name": "Team 2",
            "t1_yes_a": None, "t1_no_a": None, "t2_yes_a": None, "t2_no_a": None,
            "t1_yes_b": None, "t1_no_b": None, "t2_yes_b": None, "t2_no_b": None,
            "arb_leg_t1_buy": "", "arb_leg_t1_sell": "",
            "arb_leg_t2_buy": "", "arb_leg_t2_sell": "",
            "best_action": "NO_TRADE",
            "best_value": 0.0,
            "best_age_s": 0.0,
        }

        self.last_sig = {"t1": None, "t2": None, "dutch": None}
        self.th = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.th.start()
        return self

    def stop(self):
        self.stop_evt.set()
        self.th.join(timeout=2.0)

    def _run(self):
        while not self.stop_evt.is_set():
            try:
                # Poll both markets
                m1_data = self.m1_poll_func()
                m2_data = self.m2_poll_func()

                # Extract market data (each function should return a dict with required fields)
                t1_name = m1_data.get("name", "Team 1")
                t2_name = m2_data.get("name", "Team 2")

                # asks
                t1_yes_a = _to_float(m1_data.get("yes_ask"))
                t1_no_a = _to_float(m1_data.get("no_ask"))
                t2_yes_a = _to_float(m2_data.get("yes_ask"))
                t2_no_a = _to_float(m2_data.get("no_ask"))

                # bids
                t1_yes_b = _to_float(m1_data.get("yes_bid"))
                t1_no_b = _to_float(m1_data.get("no_bid"))
                t2_yes_b = _to_float(m2_data.get("yes_bid"))
                t2_no_b = _to_float(m2_data.get("no_bid"))

                if any(v is None for v in [t1_yes_a, t1_no_a, t2_yes_a, t2_no_a]):
                    time.sleep(self.poll_period_s)
                    continue

                # ----- entry all-in costs (per contract) -----
                c_t1_yes = all_in_buy_cost(t1_yes_a, self.C, maker=self.entry_maker)
                c_t1_no = all_in_buy_cost(t1_no_a, self.C, maker=self.entry_maker)
                c_t2_yes = all_in_buy_cost(t2_yes_a, self.C, maker=self.entry_maker)
                c_t2_no = all_in_buy_cost(t2_no_a, self.C, maker=self.entry_maker)

                # routing edges (same exposure, different instrument)
                edge_t1 = c_t1_yes - c_t2_no  # Team 1 exposure: YES t1 vs NO t2
                edge_t2 = c_t2_yes - c_t1_no  # Team 2 exposure: YES t2 vs NO t1

                # dutch-to-settle
                t1_best = min(c_t1_yes, c_t2_no)
                t2_best = min(c_t2_yes, c_t1_no)
                profit_to_settle = 1.0 - (t1_best + t2_best)

                # ----- cross-market arb PnL (buy cheap exposure, sell expensive exposure) -----
                arb_pnl_t1 = None
                arb_buy_t1 = arb_sell_t1 = ""

                if (t1_yes_b is not None) and (t2_no_b is not None):
                    buy_candidates = [
                        ("BUY YES", "m1", t1_yes_a, c_t1_yes),
                        ("BUY NO", "m2", t2_no_a, c_t2_no),
                    ]
                    sell_candidates = [
                        ("SELL YES", "m1", t1_yes_b, all_in_sell_proceeds(t1_yes_b, self.C, maker=self.exit_maker)),
                        ("SELL NO", "m2", t2_no_b, all_in_sell_proceeds(t2_no_b, self.C, maker=self.exit_maker)),
                    ]
                    buy_leg = min(buy_candidates, key=lambda x: x[3])
                    sell_leg = max(sell_candidates, key=lambda x: x[3])
                    arb_pnl_t1 = sell_leg[3] - buy_leg[3]
                    arb_buy_t1 = f"{buy_leg[0]} {buy_leg[1]} @ {buy_leg[2]:.3f}"
                    arb_sell_t1 = f"{sell_leg[0]} {sell_leg[1]} @ {sell_leg[2]:.3f}"

                arb_pnl_t2 = None
                arb_buy_t2 = arb_sell_t2 = ""

                if (t2_yes_b is not None) and (t1_no_b is not None):
                    buy_candidates = [
                        ("BUY YES", "m2", t2_yes_a, c_t2_yes),
                        ("BUY NO", "m1", t1_no_a, c_t1_no),
                    ]
                    sell_candidates = [
                        ("SELL YES", "m2", t2_yes_b, all_in_sell_proceeds(t2_yes_b, self.C, maker=self.exit_maker)),
                        ("SELL NO", "m1", t1_no_b, all_in_sell_proceeds(t1_no_b, self.C, maker=self.exit_maker)),
                    ]
                    buy_leg = min(buy_candidates, key=lambda x: x[3])
                    sell_leg = max(sell_candidates, key=lambda x: x[3])
                    arb_pnl_t2 = sell_leg[3] - buy_leg[3]
                    arb_buy_t2 = f"{buy_leg[0]} {buy_leg[1]} @ {buy_leg[2]:.3f}"
                    arb_sell_t2 = f"{sell_leg[0]} {sell_leg[1]} @ {sell_leg[2]:.3f}"

                # ----- choose best action NOW (this tick) -----
                candidates = []

                if (arb_pnl_t1 is not None) and (arb_pnl_t1 >= self.arb_floor):
                    candidates.append(("ARB_T1", arb_pnl_t1, f"{arb_buy_t1} ; {arb_sell_t1}"))

                if (arb_pnl_t2 is not None) and (arb_pnl_t2 >= self.arb_floor):
                    candidates.append(("ARB_T2", arb_pnl_t2, f"{arb_buy_t2} ; {arb_sell_t2}"))

                if profit_to_settle >= self.profit_floor:
                    candidates.append(("DUTCH_SETTLE", profit_to_settle, "Buy both cheapest legs; hold to settlement"))

                now = time.time()
                if candidates:
                    best_kind, best_val, best_detail = max(candidates, key=lambda t: t[1])
                    best_action_str = f"{best_kind}: {best_detail}"
                else:
                    best_kind, best_val, best_action_str = "NO_TRADE", 0.0, "NO_TRADE: No action clears thresholds"

                if best_kind != self.best_kind:
                    self.best_kind = best_kind
                    self.best_since = now
                best_age_s = now - self.best_since

                with self.lock:
                    self.x.append(now - self.t0)
                    self.edge_t1.append(edge_t1)
                    self.edge_t2.append(edge_t2)
                    self.profit_to_settle.append(profit_to_settle)

                    self.arb_pnl_t1.append(arb_pnl_t1 if arb_pnl_t1 is not None else float("nan"))
                    self.arb_pnl_t2.append(arb_pnl_t2 if arb_pnl_t2 is not None else float("nan"))

                    self.f_t1_yes.append(fee_per_contract(self.C, t1_yes_a, maker=self.entry_maker))
                    self.f_t1_no.append(fee_per_contract(self.C, t1_no_a, maker=self.entry_maker))
                    self.f_t2_yes.append(fee_per_contract(self.C, t2_yes_a, maker=self.entry_maker))
                    self.f_t2_no.append(fee_per_contract(self.C, t2_no_a, maker=self.entry_maker))

                    self.last_meta.update({
                        "t1_name": t1_name, "t2_name": t2_name,
                        "t1_yes_a": t1_yes_a, "t1_no_a": t1_no_a, "t2_yes_a": t2_yes_a, "t2_no_a": t2_no_a,
                        "t1_yes_b": t1_yes_b, "t1_no_b": t1_no_b, "t2_yes_b": t2_yes_b, "t2_no_b": t2_no_b,
                        "arb_leg_t1_buy": arb_buy_t1, "arb_leg_t1_sell": arb_sell_t1,
                        "arb_leg_t2_buy": arb_buy_t2, "arb_leg_t2_sell": arb_sell_t2,
                        "best_action": best_action_str,
                        "best_value": float(best_val),
                        "best_age_s": float(best_age_s),
                    })

                # ----- prints: what to do -----
                ts = datetime.now().strftime("%H:%M:%S")

                if abs(edge_t1) >= self.min_edge:
                    sig = (f"[{ts}] {t1_name} exposure cheaper via "
                           f"{'BUY NO m2' if edge_t1 > 0 else 'BUY YES m1'} "
                           f"(edge={abs(edge_t1):.4f} $/contract, fees incl.)")
                    if sig != self.last_sig["t1"]:
                        print(sig)
                        self.last_sig["t1"] = sig

                if abs(edge_t2) >= self.min_edge:
                    sig = (f"[{ts}] {t2_name} exposure cheaper via "
                           f"{'BUY NO m1' if edge_t2 > 0 else 'BUY YES m2'} "
                           f"(edge={abs(edge_t2):.4f} $/contract, fees incl.)")
                    if sig != self.last_sig["t2"]:
                        print(sig)
                        self.last_sig["t2"] = sig

                if (arb_pnl_t1 is not None) and (arb_pnl_t1 >= self.arb_floor):
                    print(f"[{ts}] ARB {t1_name} exposure: {arb_buy_t1} ; {arb_sell_t1} "
                          f"=> pnl≈{arb_pnl_t1:.4f} $/contract (fees entry+exit incl.)")

                if (arb_pnl_t2 is not None) and (arb_pnl_t2 >= self.arb_floor):
                    print(f"[{ts}] ARB {t2_name} exposure: {arb_buy_t2} ; {arb_sell_t2} "
                          f"=> pnl≈{arb_pnl_t2:.4f} $/contract (fees entry+exit incl.)")

                if profit_to_settle >= self.profit_floor:
                    print(f"[{ts}] DUTCH-to-settle: profit≈{profit_to_settle:.4f} $/contract "
                          f"(assumes teams are perfect complements)")

            except Exception as e:
                print(f"[poll error] {e}")

            time.sleep(self.poll_period_s)


# ---------- Helper functions ----------
def _to_float(x):
    """Convert value to float, return None if not possible."""
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _round_up_cent(x: float) -> float:
    """Round up to nearest cent."""
    return math.ceil(x * 100.0) / 100.0


def kalshi_fee_total(C: int, P: float, maker: bool = False) -> float:
    """Calculate total Kalshi fee for a position.
    
    Args:
        C: Number of contracts
        P: Price in dollars
        maker: Whether this is a maker order (lower fees)
    
    Returns:
        Total fee in dollars
    """
    rate = 0.0175 if maker else 0.07
    return _round_up_cent(rate * C * P * (1.0 - P))


def fee_per_contract(C: int, P: float, maker: bool = False) -> float:
    """Calculate fee per contract."""
    return kalshi_fee_total(C, P, maker=maker) / C


def all_in_buy_cost(P_ask: float, C: int, maker: bool = False) -> float:
    """Calculate all-in cost per contract to buy at ask, including fees."""
    return P_ask + fee_per_contract(C, P_ask, maker=maker)


def all_in_sell_proceeds(P_bid: float, C: int, maker: bool = False) -> float:
    """Calculate net proceeds per contract from selling at bid, after fees."""
    return P_bid - fee_per_contract(C, P_bid, maker=maker)


# ---------- Plotting function ----------
def live_plot_monitor(
    monitor: LiveArbMonitor,
    market_1_label: str = "Market 1",
    market_2_label: str = "Market 2",
    refresh_ms: int = 250,
):
    """Create live matplotlib plot for a LiveArbMonitor.
    
    Args:
        monitor: The LiveArbMonitor instance to visualize
        market_1_label: Label for the first market
        market_2_label: Label for the second market
        refresh_ms: Plot refresh interval in milliseconds
    
    Returns:
        tuple: (figure, animation) - Keep references to prevent garbage collection
    """
    fig, ax = plt.subplots(figsize=(12, 7))

    # left axis: profitability signals
    (l_edge_t1,) = ax.plot([], [], label=f"{market_1_label} routing edge")
    (l_edge_t2,) = ax.plot([], [], label=f"{market_2_label} routing edge")
    (l_arb_t1,) = ax.plot([], [], label=f"ARB pnl {market_1_label}")
    (l_arb_t2,) = ax.plot([], [], label=f"ARB pnl {market_2_label}")
    (l_dutch,) = ax.plot([], [], label="profit_to_settle")

    ax.axhline(0.0, color='gray', linestyle='-', alpha=0.3)
    ax.axhline(monitor.arb_floor, color='green', linestyle='--', alpha=0.3)
    ax.axhline(monitor.profit_floor, color='blue', linestyle='--', alpha=0.3)

    ax.set_xlabel("seconds")
    ax.set_ylabel("$/contract (fees included)")
    title = ax.set_title("Starting...")

    # overlay text for best action + age
    action_text = ax.text(
        0.02, 0.02, "",
        transform=ax.transAxes,
        va="bottom", ha="left",
        bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5),
        fontfamily='monospace',
    )

    # right axis: entry fees at asks
    axf = ax.twinx()
    (f1,) = axf.plot([], [], linestyle="--", alpha=0.6, label=f"fee {market_1_label} YES@ask")
    (f2,) = axf.plot([], [], linestyle="--", alpha=0.6, label=f"fee {market_1_label} NO@ask")
    (f3,) = axf.plot([], [], linestyle="--", alpha=0.6, label=f"fee {market_2_label} YES@ask")
    (f4,) = axf.plot([], [], linestyle="--", alpha=0.6, label=f"fee {market_2_label} NO@ask")
    axf.set_ylabel("fee per contract ($)")

    leg_left = ax.legend(loc="upper left")
    leg_right = axf.legend(loc="upper right")
    last_names = {"t1": None, "t2": None}

    def _update(_frame):
        nonlocal leg_left

        with monitor.lock:
            xs = list(monitor.x)
            e_t1 = list(monitor.edge_t1)
            e_t2 = list(monitor.edge_t2)
            a_t1 = list(monitor.arb_pnl_t1)
            a_t2 = list(monitor.arb_pnl_t2)
            dutch = list(monitor.profit_to_settle)

            ff1 = list(monitor.f_t1_yes)
            ff2 = list(monitor.f_t1_no)
            ff3 = list(monitor.f_t2_yes)
            ff4 = list(monitor.f_t2_no)

            meta = dict(monitor.last_meta)

        if not xs:
            return (l_edge_t1, l_edge_t2, l_arb_t1, l_arb_t2, l_dutch, f1, f2, f3, f4, title, action_text, leg_left, leg_right)

        l_edge_t1.set_data(xs, e_t1)
        l_edge_t2.set_data(xs, e_t2)
        l_arb_t1.set_data(xs, a_t1)
        l_arb_t2.set_data(xs, a_t2)
        l_dutch.set_data(xs, dutch)

        f1.set_data(xs, ff1)
        f2.set_data(xs, ff2)
        f3.set_data(xs, ff3)
        f4.set_data(xs, ff4)

        ax.set_xlim(xs[0], xs[-1])

        # autoscale left axis
        ys = [v for series in (e_t1, e_t2, a_t1, a_t2, dutch)
              for v in series
              if (v is not None and not (isinstance(v, float) and math.isnan(v)))]
        if ys:
            ymin = min(min(ys), 0.0) - 0.01
            ymax = max(max(ys), monitor.arb_floor, monitor.profit_floor, 0.0) + 0.01
            ax.set_ylim(ymin, ymax)

        # autoscale fee axis (stable)
        if ff1 and ff2 and ff3 and ff4:
            fmin = min(ff1[-1], ff2[-1], ff3[-1], ff4[-1])
            fmax = max(ff1[-1], ff2[-1], ff3[-1], ff4[-1])
            axf.set_ylim(max(0.0, fmin - 0.001), fmax + 0.001)

        # update legend labels to include team names
        if meta["t1_name"] != last_names["t1"] or meta["t2_name"] != last_names["t2"]:
            l_edge_t1.set_label(f"{meta['t1_name']} routing edge")
            l_edge_t2.set_label(f"{meta['t2_name']} routing edge")
            l_arb_t1.set_label(f"ARB pnl {meta['t1_name']}")
            l_arb_t2.set_label(f"ARB pnl {meta['t2_name']}")

            leg_left.remove()
            leg_left = ax.legend(loc="upper left")
            last_names["t1"] = meta["t1_name"]
            last_names["t2"] = meta["t2_name"]

        # Update title with current prices
        t1_yes_b = meta['t1_yes_b'] if meta['t1_yes_b'] is not None else float('nan')
        t2_yes_b = meta['t2_yes_b'] if meta['t2_yes_b'] is not None else float('nan')
        
        title.set_text(
            f"{meta['t1_name']} YES a/b={meta['t1_yes_a']:.3f}/{t1_yes_b:.3f} | "
            f"{meta['t2_name']} YES a/b={meta['t2_yes_a']:.3f}/{t2_yes_b:.3f}"
        )

        action_text.set_text(
            f"Best action: {meta.get('best_action', '')}\n"
            f"Value: {meta.get('best_value', 0.0):.4f} $/contract | "
            f"Best-for: {meta.get('best_age_s', 0.0):.1f}s"
        )

        return (l_edge_t1, l_edge_t2, l_arb_t1, l_arb_t2, l_dutch, f1, f2, f3, f4, title, action_text, leg_left, leg_right)

    ani = FuncAnimation(fig, _update, interval=refresh_ms, blit=False, cache_frame_data=False)
    plt.show()
    return fig, ani


# ---------- Kalshi-specific wrapper ----------
def create_kalshi_poll_func(client, ticker: str):
    """Create a polling function for Kalshi markets.
    
    Args:
        client: Kalshi client instance
        ticker: Market ticker to poll
    
    Returns:
        Callable that returns a dict with market data
    """
    def poll():
        m = client.get_market(ticker)
        d = m.market.model_dump()
        return {
            "name": d.get("yes_sub_title", ticker),
            "yes_ask": d.get("yes_ask_dollars"),
            "no_ask": d.get("no_ask_dollars"),
            "yes_bid": d.get("yes_bid_dollars"),
            "no_bid": d.get("no_bid_dollars"),
        }
    return poll


def live_plot_kalshi_pair(
    client,
    ticker_1: str,
    ticker_2: str,
    poll_period_ms: int = 500,
    contract_size: int = 100,
    entry_maker: bool = False,
    exit_maker: bool = False,
    min_edge: float = 0.01,
    arb_floor: float = 0.002,
    profit_floor: float = 0.002,
    history: int = 600,
    refresh_ms: int = 250,
):
    """Create and start a live monitor for a pair of Kalshi markets.

    Args:
        client: Kalshi client instance
        ticker_1: First market ticker
        ticker_2: Second market ticker
        poll_period_ms: Polling interval in milliseconds
        contract_size: Number of contracts for fee calculations
        entry_maker: Whether entry orders are maker orders
        exit_maker: Whether exit orders are maker orders
        min_edge: Minimum edge for routing prints
        arb_floor: Minimum PnL to print arbitrage opportunities
        profit_floor: Minimum profit to print dutch-to-settle
        history: Number of data points to keep in memory
        refresh_ms: Plot refresh interval in milliseconds

    Returns:
        tuple: (monitor, figure, animation)
    """
    poll_func_1 = create_kalshi_poll_func(client, ticker_1)
    poll_func_2 = create_kalshi_poll_func(client, ticker_2)

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
        history=history,
    ).start()

    fig, ani = live_plot_monitor(
        monitor,
        market_1_label=ticker_1,
        market_2_label=ticker_2,
        refresh_ms=refresh_ms,
    )

    return monitor, fig, ani


# =============================================================================
# Cross-Platform Market Matching Integration
# =============================================================================


class CrossPlatformMonitor:
    """Monitor for cross-platform arbitrage using automated market matching.

    This integrates with the market matching system in src/matching/ to automatically
    find equivalent markets across Kalshi and Polymarket, then monitor them for
    arbitrage opportunities.

    Usage:
    ------
    from arb.live_arb import CrossPlatformMonitor

    # Create monitor with both platform clients
    monitor = CrossPlatformMonitor(
        kalshi_client=kalshi_client,
        poly_client=poly_client,
        min_confidence=0.75,
        poll_interval_ms=1000,
    )

    # Start monitoring
    monitor.start()

    # Get current matched pairs
    pairs = monitor.get_matched_pairs()

    # Get arbitrage opportunities
    opportunities = monitor.get_opportunities()

    # Stop monitoring
    monitor.stop()
    """

    def __init__(
        self,
        kalshi_client=None,
        poly_client=None,
        min_confidence: float = 0.75,
        poll_interval_ms: int = 1000,
        market_refresh_interval_s: int = 300,  # Refresh market list every 5 minutes
        on_opportunity=None,  # Callback when opportunity found
    ):
        """Initialize the cross-platform monitor.

        Args:
            kalshi_client: Kalshi API client
            poly_client: Polymarket API client
            min_confidence: Minimum match confidence to monitor (0.0-1.0)
            poll_interval_ms: How often to poll prices (milliseconds)
            market_refresh_interval_s: How often to refresh market list (seconds)
            on_opportunity: Callback function(opportunity_dict) when arb found
        """
        self.kalshi_client = kalshi_client
        self.poly_client = poly_client
        self.min_confidence = min_confidence
        self.poll_interval_s = poll_interval_ms / 1000.0
        self.market_refresh_interval_s = market_refresh_interval_s
        self.on_opportunity = on_opportunity

        # State
        self._matched_pairs = []
        self._opportunities = []
        self._last_market_refresh = 0

        # Threading
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._thread = None

        # Import matcher lazily to avoid circular imports
        self._matcher = None

    def _get_matcher(self):
        """Lazy load the market matcher."""
        if self._matcher is None:
            try:
                from src.matching import MarketMatcher, MatcherConfig
                config = MatcherConfig(
                    min_confidence=self.min_confidence,
                    use_semantic_similarity=False,  # Faster without embeddings
                )
                self._matcher = MarketMatcher(config)
            except ImportError as e:
                print(f"[CrossPlatformMonitor] Failed to import matcher: {e}")
                raise
        return self._matcher

    def start(self):
        """Start the monitoring loop."""
        if self._thread is not None and self._thread.is_alive():
            return self

        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        """Stop the monitoring loop."""
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _run(self):
        """Main monitoring loop."""
        while not self._stop_evt.is_set():
            try:
                # Refresh market list periodically
                now = time.time()
                if now - self._last_market_refresh >= self.market_refresh_interval_s:
                    self._refresh_markets()
                    self._last_market_refresh = now

                # Check prices on matched pairs
                self._check_opportunities()

            except Exception as e:
                print(f"[CrossPlatformMonitor] Error: {e}")

            time.sleep(self.poll_interval_s)

    def _refresh_markets(self):
        """Refresh the list of matched markets."""
        if not self.kalshi_client or not self.poly_client:
            print("[CrossPlatformMonitor] Clients not configured")
            return

        try:
            # Fetch markets from both platforms
            kalshi_markets = self._fetch_kalshi_markets()
            poly_markets = self._fetch_poly_markets()

            # Match markets
            matcher = self._get_matcher()
            matches = matcher.match_markets(
                kalshi_markets,
                poly_markets,
                min_confidence=self.min_confidence
            )

            with self._lock:
                self._matched_pairs = matches

            print(f"[CrossPlatformMonitor] Found {len(matches)} matched pairs")

        except Exception as e:
            print(f"[CrossPlatformMonitor] Failed to refresh markets: {e}")

    def _fetch_kalshi_markets(self):
        """Fetch active markets from Kalshi."""
        try:
            # This depends on your Kalshi client implementation
            response = self.kalshi_client.get_markets()
            if hasattr(response, 'markets'):
                return [m.model_dump() if hasattr(m, 'model_dump') else m
                        for m in response.markets]
            return response if isinstance(response, list) else []
        except Exception as e:
            print(f"[CrossPlatformMonitor] Kalshi fetch error: {e}")
            return []

    def _fetch_poly_markets(self):
        """Fetch active markets from Polymarket."""
        try:
            # This depends on your Polymarket client implementation
            response = self.poly_client.get_markets()
            if hasattr(response, 'markets'):
                return [m.model_dump() if hasattr(m, 'model_dump') else m
                        for m in response.markets]
            return response if isinstance(response, list) else []
        except Exception as e:
            print(f"[CrossPlatformMonitor] Polymarket fetch error: {e}")
            return []

    def _check_opportunities(self):
        """Check for arbitrage opportunities on matched pairs."""
        with self._lock:
            pairs = list(self._matched_pairs)

        for pair in pairs:
            try:
                # Fetch current prices
                kalshi_price = self._get_kalshi_price(pair.kalshi_ticker)
                poly_price = self._get_poly_price(pair.poly_token_id)

                if kalshi_price is None or poly_price is None:
                    continue

                # Check for arbitrage
                opportunity = self._calculate_opportunity(pair, kalshi_price, poly_price)

                if opportunity and opportunity.get("edge", 0) > 0:
                    with self._lock:
                        self._opportunities.append(opportunity)

                    if self.on_opportunity:
                        self.on_opportunity(opportunity)

                    ts = datetime.now().strftime("%H:%M:%S")
                    print(f"[{ts}] CROSS-PLATFORM ARB: {pair.kalshi_ticker} <-> {pair.poly_token_id}")
                    print(f"        Edge: ${opportunity['edge']:.4f}/contract")

            except Exception as e:
                pass  # Skip individual pair errors

    def _get_kalshi_price(self, ticker):
        """Get current price from Kalshi."""
        try:
            m = self.kalshi_client.get_market(ticker)
            d = m.market.model_dump() if hasattr(m.market, 'model_dump') else m.market
            return {
                "yes_bid": d.get("yes_bid_dollars"),
                "yes_ask": d.get("yes_ask_dollars"),
                "no_bid": d.get("no_bid_dollars"),
                "no_ask": d.get("no_ask_dollars"),
            }
        except Exception:
            return None

    def _get_poly_price(self, token_id):
        """Get current price from Polymarket."""
        try:
            # This depends on your Polymarket client implementation
            m = self.poly_client.get_market(token_id)
            return {
                "yes_bid": m.get("best_bid"),
                "yes_ask": m.get("best_ask"),
            }
        except Exception:
            return None

    def _calculate_opportunity(self, pair, kalshi_price, poly_price):
        """Calculate arbitrage opportunity between a matched pair."""
        # Handle inversion
        if pair.kalshi_yes_equals_poly == "No":
            # Kalshi YES = Poly NO, so compare Kalshi YES with Poly NO
            k_yes_ask = kalshi_price.get("yes_ask")
            p_no_ask = 1.0 - poly_price.get("yes_bid", 0) if poly_price.get("yes_bid") else None

            if k_yes_ask and p_no_ask:
                # Dutch book opportunity
                combined = k_yes_ask + p_no_ask
                if combined < 1.0:
                    return {
                        "type": "dutch_book_inverted",
                        "kalshi_ticker": pair.kalshi_ticker,
                        "poly_token_id": pair.poly_token_id,
                        "edge": 1.0 - combined,
                        "kalshi_action": "BUY YES",
                        "poly_action": "BUY NO",
                        "kalshi_price": k_yes_ask,
                        "poly_price": p_no_ask,
                        "confidence": pair.confidence,
                    }
        else:
            # Same direction - check for price discrepancy
            k_yes_ask = kalshi_price.get("yes_ask")
            p_yes_bid = poly_price.get("yes_bid")

            if k_yes_ask and p_yes_bid and p_yes_bid > k_yes_ask:
                # Buy on Kalshi, sell on Poly
                return {
                    "type": "cross_platform_arb",
                    "kalshi_ticker": pair.kalshi_ticker,
                    "poly_token_id": pair.poly_token_id,
                    "edge": p_yes_bid - k_yes_ask,
                    "kalshi_action": "BUY YES",
                    "poly_action": "SELL YES",
                    "kalshi_price": k_yes_ask,
                    "poly_price": p_yes_bid,
                    "confidence": pair.confidence,
                }

        return None

    def get_matched_pairs(self):
        """Get current list of matched market pairs."""
        with self._lock:
            return list(self._matched_pairs)

    def get_opportunities(self, clear=False):
        """Get list of detected opportunities.

        Args:
            clear: If True, clear the opportunity list after returning

        Returns:
            List of opportunity dicts
        """
        with self._lock:
            opportunities = list(self._opportunities)
            if clear:
                self._opportunities.clear()
        return opportunities

    def refresh_now(self):
        """Force immediate refresh of market list."""
        self._last_market_refresh = 0


def create_cross_platform_monitor(
    kalshi_client,
    poly_client,
    min_confidence: float = 0.75,
    poll_interval_ms: int = 1000,
):
    """Convenience function to create and start a cross-platform monitor.

    Args:
        kalshi_client: Kalshi API client
        poly_client: Polymarket API client
        min_confidence: Minimum match confidence (0.0-1.0)
        poll_interval_ms: Price polling interval in milliseconds

    Returns:
        CrossPlatformMonitor instance (already started)
    """
    monitor = CrossPlatformMonitor(
        kalshi_client=kalshi_client,
        poly_client=poly_client,
        min_confidence=min_confidence,
        poll_interval_ms=poll_interval_ms,
    )
    return monitor.start()

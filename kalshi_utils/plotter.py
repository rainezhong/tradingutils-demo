import time
import threading
from collections import deque
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from datetime import datetime, timedelta

# --- 1. The Online Kalman Filter Class ---
class OnlineKalmanFilter:
    def __init__(self, process_noise=1e-5, measurement_noise=1e-3, initial_price=0.5):
        # Parameters
        self.Q = process_noise       # Process Variance (True value movement)
        self.R = measurement_noise   # Measurement Variance (Noise/Jitter)
        
        # State
        self.x = initial_price       # Estimate (Posterior Mean)
        self.P = 1.0                 # Uncertainty (Posterior Covariance)

    def update(self, measurement):
        """
        Performs one recursive step of the Kalman Filter.
        Returns the new filtered price.
        """
        if measurement is None:
            return self.x

        # 1. Predict (Random Walk Assumption)
        # x_pred = x_prev
        P_pred = self.P + self.Q

        # 2. Update (Correction)
        K = P_pred / (P_pred + self.R)             # Kalman Gain
        self.x = self.x + K * (measurement - self.x) # New Estimate
        self.P = (1 - K) * P_pred                  # New Uncertainty
        
        return self.x

# --- 2. The Live Plotter with Integrated Filter ---
class KalshiLivePlotter:
    def __init__(
        self,
        client,
        market_obj,
        poll_period_ms: int = 1000,
        history_size: int = 3600,
        fetch_history_min: int = 60
    ):
        self.client = client
        self.market = market_obj
        self.ticker = market_obj.ticker
        self.poll_period_s = poll_period_ms / 1000.0
        
        # Kalman Filter Instance
        # Q=1e-5 (smooths well), R=1e-3 (trusts data moderately)
        self.kf = OnlineKalmanFilter(process_noise=1e-5, measurement_noise=1e-3)
        
        # Concurrency
        self.lock = threading.Lock()
        self.stop_evt = threading.Event()
        self.th = threading.Thread(target=self._run, daemon=True)
        self.t0 = time.time()
        
        # Data History
        self.x = deque(maxlen=history_size)
        self.yes_ask = deque(maxlen=history_size)
        self.yes_bid = deque(maxlen=history_size)
        
        # NEW: Kalman History
        self.kalman_line = deque(maxlen=history_size)
        
        # Initial Metadata
        if hasattr(self.market, 'model_dump'):
            d = self.market.model_dump()
        else:
            d = self.market.__dict__

        self.last_meta = {
            "title": d.get("title", self.ticker),
            "subtitle": d.get("yes_sub_title", "YES"),
            "current_yes_bid": 0.0, "current_yes_ask": 0.0,
            "current_kalman": 0.0
        }

        # Pre-load history
        if fetch_history_min > 0:
            self._load_history_and_warmup(fetch_history_min)

    def _load_history_and_warmup(self, minutes):
        """Fetches history AND runs the filter through it to 'warm up' the state."""
        try:
            print(f"[{self.ticker}] Fetching & Warming up Filter ({minutes}m)...")
            end_time = datetime.now()
            start_time = end_time - timedelta(minutes=minutes)
            derived_series = self.ticker.split('-')[0]
            
            resp = self.client.get_market_candlesticks(
                ticker=self.ticker,
                series_ticker=derived_series, 
                start_ts=int(start_time.timestamp()),
                end_ts=int(end_time.timestamp()),
                period_interval=1
            )
            
            if not hasattr(resp, 'candlesticks') or not resp.candlesticks:
                return

            candles = sorted(resp.candlesticks, key=lambda c: c.end_period_ts)

            with self.lock:
                for c in candles:
                    # 1. Get Raw Price
                    if c.yes_bid and c.yes_bid.close is not None:
                        raw = c.yes_bid.close
                    elif c.price and hasattr(c.price, 'close') and c.price.close is not None:
                        raw = c.price.close
                    else:
                        continue
                        
                    price_dollars = float(raw) / 100.0
                    
                    # 2. Update Kalman Filter with historical point
                    k_price = self.kf.update(price_dollars)

                    # 3. Store
                    rel_time = c.end_period_ts - self.t0
                    self.x.append(rel_time)
                    self.yes_bid.append(price_dollars)
                    self.yes_ask.append(price_dollars) # History has 0 spread
                    self.kalman_line.append(k_price)
            
            print(f"[{self.ticker}] Warmup complete. Current Filter Price: {self.kf.x:.3f}")

        except Exception as e:
            print(f"[{self.ticker} History Error] {e}")

    def start(self):
        if not self.th.is_alive():
            self.th.start()
        return self

    def stop(self):
        self.stop_evt.set()
        self.th.join(timeout=2.0)

    def _run(self):
        """Internal polling loop."""
        while not self.stop_evt.is_set():
            try:
                resp = self.client.get_market(self.ticker)
                
                if hasattr(resp, "market"):
                    d = resp.market.model_dump()
                elif hasattr(resp, "model_dump"):
                    d = resp.model_dump()
                else:
                    d = resp.__dict__

                def to_f(v): 
                    try: return float(v)
                    except: return None

                y_ask = to_f(d.get("yes_ask_dollars"))
                y_bid = to_f(d.get("yes_bid_dollars"))

                if y_ask is not None and y_bid is not None:
                    now = time.time()
                    
                    # 1. Calculate Mid-Price for the Filter
                    mid_price = (y_ask + y_bid) / 2.0
                    
                    # 2. UPDATE FILTER LIVE
                    k_val = self.kf.update(mid_price)

                    with self.lock:
                        self.x.append(now - self.t0)
                        self.yes_ask.append(y_ask)
                        self.yes_bid.append(y_bid)
                        self.kalman_line.append(k_val)
                        
                        self.last_meta.update({
                            "title": d.get("title", self.ticker),
                            "subtitle": d.get("yes_sub_title", "YES"),
                            "current_yes_bid": y_bid, 
                            "current_yes_ask": y_ask,
                            "current_kalman": k_val
                        })
                    
            except Exception as e:
                print(f"[{self.ticker} Error] {e}")
            
            time.sleep(self.poll_period_s)

    def build(self, refresh_ms=500):
        self.start()

        # Setup Figure (2 Subplots: Price and Spread)
        fig, (ax_price, ax_spread) = plt.subplots(2, 1, sharex=True, gridspec_kw={'height_ratios': [3, 1]})
        fig.subplots_adjust(hspace=0.1, top=0.92)

        # --- Price Plot ---
        l_ask, = ax_price.plot([], [], 'r.', markersize=2, label='Ask', alpha=0.3)
        l_bid, = ax_price.plot([], [], 'g.', markersize=2, label='Bid', alpha=0.3)
        
        # THE KALMAN LINE (Blue, Thicker, On Top)
        l_kalman, = ax_price.plot([], [], 'b-', linewidth=2.0, label='Kalman Trend', alpha=0.9)
        
        ax_price.set_ylabel("Price ($)")
        ax_price.legend(loc="upper left", fontsize='small')
        ax_price.grid(True, alpha=0.3)
        title = ax_price.set_title(f"Loading {self.ticker}...", fontsize=12, fontweight='bold')

        # --- Spread Plot ---
        # (Simplified spread visualization for clarity)
        l_spr, = ax_spread.plot([], [], 'k-', linewidth=1, label='Spread', alpha=0.6)
        ax_spread.set_ylabel("Spread ($)")
        ax_spread.set_xlabel("Seconds (Rel)")
        ax_spread.grid(True, alpha=0.3)

        txt_info = ax_price.text(0.02, 0.5, "", transform=ax_price.transAxes, va="center", fontsize=9, 
                                 bbox=dict(facecolor='white', alpha=0.7))

        def update(frame):
            with self.lock:
                xs = list(self.x)
                if not xs: return []
                
                asks = list(self.yes_ask)
                bids = list(self.yes_bid)
                kals = list(self.kalman_line)
                meta = dict(self.last_meta)

            # Update Lines
            l_ask.set_data(xs, asks)
            l_bid.set_data(xs, bids)
            l_kalman.set_data(xs, kals) # Live updating blue line
            
            # Spread = Ask - Bid
            # Handle potential length mismatch if updates happen mid-read (rare but possible)
            min_len = min(len(asks), len(bids))
            spreads = [asks[i] - bids[i] for i in range(min_len)]
            l_spr.set_data(xs[:min_len], spreads)

            # Text & Title
            raw_title = meta['title'].replace(" Winner?", "").replace(" Match Winner?", "")
            title.set_text(f"{raw_title} | {meta['subtitle']}")
            
            txt_info.set_text(
                f"Ask: {meta['current_yes_ask']:.2f}\n"
                f"Bid: {meta['current_yes_bid']:.2f}\n"
                f"Kalman: {meta['current_kalman']:.3f}"
            )

            # Scaling
            ax_price.set_xlim(xs[0], xs[-1] + 10)
            
            all_prices = [p for p in (asks + bids) if p is not None]
            if all_prices:
                ax_price.set_ylim(min(all_prices)-0.02, max(all_prices)+0.02)
            
            if spreads:
                ax_spread.set_ylim(0, max(spreads)*1.2 if max(spreads) > 0 else 0.05)

            return [l_ask, l_bid, l_kalman, l_spr, title, txt_info]

        self.ani = FuncAnimation(fig, update, interval=refresh_ms, cache_frame_data=False)
        return self.ani

    @staticmethod
    def plot_pair(client, pair: tuple):
        print(f"Initializing Window 1: {pair[0].yes_sub_title}")
        p1 = KalshiLivePlotter(client, pair[0])
        a1 = p1.build()
        
        print(f"Initializing Window 2: {pair[1].yes_sub_title}")
        p2 = KalshiLivePlotter(client, pair[1])
        a2 = p2.build()
        
        plt.show()
        return (p1, a1), (p2, a2)
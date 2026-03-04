# Opposite Side Position Protection

## Problem

In prediction markets, YES and NO contracts on the same market are **perfectly negatively correlated**:
- One outcome always goes to $1.00 (100¢)
- The other always goes to $0.00 (0¢)

Holding both YES and NO on the same market:
1. **Overleverages your capital** - using 2x the required amount
2. **Creates a perfect hedge** - zero directional exposure
3. **Locks in a loss** - guaranteed to lose fees and spread

### Example

```python
# Buy 10 YES contracts at 50¢
Cost: 10 × $0.50 = $5.00

# Buy 10 NO contracts at 50¢
Cost: 10 × $0.50 = $5.00

# Total capital deployed: $10.00

# Outcome:
# - If YES wins: receive $10 from YES, $0 from NO = $10
# - If NO wins: receive $0 from YES, $10 from NO = $10
# - Net P&L: $10 - $10 capital = $0 (before fees)
# - With fees: GUARANTEED LOSS
```

You've used $10 in capital for $0 expected profit. This is pure waste.

## Solution

The `KalshiOrderManager` now **prevents buying both sides** of the same market:

### Position Tracking

Positions are tracked by `(ticker, side)` tuple:

```python
self._positions: Dict[Tuple[str, Side], int] = {}
```

### Pre-Submission Check

Before submitting BUY orders, the order manager checks for opposite side positions:

```python
if request.action == Action.BUY:
    opposite_side = Side.NO if request.side == Side.YES else Side.YES
    opposite_pos = self._positions.get((request.ticker, opposite_side), 0)

    if opposite_pos > 0:
        raise ValueError(
            f"Cannot buy {request.side.value} on {request.ticker}: "
            f"already holding {opposite_pos} {opposite_side.value} contracts."
        )
```

### Automatic Position Updates

Positions are automatically updated from fills:

```python
def update_position_from_fill(self, fill: Fill) -> None:
    """Update position tracking from a fill."""
    key = (fill.ticker, fill.outcome)

    # BUY adds to position, SELL reduces it
    delta = fill.quantity if fill.action == Action.BUY else -fill.quantity

    current = self._positions.get(key, 0)
    new_pos = current + delta

    if new_pos <= 0:
        # Position closed
        self._positions.pop(key, None)
    else:
        self._positions[key] = new_pos
```

## Behavior

### ✅ Allowed

1. **Buy same side multiple times**:
   ```python
   # Buy 10 YES at 50¢
   # Buy 5 more YES at 45¢ ✅ Allowed
   ```

2. **Buy opposite side after closing position**:
   ```python
   # Buy 10 YES at 50¢
   # Sell 10 YES at 60¢ (close position)
   # Buy 10 NO at 40¢ ✅ Allowed (no YES position anymore)
   ```

3. **Sell opposite side** (not a BUY):
   ```python
   # Hold 10 YES
   # Sell 5 NO ✅ Allowed (SELL action, not BUY)
   ```

4. **Different tickers are independent**:
   ```python
   # Buy YES on TICKER-A
   # Buy NO on TICKER-B ✅ Allowed (different markets)
   ```

### ❌ Blocked

1. **Buy opposite side when position exists**:
   ```python
   # Buy 10 YES at 50¢
   # Buy 10 NO at 50¢ ❌ ValueError raised
   ```

2. **Buy YES when holding NO**:
   ```python
   # Buy 10 NO at 50¢
   # Buy 10 YES at 50¢ ❌ ValueError raised
   ```

## Error Message

When blocked, you'll see:

```
ValueError: Cannot buy YES on KXNBA-ABC: already holding 10 NO contracts.
This would overleverage your position on perfectly correlated outcomes.
```

## API

### New Methods on `KalshiOrderManager`

```python
def get_position(self, ticker: str, side: Side) -> int:
    """Get current position for a ticker and side."""

def has_opposite_position(self, ticker: str, side: Side) -> bool:
    """Check if we have a position on the opposite side."""

def get_all_positions(self) -> Dict[Tuple[str, Side], int]:
    """Get all current positions."""

def update_position_from_fill(self, fill: Fill) -> None:
    """Update position tracking from a fill."""
```

## Integration

All strategies using `KalshiOrderManager` automatically get this protection:

```python
from core.order_manager.kalshi_order_manager import KalshiOrderManager
from core.order_manager.order_manager_types import OrderRequest, Action, Side

om = KalshiOrderManager(exchange_client)

# Place first order
request1 = OrderRequest(
    ticker="KXNBA-ABC",
    side=Side.YES,
    action=Action.BUY,
    size=10,
    price_cents=50,
)
await om.submit_order(request1)  # ✅ Succeeds

# Simulate fill
fill = Fill(
    ticker="KXNBA-ABC",
    outcome=Side.YES,
    action=Action.BUY,
    quantity=10,
    price_cents=50,
)
om.update_position_from_fill(fill)

# Try to buy opposite side
request2 = OrderRequest(
    ticker="KXNBA-ABC",
    side=Side.NO,
    action=Action.BUY,
    size=10,
    price_cents=50,
)
await om.submit_order(request2)  # ❌ Raises ValueError
```

## Testing

Run tests with:

```bash
python3 -m pytest tests/order_manager/test_opposite_side_protection.py -v
```

All 7 tests should pass:
- ✅ `test_can_buy_yes_initially`
- ✅ `test_blocks_opposite_side_purchase`
- ✅ `test_allows_same_side_accumulation`
- ✅ `test_allows_opposite_after_close`
- ✅ `test_blocks_yes_when_holding_no`
- ✅ `test_allows_selling_opposite_side`
- ✅ `test_different_tickers_independent`

## Migration Notes

### For Strategies

If your strategy manually tracks positions:

**Before:**
```python
self._positions: Dict[str, int] = {}  # ticker -> quantity
```

**After:**
```python
# Let OrderManager track positions
position = self._om.get_position(ticker, Side.YES)
```

### For Live Trading

No changes needed - protection is automatic when using `KalshiOrderManager`.

## Implementation Files

- `core/order_manager/kalshi_order_manager.py` - Position tracking and validation
- `core/order_manager/i_order_manager.py` - Interface with position methods
- `tests/order_manager/test_opposite_side_protection.py` - Test coverage

## Why This Matters

With this protection:
- ✅ **No wasted capital** on hedged positions
- ✅ **No accidental overleveraging**
- ✅ **Clear error messages** when logic tries to buy both sides
- ✅ **Automatic enforcement** across all strategies

Without it, a bug in strategy logic could silently:
- Lock up 2x the capital
- Generate zero profit
- Waste fees and spread
- Reduce effective portfolio size

This protection **prevents expensive mistakes** before they hit the exchange.

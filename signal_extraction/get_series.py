import pandas as pd
from datetime import datetime, timedelta

def get_time_series(client, markets, minutes=3000):
    """
    Extracts time series for a list of market objects.
    Returns a DataFrame with columns for YesBid, YesAsk, NoBid, NoAsk for each market.
    """
    
    # 1. Prepare timestamp range
    end_time = datetime.now()
    start_time = end_time - timedelta(minutes=minutes)
    
    combined_df = pd.DataFrame()

    for m in markets:
        # Extract tickers
        ticker = m.ticker
        series_ticker = ticker.split('-')[0]
        
        print(f"Fetching history for: {ticker}...")

        # 2. Fetch Candlesticks
        # Note: Period is 1 minute (period_interval=1)
        resp = client.get_market_candlesticks(
            ticker=ticker,
            series_ticker=series_ticker,
            start_ts=int(start_time.timestamp()),
            end_ts=int(end_time.timestamp()),
            period_interval=1
        )

        if not hasattr(resp, 'candlesticks') or not resp.candlesticks:
            print(f"Warning: No data found for {ticker}")
            continue

        # 3. Process Data
        # We extract the 'close' price for each minute
        data = []
        for c in resp.candlesticks:
            row = {}
            row['timestamp'] = datetime.fromtimestamp(c.end_period_ts)
            
            # --- YES SIDE ---
            # Handle missing data by checking for None
            if c.yes_bid and c.yes_bid.close is not None:
                yes_bid = float(c.yes_bid.close) / 100.0
            else:
                yes_bid = None

            if c.yes_ask and c.yes_ask.close is not None:
                yes_ask = float(c.yes_ask.close) / 100.0
            else:
                yes_ask = None
            
            # --- NO SIDE (Derived) ---
            # No Bid = 1.00 - Yes Ask
            # No Ask = 1.00 - Yes Bid
            no_bid = (1.00 - yes_ask) if yes_ask is not None else None
            no_ask = (1.00 - yes_bid) if yes_bid is not None else None

            # Add to row
            # Use market subtitle (e.g., "Toronto", "Atlanta") for column names
            name = m.yes_sub_title.replace(" ", "_")
            
            row[f'{name}_YesBid'] = yes_bid
            row[f'{name}_YesAsk'] = yes_ask
            row[f'{name}_NoBid'] = no_bid
            row[f'{name}_NoAsk'] = no_ask
            
            data.append(row)

        # 4. Convert to DataFrame
        df = pd.DataFrame(data)
        df.set_index('timestamp', inplace=True)
        
        # Merge into main dataframe (aligns by timestamp automatically)
        if combined_df.empty:
            combined_df = df
        else:
            combined_df = combined_df.join(df, how='outer')

    # 5. Clean up (Sort and Forward Fill missing minutes)
    combined_df.sort_index(inplace=True)
    combined_df.fillna(method='ffill', inplace=True)
    
    return combined_df
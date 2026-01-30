from kalshi_utils.client_wrapper import *

kalshi = KalshiWrapped()
client = kalshi.GetClient()

# Define what you are looking for
target_matchup = "atlmem".upper() 
series_ticker = "kxnbagame".upper()

# 1. Fetch the series (returns a GetMarketResponse)
response = kalshi.GetSeries(series_ticker, status='open')

search = []

# 2. Iterate over the .markets list inside the response
for market in response.markets:
    # 3. Check if your target matchup is in this market's ticker
    if target_matchup in market.ticker:
        search.append(market)

from kalshi_utils.plotter import KalshiLivePlotter
pair = (search[0], search[1])

KalshiLivePlotter.plot_pair(client, pair)


import os
from kalshi_python_sync import Configuration, KalshiClient
from kalshi_python_sync.models.market import Market
from kalshi_python_sync.models.get_market_response import GetMarketResponse
from apis.keys import kalshi_key

# Note: ExchangeClient interface is available in src.core.exchange for exchange-agnostic code
from nba_utils.fetch import *
from nhl_utils.fetch import *
from soccer_utils.fetch import *

from typing import List

class KalshiWrapped:
    client: KalshiClient

    def __init__(self, host="https://api.elections.kalshi.com/trade-api/v2"):
        self.InitClient(host)
        
    def InitClient(self, host):
        config = Configuration(host)

        # Get the path to the key file relative to this module's location
        current_dir = os.path.dirname(os.path.abspath(__file__))
        API_KEY_FILE_PATH = os.path.join(current_dir, "..", "apis", "keys", "kalshi_key.txt")
        try:
            with open(API_KEY_FILE_PATH, "r") as f:
                private_key = f.read()
        except FileNotFoundError:
            print(f"Error: Could not find the key file at {API_KEY_FILE_PATH}")
            exit()

        config.api_key_id = kalshi_key.key_id
        config.private_key_pem = private_key
        
        print("Client Created")
        self.client = KalshiClient(config)
    
    def GetClient(self) -> KalshiClient:
        return self.client
    
    def GetBalance(self):
        balance = self.client.get_balance()
        print(f"Current Balance: ${balance.balance/100 :.2f}")
        return balance.balance/100

    def GetSeries(self, series_ticker, status, limit=1000) -> GetMarketResponse:
        return self.client.get_markets(
            limit=limit,
            series_ticker=series_ticker,
            status=status
        )
        
    def GetAllNBAMarkets(self, status="open", limit=1000) -> List[Market]:
        return self.GetSeries("KXNBAGAME", status, limit).markets
    
    def GetAllNHLMarkets(self, status="open", limit=1000) -> List[Market]:
        return self.GetSeries("KXNHLGAME", status, limit).markets
    
    def GetAllUCLMarkets(self, status="open", limit=1000) -> List[Market]:
        return self.GetSeries("KXUCLGAME", status, limit).markets

    def GetALLNCAAMBMarkets(self, status="open", limit=1000) -> List[Market]:
        return self.GetSeries("KXNCAAMBGAME", status, limit).markets

    def GetALLTennisMarkets(self, status="open", limit=1000) -> List[Market]:
        return self.GetSeries("KXATPMATCH", status, limit).markets
    
    def GetLiveNBAMarkets(self) -> List[Market]:
        live_games = get_nbalive_games()
        live_keys = {g['matchup'] for g in live_games}
        live_markets = [
            m for m in self.GetAllNBAMarkets() 
            if any(key in m.ticker for key in live_keys)
        ]
        return live_markets
    
    def GetLiveNHLMarkets(self) -> List[Market]:
        live_games = get_nhllive_games()
        live_keys = {g['matchup'] for g in live_games}
        print(live_keys)
        live_markets = [
            m for m in self.GetAllNHLMarkets() 
            if any(key in m.ticker for key in live_keys)
        ]
        print([i.ticker for i in self.GetAllNHLMarkets()])
        return live_markets
    
    def GetLiveUCLMarkets(self) -> List[Market]:
        live_games = get_ucllive_games()
        live_keys = {g['matchup'] for g in live_games}
        live_markets = [
            m for m in self.GetAllUCLMarkets() 
            if any(key in m.ticker for key in live_keys)
        ]
        return live_markets
    
    def GetLiveNCAAMBMarkets(self) -> List[Market]:
        """Get college basketball markets for live games."""
        # For NCAAMB, we return all open markets as matching requires team name parsing
        return self.GetALLNCAAMBMarkets()
    
    def GetLiveTennisMarkets(self) -> List[Market]:
        """Get tennis markets for live matches."""
        # For Tennis, return all open markets as matching requires player name parsing
        return self.GetALLTennisMarkets()


    def GetMarketPairs(self, markets : List[Market]) -> List[tuple]:
        pairs = []
        seen = {}
        for market in markets:
            data = market.model_dump()
            this_event_ticker = data["event_ticker"]
            if this_event_ticker in seen.keys():
                pairs.append((seen[this_event_ticker], market))
            else:
                seen[this_event_ticker] = market
                
        return pairs
    
    def GetPairWithTeam(self, pairs : List[tuple], team_name: str) -> List[tuple]:
        result = []
        for pair in pairs:
            m1, m2 = pair
            data1 = m1.model_dump()
            data2 = m2.model_dump()
            if team_name in data1.get("yes_sub_title", "") or team_name in data2.get("yes_sub_title", ""):
                result.append(pair)
        return result
    
    def SortByVolume(self, markets, ascending=True):
        return sorted(markets, key=lambda x: x.model_dump()["liquidity_dollars"])

    def GetMarketByTicker(self, markets, event_ticker):
        for market in markets:
            this_event_ticker = market.model_dump()["event_ticker"]
            if this_event_ticker == event_ticker:
                return market
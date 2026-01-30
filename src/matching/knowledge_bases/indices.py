"""
Financial Index and Cryptocurrency Alias Database.

Canonical names mapped to their known aliases.
Used for entity extraction and matching.
"""

from typing import Dict, List, Optional, Set


# Major Stock Indices
STOCK_INDICES: Dict[str, List[str]] = {
    "s&p 500": [
        "spx", "spy", "sp500", "s&p", "s&p500", "standard and poors",
        "standard & poors", "sp 500", "spdr", ".spx"
    ],
    "dow jones": [
        "djia", "dji", "dow", "dow jones industrial average",
        "dow 30", "dow jones industrial", "the dow", ".dji"
    ],
    "nasdaq 100": [
        "ndx", "qqq", "nasdaq", "nasdaq100", "nas100",
        "nasdaq composite", "ndaq", ".ndx"
    ],
    "russell 2000": [
        "rut", "iwm", "russell", "russell2000", "r2k", "rty", ".rut"
    ],
    "vix": [
        "volatility index", "cboe vix", "fear index", "vix index", ".vix"
    ],
    "s&p 400": [
        "sp400", "mid cap", "s&p midcap", "mdy"
    ],
    "s&p 600": [
        "sp600", "small cap", "s&p smallcap", "ijr"
    ],
    "wilshire 5000": [
        "wilshire", "total market", "w5000"
    ],

    # International indices
    "ftse 100": [
        "ftse", "footsie", "uk100", "ukx"
    ],
    "dax": [
        "dax 40", "german dax", "germany 40", "de40"
    ],
    "cac 40": [
        "cac", "france 40", "french index", "fr40"
    ],
    "nikkei 225": [
        "nikkei", "nk225", "japan 225", "jp225"
    ],
    "hang seng": [
        "hsi", "hong kong", "hk50"
    ],
    "shanghai composite": [
        "sse", "shanghai", "china a50", "cn50"
    ],

    # Sector indices
    "xlf": ["financial sector", "financials", "financial etf"],
    "xlk": ["technology sector", "tech sector", "technology etf"],
    "xle": ["energy sector", "energy etf"],
    "xlv": ["healthcare sector", "health sector", "healthcare etf"],
    "xli": ["industrial sector", "industrials", "industrial etf"],
    "xlp": ["consumer staples", "staples sector"],
    "xly": ["consumer discretionary", "discretionary sector"],
    "xlb": ["materials sector", "materials etf"],
    "xlu": ["utilities sector", "utilities etf"],
    "xlre": ["real estate sector", "reits", "real estate etf"],
}

# Cryptocurrencies
CRYPTOCURRENCIES: Dict[str, List[str]] = {
    "bitcoin": [
        "btc", "xbt", "btcusd", "btc/usd", "bitcoin usd", "btc-usd"
    ],
    "ethereum": [
        "eth", "ether", "ethusd", "eth/usd", "ethereum usd", "eth-usd"
    ],
    "solana": [
        "sol", "solusd", "sol/usd", "solana usd"
    ],
    "ripple": [
        "xrp", "xrpusd", "xrp/usd", "ripple usd"
    ],
    "cardano": [
        "ada", "adausd", "ada/usd", "cardano usd"
    ],
    "dogecoin": [
        "doge", "dogeusd", "doge/usd", "dogecoin usd"
    ],
    "polkadot": [
        "dot", "dotusd", "dot/usd", "polkadot usd"
    ],
    "polygon": [
        "matic", "maticusd", "matic/usd", "polygon matic"
    ],
    "chainlink": [
        "link", "linkusd", "link/usd", "chainlink usd"
    ],
    "avalanche": [
        "avax", "avaxusd", "avax/usd", "avalanche usd"
    ],
    "litecoin": [
        "ltc", "ltcusd", "ltc/usd", "litecoin usd"
    ],
    "binance coin": [
        "bnb", "bnbusd", "bnb/usd", "binance smart chain", "bsc"
    ],
    "uniswap": [
        "uni", "uniusd", "uni/usd", "uniswap usd"
    ],
    "cosmos": [
        "atom", "atomusd", "atom/usd", "cosmos usd"
    ],
    "stellar": [
        "xlm", "xlmusd", "xlm/usd", "stellar lumens"
    ],
    "monero": [
        "xmr", "xmrusd", "xmr/usd", "monero usd"
    ],
    "tron": [
        "trx", "trxusd", "trx/usd", "tron usd"
    ],
    "near protocol": [
        "near", "nearusd", "near/usd", "near protocol"
    ],
    "arbitrum": [
        "arb", "arbusd", "arb/usd", "arbitrum usd"
    ],
    "optimism": [
        "op", "opusd", "op/usd", "optimism usd"
    ],
    "filecoin": [
        "fil", "filusd", "fil/usd", "filecoin usd"
    ],
    "aptos": [
        "apt", "aptusd", "apt/usd", "aptos usd"
    ],
    "sui": [
        "sui", "suiusd", "sui/usd", "sui usd"
    ],
    "pepe": [
        "pepe", "pepeusd", "pepe/usd", "pepe coin"
    ],
    "shiba inu": [
        "shib", "shibusd", "shib/usd", "shiba", "shiba inu usd"
    ],
    "tether": [
        "usdt", "tether usd"
    ],
    "usd coin": [
        "usdc", "usdc usd", "circle usd"
    ],
}

# Commodities
COMMODITIES: Dict[str, List[str]] = {
    "gold": [
        "xau", "xauusd", "gold usd", "gc", "gld", "gold spot"
    ],
    "silver": [
        "xag", "xagusd", "silver usd", "si", "slv", "silver spot"
    ],
    "crude oil": [
        "wti", "cl", "uso", "crude", "oil", "west texas intermediate",
        "brent", "brent crude"
    ],
    "natural gas": [
        "ng", "natgas", "nat gas", "ung", "natural gas"
    ],
    "copper": [
        "hg", "copper usd", "copx"
    ],
    "platinum": [
        "xpt", "platinum usd", "pplt"
    ],
    "palladium": [
        "xpd", "palladium usd", "pall"
    ],
}

# Combine all indices
INDICES: Dict[str, List[str]] = {}
INDICES.update(STOCK_INDICES)
INDICES.update(CRYPTOCURRENCIES)
INDICES.update(COMMODITIES)

# Build reverse lookup (alias -> canonical)
_ALIAS_TO_CANONICAL: Dict[str, str] = {}
for canonical, aliases in INDICES.items():
    _ALIAS_TO_CANONICAL[canonical.lower()] = canonical
    for alias in aliases:
        _ALIAS_TO_CANONICAL[alias.lower()] = canonical


def get_index_canonical(text: str) -> Optional[str]:
    """Get canonical index name from any alias.

    Args:
        text: Index name or alias to look up

    Returns:
        Canonical index name or None if not found
    """
    return _ALIAS_TO_CANONICAL.get(text.lower().strip())


def get_all_index_aliases(canonical_name: str) -> List[str]:
    """Get all aliases for a canonical index name.

    Args:
        canonical_name: The canonical index name

    Returns:
        List of aliases including the canonical name
    """
    canonical_lower = canonical_name.lower()
    if canonical_lower in INDICES:
        return [canonical_name] + INDICES[canonical_lower]
    return []


def get_all_aliases() -> Set[str]:
    """Get all known index aliases."""
    return set(_ALIAS_TO_CANONICAL.keys())


def is_cryptocurrency(name: str) -> bool:
    """Check if a name refers to a cryptocurrency.

    Args:
        name: Name or alias to check

    Returns:
        True if it's a cryptocurrency
    """
    canonical = get_index_canonical(name)
    return canonical is not None and canonical.lower() in CRYPTOCURRENCIES


def is_stock_index(name: str) -> bool:
    """Check if a name refers to a stock index.

    Args:
        name: Name or alias to check

    Returns:
        True if it's a stock index
    """
    canonical = get_index_canonical(name)
    return canonical is not None and canonical.lower() in STOCK_INDICES


def is_commodity(name: str) -> bool:
    """Check if a name refers to a commodity.

    Args:
        name: Name or alias to check

    Returns:
        True if it's a commodity
    """
    canonical = get_index_canonical(name)
    return canonical is not None and canonical.lower() in COMMODITIES


def get_asset_type(name: str) -> Optional[str]:
    """Get the asset type for a given name.

    Args:
        name: Name or alias to check

    Returns:
        One of 'cryptocurrency', 'stock_index', 'commodity', or None
    """
    canonical = get_index_canonical(name)
    if canonical is None:
        return None

    canonical_lower = canonical.lower()
    if canonical_lower in CRYPTOCURRENCIES:
        return "cryptocurrency"
    elif canonical_lower in STOCK_INDICES:
        return "stock_index"
    elif canonical_lower in COMMODITIES:
        return "commodity"
    return None

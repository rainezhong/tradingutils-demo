"""
Test market matching using real Polymarket data + synthetic Kalshi equivalents.

This tests the matching pipeline with real market titles from Polymarket,
paired with synthetic Kalshi markets that should match.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.matching import MarketMatcher, MatcherConfig
from src.matching.entity_extractor import EntityExtractor


def fetch_poly_markets():
    """Fetch markets from Polymarket Gamma API."""
    try:
        import httpx

        gamma_url = "https://gamma-api.polymarket.com/markets"
        params = {
            "active": "true",
            "closed": "false",
            "limit": "100",
        }

        res = httpx.get(gamma_url, params=params, timeout=30.0)
        if res.status_code == 200:
            markets = res.json()
            poly_markets = []
            for m in markets:
                poly_markets.append({
                    "token_id": str(m.get("id", "")),
                    "question": m.get("question", ""),
                    "description": m.get("description", ""),
                    "category": m.get("category", ""),
                    "end_date": m.get("endDate"),
                    "volume": m.get("volume"),
                })
            print(f"Fetched {len(poly_markets)} Polymarket markets")
            return poly_markets
        return []
    except Exception as e:
        print(f"Failed to fetch Polymarket markets: {e}")
        return []


def create_synthetic_kalshi_equivalents(poly_markets):
    """Create synthetic Kalshi markets that should match some Polymarket markets."""
    kalshi_markets = []

    # Look for specific patterns in Polymarket to create matching Kalshi markets
    for poly in poly_markets:
        question = poly.get("question", "").lower()

        # Trump-related markets
        if "trump" in question:
            # Create a slightly different phrasing
            kalshi_markets.append({
                "ticker": f"TRUMP-{len(kalshi_markets)+1}",
                "title": poly["question"].replace("Will", "Will Donald").replace("?", " happen?"),
                "category": "politics",
                "close_time": poly.get("end_date"),
            })

        # Bitcoin/crypto markets
        if "bitcoin" in question or "btc" in question:
            kalshi_markets.append({
                "ticker": f"BTC-{len(kalshi_markets)+1}",
                "title": poly["question"].replace("Bitcoin", "BTC").replace("BTC", "Bitcoin"),
                "category": "crypto",
                "close_time": poly.get("end_date"),
            })

        # S&P / stock market
        if "s&p" in question or "stock" in question or "market" in question:
            kalshi_markets.append({
                "ticker": f"SPX-{len(kalshi_markets)+1}",
                "title": poly["question"],
                "category": "finance",
                "close_time": poly.get("end_date"),
            })

    # Also add some that definitely should match
    # Look for any market with a price threshold
    extractor = EntityExtractor()
    for poly in poly_markets[:20]:
        context = extractor.extract_with_context(poly.get("question", ""))
        if context.get("has_price_comparison"):
            # Create similar Kalshi market
            kalshi_markets.append({
                "ticker": f"SYNTH-{len(kalshi_markets)+1}",
                "title": poly["question"],  # Exact copy should match perfectly
                "category": context.get("inferred_category"),
                "close_time": poly.get("end_date"),
            })

    print(f"Created {len(kalshi_markets)} synthetic Kalshi markets")
    return kalshi_markets


def main():
    print("=" * 60)
    print("Testing Matching with Real Polymarket + Synthetic Kalshi")
    print("=" * 60)

    # Fetch real Polymarket data
    poly_markets = fetch_poly_markets()
    if not poly_markets:
        print("Failed to fetch Polymarket markets")
        return

    # Create synthetic Kalshi equivalents
    kalshi_markets = create_synthetic_kalshi_equivalents(poly_markets)

    if not kalshi_markets:
        print("No synthetic Kalshi markets created")
        return

    # Test matching
    print("\n" + "=" * 60)
    print("Running Market Matching")
    print("=" * 60)

    config = MatcherConfig(
        min_confidence=0.5,
        use_semantic_similarity=False,
    )
    matcher = MarketMatcher(config)

    matches = matcher.match_markets(kalshi_markets, poly_markets)

    print(f"\nFound {len(matches)} matches")

    # Categorize by confidence
    exact = [m for m in matches if m.confidence >= 0.90]
    equiv = [m for m in matches if 0.75 <= m.confidence < 0.90]
    related = [m for m in matches if 0.50 <= m.confidence < 0.75]

    print(f"\n  Exact (>=0.90): {len(exact)}")
    print(f"  Equivalent (0.75-0.90): {len(equiv)}")
    print(f"  Related (0.50-0.75): {len(related)}")

    # Show top matches
    print("\n--- Top 10 Matches ---")
    for i, m in enumerate(matches[:10]):
        print(f"\n{i+1}. [{m.match_type.value}] Confidence: {m.confidence:.3f}")
        print(f"   Kalshi: {m.kalshi_ticker}")
        if m.kalshi_market:
            print(f"   K-Title: {m.kalshi_market.original_title[:80]}...")
        print(f"   Poly: {m.poly_token_id[:20]}...")
        if m.poly_market:
            print(f"   P-Title: {m.poly_market.original_title[:80]}...")
        print(f"   Matched entities: {m.matched_entities[:3] if m.matched_entities else 'none'}")

    # Test that exact copies match with high confidence
    print("\n" + "=" * 60)
    print("Verification: Exact copy markets should match highly")
    print("=" * 60)

    exact_copy_matches = [m for m in matches if m.kalshi_ticker.startswith("SYNTH-")]
    if exact_copy_matches:
        avg_conf = sum(m.confidence for m in exact_copy_matches) / len(exact_copy_matches)
        print(f"Average confidence for exact copies: {avg_conf:.3f}")
        print(f"Min confidence for exact copies: {min(m.confidence for m in exact_copy_matches):.3f}")
        print(f"Max confidence for exact copies: {max(m.confidence for m in exact_copy_matches):.3f}")
    else:
        print("No exact copy matches found")


if __name__ == "__main__":
    main()

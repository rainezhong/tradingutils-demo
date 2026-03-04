"""
Test market matching against real Kalshi and Polymarket data.

This script fetches live markets from both platforms and tests the matching system.
Run with: python3 tests/test_real_matching.py
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.matching import MarketMatcher, MatcherConfig
from src.matching.entity_extractor import EntityExtractor


def fetch_kalshi_markets():
    """Fetch markets from Kalshi API."""
    try:
        from kalshi_utils.client_wrapper import KalshiWrapped

        client = KalshiWrapped()

        # Get all NBA markets
        nba_markets = client.GetAllNBAMarkets(status="open", limit=50)
        nhl_markets = client.GetAllNHLMarkets(status="open", limit=50)

        # Convert to dicts
        kalshi_markets = []
        for m in nba_markets + nhl_markets:
            data = m.model_dump() if hasattr(m, "model_dump") else m
            kalshi_markets.append(data)

        print(f"Fetched {len(kalshi_markets)} Kalshi markets")
        return kalshi_markets
    except Exception as e:
        print(f"Failed to fetch Kalshi markets: {e}")
        return []


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
            # Convert to our expected format
            poly_markets = []
            for m in markets:
                poly_markets.append(
                    {
                        "token_id": str(m.get("id", "")),
                        "question": m.get("question", ""),
                        "description": m.get("description", ""),
                        "category": m.get("category", ""),
                        "end_date": m.get("endDate"),
                        "volume": m.get("volume"),
                        "outcomes": m.get("outcomes", []),
                        "outcomePrices": m.get("outcomePrices", []),
                    }
                )
            print(f"Fetched {len(poly_markets)} Polymarket markets")
            return poly_markets
        else:
            print(f"Polymarket API returned {res.status_code}")
            return []
    except Exception as e:
        print(f"Failed to fetch Polymarket markets: {e}")
        return []


def print_market_sample(markets, platform_name, n=5):
    """Print a sample of markets for inspection."""
    print(f"\n=== Sample {platform_name} Markets ===")
    for i, m in enumerate(markets[:n]):
        if platform_name == "Kalshi":
            print(f"{i + 1}. [{m.get('ticker', 'N/A')}] {m.get('title', 'N/A')}")
            print(
                f"   Category: {m.get('category', 'N/A')}, Close: {m.get('close_time', 'N/A')}"
            )
        else:
            print(
                f"{i + 1}. [{m.get('token_id', 'N/A')[:20]}...] {m.get('question', 'N/A')}"
            )
            print(f"   End: {m.get('end_date', 'N/A')}")
        print()


def run_entity_extraction(markets, platform_name):
    """Run entity extraction on real market titles."""
    print(f"\n=== Entity Extraction Test ({platform_name}) ===")
    extractor = EntityExtractor()

    for m in markets[:5]:
        title = m.get("title") or m.get("question", "")
        if not title:
            continue

        context = extractor.extract_with_context(title)
        print(f"\nTitle: {title}")
        print(f"  Category: {context['inferred_category']}")
        print(f"  Entities: {len(context['entities'])}")
        for e in context["entities"][:5]:
            print(f"    - {e.entity_type.value}: {e.normalized_form}")


def run_matching(kalshi_markets, poly_markets):
    """Run matching between Kalshi and Polymarket."""
    print("\n=== Market Matching Test ===")

    config = MatcherConfig(
        min_confidence=0.5,  # Lower threshold to see more potential matches
        use_semantic_similarity=False,  # Faster without embeddings
    )
    matcher = MarketMatcher(config)

    matches = matcher.match_markets(kalshi_markets, poly_markets)

    print(f"\nFound {len(matches)} matches with confidence >= 0.5")
    print()

    # Show matches by confidence level
    exact_matches = [m for m in matches if m.confidence >= 0.90]
    equiv_matches = [m for m in matches if 0.75 <= m.confidence < 0.90]
    related_matches = [m for m in matches if 0.50 <= m.confidence < 0.75]

    print(f"Exact matches (>=0.90): {len(exact_matches)}")
    print(f"Equivalent matches (0.75-0.90): {len(equiv_matches)}")
    print(f"Related matches (0.50-0.75): {len(related_matches)}")

    print("\n--- Top Matches ---")
    for m in matches[:10]:
        print(f"\n[{m.match_type.value.upper()}] Confidence: {m.confidence:.3f}")
        print(f"  Kalshi: {m.kalshi_ticker}")
        if m.kalshi_market:
            print(f"    Title: {m.kalshi_market.original_title}")
        print(f"  Poly: {m.poly_token_id}")
        if m.poly_market:
            print(f"    Title: {m.poly_market.original_title}")
        print(f"  Category: {m.category}")
        print(f"  Inverted: {m.is_inverted}")
        if m.warnings:
            print(f"  Warnings: {m.warnings}")
        print(
            f"  Score breakdown: entity={m.entity_score:.2f}, text={m.text_score:.2f}, "
            f"temporal={m.temporal_score:.2f}, structural={m.structural_score:.2f}"
        )

    return matches


def main():
    print("=" * 60)
    print("Testing Market Matching Against Real API Data")
    print("=" * 60)

    # Fetch real markets
    print("\n1. Fetching markets from APIs...")
    kalshi_markets = fetch_kalshi_markets()
    poly_markets = fetch_poly_markets()

    if not kalshi_markets:
        print("WARNING: No Kalshi markets fetched. API may not be configured.")
    if not poly_markets:
        print("WARNING: No Polymarket markets fetched.")

    if not kalshi_markets and not poly_markets:
        print("\nCannot run matching test without any markets.")
        print("Make sure API credentials are configured.")
        return

    # Show samples
    if kalshi_markets:
        print_market_sample(kalshi_markets, "Kalshi")
        run_entity_extraction(kalshi_markets, "Kalshi")

    if poly_markets:
        print_market_sample(poly_markets, "Polymarket")
        run_entity_extraction(poly_markets, "Polymarket")

    # Test matching
    if kalshi_markets and poly_markets:
        matches = run_matching(kalshi_markets, poly_markets)

        # Summary
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)
        print(f"Kalshi markets analyzed: {len(kalshi_markets)}")
        print(f"Polymarket markets analyzed: {len(poly_markets)}")
        print(f"Total matches found: {len(matches)}")

        if matches:
            avg_confidence = sum(m.confidence for m in matches) / len(matches)
            print(f"Average match confidence: {avg_confidence:.3f}")

            categories = {}
            for m in matches:
                cat = m.category or "unknown"
                categories[cat] = categories.get(cat, 0) + 1
            print(f"Matches by category: {categories}")
    else:
        print("\nSkipping matching test - need markets from both platforms.")


if __name__ == "__main__":
    main()

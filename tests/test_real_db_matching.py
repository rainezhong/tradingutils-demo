"""
Test market matching using real Kalshi DB + real Polymarket API.
"""

import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.matching import MarketMatcher, MatcherConfig
from src.matching.entity_extractor import EntityExtractor


def fetch_kalshi_from_db(limit=500):
    """Fetch markets from local Kalshi database."""
    db_path = "/Users/raine/tradingutils/data/markets.db"

    if not os.path.exists(db_path):
        print(f"Database not found: {db_path}")
        return []

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get diverse markets - crypto, sports outcomes, political
    query = """
    SELECT ticker, title, category, close_time
    FROM markets
    WHERE title IS NOT NULL
    AND title != ''
    AND ticker NOT LIKE 'KXMV%'
    ORDER BY RANDOM()
    LIMIT ?
    """

    cursor.execute(query, (limit,))
    rows = cursor.fetchall()
    conn.close()

    markets = []
    for row in rows:
        markets.append({
            "ticker": row[0],
            "title": row[1],
            "category": row[2],
            "close_time": row[3],
        })

    print(f"Fetched {len(markets)} Kalshi markets from DB")
    return markets


def fetch_poly_markets():
    """Fetch markets from Polymarket Gamma API."""
    try:
        import httpx

        gamma_url = "https://gamma-api.polymarket.com/markets"
        params = {
            "active": "true",
            "closed": "false",
            "archived": "false",
            "limit": "200",
            "order": "volume",
            "ascending": "false",
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


def analyze_markets(kalshi_markets, poly_markets):
    """Analyze what types of markets we have."""
    extractor = EntityExtractor()

    print("\n=== Kalshi Market Categories ===")
    kalshi_categories = {}
    for m in kalshi_markets[:100]:
        ctx = extractor.extract_with_context(m.get("title", ""))
        cat = ctx.get("inferred_category") or "other"
        kalshi_categories[cat] = kalshi_categories.get(cat, 0) + 1
    print(kalshi_categories)

    print("\n=== Polymarket Market Categories ===")
    poly_categories = {}
    for m in poly_markets[:100]:
        ctx = extractor.extract_with_context(m.get("question", ""))
        cat = ctx.get("inferred_category") or "other"
        poly_categories[cat] = poly_categories.get(cat, 0) + 1
    print(poly_categories)


def run_matching(kalshi_markets, poly_markets):
    """Run matching between platforms."""
    print("\n" + "=" * 60)
    print("Running Market Matching")
    print("=" * 60)

    config = MatcherConfig(
        min_confidence=0.4,  # Lower to see more potential matches
        use_semantic_similarity=False,
    )
    matcher = MarketMatcher(config)

    matches = matcher.match_markets(kalshi_markets, poly_markets)

    print(f"\nTotal matches found: {len(matches)}")

    # Categorize by confidence
    exact = [m for m in matches if m.confidence >= 0.90]
    equiv = [m for m in matches if 0.75 <= m.confidence < 0.90]
    related = [m for m in matches if 0.50 <= m.confidence < 0.75]
    weak = [m for m in matches if m.confidence < 0.50]

    print(f"\n  Exact (>=0.90): {len(exact)}")
    print(f"  Equivalent (0.75-0.90): {len(equiv)}")
    print(f"  Related (0.50-0.75): {len(related)}")
    print(f"  Weak (<0.50): {len(weak)}")

    # Show matches by category
    print("\n--- Matches by Category ---")
    by_category = {}
    for m in matches:
        cat = m.category or "unknown"
        by_category[cat] = by_category.get(cat, 0) + 1
    for cat, count in sorted(by_category.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")

    # Show top matches
    print("\n--- Top 15 Matches ---")
    for i, m in enumerate(matches[:15]):
        print(f"\n{i+1}. [{m.match_type.value}] Confidence: {m.confidence:.3f}")
        print(f"   Kalshi: {m.kalshi_ticker}")
        if m.kalshi_market:
            print(f"   K-Title: {m.kalshi_market.original_title[:70]}...")
        print(f"   Poly: {m.poly_token_id[:20]}...")
        if m.poly_market:
            print(f"   P-Title: {m.poly_market.original_title[:70]}...")
        print(f"   Category: {m.category}")
        print(f"   Entity overlap: {m.entity_score:.2f}, Text: {m.text_score:.2f}")

    return matches


def main():
    print("=" * 60)
    print("Testing Matching: Real Kalshi DB + Real Polymarket API")
    print("=" * 60)

    # Fetch data
    kalshi_markets = fetch_kalshi_from_db(limit=500)
    poly_markets = fetch_poly_markets()

    if not kalshi_markets or not poly_markets:
        print("Need data from both platforms to test matching")
        return

    # Analyze categories
    analyze_markets(kalshi_markets, poly_markets)

    # Test matching
    matches = run_matching(kalshi_markets, poly_markets)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Kalshi markets: {len(kalshi_markets)}")
    print(f"Polymarket markets: {len(poly_markets)}")
    print(f"Matches found: {len(matches)}")

    if matches:
        high_conf = [m for m in matches if m.confidence >= 0.75]
        print(f"High confidence matches (>=0.75): {len(high_conf)}")


if __name__ == "__main__":
    main()

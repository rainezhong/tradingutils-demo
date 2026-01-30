"""
Text Normalization Pipeline for Market Matching.

Cleans and standardizes market titles for comparison.
"""

import re
from typing import Dict, List, Optional


# Common abbreviations to expand
ABBREVIATIONS: Dict[str, str] = {
    # Financial indices
    "spx": "s&p 500",
    "spy": "s&p 500",
    "sp500": "s&p 500",
    "s&p": "s&p 500",
    "djia": "dow jones",
    "dji": "dow jones",
    "dow": "dow jones",
    "ndx": "nasdaq 100",
    "qqq": "nasdaq 100",
    "nasdaq": "nasdaq 100",
    "rut": "russell 2000",
    "iwm": "russell 2000",
    "vix": "volatility index",

    # Cryptocurrencies
    "btc": "bitcoin",
    "eth": "ethereum",
    "sol": "solana",
    "xrp": "ripple",
    "doge": "dogecoin",
    "ada": "cardano",
    "bnb": "binance coin",
    "avax": "avalanche",
    "dot": "polkadot",
    "matic": "polygon",
    "link": "chainlink",
    "ltc": "litecoin",
    "atom": "cosmos",
    "uni": "uniswap",
    "aave": "aave",

    # Sports leagues
    "nfl": "national football league",
    "nba": "national basketball association",
    "nhl": "national hockey league",
    "mlb": "major league baseball",
    "mls": "major league soccer",
    "epl": "english premier league",
    "ucl": "uefa champions league",
    "ncaa": "ncaa",  # Keep as is - well known
    "cfb": "college football",

    # Time-related
    "eod": "end of day",
    "eow": "end of week",
    "eom": "end of month",
    "eoq": "end of quarter",
    "eoy": "end of year",
    "ytd": "year to date",
    "mtd": "month to date",

    # Political
    "potus": "president of the united states",
    "scotus": "supreme court",
    "gop": "republican",
    "dem": "democrat",
    "dnc": "democratic national committee",
    "rnc": "republican national committee",

    # Common words
    "vs": "versus",
    "v": "versus",
    "&": "and",
    "w/": "with",
    "w/o": "without",
    "approx": "approximately",
    "avg": "average",
    "min": "minimum",
    "max": "maximum",
    "pct": "percent",
}

# Noise words to remove (articles, common filler words)
NOISE_WORDS = {
    "will", "the", "a", "an", "be", "to", "of", "in", "on", "at", "by",
    "for", "is", "it", "that", "this", "with", "as", "are", "was", "were",
    "been", "being", "have", "has", "had", "do", "does", "did", "shall",
    "should", "would", "could", "might", "must", "can", "may", "if", "or",
    "and", "but", "so", "yet", "both", "either", "neither", "not", "no",
    "only", "than", "then", "when", "where", "which", "who", "whom",
    "what", "how", "why", "all", "each", "every", "any", "some", "most",
    "other", "such", "own", "same", "just", "also", "very", "even",
}

# Punctuation to remove or normalize
PUNCTUATION_MAP = {
    "?": "",
    "!": "",
    "'": "",
    '"': "",
    "`": "",
    ";": "",
    ":": " ",  # Keep some spacing
    ",": "",
    ".": "",
    "(": " ",
    ")": " ",
    "[": " ",
    "]": " ",
    "{": " ",
    "}": " ",
    "/": " ",
    "\\": " ",
    "|": " ",
    "~": "",
    "@": " at ",
    "#": "",
    "$": "",
    "%": " percent ",
    "^": "",
    "*": "",
    "+": " plus ",
    "=": " equals ",
    "<": " less than ",
    ">": " greater than ",
    "≤": " less than or equal to ",
    "≥": " greater than or equal to ",
    "–": "-",  # En dash to hyphen
    "—": "-",  # Em dash to hyphen
}


class TextNormalizer:
    """Text normalization pipeline for market titles."""

    def __init__(
        self,
        expand_abbreviations: bool = True,
        remove_noise: bool = True,
        normalize_numbers: bool = True,
        lowercase: bool = True,
        custom_abbreviations: Optional[Dict[str, str]] = None,
        custom_noise_words: Optional[set] = None,
    ):
        """Initialize the normalizer.

        Args:
            expand_abbreviations: Whether to expand known abbreviations
            remove_noise: Whether to remove noise words
            normalize_numbers: Whether to standardize number formats
            lowercase: Whether to convert to lowercase
            custom_abbreviations: Additional abbreviations to expand
            custom_noise_words: Additional noise words to remove
        """
        self.expand_abbreviations = expand_abbreviations
        self.remove_noise = remove_noise
        self.normalize_numbers = normalize_numbers
        self.lowercase = lowercase

        self.abbreviations = ABBREVIATIONS.copy()
        if custom_abbreviations:
            self.abbreviations.update(custom_abbreviations)

        self.noise_words = NOISE_WORDS.copy()
        if custom_noise_words:
            self.noise_words.update(custom_noise_words)

    def normalize(self, text: str) -> str:
        """Apply full normalization pipeline to text.

        Args:
            text: The text to normalize

        Returns:
            Normalized text
        """
        if not text:
            return ""

        result = text

        # Step 1: Lowercase (if enabled)
        if self.lowercase:
            result = result.lower()

        # Step 2: Normalize punctuation
        result = self._normalize_punctuation(result)

        # Step 3: Normalize numbers
        if self.normalize_numbers:
            result = self._normalize_numbers(result)

        # Step 4: Expand abbreviations
        if self.expand_abbreviations:
            result = self._expand_abbreviations(result)

        # Step 5: Remove noise words
        if self.remove_noise:
            result = self._remove_noise(result)

        # Step 6: Clean up whitespace
        result = self._clean_whitespace(result)

        return result

    def _normalize_punctuation(self, text: str) -> str:
        """Replace punctuation with appropriate substitutes."""
        for char, replacement in PUNCTUATION_MAP.items():
            text = text.replace(char, replacement)
        return text

    def _normalize_numbers(self, text: str) -> str:
        """Standardize number formats.

        Examples:
            "6,000" -> "6000"
            "$100k" -> "100000"
            "$1M" -> "1000000"
            "1.5B" -> "1500000000"
            "100%" -> "100 percent"
        """
        # Remove commas from numbers
        text = re.sub(r'(\d),(\d)', r'\1\2', text)

        # Handle K/M/B suffixes (case insensitive)
        def expand_suffix(match):
            num = float(match.group(1))
            suffix = match.group(2).lower()
            if suffix == 'k':
                return str(int(num * 1_000))
            elif suffix == 'm':
                return str(int(num * 1_000_000))
            elif suffix == 'b':
                return str(int(num * 1_000_000_000))
            elif suffix == 't':
                return str(int(num * 1_000_000_000_000))
            return match.group(0)

        text = re.sub(r'(\d+\.?\d*)\s*([kKmMbBtT])\b', expand_suffix, text)

        # Handle currency symbols (already removed by punctuation map, but be safe)
        text = re.sub(r'\$\s*(\d)', r'\1', text)

        return text

    def _expand_abbreviations(self, text: str) -> str:
        """Expand known abbreviations."""
        words = text.split()
        expanded = []

        for word in words:
            # Check if word is an abbreviation (case insensitive)
            word_lower = word.lower().strip()
            if word_lower in self.abbreviations:
                expanded.append(self.abbreviations[word_lower])
            else:
                expanded.append(word)

        return " ".join(expanded)

    def _remove_noise(self, text: str) -> str:
        """Remove noise words from text."""
        words = text.split()
        filtered = [w for w in words if w.lower() not in self.noise_words]
        return " ".join(filtered)

    def _clean_whitespace(self, text: str) -> str:
        """Clean up extra whitespace."""
        # Replace multiple spaces with single space
        text = re.sub(r'\s+', ' ', text)
        # Strip leading/trailing whitespace
        return text.strip()

    def normalize_for_comparison(self, text: str) -> str:
        """Normalize text specifically for comparison purposes.

        This is more aggressive than normal normalization:
        - Removes all non-alphanumeric characters except spaces
        - Removes common prefixes like "Will", "What", etc.
        """
        result = self.normalize(text)

        # Remove question starters
        question_starters = [
            "will", "what", "how", "when", "where", "who", "which",
            "does", "do", "is", "are", "can", "could", "would", "should",
        ]
        words = result.split()
        while words and words[0].lower() in question_starters:
            words.pop(0)
        result = " ".join(words)

        # Keep only alphanumeric and spaces
        result = re.sub(r'[^a-z0-9\s]', ' ', result.lower())

        return self._clean_whitespace(result)

    def extract_numbers(self, text: str) -> List[float]:
        """Extract all numbers from text.

        Args:
            text: Text to extract numbers from

        Returns:
            List of numbers found
        """
        # Normalize first
        normalized = self._normalize_numbers(text)

        # Find all numbers (integers and decimals)
        pattern = r'-?\d+\.?\d*'
        matches = re.findall(pattern, normalized)

        numbers = []
        for m in matches:
            try:
                if '.' in m:
                    numbers.append(float(m))
                else:
                    numbers.append(float(int(m)))
            except ValueError:
                continue

        return numbers

    def extract_price_comparisons(self, text: str) -> List[dict]:
        """Extract price comparison expressions from text.

        Returns:
            List of dicts with 'operator' and 'value' keys
        """
        comparisons = []

        # Normalize first
        normalized = self.normalize(text)

        # Patterns for price comparisons
        patterns = [
            # "above 6000", "over 6000", "greater than 6000"
            (r'(?:above|over|greater than|more than|exceeds?|>\s*)\s*(\d+\.?\d*)', 'above'),
            # "below 6000", "under 6000", "less than 6000"
            (r'(?:below|under|less than|fewer than|<\s*)\s*(\d+\.?\d*)', 'below'),
            # "at least 6000", "minimum 6000"
            (r'(?:at least|minimum|min|>=\s*)\s*(\d+\.?\d*)', 'at_least'),
            # "at most 6000", "maximum 6000"
            (r'(?:at most|maximum|max|<=\s*)\s*(\d+\.?\d*)', 'at_most'),
            # "exactly 6000", "equal to 6000"
            (r'(?:exactly|equal to|equals?|==?\s*)\s*(\d+\.?\d*)', 'exactly'),
            # "between 6000 and 6050"
            (r'between\s*(\d+\.?\d*)\s*(?:and|-)\s*(\d+\.?\d*)', 'between'),
            # "6000-6050" range format
            (r'(\d+\.?\d*)\s*-\s*(\d+\.?\d*)', 'range'),
        ]

        for pattern, op_type in patterns:
            for match in re.finditer(pattern, normalized, re.IGNORECASE):
                if op_type in ('between', 'range'):
                    comparisons.append({
                        'operator': op_type,
                        'low': float(match.group(1)),
                        'high': float(match.group(2)),
                    })
                else:
                    comparisons.append({
                        'operator': op_type,
                        'value': float(match.group(1)),
                    })

        return comparisons


def normalize_market_title(text: str) -> str:
    """Convenience function for quick normalization."""
    normalizer = TextNormalizer()
    return normalizer.normalize(text)


def normalize_for_comparison(text: str) -> str:
    """Convenience function for comparison normalization."""
    normalizer = TextNormalizer()
    return normalizer.normalize_for_comparison(text)

"""
Political Candidate and Election Alias Database.

Canonical names mapped to their known aliases.
Used for entity extraction and matching.
"""

from typing import Dict, List, Optional, Set


# US Presidential Candidates (recent/relevant)
US_PRESIDENTIAL_CANDIDATES: Dict[str, List[str]] = {
    "donald trump": [
        "trump", "donald j trump", "djt", "the donald",
        "president trump", "former president trump", "45th president",
        "trump 2024", "maga"
    ],
    "joe biden": [
        "biden", "joseph biden", "president biden", "potus biden",
        "joe", "46th president", "biden 2024"
    ],
    "kamala harris": [
        "harris", "kamala", "vp harris", "vice president harris",
        "kamala 2024"
    ],
    "ron desantis": [
        "desantis", "ron", "governor desantis", "gov desantis",
        "desantis 2024"
    ],
    "nikki haley": [
        "haley", "nikki", "ambassador haley", "haley 2024"
    ],
    "vivek ramaswamy": [
        "vivek", "ramaswamy", "vivek 2024"
    ],
    "mike pence": [
        "pence", "michael pence", "vp pence", "vice president pence"
    ],
    "chris christie": [
        "christie", "governor christie"
    ],
    "gavin newsom": [
        "newsom", "governor newsom", "gov newsom", "california governor"
    ],
    "michelle obama": [
        "michelle", "mrs obama", "former first lady obama"
    ],
    "robert f kennedy jr": [
        "rfk", "rfk jr", "bobby kennedy", "kennedy jr", "rfk junior"
    ],
}

# US Senate/Congress Notable Figures
US_CONGRESS: Dict[str, List[str]] = {
    "mitch mcconnell": [
        "mcconnell", "senator mcconnell", "senate minority leader"
    ],
    "chuck schumer": [
        "schumer", "senator schumer", "senate majority leader"
    ],
    "nancy pelosi": [
        "pelosi", "speaker pelosi", "former speaker pelosi"
    ],
    "kevin mccarthy": [
        "mccarthy", "speaker mccarthy", "house speaker mccarthy"
    ],
    "mike johnson": [
        "johnson", "speaker johnson", "house speaker johnson"
    ],
    "aoc": [
        "alexandria ocasio-cortez", "ocasio-cortez", "ocasio cortez",
        "rep ocasio-cortez"
    ],
    "bernie sanders": [
        "bernie", "sanders", "senator sanders", "bernie 2024"
    ],
    "elizabeth warren": [
        "warren", "senator warren", "liz warren"
    ],
    "ted cruz": [
        "cruz", "senator cruz"
    ],
    "marco rubio": [
        "rubio", "senator rubio"
    ],
    "rand paul": [
        "rand", "senator paul", "dr paul"
    ],
    "mitt romney": [
        "romney", "senator romney", "governor romney"
    ],
}

# Supreme Court Justices
SUPREME_COURT: Dict[str, List[str]] = {
    "john roberts": [
        "roberts", "chief justice roberts", "chief justice"
    ],
    "clarence thomas": [
        "thomas", "justice thomas"
    ],
    "samuel alito": [
        "alito", "justice alito"
    ],
    "sonia sotomayor": [
        "sotomayor", "justice sotomayor"
    ],
    "elena kagan": [
        "kagan", "justice kagan"
    ],
    "neil gorsuch": [
        "gorsuch", "justice gorsuch"
    ],
    "brett kavanaugh": [
        "kavanaugh", "justice kavanaugh"
    ],
    "amy coney barrett": [
        "barrett", "acb", "justice barrett", "amy barrett"
    ],
    "ketanji brown jackson": [
        "jackson", "kbj", "justice jackson", "ketanji jackson"
    ],
}

# International Political Figures
INTERNATIONAL: Dict[str, List[str]] = {
    "vladimir putin": [
        "putin", "russian president", "president putin"
    ],
    "volodymyr zelenskyy": [
        "zelenskyy", "zelensky", "ukrainian president", "president zelenskyy"
    ],
    "xi jinping": [
        "xi", "president xi", "chinese president"
    ],
    "benjamin netanyahu": [
        "netanyahu", "bibi", "israeli prime minister", "pm netanyahu"
    ],
    "rishi sunak": [
        "sunak", "pm sunak", "british prime minister", "uk prime minister"
    ],
    "emmanuel macron": [
        "macron", "french president", "president macron"
    ],
    "olaf scholz": [
        "scholz", "german chancellor", "chancellor scholz"
    ],
    "justin trudeau": [
        "trudeau", "canadian prime minister", "pm trudeau"
    ],
    "narendra modi": [
        "modi", "indian prime minister", "pm modi"
    ],
}

# Federal Reserve / Economic Officials
FED_OFFICIALS: Dict[str, List[str]] = {
    "jerome powell": [
        "powell", "jay powell", "fed chair", "fed chairman",
        "federal reserve chair", "chairman powell"
    ],
    "janet yellen": [
        "yellen", "treasury secretary yellen", "secretary yellen"
    ],
}

# Political Parties and Organizations
POLITICAL_ORGS: Dict[str, List[str]] = {
    "republican party": [
        "republicans", "gop", "rnc", "republican", "red party"
    ],
    "democratic party": [
        "democrats", "dnc", "democratic", "dem", "dems", "blue party"
    ],
    "libertarian party": [
        "libertarians", "libertarian"
    ],
    "green party": [
        "greens", "green"
    ],
    "independent": [
        "ind", "no party", "unaffiliated", "third party"
    ],
}

# Combine all candidates
CANDIDATES: Dict[str, List[str]] = {}
CANDIDATES.update(US_PRESIDENTIAL_CANDIDATES)
CANDIDATES.update(US_CONGRESS)
CANDIDATES.update(SUPREME_COURT)
CANDIDATES.update(INTERNATIONAL)
CANDIDATES.update(FED_OFFICIALS)
CANDIDATES.update(POLITICAL_ORGS)

# Build reverse lookup (alias -> canonical)
_ALIAS_TO_CANONICAL: Dict[str, str] = {}
for canonical, aliases in CANDIDATES.items():
    _ALIAS_TO_CANONICAL[canonical.lower()] = canonical
    for alias in aliases:
        _ALIAS_TO_CANONICAL[alias.lower()] = canonical


def get_candidate_canonical(text: str) -> Optional[str]:
    """Get canonical candidate name from any alias.

    Args:
        text: Candidate name or alias to look up

    Returns:
        Canonical name or None if not found
    """
    return _ALIAS_TO_CANONICAL.get(text.lower().strip())


def get_all_candidate_aliases(canonical_name: str) -> List[str]:
    """Get all aliases for a canonical candidate name.

    Args:
        canonical_name: The canonical name

    Returns:
        List of aliases including the canonical name
    """
    canonical_lower = canonical_name.lower()
    if canonical_lower in CANDIDATES:
        return [canonical_name] + CANDIDATES[canonical_lower]
    return []


def get_all_aliases() -> Set[str]:
    """Get all known candidate aliases."""
    return set(_ALIAS_TO_CANONICAL.keys())


def get_candidate_type(name: str) -> Optional[str]:
    """Get the type/category of a candidate.

    Args:
        name: Name or alias to check

    Returns:
        Category string or None
    """
    canonical = get_candidate_canonical(name)
    if canonical is None:
        return None

    canonical_lower = canonical.lower()
    if canonical_lower in US_PRESIDENTIAL_CANDIDATES:
        return "presidential"
    elif canonical_lower in US_CONGRESS:
        return "congress"
    elif canonical_lower in SUPREME_COURT:
        return "supreme_court"
    elif canonical_lower in INTERNATIONAL:
        return "international"
    elif canonical_lower in FED_OFFICIALS:
        return "fed_official"
    elif canonical_lower in POLITICAL_ORGS:
        return "political_org"
    return None


def is_us_politician(name: str) -> bool:
    """Check if a name refers to a US politician.

    Args:
        name: Name or alias to check

    Returns:
        True if it's a US politician
    """
    candidate_type = get_candidate_type(name)
    return candidate_type in ("presidential", "congress", "supreme_court", "fed_official")

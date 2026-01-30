"""
Sports Team Alias Database.

Canonical team names mapped to their known aliases.
Used for entity extraction and matching.
"""

from typing import Dict, List, Optional, Set


# NHL Teams
NHL_TEAMS: Dict[str, List[str]] = {
    # Eastern Conference - Atlantic Division
    "boston bruins": ["bos", "bruins", "b's", "boston"],
    "buffalo sabres": ["buf", "sabres", "buffalo"],
    "detroit red wings": ["det", "red wings", "wings", "detroit"],
    "florida panthers": ["fla", "panthers", "florida", "cats"],
    "montreal canadiens": ["mtl", "canadiens", "habs", "montreal", "les habs"],
    "ottawa senators": ["ott", "senators", "sens", "ottawa"],
    "tampa bay lightning": ["tbl", "tb", "lightning", "tampa bay", "tampa", "bolts"],
    "toronto maple leafs": ["tor", "maple leafs", "leafs", "toronto"],

    # Eastern Conference - Metropolitan Division
    "carolina hurricanes": ["car", "hurricanes", "canes", "carolina"],
    "columbus blue jackets": ["cbj", "blue jackets", "jackets", "columbus"],
    "new jersey devils": ["njd", "nj", "devils", "new jersey"],
    "new york islanders": ["nyi", "isles", "islanders"],
    "new york rangers": ["nyr", "rangers", "blueshirts"],
    "philadelphia flyers": ["phi", "flyers", "philly", "philadelphia"],
    "pittsburgh penguins": ["pit", "penguins", "pens", "pittsburgh"],
    "washington capitals": ["wsh", "was", "capitals", "caps", "washington"],

    # Western Conference - Central Division
    "arizona coyotes": ["ari", "coyotes", "yotes", "arizona"],
    "chicago blackhawks": ["chi", "blackhawks", "hawks", "chicago"],
    "colorado avalanche": ["col", "avalanche", "avs", "colorado"],
    "dallas stars": ["dal", "stars", "dallas"],
    "minnesota wild": ["min", "wild", "minnesota"],
    "nashville predators": ["nsh", "predators", "preds", "nashville"],
    "st louis blues": ["stl", "blues", "st louis", "saint louis"],
    "winnipeg jets": ["wpg", "jets", "winnipeg"],

    # Western Conference - Pacific Division
    "anaheim ducks": ["ana", "ducks", "anaheim"],
    "calgary flames": ["cgy", "flames", "calgary"],
    "edmonton oilers": ["edm", "oilers", "edmonton", "oil"],
    "los angeles kings": ["lak", "la kings", "kings", "los angeles"],
    "san jose sharks": ["sjs", "sharks", "san jose", "sj"],
    "seattle kraken": ["sea", "kraken", "seattle"],
    "vancouver canucks": ["van", "canucks", "vancouver", "nucks"],
    "vegas golden knights": ["vgk", "vegas", "golden knights", "knights", "las vegas"],
}

# NBA Teams
NBA_TEAMS: Dict[str, List[str]] = {
    # Eastern Conference - Atlantic Division
    "boston celtics": ["bos", "celtics", "boston", "c's"],
    "brooklyn nets": ["bkn", "nets", "brooklyn"],
    "new york knicks": ["nyk", "knicks", "new york", "ny knicks"],
    "philadelphia 76ers": ["phi", "76ers", "sixers", "philly", "philadelphia"],
    "toronto raptors": ["tor", "raptors", "toronto", "raps"],

    # Eastern Conference - Central Division
    "chicago bulls": ["chi", "bulls", "chicago"],
    "cleveland cavaliers": ["cle", "cavaliers", "cavs", "cleveland"],
    "detroit pistons": ["det", "pistons", "detroit"],
    "indiana pacers": ["ind", "pacers", "indiana"],
    "milwaukee bucks": ["mil", "bucks", "milwaukee"],

    # Eastern Conference - Southeast Division
    "atlanta hawks": ["atl", "hawks", "atlanta"],
    "charlotte hornets": ["cha", "hornets", "charlotte"],
    "miami heat": ["mia", "heat", "miami"],
    "orlando magic": ["orl", "magic", "orlando"],
    "washington wizards": ["was", "wizards", "washington"],

    # Western Conference - Northwest Division
    "denver nuggets": ["den", "nuggets", "denver"],
    "minnesota timberwolves": ["min", "timberwolves", "wolves", "minnesota", "t-wolves"],
    "oklahoma city thunder": ["okc", "thunder", "oklahoma city", "oklahoma"],
    "portland trail blazers": ["por", "trail blazers", "blazers", "portland"],
    "utah jazz": ["uta", "jazz", "utah"],

    # Western Conference - Pacific Division
    "golden state warriors": ["gsw", "warriors", "golden state", "gs warriors", "dubs"],
    "los angeles clippers": ["lac", "clippers", "la clippers"],
    "los angeles lakers": ["lal", "lakers", "la lakers", "los angeles lakers"],
    "phoenix suns": ["phx", "suns", "phoenix"],
    "sacramento kings": ["sac", "kings", "sacramento"],

    # Western Conference - Southwest Division
    "dallas mavericks": ["dal", "mavericks", "mavs", "dallas"],
    "houston rockets": ["hou", "rockets", "houston"],
    "memphis grizzlies": ["mem", "grizzlies", "grizz", "memphis"],
    "new orleans pelicans": ["nop", "pelicans", "new orleans", "pels"],
    "san antonio spurs": ["sas", "spurs", "san antonio"],
}

# NFL Teams
NFL_TEAMS: Dict[str, List[str]] = {
    # AFC East
    "buffalo bills": ["buf", "bills", "buffalo"],
    "miami dolphins": ["mia", "dolphins", "miami", "fins"],
    "new england patriots": ["ne", "patriots", "pats", "new england"],
    "new york jets": ["nyj", "jets", "ny jets"],

    # AFC North
    "baltimore ravens": ["bal", "ravens", "baltimore"],
    "cincinnati bengals": ["cin", "bengals", "cincinnati"],
    "cleveland browns": ["cle", "browns", "cleveland"],
    "pittsburgh steelers": ["pit", "steelers", "pittsburgh"],

    # AFC South
    "houston texans": ["hou", "texans", "houston"],
    "indianapolis colts": ["ind", "colts", "indianapolis", "indy"],
    "jacksonville jaguars": ["jax", "jaguars", "jags", "jacksonville"],
    "tennessee titans": ["ten", "titans", "tennessee"],

    # AFC West
    "denver broncos": ["den", "broncos", "denver"],
    "kansas city chiefs": ["kc", "chiefs", "kansas city"],
    "las vegas raiders": ["lv", "raiders", "las vegas", "vegas raiders"],
    "los angeles chargers": ["lac", "chargers", "la chargers"],

    # NFC East
    "dallas cowboys": ["dal", "cowboys", "dallas", "america's team"],
    "new york giants": ["nyg", "giants", "ny giants"],
    "philadelphia eagles": ["phi", "eagles", "philly", "philadelphia"],
    "washington commanders": ["was", "commanders", "washington", "commies"],

    # NFC North
    "chicago bears": ["chi", "bears", "chicago", "da bears"],
    "detroit lions": ["det", "lions", "detroit"],
    "green bay packers": ["gb", "packers", "green bay"],
    "minnesota vikings": ["min", "vikings", "minnesota", "vikes"],

    # NFC South
    "atlanta falcons": ["atl", "falcons", "atlanta"],
    "carolina panthers": ["car", "panthers", "carolina"],
    "new orleans saints": ["no", "saints", "new orleans"],
    "tampa bay buccaneers": ["tb", "buccaneers", "bucs", "tampa bay", "tampa"],

    # NFC West
    "arizona cardinals": ["ari", "cardinals", "arizona", "cards"],
    "los angeles rams": ["lar", "rams", "la rams"],
    "san francisco 49ers": ["sf", "49ers", "niners", "san francisco"],
    "seattle seahawks": ["sea", "seahawks", "seattle", "hawks"],
}

# MLB Teams
MLB_TEAMS: Dict[str, List[str]] = {
    # American League East
    "baltimore orioles": ["bal", "orioles", "baltimore", "o's"],
    "boston red sox": ["bos", "red sox", "boston", "sox"],
    "new york yankees": ["nyy", "yankees", "new york", "bronx bombers"],
    "tampa bay rays": ["tb", "rays", "tampa bay", "tampa"],
    "toronto blue jays": ["tor", "blue jays", "jays", "toronto"],

    # American League Central
    "chicago white sox": ["cws", "white sox", "chicago", "chisox"],
    "cleveland guardians": ["cle", "guardians", "cleveland"],
    "detroit tigers": ["det", "tigers", "detroit"],
    "kansas city royals": ["kc", "royals", "kansas city"],
    "minnesota twins": ["min", "twins", "minnesota"],

    # American League West
    "houston astros": ["hou", "astros", "houston", "stros"],
    "los angeles angels": ["laa", "angels", "la angels", "halos"],
    "oakland athletics": ["oak", "athletics", "a's", "oakland"],
    "seattle mariners": ["sea", "mariners", "seattle", "m's"],
    "texas rangers": ["tex", "rangers", "texas"],

    # National League East
    "atlanta braves": ["atl", "braves", "atlanta"],
    "miami marlins": ["mia", "marlins", "miami"],
    "new york mets": ["nym", "mets", "new york", "amazins"],
    "philadelphia phillies": ["phi", "phillies", "philly", "philadelphia"],
    "washington nationals": ["was", "nationals", "nats", "washington"],

    # National League Central
    "chicago cubs": ["chc", "cubs", "chicago", "cubbies"],
    "cincinnati reds": ["cin", "reds", "cincinnati"],
    "milwaukee brewers": ["mil", "brewers", "milwaukee", "crew"],
    "pittsburgh pirates": ["pit", "pirates", "pittsburgh", "buccos"],
    "st louis cardinals": ["stl", "cardinals", "cards", "st louis", "saint louis"],

    # National League West
    "arizona diamondbacks": ["ari", "diamondbacks", "d-backs", "arizona", "dbacks"],
    "colorado rockies": ["col", "rockies", "colorado"],
    "los angeles dodgers": ["lad", "dodgers", "la dodgers"],
    "san diego padres": ["sd", "padres", "san diego"],
    "san francisco giants": ["sf", "giants", "san francisco"],
}

# Combine all teams
TEAMS: Dict[str, List[str]] = {}
TEAMS.update(NHL_TEAMS)
TEAMS.update(NBA_TEAMS)
TEAMS.update(NFL_TEAMS)
TEAMS.update(MLB_TEAMS)

# Build reverse lookup (alias -> canonical)
_ALIAS_TO_CANONICAL: Dict[str, str] = {}
for canonical, aliases in TEAMS.items():
    _ALIAS_TO_CANONICAL[canonical.lower()] = canonical
    for alias in aliases:
        _ALIAS_TO_CANONICAL[alias.lower()] = canonical


def get_team_canonical(text: str) -> Optional[str]:
    """Get canonical team name from any alias.

    Args:
        text: Team name or alias to look up

    Returns:
        Canonical team name or None if not found
    """
    return _ALIAS_TO_CANONICAL.get(text.lower().strip())


def get_all_team_aliases(canonical_name: str) -> List[str]:
    """Get all aliases for a canonical team name.

    Args:
        canonical_name: The canonical team name

    Returns:
        List of aliases including the canonical name
    """
    canonical_lower = canonical_name.lower()
    if canonical_lower in TEAMS:
        return [canonical_name] + TEAMS[canonical_lower]
    return []


def get_all_aliases() -> Set[str]:
    """Get all known team aliases."""
    return set(_ALIAS_TO_CANONICAL.keys())


def get_league_teams(league: str) -> Dict[str, List[str]]:
    """Get teams for a specific league.

    Args:
        league: One of 'nhl', 'nba', 'nfl', 'mlb'

    Returns:
        Dict of canonical names to aliases for that league
    """
    league_map = {
        'nhl': NHL_TEAMS,
        'nba': NBA_TEAMS,
        'nfl': NFL_TEAMS,
        'mlb': MLB_TEAMS,
    }
    return league_map.get(league.lower(), {})

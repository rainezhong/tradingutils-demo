import requests

def get_nhllive_games():
    # Official NHL Edge API endpoint
    url = "https://api-web.nhle.com/v1/score/now"
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException:
        print("Error fetching NHL data")
        return []

    # FULL MAPPING: Official API (3-Letter) -> Kalshi (Mixed)
    # Most match, but we define all to be safe.
    kalshi_map = {
        # --- The 4 Exceptions (2-Letter Codes) ---
        "NJD": "NJ",   # New Jersey
        "LAK": "LA",   # Los Angeles
        "TBL": "TB",   # Tampa Bay
        "SJS": "SJ",   # San Jose
        
        # --- The Rest (Standard 3-Letter Codes) ---
        "ANA": "ANA", "BOS": "BOS", "BUF": "BUF", "CGY": "CGY",
        "CAR": "CAR", "CHI": "CHI", "COL": "COL", "CBJ": "CBJ",
        "DAL": "DAL", "DET": "DET", "EDM": "EDM", "FLA": "FLA",
        "MIN": "MIN", "MTL": "MTL", "NSH": "NSH", "NYI": "NYI",
        "NYR": "NYR", "OTT": "OTT", "PHI": "PHI", "PIT": "PIT",
        "SEA": "SEA", "STL": "STL", "TOR": "TOR", "UTA": "UTA",
        "VAN": "VAN", "VGK": "VGK", "WSH": "WSH", "WPG": "WPG"
    }

    live_games = []

    for game in data.get('games', []):
        # Filter for Live Games
        if game.get('gameState') in ['LIVE', 'CRIT']:
            away = game.get('awayTeam', {})
            home = game.get('homeTeam', {})
            clock = game.get('clock', {})
            period_desc = game.get('periodDescriptor', {})

            # Clock Formatting
            per_num = period_desc.get('number', 0)
            per_type = period_desc.get('periodType', 'REG')
            
            if per_type == 'OT':
                period_display = "OT"
            elif per_type == 'SO':
                period_display = "SO"
            else:
                period_display = f"P{per_num}"

            # --- TRANSLATION STEP ---
            # Get official 3-letter code
            a_code_official = away.get('abbrev')
            h_code_official = home.get('abbrev')
            
            # Translate to Kalshi code (Default to official if missing)
            a_kalshi = kalshi_map.get(a_code_official, a_code_official)
            h_kalshi = kalshi_map.get(h_code_official, h_code_official)
            # ------------------------

            live_games.append({
                'id': game.get('id'),
                # Creates "NJEDM" instead of "NJDEDM"
                'matchup': f"{a_kalshi}{h_kalshi}",
                'score': f"{away.get('score', 0)} - {home.get('score', 0)}",
                'clock': f"{period_display} {clock.get('timeRemaining', '00:00')}"
            })
            
    return live_games
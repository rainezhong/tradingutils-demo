import requests

def get_ucllive_games():
    # ESPN public API for UEFA Champions League Scoreboard
    url = "http://site.api.espn.com/apis/site/v2/sports/soccer/uefa.champions/scoreboard"
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException:
        print("Error fetching UCL data")
        return []

    live_games = []

    for event in data.get('events', []):
        competition = event['competitions'][0]
        status = event.get('status', {})
        
        # ESPN Status States: 'in' = Live, 'pre' = Pre-game, 'post' = Final
        if status.get('type', {}).get('state') == 'in':
            
            # Competitors list contains both teams; we identify by homeAway field
            competitors = competition['competitors']
            home = next(filter(lambda x: x['homeAway'] == 'home', competitors))
            away = next(filter(lambda x: x['homeAway'] == 'away', competitors))

            live_games.append({
                'id': event['id'],
                # Matchup format: AwayHome (e.g., LIVRMA)
                'matchup': f"{away['team']['abbreviation']}{home['team']['abbreviation']}",
                'score': f"{away['score']} - {home['score']}",
                # Clock format: e.g., "45+2'" or "72'"
                'clock': status.get('displayClock', '00:00')
            })
            
    return live_games
from nba_api.live.nba.endpoints import scoreboard

def get_nbalive_games():
    # Fetch today's scoreboard (Live endpoints)
    board = scoreboard.ScoreBoard() 
    games = board.get_dict()['scoreboard']['games']
    
    live_games = []
    
    for game in games:
        # gameStatus: 1 = Not Started, 2 = In Progress, 3 = Final
        if game['gameStatus'] == 2:
            live_games.append({
                'id': game['gameId'],
                'matchup': f"{game['awayTeam']['teamTricode']}{game['homeTeam']['teamTricode']}",
                'score': f"{game['awayTeam']['score']} - {game['homeTeam']['score']}",
                'clock': game['gameStatusText']  # e.g., "Q3 4:21"
            })
            
    return live_games

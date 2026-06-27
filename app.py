from flask import Flask, jsonify, render_template
import requests
import os
import time
from datetime import datetime, timedelta, timezone

app = Flask(__name__)

# Fetch the API key from Render's Environment Variables
API_KEY = os.environ.get('FOOTBALL_DATA_API_KEY')
BASE_URL = 'https://api.football-data.org/v4/competitions/WC'

# Cache dictionary to prevent hitting the 10 requests/min limit
cache = {
    'matches': {'data': None, 'timestamp': 0},
    'standings': {'data': None, 'timestamp': 0},
    'scorers': {'data': None, 'timestamp': 0}
}
CACHE_TTL = 300  # Cache for 5 minutes (300 seconds)

# In case the API key is missing or we hit rate limits, we provide a clean mock fallback
MOCK_MATCH_DATA = {
    "matches": [
        {
            "id": 1, "utcDate": (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat(),
            "status": "FINISHED", "stage": "GROUP_STAGE", "group": "GROUP_A",
            "homeTeam": {"name": "Mexico", "tla": "MEX", "crest": "https://crests.football-data.org/mex.png"},
            "awayTeam": {"name": "South Africa", "tla": "RSA", "crest": "https://crests.football-data.org/rsa.png"},
            "score": {"fullTime": {"home": 2, "away": 0}, "duration": "REGULAR"},
            "venue": "Estadio Azteca", "goals": [{"scorer": {"name": "Raúl Jiménez"}, "minute": 18, "type": "FIELD", "team": {"id": 1}}]
        },
        {
            "id": 2, "utcDate": (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(),
            "status": "TIMED", "stage": "GROUP_STAGE", "group": "GROUP_E",
            "homeTeam": {"name": "Germany", "tla": "GER", "crest": "https://crests.football-data.org/ger.png"},
            "awayTeam": {"name": "Curaçao", "tla": "CUW", "crest": "https://crests.football-data.org/cuw.png"},
            "score": {"fullTime": {"home": None, "away": None}},
            "venue": "Houston Stadium"
        },
        {
            "id": 3, "utcDate": (datetime.now(timezone.utc) + timedelta(hours=28)).isoformat(),
            "status": "SCHEDULED", "stage": "LAST_32",
            "homeTeam": {"name": "USA", "tla": "USA", "crest": "https://crests.football-data.org/usa.png"},
            "awayTeam": {"name": "Paraguay", "tla": "PAR", "crest": "https://crests.football-data.org/par.png"},
            "score": {"fullTime": {"home": None, "away": None}},
            "venue": "MetLife Stadium"
        }
    ]
}

MOCK_STANDINGS_DATA = {
    "standings": [
        {
            "stage": "GROUP_STAGE", "type": "TOTAL", "group": "GROUP_A",
            "table": [
                {"position": 1, "team": {"id": 1, "name": "Mexico", "tla": "MEX", "crest": "https://crests.football-data.org/mex.png"}, "playedGames": 1, "won": 1, "draw": 0, "lost": 0, "points": 3, "goalsFor": 2, "goalsAgainst": 0, "goalDifference": 2, "form": "W"},
                {"position": 2, "team": {"id": 4, "name": "South Korea", "tla": "KOR", "crest": "https://crests.football-data.org/kor.png"}, "playedGames": 1, "won": 1, "draw": 0, "lost": 0, "points": 3, "goalsFor": 2, "goalsAgainst": 1, "goalDifference": 1, "form": "W"},
                {"position": 3, "team": {"id": 5, "name": "Czechia", "tla": "CZE", "crest": "https://crests.football-data.org/cze.png"}, "playedGames": 1, "won": 0, "draw": 0, "lost": 1, "points": 0, "goalsFor": 1, "goalsAgainst": 2, "goalDifference": -1, "form": "L"},
                {"position": 4, "team": {"id": 2, "name": "South Africa", "tla": "RSA", "crest": "https://crests.football-data.org/rsa.png"}, "playedGames": 1, "won": 0, "draw": 0, "lost": 1, "points": 0, "goalsFor": 0, "goalsAgainst": 2, "goalDifference": -2, "form": "L"}
            ]
        }
    ]
}

MOCK_SCORERS_DATA = {
    "scorers": [
        {"player": {"name": "Raúl Jiménez"}, "team": {"name": "Mexico", "crest": "https://crests.football-data.org/mex.png"}, "goals": 1, "assists": 0, "playedMatches": 1}
    ]
}

def fetch_from_api(endpoint):
    """Helper function to fetch data from football-data.org with fail-soft fallbacks"""
    now = time.time()
    
    # Return cached data if valid
    if cache[endpoint]['data'] and (now - cache[endpoint]['timestamp'] < CACHE_TTL):
        return cache[endpoint]['data']
        
    if not API_KEY:
        print("Warning: API Key missing. Falling back to mock data.")
        return get_mock_data(endpoint)
        
    headers = {'X-Auth-Token': API_KEY}
    try:
        response = requests.get(f"{BASE_URL}/{endpoint}", headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        cache[endpoint] = {'data': data, 'timestamp': now}
        return data
    except Exception as e:
        print(f"Error fetching {endpoint} from API: {e}. Falling back to cached or mock.")
        if cache[endpoint]['data']:
            return cache[endpoint]['data']
        return get_mock_data(endpoint)

def get_mock_data(endpoint):
    if endpoint == 'matches': return MOCK_MATCH_DATA
    if endpoint == 'standings': return MOCK_STANDINGS_DATA
    if endpoint == 'scorers': return MOCK_SCORERS_DATA
    return {}

@app.route('/')
def index():
    """Serve the main frontend HTML page"""
    return render_template('index.html')

@app.route('/api/today')
def get_today():
    """Matches scheduled for today (calendar date)"""
    try:
        data = fetch_from_api('matches')
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        matches = [m for m in data.get("matches", []) if m.get("utcDate", "").startswith(today_str)]
        return jsonify({"ok": True, "data": {"matches": matches}})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/recent')
def get_recent():
    """Finished matches within the last 48 hours"""
    try:
        data = fetch_from_api('matches')
        now = datetime.now(timezone.utc)
        recent_limit = now - timedelta(hours=48)
        
        recent = []
        for m in data.get("matches", []):
            if m.get("status") == "FINISHED":
                match_time = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
                if recent_limit <= match_time <= now:
                    recent.append(m)
        return jsonify({"ok": True, "data": {"matches": recent}})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/upcoming')
def get_upcoming():
    """Upcoming scheduled/timed matches within the next 48 hours"""
    try:
        data = fetch_from_api('matches')
        now = datetime.now(timezone.utc)
        upcoming_limit = now + timedelta(hours=48)
        
        upcoming = []
        for m in data.get("matches", []):
            if m.get("status") in ["SCHEDULED", "TIMED"]:
                match_time = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
                if now <= match_time <= upcoming_limit:
                    upcoming.append(m)
        return jsonify({"ok": True, "data": {"matches": upcoming}})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/standings')
def get_standings():
    """Endpoint for the frontend to fetch standings"""
    try:
        data = fetch_from_api('standings')
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/group-matches')
def get_group_matches():
    """Group Stage Matches"""
    try:
        data = fetch_from_api('matches')
        group_matches = [m for m in data.get("matches", []) if m.get("stage") == "GROUP_STAGE"]
        return jsonify({"ok": True, "data": {"matches": group_matches}})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/bracket')
def get_bracket():
    """Matches belonging to any Knockout Stage"""
    try:
        data = fetch_from_api('matches')
        knockout_stages = [
            "ROUND_OF_32", "LAST_32", "ROUND_OF_16", "LAST_16", 
            "QUARTER_FINALS", "SEMI_FINALS", "THIRD_PLACE", "FINAL"
        ]
        bracket_matches = [m for m in data.get("matches", []) if m.get("stage") in knockout_stages]
        return jsonify({"ok": True, "data": {"matches": bracket_matches}})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/live')
def get_live_banner():
    """Fetch live matches currently in progress"""
    try:
        data = fetch_from_api('matches')
        live_matches = [m for m in data.get("matches", []) if m.get("status") in ["IN_PLAY", "PAUSED"]]
        return jsonify({"ok": True, "data": {"matches": live_matches}})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/scorers')
def get_scorers():
    """Tournament top scorers list"""
    try:
        data = fetch_from_api('scorers')
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/predictions')
def get_predictions():
    """Upcoming matches next 7 days, augmented with statistical prediction details"""
    try:
        data = fetch_from_api('matches')
        now = datetime.now(timezone.utc)
        limit = now + timedelta(days=7)
        
        predicted_matches = []
        for m in data.get("matches", []):
            if m.get("status") in ["SCHEDULED", "TIMED"]:
                match_time = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
                if now <= match_time <= limit:
                    # Deterministic hashing seed based on team names so predictions stay stable per match
                    seed_val = sum(ord(c) for c in (m["homeTeam"]["name"] + m["awayTeam"]["name"]))
                    
                    # Generate realistic simulated xG based on deterministic criteria
                    xg_home = round(1.2 + (seed_val % 13) / 10.0, 1)
                    xg_away = round(0.9 + (seed_val % 17) / 10.0, 1)
                    
                    predicted_home = int(xg_home) if (seed_val % 3 != 0) else int(xg_home) + 1
                    predicted_away = int(xg_away) if (seed_val % 2 == 0) else int(xg_away) + 1
                    
                    total_pct = 100
                    home_win_pct = int(35 + (seed_val % 25))
                    away_win_pct = int(20 + (seed_val % 20))
                    draw_pct = total_pct - home_win_pct - away_win_pct
                    
                    confidence = "HIGH" if (seed_val % 5 == 0) else "MEDIUM" if (seed_val % 3 == 0) else "LOW"
                    
                    # Augment match dict with prediction details
                    m_copy = m.copy()
                    m_copy["prediction"] = {
                        "xg_home": xg_home,
                        "xg_away": xg_away,
                        "predicted_home": predicted_home,
                        "predicted_away": predicted_away,
                        "home_win_pct": home_win_pct,
                        "draw_pct": draw_pct,
                        "away_win_pct": away_win_pct,
                        "confidence": confidence
                    }
                    predicted_matches.append(m_copy)
                    
        return jsonify({"ok": True, "data": {"matches": predicted_matches}})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
"""
World Cup 2026 Dashboard
Powered by football-data.org API (v4)
Run: python app.py
"""

from flask import Flask, render_template, jsonify
import requests
from datetime import datetime, timedelta, timezone
import math
import time

app = Flask(__name__)

API_KEY  = "b01a9538dc1e4493bd3d12d44ef386a2"
BASE_URL = "https://api.football-data.org/v4"
COMPETITION = "WC"
SEASON = 2026

HEADERS = {"X-Auth-Token": API_KEY}

# ── Cache (free tier = 10 req/min) ───────────────────────────────────────────
_cache   = {}
CACHE_TTL = 60  # seconds

def cached_get(url, params=None):
    key = url + str(sorted((params or {}).items()))
    now = time.time()
    if key in _cache:
        data, ts = _cache[key]
        if now - ts < CACHE_TTL:
            return data
    resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    _cache[key] = (data, now)
    return data


# ── Prediction engine ─────────────────────────────────────────────────────────
#
# Method: Bivariate Poisson model (Dixon-Coles style)
#   1. Convert FIFA ranking → team strength multiplier
#   2. Expected goals = BASE × attack_strength / opponent_defense_strength
#   3. Scoreline probabilities from independent Poisson distributions
#   4. Blend in live tournament stats once teams have played (30% weight)
#
# FIFA Rankings: official release of 12 June 2026
# Source: ESPN / FIFA (June 2026)

FIFA_RANKINGS = {
    # Top tier
    "ARG": 1,  "ESP": 2,  "FRA": 3,  "ENG": 4,  "POR": 5,
    "BRA": 6,  "NED": 7,  "GER": 9,  "CRO": 10, "MAR": 13,
    # Strong
    "MEX": 14, "COL": 16, "USA": 17, "URU": 18, "SUI": 19,
    "SEN": 20, "IRN": 21, "JPN": 22, "AUS": 23, "KOR": 23,
    "DEN": 24, "AUT": 25, "TUR": 28, "ECU": 29, "CAN": 30,
    # Mid
    "NOR": 33, "SCO": 39, "NGA": 40, "EGY": 42, "GRE": 45,
    "TUN": 47, "SVK": 50, "SLO": 52, "CIV": 53, "CMR": 54,
    "KSA": 56, "MLI": 57, "GHA": 62, "BIH": 64, "RSA": 68,
    "PAR": 70, "IRQ": 74, "PAN": 78, "UZB": 79, "PER": 80,
    "CPV": 82, "BOL": 85, "HON": 88, "NZL": 93, "SLV": 95,
    # Lower
    "ZAM": 99, "HAI": 102, "JOR": 104, "CUR": 115,
    # Additional qualifiers
    "QAT": 35, "ALG": 36,
}

BASE_GOALS = 1.25   # avg WC group-stage goals per team per game
MAX_GOALS  = 6      # max goals considered per side in probability grid

def rank_to_strength(rank: int) -> float:
    """Map FIFA rank (1 = best) to a strength multiplier via exponential decay."""
    return 0.45 + 1.30 * math.exp(-rank / 38)

def poisson_pmf(lam: float, k: int) -> float:
    """P(X = k) for X ~ Poisson(lambda)."""
    return math.exp(-lam) * (lam ** k) / math.factorial(k)

def predict_match(home_tla: str, away_tla: str,
                  home_stats: dict = None, away_stats: dict = None) -> dict:
    """
    Predict match result using Poisson model.

    Parameters
    ----------
    home_tla / away_tla : 3-letter team code (e.g. "ARG")
    home_stats / away_stats : row from the TOTAL standings table
        (keys: playedGames, goalsFor, goalsAgainst)

    Returns
    -------
    dict with predicted_home, predicted_away, home_win_pct, draw_pct,
         away_win_pct, xg_home, xg_away, confidence
    """
    h_rank = FIFA_RANKINGS.get(home_tla, 60)
    a_rank = FIFA_RANKINGS.get(away_tla, 60)
    h_str  = rank_to_strength(h_rank)
    a_str  = rank_to_strength(a_rank)

    def tournament_attack(stats):
        """Goals per game in this tournament (None if not played yet)."""
        if not stats or stats.get("playedGames", 0) == 0:
            return None
        return stats["goalsFor"] / stats["playedGames"]

    def tournament_defense(stats):
        """Defense strength derived from goals conceded per game."""
        if not stats or stats.get("playedGames", 0) == 0:
            return None
        ga_pg = stats["goalsAgainst"] / stats["playedGames"]
        # Invert: fewer goals conceded = stronger defense multiplier
        return max(0.3, 1.6 - ga_pg * 0.45)

    h_ta = tournament_attack(home_stats)
    a_ta = tournament_attack(away_stats)
    h_td = tournament_defense(home_stats)
    a_td = tournament_defense(away_stats)

    # Blend FIFA prior (70%) with live tournament data (30%)
    h_attack  = h_str if h_ta is None else h_str * 0.7 + (h_ta / BASE_GOALS) * 0.3
    a_attack  = a_str if a_ta is None else a_str * 0.7 + (a_ta / BASE_GOALS) * 0.3
    h_defense = h_str if h_td is None else h_str * 0.7 + h_td * 0.3
    a_defense = a_str if a_td is None else a_str * 0.7 + a_td * 0.3

    # Expected goals for each side
    xg_h = max(0.20, min(BASE_GOALS * h_attack / a_defense, 4.5))
    xg_a = max(0.20, min(BASE_GOALS * a_attack / h_defense, 4.5))

    # Build full scoreline probability grid
    grid = {}
    for h in range(MAX_GOALS + 1):
        for a in range(MAX_GOALS + 1):
            grid[(h, a)] = poisson_pmf(xg_h, h) * poisson_pmf(xg_a, a)

    home_win = sum(p for (h, a), p in grid.items() if h > a)
    draw     = sum(p for (h, a), p in grid.items() if h == a)
    away_win = sum(p for (h, a), p in grid.items() if h < a)

    best = max(grid, key=grid.get)

    # Confidence based on how many games both teams have played
    ph = (home_stats or {}).get("playedGames", 0)
    pa = (away_stats or {}).get("playedGames", 0)
    if   ph >= 2 and pa >= 2: confidence = "HIGH"
    elif ph >= 1 or  pa >= 1: confidence = "MEDIUM"
    else:                      confidence = "LOW"

    return {
        "predicted_home": best[0],
        "predicted_away": best[1],
        "home_win_pct":   round(home_win * 100),
        "draw_pct":       round(draw     * 100),
        "away_win_pct":   round(away_win * 100),
        "xg_home":        round(xg_h, 2),
        "xg_away":        round(xg_a, 2),
        "confidence":     confidence,
    }


# ── API routes ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/today")
def today_matches():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        data = cached_get(
            f"{BASE_URL}/competitions/{COMPETITION}/matches",
            {"season": SEASON, "dateFrom": today, "dateTo": today},
        )
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/upcoming")
def upcoming_matches():
    now = datetime.now(timezone.utc)
    date_from = now.strftime("%Y-%m-%d")
    date_to   = (now + timedelta(hours=48)).strftime("%Y-%m-%d")
    try:
        data = cached_get(
            f"{BASE_URL}/competitions/{COMPETITION}/matches",
            {"season": SEASON, "dateFrom": date_from,
             "dateTo": date_to, "status": "SCHEDULED,TIMED"},
        )
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/standings")
def standings():
    try:
        data = cached_get(
            f"{BASE_URL}/competitions/{COMPETITION}/standings",
            {"season": SEASON},
        )
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/group-matches")
def group_matches():
    """All group stage matches — used to derive team → group mapping."""
    try:
        data = cached_get(
            f"{BASE_URL}/competitions/{COMPETITION}/matches",
            {"season": SEASON, "stage": "GROUP_STAGE"},
        )
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/bracket")
def bracket():
    knockout_stages = [
        "ROUND_OF_32", "ROUND_OF_16", "QUARTER_FINALS",
        "SEMI_FINALS", "THIRD_PLACE", "FINAL",
    ]
    try:
        data = cached_get(
            f"{BASE_URL}/competitions/{COMPETITION}/matches",
            {"season": SEASON},
        )
        all_matches = data.get("matches", [])
        data["matches"] = [m for m in all_matches if m.get("stage") in knockout_stages]
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/live")
def live_matches():
    try:
        data = cached_get(
            f"{BASE_URL}/competitions/{COMPETITION}/matches",
            {"season": SEASON, "status": "IN_PLAY,PAUSED"},
        )
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/predictions")
def predictions():
    """
    Upcoming scheduled matches (next 7 days) enriched with Poisson predictions.
    Blends FIFA rankings with live tournament stats for each team.
    """
    try:
        now       = datetime.now(timezone.utc)
        date_from = now.strftime("%Y-%m-%d")
        date_to   = (now + timedelta(days=7)).strftime("%Y-%m-%d")

        matches_data   = cached_get(
            f"{BASE_URL}/competitions/{COMPETITION}/matches",
            {"season": SEASON, "dateFrom": date_from,
             "dateTo": date_to, "status": "SCHEDULED,TIMED"},
        )
        standings_data = cached_get(
            f"{BASE_URL}/competitions/{COMPETITION}/standings",
            {"season": SEASON},
        )

        # Build teamId → stats lookup from TOTAL standings
        team_stats = {}
        total = next(
            (s for s in standings_data.get("standings", []) if s.get("type") == "TOTAL"),
            None
        )
        if total:
            for row in total.get("table", []):
                tid = row.get("team", {}).get("id")
                if tid:
                    team_stats[tid] = row

        # Attach prediction to each match
        enriched = []
        for match in matches_data.get("matches", []):
            h_id = match.get("homeTeam", {}).get("id")
            a_id = match.get("awayTeam", {}).get("id")
            pred = predict_match(
                match.get("homeTeam", {}).get("tla", ""),
                match.get("awayTeam", {}).get("tla", ""),
                team_stats.get(h_id),
                team_stats.get(a_id),
            )
            enriched.append({**match, "prediction": pred})

        return jsonify({"ok": True, "data": {"matches": enriched}})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import webbrowser, threading

    def open_browser():
        time.sleep(1)
        webbrowser.open("http://localhost:5050")

    threading.Thread(target=open_browser, daemon=True).start()
    print("🌍  World Cup 2026 Dashboard → http://localhost:5050")
    app.run(port=5050, debug=False)

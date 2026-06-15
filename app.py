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
# Three upgrades over basic Poisson (as suggested by Gemini):
#
# [1] Elo Ratings (not FIFA rankings)
#     Source: worldfootballrankings.com, updated June 14 2026.
#     Elo updates after every match based on result, margin, and opponent strength.
#     More predictive than ordinal FIFA rankings for this purpose.
#
# [2] Dixon-Coles τ correction
#     Pure Poisson overestimates the frequency of 1-0 and 0-1 scorelines and
#     underestimates 0-0 and 1-1.  The τ factor corrects low-scoring cells
#     (0-0, 1-0, 0-1, 1-1) with parameter ρ ≈ -0.13 (empirically fitted).
#
# [3] Progressive tournament weighting
#     Since all stats we have ARE tournament games (the highest-weight context),
#     we increase their blend weight as teams accumulate more games:
#       0 games  → pure Elo prior
#       1 game   → 22 % tournament
#       2 games  → 44 % tournament
#       3 games  → 65 % tournament (cap)

# Elo Ratings — worldfootballrankings.com, June 14 2026
ELO_RATINGS = {
    # Top tier (confirmed from live leaderboard)
    "ARG": 1877, "ESP": 1875, "FRA": 1871, "ENG": 1828, "POR": 1768,
    "BRA": 1765, "MAR": 1756, "NED": 1754, "BEL": 1742, "GER": 1736,
    "CRO": 1715, "MEX": 1701, "COL": 1698, "USA": 1689, "SEN": 1684,
    "URU": 1673, "JPN": 1662, "SUI": 1641, "IRN": 1620, "DEN": 1619,
    "KOR": 1613, "TUR": 1606, "ECU": 1599, "AUT": 1597, "NGA": 1585,
    "AUS": 1579, "ALG": 1571, "EGY": 1562, "NOR": 1557, "CAN": 1552,
    "CIV": 1541, "PAN": 1539, "SCO": 1519, "PAR": 1488, "CMR": 1481,
    "TUN": 1476, "COD": 1474, "SVK": 1474, "GRE": 1473, "VEN": 1469,
    "QAT": 1459,
    # Estimated from context / regional averages
    "SLO": 1470, "BIH": 1460, "RSA": 1458, "KSA": 1448, "GHA": 1440,
    "MLI": 1430, "UZB": 1415, "IRQ": 1410, "HAI": 1395, "HON": 1385,
    "JOR": 1380, "CPV": 1340, "SLV": 1335, "ZAM": 1355, "BOL": 1326,
    "NZL": 1276, "CUR": 1305,
}

BASE_GOALS = 1.25   # avg WC group-stage goals per team per game
ELO_SCALE  = 700    # calibration: 700 pts → √10 ratio in xG (empirically fitted)
DC_RHO     = -0.13  # Dixon-Coles ρ (negative → more draws/0-0, fewer 1-0/0-1)
MAX_GOALS  = 6      # max goals per side in the probability grid

def poisson_pmf(lam: float, k: int) -> float:
    """P(X = k) for X ~ Poisson(lambda)."""
    return math.exp(-lam) * (lam ** k) / math.factorial(k)

def elo_base_xg(elo_home: float, elo_away: float):
    """
    Convert Elo ratings to base expected goals via relative rating difference.
    Each √10 difference in the strength ratio shifts xG by √(ratio).
    """
    diff  = elo_home - elo_away
    ratio = 10 ** (diff / ELO_SCALE)
    return BASE_GOALS * math.sqrt(ratio), BASE_GOALS / math.sqrt(ratio)

def dixon_coles_tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    """
    Dixon-Coles correction factor τ for low-scoring scorelines.
    Applies only to (0,0), (0,1), (1,0), (1,1).
    """
    if   x == 0 and y == 0: return 1.0 - lam * mu * rho
    elif x == 0 and y == 1: return 1.0 + lam * rho
    elif x == 1 and y == 0: return 1.0 + mu  * rho
    elif x == 1 and y == 1: return 1.0 - rho
    else:                   return 1.0

def predict_match(home_tla: str, away_tla: str,
                  home_stats: dict = None, away_stats: dict = None) -> dict:
    """
    Predict a match using the upgraded three-part model.

    Parameters
    ----------
    home_tla / away_tla : 3-letter team code (e.g. "ARG")
    home_stats / away_stats : row from the TOTAL standings table

    Returns
    -------
    dict with predicted_home, predicted_away, home_win_pct, draw_pct,
         away_win_pct, xg_home, xg_away, confidence
    """
    # [1] Elo-based prior expected goals
    h_elo = ELO_RATINGS.get(home_tla, 1450)
    a_elo = ELO_RATINGS.get(away_tla, 1450)
    xg_h_prior, xg_a_prior = elo_base_xg(h_elo, a_elo)

    # [3] Progressive tournament weighting
    ph = (home_stats or {}).get("playedGames", 0)
    pa = (away_stats or {}).get("playedGames", 0)
    h_tw = min(0.65, ph * 0.22)   # 0→0%, 1→22%, 2→44%, 3→65% (cap)
    a_tw = min(0.65, pa * 0.22)

    def tourn_gf(stats):
        if not stats or stats.get("playedGames", 0) == 0: return None
        return stats["goalsFor"]    / stats["playedGames"]

    def tourn_ga(stats):
        if not stats or stats.get("playedGames", 0) == 0: return None
        return stats["goalsAgainst"] / stats["playedGames"]

    h_gf = tourn_gf(home_stats)
    a_gf = tourn_gf(away_stats)
    h_ga = tourn_ga(home_stats)
    a_ga = tourn_ga(away_stats)

    # Blend: Elo prior × (1 - weight) + tournament performance × weight
    # Attack: team's own goals scored; Defence adjustment: opponent's goals conceded
    if h_gf is not None:
        xg_h = xg_h_prior * (1 - h_tw) + h_gf * h_tw
        if a_ga is not None:                           # away team's defensive record
            def_adj = (a_ga / BASE_GOALS - 1) * a_tw  # positive → leaky defence
            xg_h   *= (1 + def_adj)
    else:
        xg_h = xg_h_prior

    if a_gf is not None:
        xg_a = xg_a_prior * (1 - a_tw) + a_gf * a_tw
        if h_ga is not None:
            def_adj = (h_ga / BASE_GOALS - 1) * h_tw
            xg_a   *= (1 + def_adj)
    else:
        xg_a = xg_a_prior

    xg_h = max(0.15, min(xg_h, 4.5))
    xg_a = max(0.15, min(xg_a, 4.5))

    # [2] Dixon-Coles corrected probability grid
    grid = {}
    for h in range(MAX_GOALS + 1):
        for a in range(MAX_GOALS + 1):
            p_raw = poisson_pmf(xg_h, h) * poisson_pmf(xg_a, a)
            tau   = dixon_coles_tau(h, a, xg_h, xg_a, DC_RHO)
            grid[(h, a)] = max(0.0, p_raw * tau)

    # Renormalise (grid cutoff means probabilities don't sum to exactly 1)
    total = sum(grid.values()) or 1.0
    grid  = {k: v / total for k, v in grid.items()}

    home_win = sum(p for (h, a), p in grid.items() if h > a)
    draw     = sum(p for (h, a), p in grid.items() if h == a)
    away_win = sum(p for (h, a), p in grid.items() if h < a)
    best     = max(grid, key=grid.get)

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


@app.route("/api/recent")
def recent_matches():
    """Finished matches in the past 48 hours."""
    now      = datetime.now(timezone.utc)
    date_from = (now - timedelta(hours=48)).strftime("%Y-%m-%d")
    date_to   = now.strftime("%Y-%m-%d")
    try:
        data = cached_get(
            f"{BASE_URL}/competitions/{COMPETITION}/matches",
            {"season": SEASON, "dateFrom": date_from,
             "dateTo": date_to, "status": "FINISHED"},
        )
        # Sort most-recent first
        data["matches"] = sorted(
            data.get("matches", []),
            key=lambda m: m.get("utcDate", ""),
            reverse=True,
        )
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/scorers")
def scorers():
    """Top 10 goal scorers in the tournament."""
    try:
        data = cached_get(
            f"{BASE_URL}/competitions/{COMPETITION}/scorers",
            {"season": SEASON, "limit": 10},
        )
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
    import os

    port = int(os.environ.get("PORT", 5050))
    local = os.environ.get("RENDER") is None  # False on Render, True locally

    if local:
        import webbrowser, threading
        def open_browser():
            time.sleep(1)
            webbrowser.open(f"http://localhost:{port}")
        threading.Thread(target=open_browser, daemon=True).start()
        print(f"🌍  World Cup 2026 Dashboard → http://localhost:{port}")

    app.run(host="0.0.0.0", port=port, debug=False)

"""
World Cup 2026 Dashboard — backend
Powered by football-data.org API (v4)

Design notes
------------
- API key comes from the FOOTBALL_DATA_API_KEY environment variable (set it in
  Render → your service → Environment). It is NOT hardcoded.
- A single cached season-wide /matches call backs every match-based endpoint
  (today / recent / upcoming / group / bracket / live / predictions). Filtering
  happens in Python. This keeps us well under the free-tier 10 req/min limit.
- Every response carries a "source" field: "live" (fresh/cached real API data),
  "cache" (last good data after a transient API error), or "mock" (fail-soft
  sample data — shown only when the key is missing or the API is unreachable).
  If a tab looks wrong, check "source" first.
- The real prediction engine (Elo + Dixon-Coles + Poisson) is preserved.
"""

from flask import Flask, render_template, jsonify
import requests
import os
import time
import math
from datetime import datetime, timedelta, timezone

app = Flask(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
# Read the key from the environment. Must be set on Render as FOOTBALL_DATA_API_KEY.
API_KEY     = os.environ.get("FOOTBALL_DATA_API_KEY")
BASE_URL    = "https://api.football-data.org/v4"
COMPETITION = "WC"
SEASON      = 2026

HEADERS = {"X-Auth-Token": API_KEY} if API_KEY else {}

# ── Cache (free tier = 10 req/min) ───────────────────────────────────────────
_cache          = {}
CACHE_TTL       = 60      # seconds — for live/list endpoints
MATCH_CACHE_TTL = 3600    # 1 hour  — finished match details never change

# Last successful payload per logical dataset, used for fail-soft on transient errors.
_last_good = {}

def cached_get(url, params=None, ttl=None):
    """GET with a small in-memory cache keyed by url+params. Raises on failure."""
    key = url + str(sorted((params or {}).items()))
    now = time.time()
    effective_ttl = ttl if ttl is not None else CACHE_TTL
    if key in _cache:
        data, ts = _cache[key]
        if now - ts < effective_ttl:
            return data
    resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    _cache[key] = (data, now)
    return data


# ── Fail-soft sample data (served only when the API is unavailable) ───────────
MOCK_MATCH_DATA = {
    "matches": [
        {
            "id": 1, "utcDate": (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat(),
            "status": "FINISHED", "stage": "GROUP_STAGE", "group": "GROUP_A",
            "homeTeam": {"id": 1, "name": "Mexico", "tla": "MEX", "crest": "https://crests.football-data.org/mex.png"},
            "awayTeam": {"id": 2, "name": "South Africa", "tla": "RSA", "crest": "https://crests.football-data.org/rsa.png"},
            "score": {"fullTime": {"home": 2, "away": 0}, "duration": "REGULAR"},
            "venue": "Estadio Azteca", "goals": [{"scorer": {"name": "Raúl Jiménez"}, "minute": 18, "type": "FIELD", "team": {"id": 1}}]
        },
        {
            "id": 2, "utcDate": (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(),
            "status": "TIMED", "stage": "GROUP_STAGE", "group": "GROUP_E",
            "homeTeam": {"id": 6, "name": "Germany", "tla": "GER", "crest": "https://crests.football-data.org/ger.png"},
            "awayTeam": {"id": 7, "name": "Curaçao", "tla": "CUW", "crest": "https://crests.football-data.org/cuw.png"},
            "score": {"fullTime": {"home": None, "away": None}},
            "venue": "Houston Stadium"
        },
        {
            "id": 3, "utcDate": (datetime.now(timezone.utc) + timedelta(hours=28)).isoformat(),
            "status": "SCHEDULED", "stage": "LAST_32",
            "homeTeam": {"id": 8, "name": "USA", "tla": "USA", "crest": "https://crests.football-data.org/usa.png"},
            "awayTeam": {"id": 9, "name": "Paraguay", "tla": "PAR", "crest": "https://crests.football-data.org/par.png"},
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


# ── Fetch layer with source tracking ─────────────────────────────────────────
def _fetch(name, path, params, mock):
    """
    Fetch a logical dataset. Returns (data, source).
    source ∈ {"live", "cache", "mock"}.
    """
    if not API_KEY:
        print(f"[{name}] FOOTBALL_DATA_API_KEY is not set — serving mock data. "
              f"Set it in Render → Environment to get real data.")
        return mock, "mock"
    try:
        data = cached_get(f"{BASE_URL}/{path}", params)
        _last_good[name] = data
        return data, "live"
    except Exception as e:
        print(f"[{name}] API error: {e}")
        if name in _last_good:
            print(f"[{name}] Falling back to last good data.")
            return _last_good[name], "cache"
        print(f"[{name}] No cached data available — serving mock.")
        return mock, "mock"

def get_all_matches():
    """Single season-wide match list, reused by every match-based endpoint."""
    return _fetch("matches", f"competitions/{COMPETITION}/matches", {"season": SEASON}, MOCK_MATCH_DATA)

def get_standings_data():
    return _fetch("standings", f"competitions/{COMPETITION}/standings", {"season": SEASON}, MOCK_STANDINGS_DATA)

def get_scorers_data():
    return _fetch("scorers", f"competitions/{COMPETITION}/scorers", {"season": SEASON, "limit": 10}, MOCK_SCORERS_DATA)

def fetch_match_goals(match_id):
    """Goal-scorer data for a single finished match (cached 1 hour). Best-effort."""
    try:
        data = cached_get(f"{BASE_URL}/matches/{match_id}", ttl=MATCH_CACHE_TTL)
        return data.get("goals") or []
    except Exception:
        return []

def _combined_source(*sources):
    """If anything is mock → mock; else if anything is cache → cache; else live."""
    if "mock" in sources:
        return "mock"
    if "cache" in sources:
        return "cache"
    return "live"


# ── Prediction engine ─────────────────────────────────────────────────────────
#
# Three upgrades over basic Poisson:
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

def poisson_pmf(lam, k):
    """P(X = k) for X ~ Poisson(lambda)."""
    return math.exp(-lam) * (lam ** k) / math.factorial(k)

def elo_base_xg(elo_home, elo_away):
    """
    Convert Elo ratings to base expected goals via relative rating difference.
    Each √10 difference in the strength ratio shifts xG by √(ratio).
    """
    diff  = elo_home - elo_away
    ratio = 10 ** (diff / ELO_SCALE)
    return BASE_GOALS * math.sqrt(ratio), BASE_GOALS / math.sqrt(ratio)

def dixon_coles_tau(x, y, lam, mu, rho):
    """
    Dixon-Coles correction factor τ for low-scoring scorelines.
    Applies only to (0,0), (0,1), (1,0), (1,1).
    """
    if   x == 0 and y == 0: return 1.0 - lam * mu * rho
    elif x == 0 and y == 1: return 1.0 + lam * rho
    elif x == 1 and y == 0: return 1.0 + mu  * rho
    elif x == 1 and y == 1: return 1.0 - rho
    else:                   return 1.0

def predict_match(home_tla, away_tla, home_stats=None, away_stats=None):
    """
    Predict a match using the upgraded three-part model.

    home_tla / away_tla     : 3-letter team code (e.g. "ARG")
    home_stats / away_stats : row from the TOTAL standings table (or None)

    Returns a dict with predicted_home, predicted_away, home_win_pct, draw_pct,
    away_win_pct, xg_home, xg_away, confidence.
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
        if a_ga is not None:                          # away team's defensive record
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


# ── Helpers ──────────────────────────────────────────────────────────────────
def parse_utc(s):
    """Parse an ISO timestamp, tolerating a trailing 'Z'."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ── Routes ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    """Serve the main frontend HTML page."""
    return render_template("index.html")

@app.route("/api/today")
def get_today():
    """Matches on today's (UTC) calendar date."""
    try:
        data, source = get_all_matches()
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        matches = [m for m in data.get("matches", []) if m.get("utcDate", "").startswith(today_str)]
        return jsonify({"ok": True, "source": source, "data": {"matches": matches}})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/recent")
def get_recent():
    """Finished matches in the last 48 hours, enriched with goal scorers."""
    try:
        data, source = get_all_matches()
        now = datetime.now(timezone.utc)
        lo  = now - timedelta(hours=48)
        recent = []
        for m in data.get("matches", []):
            if m.get("status") == "FINISHED":
                t = parse_utc(m["utcDate"])
                if lo <= t <= now:
                    recent.append(m)
        recent.sort(key=lambda m: m.get("utcDate", ""), reverse=True)
        # Enrich the most recent 8 with goal scorers (only on live data — mock
        # already includes goals, and we don't want to burn quota on cache/mock).
        # 1 list call + up to 8 match calls = 9, within the 10 req/min free tier.
        if source == "live":
            for m in recent[:8]:
                if not m.get("goals"):
                    m["goals"] = fetch_match_goals(m["id"])
        return jsonify({"ok": True, "source": source, "data": {"matches": recent}})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/upcoming")
def get_upcoming():
    """Scheduled/timed matches within the next 48 hours."""
    try:
        data, source = get_all_matches()
        now = datetime.now(timezone.utc)
        hi  = now + timedelta(hours=48)
        upcoming = []
        for m in data.get("matches", []):
            if m.get("status") in ("SCHEDULED", "TIMED"):
                t = parse_utc(m["utcDate"])
                if now <= t <= hi:
                    upcoming.append(m)
        upcoming.sort(key=lambda m: m.get("utcDate", ""))
        return jsonify({"ok": True, "source": source, "data": {"matches": upcoming}})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/standings")
def get_standings():
    """Group standings table."""
    try:
        data, source = get_standings_data()
        return jsonify({"ok": True, "source": source, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/group-matches")
def get_group_matches():
    """All group-stage matches — used by the frontend to map teams to groups."""
    try:
        data, source = get_all_matches()
        group_matches = [m for m in data.get("matches", []) if m.get("stage") == "GROUP_STAGE"]
        return jsonify({"ok": True, "source": source, "data": {"matches": group_matches}})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/bracket")
def get_bracket():
    """All knockout-stage matches. Accepts both ROUND_OF_xx and LAST_xx labels."""
    try:
        data, source = get_all_matches()
        knockout_stages = [
            "ROUND_OF_32", "LAST_32", "ROUND_OF_16", "LAST_16",
            "QUARTER_FINALS", "SEMI_FINALS", "THIRD_PLACE", "FINAL",
        ]
        bracket_matches = [m for m in data.get("matches", []) if m.get("stage") in knockout_stages]
        return jsonify({"ok": True, "source": source, "data": {"matches": bracket_matches}})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/live")
def get_live():
    """Matches currently in progress (for the live banner)."""
    try:
        data, source = get_all_matches()
        live_matches = [m for m in data.get("matches", []) if m.get("status") in ("IN_PLAY", "PAUSED")]
        return jsonify({"ok": True, "source": source, "data": {"matches": live_matches}})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/scorers")
def get_scorers():
    """Tournament top scorers."""
    try:
        data, source = get_scorers_data()
        return jsonify({"ok": True, "source": source, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/predictions")
def get_predictions():
    """
    Scheduled matches in the next 7 days, each enriched with an Elo + Dixon-Coles
    + Poisson prediction blended with live tournament standings.
    """
    try:
        mdata, msrc = get_all_matches()
        sdata, ssrc = get_standings_data()
        now   = datetime.now(timezone.utc)
        limit = now + timedelta(days=7)

        # Build teamId → TOTAL standings row lookup
        team_stats = {}
        total = next((s for s in sdata.get("standings", []) if s.get("type") == "TOTAL"), None)
        if total:
            for row in total.get("table", []):
                tid = row.get("team", {}).get("id")
                if tid:
                    team_stats[tid] = row

        enriched = []
        for m in mdata.get("matches", []):
            if m.get("status") in ("SCHEDULED", "TIMED"):
                t = parse_utc(m["utcDate"])
                if now <= t <= limit:
                    h_id = m.get("homeTeam", {}).get("id")
                    a_id = m.get("awayTeam", {}).get("id")
                    pred = predict_match(
                        m.get("homeTeam", {}).get("tla", "") or "",
                        m.get("awayTeam", {}).get("tla", "") or "",
                        team_stats.get(h_id),
                        team_stats.get(a_id),
                    )
                    enriched.append({**m, "prediction": pred})

        return jsonify({
            "ok": True,
            "source": _combined_source(msrc, ssrc),
            "data": {"matches": enriched},
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 5000))
    # debug defaults OFF (safer in production). Set FLASK_DEBUG=1 locally to enable.
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)

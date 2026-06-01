import os
import time
import json
import math
import csv
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from twilio.rest import Client

"""
SHIFT MLB V2.3.3 SELF-LEARNING TRACKER + FINAL LOCK

Professional live MLB totals monitor.
V2.2.1 adds:
    - Dedicated Pre-Run OVER WATCH engine
    - Pressure-to-runs conversion confirmation
    - Market-lag detection before line movement
    - Better entry guidance for OVERs without forcing bad STRIKES
    - Predictive Market Move Engine
    - Run Conversion Score for OVER alerts
    - Run Prevention Score for UNDER alerts
    - Better Entry Guidance
    - Stronger WATCH vs STRIKE separation
    - WATCH vs STRIKE separation
    - Real UNDER engine
    - Fake-pressure filter
    - Market resistance / overreaction scoring
    - Contact trend scoring
    - Times-through-order pressure
    - Starter exit probability
    - Duplicate alert control

Core idea:
    Current Score + Expected Future Runs = Projected Final Total

V2 focuses on:
    - Small market inefficiencies
    - Explainable alerts
    - No daily cap
    - Full game totals first
    - Team totals and remaining totals when odds provider returns them
    - 1st through 9th inning monitoring
    - Scenario-based interpretation

Important:
    This bot can only evaluate odds markets your odds provider actually returns.
    Some Odds API plans do not provide true in-play/live totals, team totals, or remaining-game totals.
"""

load_dotenv()

TZ = ZoneInfo("America/Phoenix")
STATE_FILE = os.getenv("STATE_FILE", "shift_v2_state.json")

STRIKE_HISTORY_FILE = os.getenv("STRIKE_HISTORY_FILE", "strike_history.csv")
CLV_HISTORY_FILE = os.getenv("CLV_HISTORY_FILE", "clv_history.csv")
GRADED_RESULTS_FILE = os.getenv("GRADED_RESULTS_FILE", "graded_results.csv")
LEARNING_SUMMARY_FILE = os.getenv("LEARNING_SUMMARY_FILE", "learning_summary.csv")
ENABLE_SELF_LEARNING = os.getenv("ENABLE_SELF_LEARNING", "true").lower() == "true"


ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
ALERT_TO_NUMBER = os.getenv("ALERT_TO_NUMBER", "")

SLOW_POLL_SECONDS = int(os.getenv("SLOW_POLL_SECONDS", "300"))
ACTIVE_POLL_SECONDS = int(os.getenv("ACTIVE_POLL_SECONDS", "45"))
FAST_POLL_SECONDS = int(os.getenv("FAST_POLL_SECONDS", "20"))

PREGAME_WINDOW_MINUTES = int(os.getenv("PREGAME_WINDOW_MINUTES", "45"))

# V2.1 separates OVER and UNDER thresholds.
# Unders often require a slightly smaller model edge because live markets can overinflate after early runs.
MIN_EDGE_RUNS = float(os.getenv("MIN_EDGE_RUNS", "0.9"))  # backward compatible fallback
MIN_OVER_EDGE_RUNS = float(os.getenv("MIN_OVER_EDGE_RUNS", "0.95"))
MIN_UNDER_EDGE_RUNS = float(os.getenv("MIN_UNDER_EDGE_RUNS", "0.70"))
MIN_WATCH_EDGE_RUNS = float(os.getenv("MIN_WATCH_EDGE_RUNS", "0.35"))
STRONG_EDGE_RUNS = float(os.getenv("STRONG_EDGE_RUNS", "1.4"))

MAX_PRICE_FAVORITE = int(os.getenv("MAX_PRICE_FAVORITE", "-140"))
MAX_PRICE_DOG = int(os.getenv("MAX_PRICE_DOG", "110"))

ALERT_COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN_SECONDS", "720"))
EDGE_IMPROVEMENT_TO_REPEAT = float(os.getenv("EDGE_IMPROVEMENT_TO_REPEAT", "0.7"))

# Alert controls
SEND_WATCH_ALERTS = os.getenv("SEND_WATCH_ALERTS", "false").lower() == "true"
MAX_ALERTS_PER_GAME_SIDE = int(os.getenv("MAX_ALERTS_PER_GAME_SIDE", "2"))
LINE_IMPROVEMENT_TO_REPEAT = float(os.getenv("LINE_IMPROVEMENT_TO_REPEAT", "1.0"))

# SMS safety:
# Twilio rejects message bodies over 1600 characters.
# Keep texts short; keep full details in Railway logs.
MAX_SMS_CHARS = int(os.getenv("MAX_SMS_CHARS", "1350"))

# V2.2.5 SMS behavior:
# WATCH alerts remain in Railway logs only.
# Only STRIKE / BET NOW alerts are sent by SMS.
SEND_ONLY_STRIKE_SMS = os.getenv("SEND_ONLY_STRIKE_SMS", "true").lower() == "true"
SHORT_STRIKE_SMS = os.getenv("SHORT_STRIKE_SMS", "true").lower() == "true"
MAX_SHORT_SMS_CHARS = int(os.getenv("MAX_SHORT_SMS_CHARS", "650"))
ELITE_STRIKE_PROJECTION = int(os.getenv("ELITE_STRIKE_PROJECTION", "85"))
ELITE_STRIKE_CONFIRMATION = int(os.getenv("ELITE_STRIKE_CONFIRMATION", "80"))
ELITE_STRIKE_EDGE = float(os.getenv("ELITE_STRIKE_EDGE", "2.0"))

# V2.3.0 professional calibration:
# Improve accuracy without handcuffing the model or increasing API credit usage.
ENABLE_TIME_REMAINING_ADJUSTMENT = os.getenv("ENABLE_TIME_REMAINING_ADJUSTMENT", "true").lower() == "true"
ENABLE_DUPLICATE_STRIKE_LOCK = os.getenv("ENABLE_DUPLICATE_STRIKE_LOCK", "true").lower() == "true"
ENABLE_STRIKE_HISTORY = os.getenv("ENABLE_STRIKE_HISTORY", "true").lower() == "true"
ENABLE_CLV_TRACKING = os.getenv("ENABLE_CLV_TRACKING", "true").lower() == "true"

# Re-alert only when the same game/side materially improves.
RE_ALERT_MIN_EDGE_IMPROVEMENT = float(os.getenv("RE_ALERT_MIN_EDGE_IMPROVEMENT", "2.0"))
RE_ALERT_MIN_PROJECTION_IMPROVEMENT = float(os.getenv("RE_ALERT_MIN_PROJECTION_IMPROVEMENT", "2.5"))
RE_ALERT_MIN_CONFIRMATION_IMPROVEMENT = int(os.getenv("RE_ALERT_MIN_CONFIRMATION_IMPROVEMENT", "15"))

# Time remaining adjustment is intentionally moderate so we do not kill good winners.
TIME_ADJ_INNING_1_4 = float(os.getenv("TIME_ADJ_INNING_1_4", "1.00"))
TIME_ADJ_INNING_5_6 = float(os.getenv("TIME_ADJ_INNING_5_6", "0.90"))
TIME_ADJ_INNING_7_8 = float(os.getenv("TIME_ADJ_INNING_7_8", "0.78"))
TIME_ADJ_INNING_9_PLUS = float(os.getenv("TIME_ADJ_INNING_9_PLUS", "0.55"))

# Credit usage:
# These features do not add extra API calls. They only use data already fetched during normal polling.

# V2.3.1 Winner Pattern Enhancements:
# These are additive/refinement signals, not restrictive filters.
# They use already-fetched MLB feed + odds data only.
ENABLE_WINNER_PATTERN_ENHANCEMENTS = os.getenv("ENABLE_WINNER_PATTERN_ENHANCEMENTS", "true").lower() == "true"
P2R_ELITE_THRESHOLD = int(os.getenv("P2R_ELITE_THRESHOLD", "85"))
P2R_SUPER_ELITE_THRESHOLD = int(os.getenv("P2R_SUPER_ELITE_THRESHOLD", "95"))
P2R_ELITE_BOOST = int(os.getenv("P2R_ELITE_BOOST", "8"))
P2R_SUPER_ELITE_BOOST = int(os.getenv("P2R_SUPER_ELITE_BOOST", "14"))
MARKET_LAG_STRONG = float(os.getenv("MARKET_LAG_STRONG", "2.0"))
MARKET_LAG_ELITE = float(os.getenv("MARKET_LAG_ELITE", "4.0"))
CONV_ACCEL_MIN_JUMP = int(os.getenv("CONV_ACCEL_MIN_JUMP", "15"))
SIGNAL_STACK_STRONG = int(os.getenv("SIGNAL_STACK_STRONG", "4"))
SIGNAL_STACK_ELITE = int(os.getenv("SIGNAL_STACK_ELITE", "5"))


# V2.2.4 decision calibration:
# Separates projected edge from live confirmation so WATCH/STRIKE is cleaner.
MIN_OVER_CONFIRMATION_FOR_STRIKE = int(os.getenv("MIN_OVER_CONFIRMATION_FOR_STRIKE", "60"))
MIN_LATE_OVER_CONFIRMATION_FOR_STRIKE = int(os.getenv("MIN_LATE_OVER_CONFIRMATION_FOR_STRIKE", "68"))
MIN_UNDER_CONFIRMATION_FOR_STRIKE = int(os.getenv("MIN_UNDER_CONFIRMATION_FOR_STRIKE", "62"))
MIN_LATE_UNDER_CONFIRMATION_FOR_STRIKE = int(os.getenv("MIN_LATE_UNDER_CONFIRMATION_FOR_STRIKE", "68"))
MIN_UNDER_STRIKE_EDGE_RUNS = float(os.getenv("MIN_UNDER_STRIKE_EDGE_RUNS", "1.0"))
MIN_LATE_OVER_STRIKE_EDGE_RUNS = float(os.getenv("MIN_LATE_OVER_STRIKE_EDGE_RUNS", "1.25"))
MAX_PROJECTION_EDGE_INNING_7 = float(os.getenv("MAX_PROJECTION_EDGE_INNING_7", "4.5"))
MAX_PROJECTION_EDGE_INNING_8 = float(os.getenv("MAX_PROJECTION_EDGE_INNING_8", "3.0"))
MAX_PROJECTION_EDGE_INNING_9 = float(os.getenv("MAX_PROJECTION_EDGE_INNING_9", "2.0"))

# Professional decision-layer gates
MIN_STRIKE_CONFIDENCE = int(os.getenv("MIN_STRIKE_CONFIDENCE", "64"))
MIN_WATCH_CONFIDENCE = int(os.getenv("MIN_WATCH_CONFIDENCE", "52"))
MAX_LATE_OVER_INNING = int(os.getenv("MAX_LATE_OVER_INNING", "8"))

# V2.2 predictive layers
MIN_OVER_RUN_CONVERSION_FOR_STRIKE = int(os.getenv("MIN_OVER_RUN_CONVERSION_FOR_STRIKE", "62"))
MIN_UNDER_RUN_PREVENTION_FOR_STRIKE = int(os.getenv("MIN_UNDER_RUN_PREVENTION_FOR_STRIKE", "66"))
MIN_PREDICTIVE_MARKET_MOVE_FOR_WATCH = int(os.getenv("MIN_PREDICTIVE_MARKET_MOVE_FOR_WATCH", "62"))
MIN_PREDICTIVE_MARKET_MOVE_FOR_STRIKE = int(os.getenv("MIN_PREDICTIVE_MARKET_MOVE_FOR_STRIKE", "76"))
MARKET_HAS_ALREADY_MOVED_RUNS = float(os.getenv("MARKET_HAS_ALREADY_MOVED_RUNS", "1.5"))
BEST_ENTRY_HALF_RUN_BUFFER = float(os.getenv("BEST_ENTRY_HALF_RUN_BUFFER", "0.5"))

# V2.2.1 professional over-prediction layer
ENABLE_PRE_RUN_OVER_WATCH = os.getenv("ENABLE_PRE_RUN_OVER_WATCH", "true").lower() == "true"
PRE_RUN_OVER_WATCH_SCORE = int(os.getenv("PRE_RUN_OVER_WATCH_SCORE", "58"))
PRE_RUN_OVER_STRIKE_SCORE = int(os.getenv("PRE_RUN_OVER_STRIKE_SCORE", "72"))
MIN_OVER_RUN_CONVERSION_FOR_WATCH = int(os.getenv("MIN_OVER_RUN_CONVERSION_FOR_WATCH", "48"))
MARKET_LAG_MAX_UPWARD_MOVE = float(os.getenv("MARKET_LAG_MAX_UPWARD_MOVE", "1.0"))
PRE_RUN_MAX_FAKE_PRESSURE = int(os.getenv("PRE_RUN_MAX_FAKE_PRESSURE", "55"))

# Live-evidence gates:
# Prevents false pregame / first-pitch alerts when there is no real game data yet.
MIN_LIVE_PITCHES_FOR_STRIKE = int(os.getenv("MIN_LIVE_PITCHES_FOR_STRIKE", "18"))
MIN_BALLS_IN_PLAY_FOR_STRIKE = int(os.getenv("MIN_BALLS_IN_PLAY_FOR_STRIKE", "2"))
MIN_REAL_SIGNAL_COUNT = int(os.getenv("MIN_REAL_SIGNAL_COUNT", "2"))
MIN_INNING_FOR_NEUTRAL_ALERT = int(os.getenv("MIN_INNING_FOR_NEUTRAL_ALERT", "99"))

ODDS_MARKETS = os.getenv("ODDS_MARKETS", "totals")

TEAM_MAP = {
    "Oakland Athletics": "Athletics",
    "Athletics": "Athletics",
    "Arizona Diamondbacks": "Diamondbacks",
    "Atlanta Braves": "Braves",
    "Baltimore Orioles": "Orioles",
    "Boston Red Sox": "Red Sox",
    "Chicago Cubs": "Cubs",
    "Chicago White Sox": "White Sox",
    "Cincinnati Reds": "Reds",
    "Cleveland Guardians": "Guardians",
    "Colorado Rockies": "Rockies",
    "Detroit Tigers": "Tigers",
    "Houston Astros": "Astros",
    "Kansas City Royals": "Royals",
    "Los Angeles Angels": "Angels",
    "Los Angeles Dodgers": "Dodgers",
    "Miami Marlins": "Marlins",
    "Milwaukee Brewers": "Brewers",
    "Minnesota Twins": "Twins",
    "New York Mets": "Mets",
    "New York Yankees": "Yankees",
    "Philadelphia Phillies": "Phillies",
    "Pittsburgh Pirates": "Pirates",
    "San Diego Padres": "Padres",
    "San Francisco Giants": "Giants",
    "Seattle Mariners": "Mariners",
    "St. Louis Cardinals": "Cardinals",
    "Tampa Bay Rays": "Rays",
    "Texas Rangers": "Rangers",
    "Toronto Blue Jays": "Blue Jays",
    "Washington Nationals": "Nationals",
}


FINAL_STATUS_WORDS = {
    "final",
    "game over",
    "completed early",
    "completed",
    "cancelled",
    "canceled",
    "postponed",
    "suspended",
}


def normalize_status(status):
    return str(status or "").strip().lower()


def is_final_status(status):
    """
    Current-day final lock.
    Once a game is final/completed/postponed/suspended/cancelled today,
    stop tracking it for the rest of the day.
    State resets automatically on a new calendar day through load_state().
    """
    s = normalize_status(status)
    if not s:
        return False
    return s in FINAL_STATUS_WORDS or s.startswith("final")


def schedule_status(game):
    status = game.get("status", {}) or {}
    return (
        status.get("abstractGameState")
        or status.get("detailedState")
        or status.get("codedGameState")
        or ""
    )


def schedule_label(game):
    home = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "Home")
    away = game.get("teams", {}).get("away", {}).get("team", {}).get("name", "Away")
    return f"{away} at {home}"


def is_final_locked_today(state, game_pk):
    """
    State file resets daily. If a game is marked final today, skip it completely.
    """
    return bool(state.setdefault("final_games", {}).get(str(game_pk)))


def mark_final_locked_today(state, game_pk, label="", status="", score=""):
    state.setdefault("final_games", {})[str(game_pk)] = {
        "date": today(),
        "label": label,
        "status": status,
        "score": score,
        "locked_at": now_local().isoformat(),
    }
    games = state.setdefault("games", {})
    games.setdefault(str(game_pk), {})
    games[str(game_pk)]["final_locked"] = True
    games[str(game_pk)]["final_status"] = status
    games[str(game_pk)]["final_score"] = score



def now_local():
    return datetime.now(TZ)


def today():
    return now_local().strftime("%Y-%m-%d")


def clean_team(name):
    if not name:
        return ""
    return TEAM_MAP.get(name, name).lower().replace(".", "").replace("-", " ").strip()


def clamp(x, lo=0, hi=100):
    return max(lo, min(hi, x))


def avg(nums):
    return round(sum(nums) / len(nums), 2) if nums else 0


def safe_float(x, default=0):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def safe_int(x, default=0):
    try:
        if x is None:
            return default
        return int(x)
    except Exception:
        return default


def price_ok(price, edge):
    if price is None:
        return True
    p = int(price)
    if p < MAX_PRICE_FAVORITE or p > MAX_PRICE_DOG:
        return False
    if p <= -135 and edge < 1.35:
        return False
    if p <= -125 and edge < 1.15:
        return False
    return True


def market_label(price):
    if price is None:
        return "Unknown price"
    p = int(price)
    if -115 <= p <= 100:
        return "Clean price"
    if -140 <= p < -115 or 100 < p <= 110:
        return "Acceptable price"
    return "Bad price"


def csv_append_once(path, fieldnames, row):
    """
    Lightweight local CSV logging. No extra API calls / no extra credits.
    """
    exists = os.path.exists(path)
    try:
        with open(path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not exists:
                writer.writeheader()
            writer.writerow(row)
    except Exception as e:
        print(f"CSV LOG ERROR {path}:", repr(e))



def csv_read_rows(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        print(f"CSV READ ERROR {path}:", repr(e))
        return []


def csv_write_rows(path, fieldnames, rows):
    try:
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fieldnames})
    except Exception as e:
        print(f"CSV WRITE ERROR {path}:", repr(e))


def game_key_from_info(info):
    return f"{today()}::{info.get('away')} at {info.get('home')}"


def score_text(info):
    return f"{info.get('away_runs', 0)}-{info.get('home_runs', 0)}"


def final_total_from_score(score):
    try:
        parts = str(score).replace(" ", "").split("-")
        if len(parts) != 2:
            return None
        return int(parts[0]) + int(parts[1])
    except Exception:
        return None


def schedule_final_score(game):
    try:
        away = game.get("teams", {}).get("away", {}).get("score")
        home = game.get("teams", {}).get("home", {}).get("score")
        if away is None or home is None:
            return ""
        return f"{away}-{home}"
    except Exception:
        return ""


def grade_bet(side, line, final_total):
    line = safe_float(line, None)
    final_total = safe_float(final_total, None)
    if line is None or final_total is None:
        return "UNKNOWN"
    side = str(side or "").upper()
    if final_total == line:
        return "PUSH"
    if side == "OVER":
        return "WIN" if final_total > line else "LOSS"
    if side == "UNDER":
        return "WIN" if final_total < line else "LOSS"
    return "UNKNOWN"


def pattern_tags_from_row(row):
    tags = []
    side = str(row.get("side", "")).upper()
    if side:
        tags.append(side)

    inning = safe_int(row.get("inning"), 0)
    if inning:
        if inning <= 3:
            tags.append("EARLY")
        elif inning <= 6:
            tags.append("MID")
        else:
            tags.append("LATE")

    base_out = str(row.get("base_out", "")).lower()
    if "bases loaded" in base_out:
        tags.append("BASES_LOADED")
    elif "1st and 3rd" in base_out or "2nd and 3rd" in base_out:
        tags.append("MULTI_RUNNERS_SCORING")
    elif "runner on 2nd" in base_out or "runner on 3rd" in base_out:
        tags.append("SCORING_POSITION")
    elif "bases empty" in base_out:
        tags.append("BASES_EMPTY")

    outs = safe_int(row.get("outs"), -1)
    if outs == 0:
        tags.append("NO_OUTS")
    elif outs == 1:
        tags.append("ONE_OUT")
    elif outs == 2:
        tags.append("TWO_OUTS")

    stress = safe_int(row.get("stress"), 0)
    contact = safe_int(row.get("contact"), 0)
    p2r = safe_int(row.get("p2r"), 0)
    conv = safe_int(row.get("conv"), 0)
    prev = safe_int(row.get("prev"), 0)
    pred = safe_int(row.get("pred_move"), 0)
    threat = safe_int(row.get("threat_index"), 0)
    stack = safe_int(row.get("signal_stack"), 0)
    lag = safe_int(row.get("market_lag"), 0)

    if stress >= 80:
        tags.append("STRESS_80_PLUS")
    elif stress >= 65:
        tags.append("STRESS_65_PLUS")

    if contact >= 70:
        tags.append("CONTACT_70_PLUS")
    elif contact >= 55:
        tags.append("CONTACT_55_PLUS")

    if p2r >= 85:
        tags.append("P2R_85_PLUS")
    elif p2r >= 70:
        tags.append("P2R_70_PLUS")

    if conv >= 80:
        tags.append("CONV_80_PLUS")
    elif conv >= 65:
        tags.append("CONV_65_PLUS")

    if prev >= 75:
        tags.append("PREV_75_PLUS")
    elif prev >= 60:
        tags.append("PREV_60_PLUS")

    if pred >= 75:
        tags.append("PRED_75_PLUS")

    if threat >= 85:
        tags.append("THREAT_85_PLUS")
    elif threat >= 70:
        tags.append("THREAT_70_PLUS")

    if stack >= 5:
        tags.append("STACK_5_PLUS")
    elif stack >= 4:
        tags.append("STACK_4_PLUS")

    if lag >= 80:
        tags.append("LAG_80_PLUS")
    elif lag >= 60:
        tags.append("LAG_60_PLUS")

    return tags


def strike_fieldnames():
    return [
        "strike_id", "timestamp", "date", "game_key", "game_pk", "game",
        "side", "line", "price",
        "opening_total", "live_total", "projected_total", "edge", "edge_grade",
        "inning", "inning_state", "outs", "score", "base_out",
        "projection_score", "confirmation_score",
        "stress", "contact", "p2r", "conv", "prev", "pred_move",
        "threat_index", "signal_stack", "market_lag", "conv_acceleration",
        "scenario", "action", "pattern_tags",
        "final_score", "final_total", "result", "graded_at",
    ]


def graded_fieldnames():
    return strike_fieldnames()


def learning_summary_fieldnames():
    return [
        "updated_at", "pattern", "side", "total", "wins", "losses", "pushes",
        "win_pct", "sample_note", "recommendation",
    ]


def build_learning_summary():
    if not ENABLE_SELF_LEARNING:
        return []

    rows = csv_read_rows(GRADED_RESULTS_FILE)
    graded = [r for r in rows if r.get("result") in ["WIN", "LOSS", "PUSH"]]
    if not graded:
        return []

    buckets = {}

    def add_bucket(pattern, row):
        side = str(row.get("side", "")).upper()
        key = (pattern, side)
        node = buckets.setdefault(key, {"total": 0, "wins": 0, "losses": 0, "pushes": 0})
        node["total"] += 1
        if row.get("result") == "WIN":
            node["wins"] += 1
        elif row.get("result") == "LOSS":
            node["losses"] += 1
        elif row.get("result") == "PUSH":
            node["pushes"] += 1

    for row in graded:
        tags = row.get("pattern_tags", "")
        tag_list = [t for t in tags.split("|") if t]

        # Single-pattern buckets.
        for t in tag_list:
            add_bucket(t, row)

        # High-value combinations we care about professionally.
        combos = [
            ("P2R_85_PLUS+CONV_80_PLUS", {"P2R_85_PLUS", "CONV_80_PLUS"}),
            ("STRESS_80_PLUS+P2R_85_PLUS", {"STRESS_80_PLUS", "P2R_85_PLUS"}),
            ("THREAT_70_PLUS+P2R_70_PLUS", {"THREAT_70_PLUS", "P2R_70_PLUS"}),
            ("STACK_5_PLUS+LAG_60_PLUS", {"STACK_5_PLUS", "LAG_60_PLUS"}),
            ("LATE+BASES_EMPTY", {"LATE", "BASES_EMPTY"}),
            ("LATE+PREV_75_PLUS", {"LATE", "PREV_75_PLUS"}),
            ("CONTACT_70_PLUS+CONV_80_PLUS", {"CONTACT_70_PLUS", "CONV_80_PLUS"}),
        ]
        tag_set = set(tag_list)
        for label, required in combos:
            if required.issubset(tag_set):
                add_bucket(label, row)

    output = []
    now = now_local().isoformat()
    for (pattern, side), stats in sorted(buckets.items(), key=lambda kv: (kv[1]["total"], kv[1]["wins"]), reverse=True):
        total = stats["total"]
        if total <= 0:
            continue
        wins = stats["wins"]
        losses = stats["losses"]
        pushes = stats["pushes"]
        denom = max(1, wins + losses)
        win_pct = round((wins / denom) * 100, 1)

        if total < 5:
            note = "Small sample"
            recommendation = "Track only"
        elif win_pct >= 62:
            note = "Strong historical pattern"
            recommendation = "Boost confidence"
        elif win_pct <= 45:
            note = "Weak historical pattern"
            recommendation = "Caution flag"
        else:
            note = "Neutral historical pattern"
            recommendation = "No adjustment"

        output.append({
            "updated_at": now,
            "pattern": pattern,
            "side": side,
            "total": total,
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "win_pct": win_pct,
            "sample_note": note,
            "recommendation": recommendation,
        })

    csv_write_rows(LEARNING_SUMMARY_FILE, learning_summary_fieldnames(), output)
    return output


def historical_pattern_note(info, opportunity):
    """
    Reads our own graded history and returns a short note for the alert.
    This does not block alerts. It only informs us whether similar past alerts
    have been strong, weak, or still too small of a sample.
    """
    if not ENABLE_SELF_LEARNING:
        return "Historical Match: tracking disabled"

    scores = opportunity.get("scores", {})
    row = {
        "side": opportunity.get("side"),
        "inning": info.get("inning"),
        "outs": info.get("outs"),
        "base_out": f"{info.get('base_state', {}).get('label', '')}, {info.get('outs')} out(s)",
        "stress": scores.get("pitcher_stress"),
        "contact": scores.get("contact_quality"),
        "p2r": scores.get("pressure_to_runs"),
        "conv": scores.get("run_conversion"),
        "prev": scores.get("run_prevention"),
        "pred_move": scores.get("predictive_market_move"),
        "threat_index": scores.get("threat_index"),
        "signal_stack": scores.get("signal_stack"),
        "market_lag": scores.get("market_lag"),
    }

    tags = set(pattern_tags_from_row(row))
    graded = [r for r in csv_read_rows(GRADED_RESULTS_FILE) if r.get("result") in ["WIN", "LOSS", "PUSH"]]

    if not graded:
        return "Historical Match: building sample"

    best = None
    for r in graded:
        r_tags = set((r.get("pattern_tags") or "").split("|"))
        overlap = len(tags.intersection(r_tags))
        if str(r.get("side", "")).upper() == str(opportunity.get("side", "")).upper():
            overlap += 2
        if overlap <= 1:
            continue
        if best is None or overlap > best[0]:
            best = (overlap, [])

    # Instead of nearest-neighbor only, use all rows with strong tag overlap.
    similar = []
    for r in graded:
        r_tags = set((r.get("pattern_tags") or "").split("|"))
        overlap = len(tags.intersection(r_tags))
        if str(r.get("side", "")).upper() == str(opportunity.get("side", "")).upper():
            overlap += 2
        if overlap >= 4:
            similar.append(r)

    if len(similar) < 5:
        return f"Historical Match: small sample ({len(similar)} similar)"

    wins = sum(1 for r in similar if r.get("result") == "WIN")
    losses = sum(1 for r in similar if r.get("result") == "LOSS")
    pushes = sum(1 for r in similar if r.get("result") == "PUSH")
    denom = max(1, wins + losses)
    pct = round((wins / denom) * 100, 1)

    if pct >= 62:
        strength = "Strong"
    elif pct <= 45:
        strength = "Caution"
    else:
        strength = "Neutral"

    return f"Historical Match: {wins}-{losses}-{pushes} ({pct}%) | {strength}"


def grade_completed_strikes(game_pk, label, final_score):
    """
    When a game goes final, grade all stored STRIKE rows for that game.
    This is the core self-learning loop:
    strike alert -> final score -> WIN/LOSS/PUSH -> pattern database.
    """
    if not ENABLE_SELF_LEARNING:
        return

    final_total = final_total_from_score(final_score)
    if final_total is None:
        return

    rows = csv_read_rows(STRIKE_HISTORY_FILE)
    if not rows:
        return

    changed = False
    newly_graded = []

    for row in rows:
        same_game = (
            row.get("date") == today()
            and (
                row.get("game_pk") == str(game_pk)
                or row.get("game") == label
                or row.get("game_key") == f"{today()}::{label}"
            )
        )
        if not same_game:
            continue
        if row.get("action") != "STRIKE":
            continue
        if row.get("result") in ["WIN", "LOSS", "PUSH"]:
            continue

        result = grade_bet(row.get("side"), row.get("line"), final_total)
        row["final_score"] = final_score
        row["final_total"] = final_total
        row["result"] = result
        row["graded_at"] = now_local().isoformat()
        if not row.get("pattern_tags"):
            row["pattern_tags"] = "|".join(pattern_tags_from_row(row))
        newly_graded.append(dict(row))
        changed = True

    if changed:
        csv_write_rows(STRIKE_HISTORY_FILE, strike_fieldnames(), rows)

        existing = csv_read_rows(GRADED_RESULTS_FILE)
        existing_ids = {r.get("strike_id") for r in existing}
        for row in newly_graded:
            if row.get("strike_id") not in existing_ids:
                existing.append(row)
                existing_ids.add(row.get("strike_id"))
        csv_write_rows(GRADED_RESULTS_FILE, graded_fieldnames(), existing)

        summary = build_learning_summary()
        wins = sum(1 for r in newly_graded if r.get("result") == "WIN")
        losses = sum(1 for r in newly_graded if r.get("result") == "LOSS")
        pushes = sum(1 for r in newly_graded if r.get("result") == "PUSH")
        print(
            f"SELF-LEARNING GRADED | {label} | Final {final_score} | "
            f"New grades W-L-P: {wins}-{losses}-{pushes} | "
            f"Summary patterns: {len(summary)}"
        )

def time_remaining_multiplier(info):
    """
    Moderate time-remaining adjustment.
    It reduces late-game projection aggression without handcuffing early/mid-game winners.
    """
    if not ENABLE_TIME_REMAINING_ADJUSTMENT:
        return 1.0

    inning = safe_int(info.get("inning", 1), 1)

    if inning <= 4:
        return TIME_ADJ_INNING_1_4
    if inning <= 6:
        return TIME_ADJ_INNING_5_6
    if inning <= 8:
        return TIME_ADJ_INNING_7_8
    return TIME_ADJ_INNING_9_PLUS


def adjusted_projection_for_time(info, live_total, projected_total):
    """
    Adjusts only the EDGE between live and projected line, not total runs directly.
    This preserves early signals while avoiding wild late projections.
    """
    if live_total is None or projected_total is None:
        return projected_total

    mult = time_remaining_multiplier(info)
    edge = projected_total - live_total
    adjusted = live_total + (edge * mult)
    return round(adjusted, 1)


def strike_key(info, side):
    return f"{today()}::{info.get('away','')}@{info.get('home','')}::{side}"


def should_allow_strike(state, info, opportunity):
    """
    Duplicate strike lock:
    - First STRIKE for game/side is allowed.
    - Re-alert only if line/projection/confirmation materially improves.
    This reduces repeat SMS and avoids chasing the same thesis repeatedly.
    """
    if not ENABLE_DUPLICATE_STRIKE_LOCK:
        return True, "duplicate lock disabled"

    if opportunity.get("action") != "STRIKE":
        return True, "not a strike"

    games = state.setdefault("games", {})
    key = strike_key(info, opportunity.get("side"))
    prior = games.get(key, {}).get("strike_lock")

    if not prior:
        return True, "first strike"

    old_edge = safe_float(prior.get("edge"), 0)
    old_proj = safe_float(prior.get("projection"), 0)
    old_confirm = safe_int(prior.get("confirmation_score"), 0)

    new_edge = safe_float(opportunity.get("edge"), 0)
    new_proj = safe_float(opportunity.get("projection"), safe_float(opportunity.get("projected_total"), 0))
    new_confirm = safe_int(opportunity.get("scores", {}).get("confirmation_score"), 0)

    edge_improved = abs(new_edge) >= abs(old_edge) + RE_ALERT_MIN_EDGE_IMPROVEMENT
    projection_improved = abs(new_proj - old_proj) >= RE_ALERT_MIN_PROJECTION_IMPROVEMENT
    confirmation_improved = new_confirm >= old_confirm + RE_ALERT_MIN_CONFIRMATION_IMPROVEMENT

    if edge_improved or projection_improved or confirmation_improved:
        return True, "material improvement"

    return False, "duplicate strike suppressed"


def record_strike_lock(state, info, opportunity):
    if not ENABLE_DUPLICATE_STRIKE_LOCK or opportunity.get("action") != "STRIKE":
        return

    games = state.setdefault("games", {})
    key = strike_key(info, opportunity.get("side"))
    games.setdefault(key, {})
    games[key]["strike_lock"] = {
        "time": now_local().isoformat(),
        "side": opportunity.get("side"),
        "line": opportunity.get("line"),
        "edge": opportunity.get("edge"),
        "projection": opportunity.get("projection", opportunity.get("projected_total")),
        "confirmation_score": opportunity.get("scores", {}).get("confirmation_score"),
    }


def log_strike_history(info, opportunity, market_context=None):
    """
    Logs each sent STRIKE for later self-learning.
    No API call. Just local CSV memory that the bot can grade and summarize.
    """
    if not ENABLE_STRIKE_HISTORY or opportunity.get("action") != "STRIKE":
        return

    scores = opportunity.get("scores", {})
    market_context = market_context or {}

    strike_id = (
        f"{today()}|{info.get('game_pk', '')}|{opportunity.get('side')}|"
        f"{opportunity.get('line')}|{safe_int(info.get('inning'), 0)}|"
        f"{safe_int(info.get('outs'), 0)}|{int(time.time())}"
    )

    row = {
        "strike_id": strike_id,
        "timestamp": now_local().isoformat(),
        "date": today(),
        "game_key": game_key_from_info(info),
        "game_pk": info.get("game_pk", ""),
        "game": f"{info.get('away')} at {info.get('home')}",
        "side": opportunity.get("side"),
        "line": opportunity.get("line"),
        "price": opportunity.get("price"),
        "opening_total": market_context.get("opening_total"),
        "live_total": market_context.get("live_total", opportunity.get("line")),
        "projected_total": opportunity.get("projection", opportunity.get("projected_total")),
        "edge": opportunity.get("edge"),
        "edge_grade": opportunity.get("edge_grade"),
        "inning": info.get("inning"),
        "inning_state": info.get("inning_state"),
        "outs": info.get("outs"),
        "score": score_text(info),
        "base_out": f"{info.get('base_state', {}).get('label', '')}, {info.get('outs')} out(s)",
        "projection_score": scores.get("projection_score"),
        "confirmation_score": scores.get("confirmation_score"),
        "stress": scores.get("pitcher_stress"),
        "contact": scores.get("contact_quality"),
        "p2r": scores.get("pressure_to_runs"),
        "conv": scores.get("run_conversion"),
        "prev": scores.get("run_prevention"),
        "pred_move": scores.get("predictive_market_move"),
        "threat_index": scores.get("threat_index"),
        "signal_stack": scores.get("signal_stack"),
        "market_lag": scores.get("market_lag"),
        "conv_acceleration": scores.get("conv_acceleration"),
        "scenario": opportunity.get("scenario"),
        "action": opportunity.get("action"),
        "final_score": "",
        "final_total": "",
        "result": "PENDING",
        "graded_at": "",
    }
    row["pattern_tags"] = "|".join(pattern_tags_from_row(row))

    csv_append_once(STRIKE_HISTORY_FILE, strike_fieldnames(), row)
    print(f"SELF-LEARNING STORED STRIKE | {row['game']} | {row['side']} {row['line']} | {row['pattern_tags']}")



def log_clv_snapshot(info, opportunity, current_live_total):
    """
    CLV tracking snapshot.
    This uses odds already fetched during normal polling. No extra credit usage.
    """
    if not ENABLE_CLV_TRACKING or opportunity.get("action") != "STRIKE":
        return

    side = opportunity.get("side")
    alert_line = safe_float(opportunity.get("line"), None)
    current_line = safe_float(current_live_total, None)

    if alert_line is None or current_line is None:
        return

    if side == "OVER":
        clv = round(current_line - alert_line, 1)
        beat_market = clv > 0
    else:
        clv = round(alert_line - current_line, 1)
        beat_market = clv > 0

    fieldnames = [
        "timestamp", "date", "game", "side", "alert_line", "current_line",
        "clv", "beat_market", "inning", "score"
    ]

    row = {
        "timestamp": now_local().isoformat(),
        "date": today(),
        "game": f"{info.get('away')} at {info.get('home')}",
        "side": side,
        "alert_line": alert_line,
        "current_line": current_line,
        "clv": clv,
        "beat_market": beat_market,
        "inning": info.get("inning"),
        "score": f"{info.get('away_runs')}-{info.get('home_runs')}",
    }

    csv_append_once(CLV_HISTORY_FILE, fieldnames, row)


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"date": today(), "games": {}, "final_games": {}}
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
    except Exception:
        return {"date": today(), "games": {}, "final_games": {}}
    if state.get("date") != today():
        return {"date": today(), "games": {}, "final_games": {}}
    state.setdefault("games", {})
    state.setdefault("final_games", {})
    return state


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)



def compact_sms_message(msg, max_chars=MAX_SMS_CHARS):
    """
    Twilio hard rejects long message bodies.
    This keeps the FULL alert printed in Railway logs while sending
    a compact SMS that stays under the limit.
    """
    if not msg:
        return msg

    if len(msg) <= max_chars:
        return msg

    lines = [ln.rstrip() for ln in msg.splitlines() if ln.strip()]
    keep_prefixes = (
        "SHIFT",
        "PLAY:",
        "Market:",
        "Scenario:",
        "Instruction:",
        "Open:",
        "Live Line:",
        "Proj:",
        "Edge:",
        "Entry Guidance:",
        "Score:",
        "Inning:",
        "Base/Out:",
        "Projection Score:",
        "Confirmation Score:",
        "Need Runs:",
        "Action Window:",
        "Confidence:",
        "CIP:",
        "RO:",
        "Stress:",
        "Dom:",
        "Contact:",
        "Lineup:",
        "Conv:",
        "P2R:",
        "PreOver:",
        "OVal:",
        "Prev:",
        "PredMove:",
    )

    compact = []
    # Always keep the header and matchup block.
    for ln in lines[:8]:
        compact.append(ln)

    # Keep key scoring/decision lines.
    for ln in lines[8:]:
        if ln.startswith(keep_prefixes):
            compact.append(ln)

    # Keep the strongest "why" bullets only.
    why_lines = [ln for ln in lines if ln.startswith("•")]
    if why_lines:
        compact.append("Why:")
        compact.extend(why_lines[:5])

    # Keep only the first pitch-type flag line if present.
    for idx, ln in enumerate(lines):
        if ln.startswith("Pitch-Type Flags:"):
            compact.append("Pitch-Type Flags:")
            if idx + 1 < len(lines):
                compact.append(lines[idx + 1])
            break

    text = "\n".join(compact)

    if len(text) <= max_chars:
        return text

    # Final hard cap.
    return text[: max_chars - 90].rstrip() + "\n\n[Trimmed for SMS. Full alert in Railway logs.]"




def alert_action_from_message(msg):
    first_line = (msg.splitlines()[0] if msg else "").upper()
    if "STRIKE" in first_line:
        return "STRIKE"
    if "WATCH" in first_line:
        return "WATCH"
    return "UNKNOWN"


def extract_alert_value(msg, label):
    """
    Pulls simple values out of the full alert body for compact SMS.
    """
    for line in msg.splitlines():
        line = line.strip()
        if line.startswith(label):
            return line.split(":", 1)[1].strip() if ":" in line else line.replace(label, "").strip()
    return ""


def first_nonempty_after_label(msg, label):
    lines = [ln.rstrip() for ln in msg.splitlines()]
    for i, ln in enumerate(lines):
        if ln.strip().startswith(label):
            for j in range(i + 1, min(i + 4, len(lines))):
                if lines[j].strip():
                    return lines[j].strip()
    return ""


def compact_strike_sms(msg):
    """
    Short betting text only. Full alert remains in Railway logs.
    """
    if not SHORT_STRIKE_SMS:
        return compact_sms_message(msg, MAX_SHORT_SMS_CHARS)

    lines = [ln.strip() for ln in msg.splitlines() if ln.strip()]
    header = lines[0] if lines else "SHIFT MLB STRIKE"
    matchup = lines[1] if len(lines) > 1 else ""

    play = first_nonempty_after_label(msg, "PLAY:")
    if not play:
        side = extract_alert_value(msg, "Side")
        line = extract_alert_value(msg, "Live Line")
        play = f"{side} {line}".strip()

    score = extract_alert_value(msg, "Score")
    inning = extract_alert_value(msg, "Inning")
    base_out = extract_alert_value(msg, "Base/Out")
    proj = extract_alert_value(msg, "Proj")
    edge = extract_alert_value(msg, "Edge")
    projection = extract_alert_value(msg, "Projection Score")
    confirmation = extract_alert_value(msg, "Confirmation Score")
    need = extract_alert_value(msg, "Need Runs")
    action_window = extract_alert_value(msg, "Action Window")
    historical = extract_alert_value(msg, "Historical Match")

    # Key metrics
    stress = extract_alert_value(msg, "Stress")
    contact = extract_alert_value(msg, "Contact")
    p2r = extract_alert_value(msg, "P2R")
    conv = extract_alert_value(msg, "Conv")
    prev = extract_alert_value(msg, "Prev")
    pred = extract_alert_value(msg, "PredMove")
    threat = extract_alert_value(msg, "Threat")
    stack = extract_alert_value(msg, "Stack")
    lag = extract_alert_value(msg, "Lag")

    # Elite label
    try:
        edge_num = float(edge.replace("+", "").replace("runs", "").strip().split()[0])
    except Exception:
        edge_num = 0.0
    try:
        proj_score = int(projection.split("/")[0])
    except Exception:
        proj_score = 0
    try:
        conf_score = int(confirmation.split("/")[0])
    except Exception:
        conf_score = 0

    elite = (
        abs(edge_num) >= ELITE_STRIKE_EDGE
        and proj_score >= ELITE_STRIKE_PROJECTION
        and conf_score >= ELITE_STRIKE_CONFIRMATION
    )

    title = "🔥 ELITE MLB STRIKE" if elite else "🚨 MLB STRIKE"

    compact = [
        title,
        matchup,
        "",
        play,
    ]

    if proj or edge:
        compact.append(f"Proj: {proj} | Edge: {edge}")
    if projection or confirmation:
        compact.append(f"ProjScore: {projection} | Confirm: {confirmation}")
    if score or inning:
        compact.append(f"Score: {score} | {inning}")
    if base_out:
        compact.append(f"Base/Out: {base_out}")
    if need:
        compact.append(need)
    if action_window:
        compact.append(action_window)
    if historical:
        compact.append(f"Hist: {historical}")

    reason_bits = []
    if stress:
        reason_bits.append(f"Stress {stress}")
    if contact:
        reason_bits.append(f"Contact {contact}")
    if p2r:
        reason_bits.append(f"P2R {p2r}")
    if conv:
        reason_bits.append(f"Conv {conv}")
    if prev:
        reason_bits.append(f"Prev {prev}")
    if pred:
        reason_bits.append(f"Pred {pred}")
    if threat:
        reason_bits.append(f"Threat {threat}")
    if stack:
        reason_bits.append(f"Stack {stack}")
    if lag:
        reason_bits.append(f"Lag {lag}")

    if reason_bits:
        compact.extend(["", "Signals: " + " | ".join(reason_bits[:4])])

    compact.append("")
    compact.append("BET NOW")

    text = "\n".join(compact)

    if len(text) > MAX_SHORT_SMS_CHARS:
        text = text[:MAX_SHORT_SMS_CHARS - 20].rstrip() + "\n[Trimmed]"

    return text


def should_send_sms(msg):
    """
    Watches are intentionally not sent by SMS.
    They still appear in Railway logs.
    """
    if not SEND_ONLY_STRIKE_SMS:
        return True
    return alert_action_from_message(msg) == "STRIKE"


def send_text(msg):
    # Full alert always stays in Railway logs.
    print("\n" + msg + "\n")

    # WATCH alerts stay in logs only. STRIKE alerts go to SMS.
    if not should_send_sms(msg):
        print("TEXT NOT SENT: WATCH alert logged only.")
        return

    sms_body = compact_strike_sms(msg)
    if len(sms_body) > MAX_SHORT_SMS_CHARS:
        sms_body = sms_body[:MAX_SHORT_SMS_CHARS - 40].rstrip() + "\n[Trimmed]"

    print(f"SMS LENGTH: {len(sms_body)} chars")

    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, ALERT_TO_NUMBER]):
        print("TEXT NOT SENT: Missing Twilio variables.")
        return
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(body=sms_body, from_=TWILIO_FROM_NUMBER, to=ALERT_TO_NUMBER)
        print("TEXT SENT SUCCESSFULLY")
    except Exception as e:
        print("TEXT ERROR:", repr(e))


def get_schedule():
    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today()}"
    data = requests.get(url, timeout=15).json()
    games = []
    for d in data.get("dates", []):
        games.extend(d.get("games", []))
    return games


def get_feed(game_pk):
    url = f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
    return requests.get(url, timeout=15).json()


def get_odds():
    if not ODDS_API_KEY:
        print("ODDS API KEY MISSING")
        return []

    url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": ODDS_MARKETS,
        "oddsFormat": "american",
    }

    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code != 200:
            print("ODDS API ERROR:", r.status_code, r.text)
            return []
        data = r.json()
        print(f"ODDS EVENTS RETURNED: {len(data)} | Markets: {ODDS_MARKETS} | Credit-smart: no extra CLV/WATCH calls")
        return data
    except Exception as e:
        print("ODDS API EXCEPTION:", repr(e))
        return []


def parse_start_time(game):
    raw = game.get("gameDate")
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(TZ)


def should_fetch_feed(start_time):
    if not start_time:
        return True
    minutes_until = (start_time - now_local()).total_seconds() / 60
    return minutes_until <= PREGAME_WINDOW_MINUTES


def get_current_base_state(linescore):
    offense = linescore.get("offense", {})
    first = bool(offense.get("first"))
    second = bool(offense.get("second"))
    third = bool(offense.get("third"))

    if first and second and third:
        label = "Bases loaded"
    elif first and second:
        label = "1st and 2nd"
    elif first and third:
        label = "1st and 3rd"
    elif second and third:
        label = "2nd and 3rd"
    elif first:
        label = "Runner on 1st"
    elif second:
        label = "Runner on 2nd"
    elif third:
        label = "Runner on 3rd"
    else:
        label = "Bases empty"

    return {
        "first": first,
        "second": second,
        "third": third,
        "runners_on": int(first) + int(second) + int(third),
        "label": label,
    }


def base_out_expectancy_score(base_state, outs):
    first = base_state["first"]
    second = base_state["second"]
    third = base_state["third"]
    runners = base_state["runners_on"]

    if runners == 0:
        raw = 4
    elif first and not second and not third:
        raw = 15
    elif second and not first and not third:
        raw = 30
    elif third and not first and not second:
        raw = 40
    elif first and second and not third:
        raw = 42
    elif first and third and not second:
        raw = 55
    elif second and third and not first:
        raw = 68
    elif first and second and third:
        raw = 82
    else:
        raw = 0

    if outs == 0:
        raw *= 1.15
    elif outs == 2:
        raw *= 0.50

    return round(clamp(raw))


def parse_game(feed, schedule_game):
    gd = feed.get("gameData", {})
    ld = feed.get("liveData", {})
    linescore = ld.get("linescore", {})
    current_play = ld.get("plays", {}).get("currentPlay", {})
    matchup = current_play.get("matchup", {})

    home = gd.get("teams", {}).get("home", {}).get("name", "")
    away = gd.get("teams", {}).get("away", {}).get("name", "")
    status = gd.get("status", {}).get("abstractGameState", "")

    defense = linescore.get("defense", {})
    pitcher = defense.get("pitcher", {})
    inning_state = linescore.get("inningState", "")

    if inning_state == "Top":
        batting_side = "away"
    elif inning_state == "Bottom":
        batting_side = "home"
    else:
        batting_side = None

    base_state = get_current_base_state(linescore)
    outs = safe_int(linescore.get("outs", 0), 0)
    inning = safe_int(linescore.get("currentInning", 1), 1)

    away_runs = linescore.get("teams", {}).get("away", {}).get("runs", 0) or 0
    home_runs = linescore.get("teams", {}).get("home", {}).get("runs", 0) or 0

    return {
        "game_pk": str(schedule_game.get("gamePk", "")),
        "home": home,
        "away": away,
        "status": status,
        "start_time": parse_start_time(schedule_game),
        "inning": inning,
        "inning_state": inning_state,
        "outs": outs,
        "home_runs": home_runs,
        "away_runs": away_runs,
        "total_runs": home_runs + away_runs,
        "base_state": base_state,
        "runners_on": base_state["runners_on"],
        "base_out_pressure": base_out_expectancy_score(base_state, outs),
        "batting_side": batting_side,
        "current_batter_id": matchup.get("batter", {}).get("id"),
        "current_batter_name": matchup.get("batter", {}).get("fullName", "Unknown"),
        "current_batter_hand": matchup.get("batSide", {}).get("code"),
        "pitcher_name": pitcher.get("fullName", "Unknown"),
        "pitcher_id": pitcher.get("id"),
        "pitcher_hand": matchup.get("pitchHand", {}).get("code"),
    }


def innings_remaining_estimate(info):
    inning = safe_int(info["inning"], 1)
    state = info["inning_state"]
    half_innings_played = max(0, (inning - 1) * 2 + (0 if state == "Top" else 1))
    total_scheduled_halves = 18

    if inning >= 9 and info["home_runs"] > info["away_runs"]:
        total_scheduled_halves = 17

    halves_remaining = max(0, total_scheduled_halves - half_innings_played)
    return halves_remaining / 2.0


def get_player_hand(feed, player_id, hand_type):
    if not player_id:
        return None
    player = feed.get("gameData", {}).get("players", {}).get(f"ID{player_id}", {})
    if hand_type == "bat":
        return player.get("batSide", {}).get("code")
    if hand_type == "pitch":
        return player.get("pitchHand", {}).get("code")
    return None


def get_batting_order(feed, side):
    if side not in ["home", "away"]:
        return []

    box_team = feed.get("liveData", {}).get("boxscore", {}).get("teams", {}).get(side, {})
    players = box_team.get("players", {})
    order = []

    for pdata in players.values():
        bo = pdata.get("battingOrder")
        person = pdata.get("person", {})
        if not bo:
            continue
        try:
            slot = int(str(bo)[0])
        except Exception:
            continue
        pid = person.get("id")
        order.append({
            "slot": slot,
            "id": pid,
            "name": person.get("fullName", "Unknown"),
            "hand": get_player_hand(feed, pid, "bat"),
        })

    seen = set()
    cleaned = []
    for h in sorted(order, key=lambda x: (x["slot"], x["name"])):
        key = (h["slot"], h["id"])
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(h)
    return cleaned


def upcoming_hitters(feed, info, count=4):
    order = get_batting_order(feed, info.get("batting_side"))
    if not order:
        return []

    current_id = info.get("current_batter_id")
    current_index = None

    for i, h in enumerate(order):
        if h["id"] == current_id:
            current_index = i
            break

    if current_index is None:
        return order[:count]

    return [order[(current_index + step) % len(order)] for step in range(count)]


def lineup_slot_value(slot):
    if slot in [1, 2, 3]:
        return 22
    if slot in [4, 5]:
        return 20
    if slot == 6:
        return 12
    if slot == 7:
        return 7
    return 4


def platoon_value(pitcher_hand, batter_hand):
    if not pitcher_hand or not batter_hand:
        return 0
    if batter_hand == "S":
        return 8
    if pitcher_hand == "L" and batter_hand == "R":
        return 10
    if pitcher_hand == "R" and batter_hand == "L":
        return 10
    return 2


def lineup_pressure_score(info, hitters):
    if not hitters:
        return 0

    score = 0
    pitcher_hand = info.get("pitcher_hand")

    for idx, h in enumerate(hitters):
        weight = 1.0 if idx == 0 else 0.70 if idx == 1 else 0.50 if idx == 2 else 0.35
        score += lineup_slot_value(h["slot"]) * weight
        score += platoon_value(pitcher_hand, h.get("hand")) * weight

    base_pressure = info["base_out_pressure"]
    if base_pressure >= 75:
        score *= 1.35
    elif base_pressure >= 55:
        score *= 1.20
    elif base_pressure >= 30:
        score *= 1.08

    return round(clamp(score))


def format_hitters(hitters):
    if not hitters:
        return "Unknown"
    return ", ".join([f"{h['slot']}-{h['name']}({h.get('hand') or '?'})" for h in hitters])


def pitcher_box(feed, pitcher_id):
    empty = {
        "pitch_count": 0,
        "walks": 0,
        "strikeouts": 0,
        "runs": 0,
        "hits": 0,
        "innings": 0.0,
        "outs_recorded": 0,
        "batters_faced": 0,
        "hbp": 0,
        "home_runs": 0,
    }

    if not pitcher_id:
        return empty

    box = feed.get("liveData", {}).get("boxscore", {})
    pid = f"ID{pitcher_id}"

    for side in ["home", "away"]:
        players = box.get("teams", {}).get(side, {}).get("players", {})
        if pid in players:
            p = players[pid].get("stats", {}).get("pitching", {})
            innings_raw = str(p.get("inningsPitched", "0"))
            innings = safe_float(innings_raw.replace(".1", ".33").replace(".2", ".67"), 0)

            outs = int(math.floor(innings)) * 3
            if ".1" in innings_raw:
                outs += 1
            elif ".2" in innings_raw:
                outs += 2

            return {
                "pitch_count": safe_int(p.get("numberOfPitches")),
                "walks": safe_int(p.get("baseOnBalls")),
                "strikeouts": safe_int(p.get("strikeOuts")),
                "runs": safe_int(p.get("runs")),
                "hits": safe_int(p.get("hits")),
                "innings": innings,
                "outs_recorded": outs,
                "batters_faced": safe_int(p.get("battersFaced")),
                "hbp": safe_int(p.get("hitBatsmen")),
                "home_runs": safe_int(p.get("homeRuns")),
            }

    return empty


def extract_recent_sequence(feed, pitcher_id=None, last_n=14):
    plays = feed.get("liveData", {}).get("plays", {}).get("allPlays", [])
    if pitcher_id:
        plays = [p for p in plays if p.get("matchup", {}).get("pitcher", {}).get("id") == pitcher_id]
    return plays[-last_n:]


def event_text(play):
    return (play.get("result", {}).get("event") or "").lower()


def play_is_baserunner(play):
    event = event_text(play)
    return any(x in event for x in [
        "single", "double", "triple", "home run", "walk", "hit by pitch",
        "fielding error", "catcher interference"
    ])


def play_is_hit(play):
    event = event_text(play)
    return any(x in event for x in ["single", "double", "triple", "home run"])


def play_is_walk(play):
    return "walk" in event_text(play)


def play_is_strikeout(play):
    return "strikeout" in event_text(play)


def max_consecutive_baserunners(plays):
    best = 0
    cur = 0
    for p in plays:
        if play_is_baserunner(p):
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def traffic_metrics(feed, pitcher_id):
    recent = extract_recent_sequence(feed, pitcher_id, last_n=14)
    return {
        "recent_baserunners": sum(1 for p in recent if play_is_baserunner(p)),
        "recent_hits": sum(1 for p in recent if play_is_hit(p)),
        "recent_walks": sum(1 for p in recent if play_is_walk(p)),
        "recent_strikeouts": sum(1 for p in recent if play_is_strikeout(p)),
        "consecutive_baserunners": max_consecutive_baserunners(recent),
    }


def live_statcast_quality(feed, pitcher_id):
    summary = {
        "total_pitches": 0,
        "strike_pct": 0,
        "whiff_pct": 0,
        "zone_pct": 0,
        "first_pitch_strike_pct": 0,
        "csw_pct": 0,
        "hard_hit": 0,
        "barrels": 0,
        "balls_in_play": 0,
        "avg_ev": 0,
        "max_ev": 0,
        "ev_trend": 0,
        "recent_avg_ev": 0,
        "early_avg_ev": 0,
        "velo_drop": 0,
        "spin_drop": 0,
        "movement_drop": 0,
        "release_drift": 0,
        "pitch_types": {},
    }

    if not pitcher_id:
        return summary

    total_pitches = 0
    strikes = 0
    called_or_swinging = 0
    swings = 0
    whiffs = 0
    zone_pitches = 0
    first_pitch_total = 0
    first_pitch_strikes = 0

    all_velos = []
    all_spins = []
    all_breaks = []
    release_points = []
    exit_velos = []
    pitch_types = {}

    for play in feed.get("liveData", {}).get("plays", {}).get("allPlays", []):
        pitcher = play.get("matchup", {}).get("pitcher", {})
        if pitcher.get("id") != pitcher_id:
            continue

        play_pitch_number = 0

        for event in play.get("playEvents", []):
            details = event.get("details", {})
            pitch_data = event.get("pitchData", {})
            hit_data = event.get("hitData", {})

            pitch_type = (
                details.get("type", {}).get("description")
                or details.get("type", {}).get("code")
                or "Unknown"
            )

            if pitch_type not in pitch_types:
                pitch_types[pitch_type] = {
                    "count": 0,
                    "velos": [],
                    "spins": [],
                    "breaks": [],
                    "hard_hit": 0,
                    "barrels": 0,
                    "bip": 0,
                    "whiffs": 0,
                    "swings": 0,
                    "strikes": 0,
                }

            if event.get("isPitch"):
                play_pitch_number += 1
                total_pitches += 1
                pitch_types[pitch_type]["count"] += 1

                code = details.get("code", "")
                desc = (details.get("description") or "").lower()

                is_strike = (
                    code in ["S", "C", "F", "T", "W", "M"]
                    or "strike" in desc
                    or "foul" in desc
                )
                is_called_or_swinging = ("called strike" in desc) or ("swinging strike" in desc)
                is_swing = any(x in desc for x in ["swing", "foul", "in play"])
                is_whiff = any(x in desc for x in ["swinging strike", "missed bunt"])

                if is_strike:
                    strikes += 1
                    pitch_types[pitch_type]["strikes"] += 1
                if is_called_or_swinging:
                    called_or_swinging += 1
                if is_swing:
                    swings += 1
                    pitch_types[pitch_type]["swings"] += 1
                if is_whiff:
                    whiffs += 1
                    pitch_types[pitch_type]["whiffs"] += 1

                if play_pitch_number == 1:
                    first_pitch_total += 1
                    if is_strike:
                        first_pitch_strikes += 1

                coords = pitch_data.get("coordinates", {})
                px = safe_float(coords.get("pX"), None)
                pz = safe_float(coords.get("pZ"), None)
                sz_top = safe_float(pitch_data.get("strikeZoneTop"), None)
                sz_bot = safe_float(pitch_data.get("strikeZoneBottom"), None)

                if px is not None and pz is not None and sz_top is not None and sz_bot is not None:
                    if -0.83 <= px <= 0.83 and sz_bot <= pz <= sz_top:
                        zone_pitches += 1

                velo = pitch_data.get("startSpeed")
                if velo:
                    v = float(velo)
                    all_velos.append(v)
                    pitch_types[pitch_type]["velos"].append(v)

                breaks = pitch_data.get("breaks", {})
                spin = breaks.get("spinRate")
                if spin:
                    s = float(spin)
                    all_spins.append(s)
                    pitch_types[pitch_type]["spins"].append(s)

                break_length = breaks.get("breakLength")
                if break_length:
                    b = float(break_length)
                    all_breaks.append(b)
                    pitch_types[pitch_type]["breaks"].append(b)

                rx = safe_float(coords.get("x"), None)
                ry = safe_float(coords.get("y"), None)
                if rx is not None and ry is not None:
                    release_points.append((rx, ry))

            ev = hit_data.get("launchSpeed")
            la = hit_data.get("launchAngle")
            if ev:
                ev = float(ev)
                exit_velos.append(ev)
                summary["balls_in_play"] += 1
                pitch_types[pitch_type]["bip"] += 1

                if ev >= 95:
                    summary["hard_hit"] += 1
                    pitch_types[pitch_type]["hard_hit"] += 1

                if la is not None:
                    la = float(la)
                    if ev >= 98 and 20 <= la <= 35:
                        summary["barrels"] += 1
                        pitch_types[pitch_type]["barrels"] += 1

    summary["total_pitches"] = total_pitches
    summary["strike_pct"] = round((strikes / total_pitches) * 100, 1) if total_pitches else 0
    summary["whiff_pct"] = round((whiffs / swings) * 100, 1) if swings else 0
    summary["zone_pct"] = round((zone_pitches / total_pitches) * 100, 1) if total_pitches else 0
    summary["first_pitch_strike_pct"] = round((first_pitch_strikes / first_pitch_total) * 100, 1) if first_pitch_total else 0
    summary["csw_pct"] = round((called_or_swinging / total_pitches) * 100, 1) if total_pitches else 0

    summary["avg_ev"] = avg(exit_velos)
    summary["max_ev"] = round(max(exit_velos), 1) if exit_velos else 0
    summary["early_avg_ev"] = avg(exit_velos[:4])
    summary["recent_avg_ev"] = avg(exit_velos[-4:])
    summary["ev_trend"] = round(summary["recent_avg_ev"] - summary["early_avg_ev"], 1) if len(exit_velos) >= 6 else 0

    early_velo = avg(all_velos[:10])
    recent_velo = avg(all_velos[-10:])
    summary["velo_drop"] = round(max(0, early_velo - recent_velo), 1) if early_velo and recent_velo else 0

    early_spin = avg(all_spins[:10])
    recent_spin = avg(all_spins[-10:])
    summary["spin_drop"] = round(max(0, early_spin - recent_spin), 1) if early_spin and recent_spin else 0

    early_break = avg(all_breaks[:10])
    recent_break = avg(all_breaks[-10:])
    summary["movement_drop"] = round(max(0, early_break - recent_break), 1) if early_break and recent_break else 0

    if len(release_points) >= 10:
        early = release_points[:5]
        recent = release_points[-5:]
        ex, ey = avg([p[0] for p in early]), avg([p[1] for p in early])
        rx, ry = avg([p[0] for p in recent]), avg([p[1] for p in recent])
        summary["release_drift"] = round(math.sqrt((rx - ex) ** 2 + (ry - ey) ** 2), 2)

    cleaned = {}
    for ptype, data in pitch_types.items():
        velos = data["velos"]
        spins = data["spins"]
        breaks = data["breaks"]

        early_v = avg(velos[:5])
        recent_v = avg(velos[-5:])
        early_s = avg(spins[:5])
        recent_s = avg(spins[-5:])
        early_b = avg(breaks[:5])
        recent_b = avg(breaks[-5:])

        cleaned[ptype] = {
            "count": data["count"],
            "avg_velo": avg(velos),
            "recent_velo": recent_v,
            "velo_drop": round(max(0, early_v - recent_v), 1) if early_v and recent_v else 0,
            "avg_spin": avg(spins),
            "recent_spin": recent_s,
            "spin_drop": round(max(0, early_s - recent_s), 1) if early_s and recent_s else 0,
            "avg_break": avg(breaks),
            "recent_break": recent_b,
            "movement_drop": round(max(0, early_b - recent_b), 1) if early_b and recent_b else 0,
            "hard_hit": data["hard_hit"],
            "barrels": data["barrels"],
            "bip": data["bip"],
            "whiff_pct": round((data["whiffs"] / data["swings"]) * 100, 1) if data["swings"] else 0,
            "strike_pct": round((data["strikes"] / data["count"]) * 100, 1) if data["count"] else 0,
        }

    summary["pitch_types"] = cleaned
    return summary


def pitch_type_red_flags(q):
    flags = []
    for ptype, d in q["pitch_types"].items():
        if d["count"] < 5:
            continue
        if d["velo_drop"] >= 1.5:
            flags.append(f"{ptype}: velo down {d['velo_drop']} mph")
        if d["spin_drop"] >= 150:
            flags.append(f"{ptype}: spin down {d['spin_drop']} rpm")
        if d["movement_drop"] >= 2:
            flags.append(f"{ptype}: movement down {d['movement_drop']}")
        if d["hard_hit"] >= 2:
            flags.append(f"{ptype}: {d['hard_hit']} hard-hit balls")
        if d["barrels"] >= 1:
            flags.append(f"{ptype}: {d['barrels']} barrel(s)")
        if d["whiff_pct"] <= 10 and d["count"] >= 8:
            flags.append(f"{ptype}: low whiff {d['whiff_pct']}%")
    return flags[:5]


def pitcher_stress_score(p, q, traffic):
    score = 0
    outs = p.get("outs_recorded", 0)
    pitches = p.get("pitch_count", 0)
    baserunners_total = p.get("hits", 0) + p.get("walks", 0) + p.get("hbp", 0)
    pitches_per_out = round(pitches / outs, 2) if outs else pitches
    baserunners_per_inning = round(baserunners_total / p["innings"], 2) if p.get("innings", 0) else baserunners_total

    if pitches >= 95:
        score += 22
    elif pitches >= 85:
        score += 17
    elif pitches >= 75:
        score += 12
    elif pitches >= 60:
        score += 7

    if pitches_per_out >= 6.5:
        score += 22
    elif pitches_per_out >= 5.5:
        score += 16
    elif pitches_per_out >= 4.7:
        score += 9

    if baserunners_per_inning >= 2.0:
        score += 20
    elif baserunners_per_inning >= 1.5:
        score += 14
    elif baserunners_per_inning >= 1.1:
        score += 8

    if traffic["recent_baserunners"] >= 5:
        score += 18
    elif traffic["recent_baserunners"] >= 3:
        score += 11

    if traffic["consecutive_baserunners"] >= 3:
        score += 15
    elif traffic["consecutive_baserunners"] >= 2:
        score += 8

    if q["velo_drop"] >= 2:
        score += 12
    elif q["velo_drop"] >= 1:
        score += 7

    if q["spin_drop"] >= 250:
        score += 8
    elif q["spin_drop"] >= 150:
        score += 5

    if q["release_drift"] >= 2:
        score += 6

    return round(clamp(score))


def pitcher_dominance_score(p, q, traffic):
    score = 0
    outs = p.get("outs_recorded", 0)
    pitches = p.get("pitch_count", 0)
    baserunners_total = p.get("hits", 0) + p.get("walks", 0) + p.get("hbp", 0)
    pitches_per_out = round(pitches / outs, 2) if outs else 99

    if q["strike_pct"] >= 68:
        score += 18
    elif q["strike_pct"] >= 63:
        score += 10

    if q["whiff_pct"] >= 32:
        score += 18
    elif q["whiff_pct"] >= 25:
        score += 10

    if q["csw_pct"] >= 32:
        score += 10

    if q["first_pitch_strike_pct"] >= 65:
        score += 8

    if pitches_per_out <= 4.0 and outs >= 9:
        score += 18
    elif pitches_per_out <= 4.8 and outs >= 9:
        score += 10

    if baserunners_total <= 2 and outs >= 9:
        score += 15
    elif baserunners_total <= 4 and outs >= 12:
        score += 8

    if p["walks"] == 0:
        score += 8
    elif p["walks"] == 1:
        score += 4

    if q["hard_hit"] <= 1 and q["balls_in_play"] >= 5:
        score += 10
    elif q["hard_hit"] <= 2 and q["balls_in_play"] >= 8:
        score += 5

    if traffic["recent_baserunners"] >= 3:
        score -= 15
    if traffic["consecutive_baserunners"] >= 2:
        score -= 10

    return round(clamp(score))


def contact_quality_score(q, traffic):
    score = 0
    if q["hard_hit"] >= 6:
        score += 30
    elif q["hard_hit"] >= 4:
        score += 22
    elif q["hard_hit"] >= 2:
        score += 10

    if q["barrels"] >= 2:
        score += 22
    elif q["barrels"] >= 1:
        score += 12

    if q["avg_ev"] >= 91:
        score += 12
    elif q["avg_ev"] >= 88:
        score += 7

    if q["max_ev"] >= 108:
        score += 10
    elif q["max_ev"] >= 103:
        score += 6

    if traffic["recent_hits"] >= 4:
        score += 12
    elif traffic["recent_hits"] >= 2:
        score += 6

    return round(clamp(score))


def bullpen_risk_score(info, p):
    inning = safe_int(info["inning"], 1)
    pitches = p.get("pitch_count", 0)
    score = 0

    if inning >= 5 and pitches >= 80:
        score += 25
    elif inning >= 5 and pitches >= 70:
        score += 18
    elif inning >= 4 and pitches >= 80:
        score += 15

    if pitches >= 95:
        score += 18
    elif pitches >= 85:
        score += 12

    if inning >= 6:
        score += 15
    elif inning >= 5:
        score += 10

    if p.get("innings", 0) < 5 and inning >= 5:
        score += 15

    return round(clamp(score))


def remaining_opportunity_score(info, lineup_pressure, bullpen_risk):
    innings_left = innings_remaining_estimate(info)
    score = 0

    if innings_left >= 6:
        score += 35
    elif innings_left >= 4:
        score += 28
    elif innings_left >= 2.5:
        score += 20
    elif innings_left >= 1:
        score += 10
    else:
        score += 2

    score += lineup_pressure * 0.25
    score += bullpen_risk * 0.25

    run_diff = abs(info["home_runs"] - info["away_runs"])
    if info["inning"] >= 8 and run_diff <= 1:
        score += 8

    return round(clamp(score))


def current_inning_pressure_score(info, lineup_pressure, pitcher_stress, contact_quality):
    score = (
        info["base_out_pressure"] * 0.45
        + lineup_pressure * 0.25
        + pitcher_stress * 0.18
        + contact_quality * 0.12
    )

    if info["runners_on"] >= 2 and info["outs"] <= 1:
        score += 8
    if info["base_state"]["third"] and info["outs"] <= 1:
        score += 8
    if info["base_state"]["runners_on"] == 3:
        score += 10

    return round(clamp(score))


def run_suppression_score(info, p, q, traffic, contact_quality):
    total_runs = info["total_runs"]
    inning = info["inning"]
    score = 0

    if inning >= 4 and total_runs <= 2:
        score += 25
    elif inning >= 5 and total_runs <= 4:
        score += 15

    score += contact_quality * 0.25

    if traffic["recent_baserunners"] >= 4:
        score += 18
    elif traffic["recent_baserunners"] >= 2:
        score += 9

    if p["pitch_count"] >= 75:
        score += 10

    if q["hard_hit"] >= 4:
        score += 10

    return round(clamp(score))


def false_dominance_score(dominance, stress, contact_quality, traffic):
    score = 0
    if dominance >= 55 and stress >= 55:
        score += 35
    if contact_quality >= 55:
        score += 20
    if traffic["recent_baserunners"] >= 3:
        score += 20
    if traffic["consecutive_baserunners"] >= 2:
        score += 15
    return round(clamp(score))


def contact_trend_score(q):
    """
    Measures whether contact quality is improving or dying.
    Positive trend helps OVER. Negative trend helps UNDER.
    """
    score = 50
    trend = q.get("ev_trend", 0)

    if trend >= 8:
        score += 28
    elif trend >= 5:
        score += 20
    elif trend >= 3:
        score += 12
    elif trend <= -8:
        score -= 28
    elif trend <= -5:
        score -= 20
    elif trend <= -3:
        score -= 12

    if q.get("hard_hit", 0) >= 4:
        score += 10
    if q.get("barrels", 0) >= 1:
        score += 8
    if q.get("balls_in_play", 0) >= 6 and q.get("hard_hit", 0) <= 1:
        score -= 12

    return round(clamp(score))


def times_through_order_score(info, p, hitters):
    """
    Estimates third-time-through-the-order danger.
    This is a forward-looking OVER signal, not just current pressure.
    """
    bf = p.get("batters_faced", 0)
    inning = info.get("inning", 1)
    score = 0

    if bf >= 27:
        score += 35
    elif bf >= 23:
        score += 28
    elif bf >= 18:
        score += 18
    elif bf >= 14:
        score += 8

    if inning >= 5 and bf >= 18:
        score += 12

    if hitters:
        top_slots = sum(1 for h in hitters[:3] if h.get("slot") in [1, 2, 3, 4, 5])
        score += top_slots * 5

    return round(clamp(score))


def starter_exit_probability(info, p, stress, bullpen):
    """
    Predicts starter-to-bullpen transition risk.
    This is one of the most important live-total transition moments.
    """
    pitches = p.get("pitch_count", 0)
    inning = info.get("inning", 1)
    prob = 0

    if pitches >= 100:
        prob += 70
    elif pitches >= 90:
        prob += 58
    elif pitches >= 80:
        prob += 45
    elif pitches >= 70:
        prob += 28
    elif pitches >= 60:
        prob += 15

    if inning >= 6:
        prob += 18
    elif inning >= 5:
        prob += 10

    prob += stress * 0.15
    prob += bullpen * 0.10

    return round(clamp(prob))


def fake_pressure_score(info, p, q, traffic, dominance, contact, current_pressure):
    """
    Reduces false OVER alerts when traffic exists but the quality underneath is weak.
    """
    score = 0

    if current_pressure >= 55 and contact <= 35:
        score += 25
    if traffic.get("recent_baserunners", 0) >= 2 and q.get("avg_ev", 0) and q.get("avg_ev", 0) < 86:
        score += 20
    if dominance >= 60 and contact <= 40:
        score += 20
    if q.get("strike_pct", 0) >= 66 and q.get("hard_hit", 0) <= 1 and q.get("balls_in_play", 0) >= 5:
        score += 18
    if info.get("runners_on", 0) >= 1 and q.get("whiff_pct", 0) >= 28:
        score += 10

    return round(clamp(score))


def market_resistance_score(opening, live, info, current_pressure, contact, dominance):
    """
    Detects whether the market is resisting the scoreboard.
    Positive = OVER help when live total is suppressed despite real pressure.
    Negative = UNDER help when live total is inflated without real pressure.
    """
    mp = market_pressure(opening, live)
    score = 0

    if mp["direction"] == "suppressed":
        score += 18
        if current_pressure >= 55 or contact >= 50:
            score += 18
        if dominance >= 65 and contact <= 35:
            score -= 22

    if mp["direction"] == "inflated":
        score -= 18
        if dominance >= 55 and current_pressure <= 35 and contact <= 40:
            score -= 22
        if contact >= 60 or current_pressure >= 65:
            score += 12

    return round(max(-100, min(100, score)))


def blowout_kill_score(info):
    """
    Late blowouts reduce scoring intent and can kill overs.
    """
    run_diff = abs(info.get("home_runs", 0) - info.get("away_runs", 0))
    inning = info.get("inning", 1)
    score = 0

    if inning >= 7 and run_diff >= 6:
        score += 45
    elif inning >= 6 and run_diff >= 5:
        score += 30
    elif inning >= 5 and run_diff >= 7:
        score += 25

    return round(clamp(score))


def under_environment_score(info, p, q, traffic, dominance, contact, current_pressure, remaining_opp, fake_pressure, blowout):
    """
    Builds real UNDER paths instead of only allowing UNDER through raw projected edge.
    """
    score = 0

    if dominance >= 70:
        score += 30
    elif dominance >= 60:
        score += 22
    elif dominance >= 50:
        score += 12

    if contact <= 25 and q.get("balls_in_play", 0) >= 5:
        score += 22
    elif contact <= 35:
        score += 12

    if current_pressure <= 25:
        score += 18
    elif current_pressure <= 35:
        score += 10

    if traffic.get("recent_baserunners", 0) <= 1:
        score += 12

    if remaining_opp <= 35:
        score += 14

    score += fake_pressure * 0.30
    score += blowout * 0.20

    if p.get("walks", 0) == 0 and q.get("strike_pct", 0) >= 64:
        score += 8

    return round(clamp(score))



def run_conversion_score(info, p, q, traffic, hitters, current_pressure, remaining_opp, stress, contact, lineup, contact_trend, tto, starter_exit, fake_pressure):
    """
    OVER quality score: pressure must be likely to become actual runs.
    This prevents the bot from betting every traffic situation.
    """
    score = 0
    outs = info.get("outs", 0)
    runners_on = info.get("runners_on", 0)
    base_state = info.get("base_state", {})

    # Base/out conversion value.
    if base_state.get("third") and outs <= 1:
        score += 26
    elif base_state.get("second") and outs <= 1:
        score += 20
    elif runners_on >= 2 and outs <= 1:
        score += 28
    elif runners_on >= 2 and outs == 2:
        score += 12
    elif runners_on == 1 and outs <= 1:
        score += 10

    # Current pressure matters, but only when it can convert.
    if current_pressure >= 70:
        score += 18
    elif current_pressure >= 60:
        score += 12
    elif current_pressure >= 50:
        score += 6

    # Contact quality: hard contact converts pressure.
    if contact >= 65:
        score += 18
    elif contact >= 55:
        score += 12
    elif contact >= 45:
        score += 6

    if q.get("barrels", 0) >= 1:
        score += 10
    if q.get("hard_hit", 0) >= 3:
        score += 8
    if q.get("ev_trend", 0) >= 5:
        score += 8

    # Command/traffic.
    if p.get("walks", 0) >= 2:
        score += 8
    if q.get("strike_pct", 100) <= 55 and q.get("total_pitches", 0) >= 18:
        score += 9
    if q.get("zone_pct", 100) <= 42 and q.get("total_pitches", 0) >= 18:
        score += 6
    if traffic.get("consecutive_baserunners", 0) >= 2:
        score += 10
    if traffic.get("recent_baserunners", 0) >= 4:
        score += 8

    # Future conversion: lineup, starter exit, third time through.
    if lineup >= 75:
        score += 10
    elif lineup >= 65:
        score += 6

    if starter_exit >= 65:
        score += 10
    elif starter_exit >= 50:
        score += 6

    if tto >= 55:
        score += 8

    if remaining_opp >= 65:
        score += 6

    # Swing-and-miss and fake pressure reduce conversion odds.
    if q.get("whiff_pct", 0) >= 34 and q.get("csw_pct", 0) >= 30:
        score -= 16
    elif q.get("whiff_pct", 0) >= 28 and q.get("csw_pct", 0) >= 29:
        score -= 8

    score -= fake_pressure * 0.22

    # Two outs makes conversion harder unless there is elite contact/command collapse.
    if outs == 2 and current_pressure < 70 and contact < 60:
        score -= 10

    return round(clamp(score))


def run_prevention_score(info, p, q, traffic, dominance, contact, current_pressure, remaining_opp, fake_pressure, under_environment, blowout):
    """
    UNDER quality score: low scoring must be likely to continue.
    """
    score = 0

    if dominance >= 75:
        score += 24
    elif dominance >= 65:
        score += 18
    elif dominance >= 55:
        score += 10

    if q.get("whiff_pct", 0) >= 32 and q.get("csw_pct", 0) >= 30:
        score += 18
    elif q.get("whiff_pct", 0) >= 26 and q.get("csw_pct", 0) >= 28:
        score += 10

    if contact <= 25 and q.get("balls_in_play", 0) >= 4:
        score += 18
    elif contact <= 35:
        score += 10

    if current_pressure <= 25:
        score += 16
    elif current_pressure <= 35:
        score += 8

    if traffic.get("recent_baserunners", 0) <= 1:
        score += 10

    if p.get("walks", 0) == 0 and q.get("strike_pct", 0) >= 64:
        score += 8

    if remaining_opp <= 35:
        score += 10

    score += fake_pressure * 0.20
    score += under_environment * 0.25
    score += blowout * 0.12

    # Under danger penalties.
    if current_pressure >= 60:
        score -= 15
    if contact >= 55:
        score -= 15
    if p.get("walks", 0) >= 2:
        score -= 8
    if q.get("barrels", 0) >= 1:
        score -= 8

    return round(clamp(score))


def predictive_market_move_score(opening, live, info, p, q, traffic, scores):
    """
    Attempts to identify the market moving BEFORE runs score.
    This is a WATCH-first engine unless the run conversion/prevention is already strong.
    Positive score = likely upward total move.
    Negative score = likely downward total move.
    """
    if live is None:
        return 0

    move = 0
    if opening is not None:
        move = live - opening

    score = 0

    # Leading OVER indicators before score changes.
    if p.get("batters_faced", 0) >= 6:
        pitches_per_batter = p.get("pitch_count", 0) / max(1, p.get("batters_faced", 1))
        if pitches_per_batter >= 4.2:
            score += 18
        elif pitches_per_batter >= 3.8:
            score += 10

    if q.get("strike_pct", 100) <= 55 and q.get("total_pitches", 0) >= 18:
        score += 14
    if q.get("zone_pct", 100) <= 42 and q.get("total_pitches", 0) >= 18:
        score += 10
    if q.get("ev_trend", 0) >= 5:
        score += 12
    if q.get("hard_hit", 0) >= 2:
        score += 10
    if q.get("barrels", 0) >= 1:
        score += 12
    if traffic.get("recent_baserunners", 0) >= 3:
        score += 14
    if traffic.get("consecutive_baserunners", 0) >= 2:
        score += 12
    if scores.get("times_through_order", 0) >= 45:
        score += 8
    if scores.get("starter_exit_probability", 0) >= 55:
        score += 10
    if scores.get("lineup_pressure", 0) >= 70:
        score += 8

    # If the market has already moved upward, reduce predictive score.
    if move >= 1.0:
        score -= int(move * 18)

    # Leading UNDER indicators.
    under_pull = 0
    if scores.get("run_prevention", 0) >= 70:
        under_pull += 18
    if scores.get("under_environment", 0) >= 60:
        under_pull += 14
    if scores.get("fake_pressure", 0) >= 55:
        under_pull += 12
    if scores.get("current_inning_pressure", 0) <= 25 and scores.get("contact_quality", 0) <= 35:
        under_pull += 10

    if under_pull > score:
        return -round(clamp(under_pull))

    return round(clamp(score))


def best_entry_guidance(side, live, opening, edge, action, scores):
    """
    Gives practical entry guidance so the alert does not mean 'bet at any price.'
    """
    if live is None:
        return "No live line available."

    move = 0
    if opening is not None:
        move = live - opening

    if side == "OVER":
        strong_line = live - BEST_ENTRY_HALF_RUN_BUFFER
        if move >= MARKET_HAS_ALREADY_MOVED_RUNS and action == "WATCH":
            return f"Best entry: {strong_line:.1f} or lower. Current {live} has already moved +{move:.1f}; avoid chasing unless pressure continues."
        if scores.get("confirmation_score", 0) >= 68 or scores.get("run_conversion", 0) >= 70:
            return f"Best entry: {live} playable; {strong_line:.1f} or lower is stronger."
        return f"Best entry: wait for {strong_line:.1f} or renewed pressure confirmation."

    if side == "UNDER":
        strong_line = live + BEST_ENTRY_HALF_RUN_BUFFER
        if move <= -MARKET_HAS_ALREADY_MOVED_RUNS and action == "WATCH":
            return f"Best entry: {strong_line:.1f} or higher. Current {live} has already moved down {move:.1f}; avoid chasing unless run prevention stays strong."
        if scores.get("confirmation_score", 0) >= 68 or scores.get("run_prevention", 0) >= 75:
            return f"Best entry: {live} playable; {strong_line:.1f} or higher is stronger."
        return f"Best entry: wait for {strong_line:.1f} or cleaner run-prevention confirmation."

    return "No entry guidance."


def pressure_to_runs_score(info, p, q, traffic, hitters, scores):
    """
    Measures whether pressure is likely to become ACTUAL RUNS.
    This is different from simple pressure.

    Strong OVER predictors:
      - Runner in scoring position with <2 outs
      - Hard contact / barrel
      - Command problems
      - Top/middle lineup
      - Starter stress and early bullpen risk
    """
    score = 0
    outs = info.get("outs", 0)
    runners = info.get("runners_on", 0)
    base = info.get("base_state", {})

    # Base/out run conversion.
    if base.get("third") and outs <= 1:
        score += 24
    elif base.get("second") and outs <= 1:
        score += 18
    elif runners >= 2 and outs <= 1:
        score += 26
    elif runners >= 2 and outs == 2:
        score += 10
    elif runners == 1 and outs <= 1:
        score += 8

    # Quality of contact.
    if scores.get("contact_quality", 0) >= 60:
        score += 16
    elif scores.get("contact_quality", 0) >= 48:
        score += 10

    if q.get("barrels", 0) >= 1:
        score += 10
    if q.get("hard_hit", 0) >= 2:
        score += 8
    if q.get("ev_trend", 0) >= 5:
        score += 8

    # Pitcher command and stress.
    if scores.get("pitcher_stress", 0) >= 60:
        score += 12
    elif scores.get("pitcher_stress", 0) >= 45:
        score += 8

    if q.get("strike_pct", 100) <= 56 and q.get("total_pitches", 0) >= 18:
        score += 9
    if q.get("zone_pct", 100) <= 42 and q.get("total_pitches", 0) >= 18:
        score += 7
    if p.get("walks", 0) >= 2:
        score += 8

    # Traffic sequencing.
    if traffic.get("recent_baserunners", 0) >= 4:
        score += 10
    elif traffic.get("recent_baserunners", 0) >= 2:
        score += 6
    if traffic.get("consecutive_baserunners", 0) >= 2:
        score += 8

    # Lineup and future path.
    if scores.get("lineup_pressure", 0) >= 75:
        score += 10
    elif scores.get("lineup_pressure", 0) >= 62:
        score += 6

    if scores.get("starter_exit_probability", 0) >= 60:
        score += 8
    if scores.get("times_through_order", 0) >= 45:
        score += 6

    # Penalize fake pressure and dominant swing-and-miss.
    score -= scores.get("fake_pressure", 0) * 0.22

    if q.get("whiff_pct", 0) >= 34 and q.get("csw_pct", 0) >= 31 and scores.get("contact_quality", 0) < 50:
        score -= 12
    elif q.get("whiff_pct", 0) >= 28 and q.get("csw_pct", 0) >= 29 and scores.get("contact_quality", 0) < 45:
        score -= 7

    if outs == 2 and runners < 2 and scores.get("contact_quality", 0) < 55:
        score -= 8

    return round(clamp(score))


def pre_run_over_watch_score(opening, live, info, p, q, traffic, scores):
    """
    Predictive OVER engine.

    Goal:
      Detect 'market likely to move up' BEFORE the runs fully arrive.

    This sends WATCH first, unless the normal STRIKE engine already confirms.
    """
    if not ENABLE_PRE_RUN_OVER_WATCH or live is None:
        return 0

    move = 0
    if opening is not None:
        move = live - opening

    # If the market already moved up too much, this is no longer pre-run value.
    if move > MARKET_LAG_MAX_UPWARD_MOVE:
        return 0

    score = 0

    # Pressure-to-runs is the anchor.
    score += scores.get("pressure_to_runs", 0) * 0.45

    # Leading indicators.
    if p.get("batters_faced", 0) >= 6:
        ppb = p.get("pitch_count", 0) / max(1, p.get("batters_faced", 1))
        if ppb >= 4.2:
            score += 14
        elif ppb >= 3.8:
            score += 8

    if scores.get("pitcher_stress", 0) >= 50:
        score += 10
    if scores.get("contact_quality", 0) >= 45:
        score += 8
    if scores.get("lineup_pressure", 0) >= 65:
        score += 8
    if scores.get("remaining_opportunity", 0) >= 55:
        score += 5

    if q.get("strike_pct", 100) <= 56 and q.get("total_pitches", 0) >= 18:
        score += 8
    if q.get("zone_pct", 100) <= 42 and q.get("total_pitches", 0) >= 18:
        score += 6
    if q.get("hard_hit", 0) >= 2:
        score += 8
    if q.get("barrels", 0) >= 1:
        score += 10
    if traffic.get("recent_baserunners", 0) >= 3:
        score += 9
    if traffic.get("consecutive_baserunners", 0) >= 2:
        score += 8

    # Good over spots are often early or middle game.
    if info.get("inning", 1) <= 4:
        score += 7
    elif info.get("inning", 1) <= 6:
        score += 3

    # Fake pressure and strong run prevention block it.
    if scores.get("fake_pressure", 0) >= PRE_RUN_MAX_FAKE_PRESSURE:
        score -= 18
    if scores.get("under_environment", 0) >= 65:
        score -= 12
    if scores.get("run_prevention", 0) >= 70:
        score -= 12

    return round(clamp(score))


def over_value_score(opening, live, edge, scores):
    """
    Price/number discipline for OVERs.
    We want to predict the move, not chase it.
    """
    if live is None:
        return 0

    move = 0
    if opening is not None:
        move = live - opening

    score = 50
    score += max(0, edge) * 8
    score += scores.get("pressure_to_runs", 0) * 0.20
    score += scores.get("pre_run_over_watch", 0) * 0.15

    if move <= 0.5:
        score += 12
    elif move <= 1.0:
        score += 6
    elif move >= 1.5:
        score -= 14
    elif move >= 2.0:
        score -= 22

    return round(clamp(score))


def threat_index_score(info):
    """
    Immediate run-danger score from base/out state.
    This came directly from the winning alerts: bases and outs mattered as much as raw contact.
    """
    base = info.get("base_state", {})
    outs = safe_int(info.get("outs", 0), 0)
    runners = safe_int(info.get("runners_on", 0), 0)

    first = bool(base.get("first"))
    second = bool(base.get("second"))
    third = bool(base.get("third"))

    if first and second and third:
        raw = 100
    elif first and third:
        raw = 90
    elif second and third:
        raw = 92
    elif first and second:
        raw = 82
    elif third:
        raw = 72
    elif second:
        raw = 65
    elif first:
        raw = 45
    else:
        raw = 10

    if outs == 0:
        raw += 12
    elif outs == 1:
        raw += 4
    elif outs == 2:
        raw -= 18

    if runners >= 2 and outs <= 1:
        raw += 8

    return round(clamp(raw))


def market_lag_score(side, live_total, projected_total):
    """
    Measures how far the live line appears behind the projection.
    Positive score means the market is lagging in the direction of the proposed side.
    """
    if live_total is None or projected_total is None:
        return 0

    gap = projected_total - live_total

    if side == "UNDER":
        gap = -gap

    if gap >= MARKET_LAG_ELITE:
        return 100
    if gap >= MARKET_LAG_STRONG:
        return round(clamp(60 + (gap - MARKET_LAG_STRONG) * 12))
    if gap >= 1.0:
        return round(clamp(35 + gap * 15))
    if gap >= 0.5:
        return 20
    return 0


def signal_stack_score(side, scores):
    """
    Counts aligned winner-pattern signals.
    This is not a hard filter; it helps separate normal strikes from elite strikes.
    """
    stack = 0
    labels = []

    if side == "OVER":
        checks = [
            ("Stress", scores.get("pitcher_stress", 0) >= 65),
            ("Contact", scores.get("contact_quality", 0) >= 55),
            ("P2R", scores.get("pressure_to_runs", 0) >= 70),
            ("Conv", scores.get("run_conversion", 0) >= 65),
            ("Threat", scores.get("threat_index", 0) >= 70),
            ("MarketLag", scores.get("market_lag", 0) >= 60),
            ("PreOver", scores.get("pre_run_over_watch", 0) >= 72),
            ("PredMove", scores.get("predictive_market_move", 0) >= 65),
        ]
    else:
        checks = [
            ("Prev", scores.get("run_prevention", 0) >= 70),
            ("UnderEnv", scores.get("under_environment", 0) >= 60),
            ("LowCIP", scores.get("current_inning_pressure", 0) <= 30),
            ("LowContact", scores.get("contact_quality", 0) <= 35),
            ("MarketLag", scores.get("market_lag", 0) >= 60),
            ("NoP2R", scores.get("pressure_to_runs", 0) <= 25),
            ("Dominance", scores.get("pitcher_dominance", 0) >= 55),
        ]

    for label, ok in checks:
        if ok:
            stack += 1
            labels.append(label)

    return stack, labels[:6]


def p2r_multiplier_boost(scores):
    """
    Additive boost from winner review: P2R was carrying many of the best overs.
    """
    p2r = scores.get("pressure_to_runs", 0)
    if p2r >= P2R_SUPER_ELITE_THRESHOLD:
        return P2R_SUPER_ELITE_BOOST
    if p2r >= P2R_ELITE_THRESHOLD:
        return P2R_ELITE_BOOST
    return 0


def conv_acceleration_score(state, info, side, current_conv):
    """
    Tracks conversion acceleration by game/side using local state only.
    No extra API calls. Rapid Conv jumps are a 'runs are coming' signal.
    """
    if not ENABLE_WINNER_PATTERN_ENHANCEMENTS:
        return 0

    key = f"{today()}::{info.get('away','')}@{info.get('home','')}::{side}::conv"
    games = state.setdefault("games", {})
    node = games.setdefault(key, {})

    prev = safe_int(node.get("last_conv"), current_conv)
    jump = current_conv - prev

    node["last_conv"] = current_conv
    node["last_seen"] = now_local().isoformat()

    if jump >= CONV_ACCEL_MIN_JUMP * 2:
        return 100
    if jump >= CONV_ACCEL_MIN_JUMP:
        return round(clamp(55 + jump))
    if current_conv >= 85:
        return 70
    if current_conv >= 70:
        return 45
    return 0


def apply_winner_pattern_enhancements(state, info, side, scores, live_total=None, projected_total=None):
    """
    Adds the five V2.3.1 winner-pattern signals.
    Additive only. Does not suppress the original engine.
    """
    if not ENABLE_WINNER_PATTERN_ENHANCEMENTS:
        return scores

    scores["threat_index"] = threat_index_score(info)
    scores["market_lag"] = market_lag_score(side, live_total, projected_total)
    scores["p2r_boost"] = p2r_multiplier_boost(scores) if side == "OVER" else 0
    scores["conv_acceleration"] = conv_acceleration_score(
        state, info, side, scores.get("run_conversion", 0)
    )

    stack, labels = signal_stack_score(side, scores)
    scores["signal_stack"] = stack
    scores["signal_stack_labels"] = labels

    return scores


def projection_score(side, edge, scores):
    """
    How strongly the model thinks the current market line is wrong.
    This is separate from whether the live game confirms a bet right now.
    """
    ae = abs(edge)
    score = 0

    if ae >= 3.0:
        score += 55
    elif ae >= 2.0:
        score += 42
    elif ae >= 1.5:
        score += 32
    elif ae >= 1.0:
        score += 22
    elif ae >= 0.7:
        score += 12

    if side == "OVER":
        score += scores.get("over_value", 0) * 0.18
        score += max(0, scores.get("predictive_market_move", 0)) * 0.18
        score += scores.get("pre_run_over_watch", 0) * 0.12

        # V2.3.1 winner-pattern boosts.
        score += scores.get("market_lag", 0) * 0.10
        score += scores.get("threat_index", 0) * 0.06
        score += scores.get("p2r_boost", 0)
        score += scores.get("conv_acceleration", 0) * 0.06
        if scores.get("signal_stack", 0) >= SIGNAL_STACK_ELITE:
            score += 8
        elif scores.get("signal_stack", 0) >= SIGNAL_STACK_STRONG:
            score += 4
    else:
        score += scores.get("run_prevention", 0) * 0.20
        score += max(0, -scores.get("predictive_market_move", 0)) * 0.15
        score += scores.get("under_environment", 0) * 0.15

        # V2.3.1 under-side intelligence.
        score += scores.get("market_lag", 0) * 0.08
        if scores.get("signal_stack", 0) >= SIGNAL_STACK_ELITE:
            score += 6
        elif scores.get("signal_stack", 0) >= SIGNAL_STACK_STRONG:
            score += 3

    return round(clamp(score))


def confirmation_score(side, info, scores):
    """
    How much the live game state confirms action right now.
    This prevents misleading 100/100 confidence on low-confirmation WATCH spots.
    """
    inning = safe_int(info.get("inning", 1), 1)

    if side == "OVER":
        score = (
            scores.get("current_inning_pressure", 0) * 0.25
            + scores.get("pressure_to_runs", 0) * 0.25
            + scores.get("run_conversion", 0) * 0.20
            + scores.get("contact_quality", 0) * 0.15
            + scores.get("pitcher_stress", 0) * 0.10
            + scores.get("lineup_pressure", 0) * 0.05
        )

        # Late overs need stronger current or near-current evidence.
        if inning >= 8 and scores.get("current_inning_pressure", 0) < 45:
            score -= 12
        elif inning >= 7 and scores.get("current_inning_pressure", 0) < 35:
            score -= 7

        if scores.get("pre_run_over_watch", 0) >= 85 and scores.get("pressure_to_runs", 0) >= 70:
            score += 8

        # V2.3.1: winners often had immediate base/out threat and stacked signals.
        if scores.get("threat_index", 0) >= 85 and scores.get("pressure_to_runs", 0) >= 70:
            score += 8
        elif scores.get("threat_index", 0) >= 70 and scores.get("pressure_to_runs", 0) >= 65:
            score += 5

        if scores.get("conv_acceleration", 0) >= 70:
            score += 6

        if scores.get("signal_stack", 0) >= SIGNAL_STACK_ELITE:
            score += 7
        elif scores.get("signal_stack", 0) >= SIGNAL_STACK_STRONG:
            score += 4

        if scores.get("run_prevention", 0) >= 65:
            score -= 12

    else:
        score = (
            scores.get("run_prevention", 0) * 0.35
            + scores.get("under_environment", 0) * 0.25
            + max(0, 100 - scores.get("current_inning_pressure", 0)) * 0.15
            + max(0, 100 - scores.get("contact_quality", 0)) * 0.10
            + max(0, 100 - scores.get("pressure_to_runs", 0)) * 0.10
            + max(0, -scores.get("predictive_market_move", 0)) * 0.05
        )

        if inning >= 7:
            score += 6
        if scores.get("signal_stack", 0) >= SIGNAL_STACK_ELITE:
            score += 5
        elif scores.get("signal_stack", 0) >= SIGNAL_STACK_STRONG:
            score += 3
        if scores.get("current_inning_pressure", 0) > 45:
            score -= 12
        if scores.get("contact_quality", 0) > 55:
            score -= 12

    return round(clamp(score))


def inning_adjusted_projection(info, live_total, projected_total):
    """
    Caps unrealistic late-game projections so one hot model signal does not produce
    impossible-looking final totals such as +8 to +10 runs after the 7th.
    """
    if live_total is None or projected_total is None:
        return projected_total

    inning = safe_int(info.get("inning", 1), 1)
    cap = None

    if inning >= 9:
        cap = MAX_PROJECTION_EDGE_INNING_9
    elif inning >= 8:
        cap = MAX_PROJECTION_EDGE_INNING_8
    elif inning >= 7:
        cap = MAX_PROJECTION_EDGE_INNING_7

    if cap is None:
        return projected_total

    if projected_total > live_total + cap:
        return round(live_total + cap, 1)
    if projected_total < live_total - cap:
        return round(live_total - cap, 1)

    return projected_total


def need_runs_context(info, live_total, side):
    """
    Simple late-game context for the text:
    how many runs matter relative to the current live total.
    """
    if live_total is None:
        return "Need Runs: live total unavailable"

    current_runs = safe_float(info.get("total_runs", 0), 0)
    innings_left = innings_remaining_estimate(info)

    if side == "OVER":
        need = max(0, math.floor(live_total - current_runs) + 1)
        return f"Need Runs: {need} more to clear OVER {live_total} | InnLeft: {innings_left:.1f}"
    else:
        cushion = max(0, math.ceil(live_total - current_runs) - 1)
        return f"Need Runs: UNDER {live_total} has {cushion} run cushion | InnLeft: {innings_left:.1f}"


def action_window(side, line, edge, scores, info):
    """
    Practical action guidance.
    """
    inning = safe_int(info.get("inning", 1), 1)
    if line is None:
        return "Action Window: no live line"

    if side == "OVER":
        preferred = line - 0.5
        pass_line = line + (1.0 if inning < 7 else 0.5)
        if scores.get("confirmation_score", 0) >= 70:
            return f"Action Window: play {line}; prefer {preferred:.1f}; pass if {pass_line:.1f}+"
        return f"Action Window: wait for {preferred:.1f} or confirmation; pass if {pass_line:.1f}+"

    preferred = line + 0.5
    pass_line = line - (1.0 if inning < 7 else 0.5)
    if scores.get("confirmation_score", 0) >= 70:
        return f"Action Window: play {line}; prefer {preferred:.1f}; pass if {pass_line:.1f}-"
    return f"Action Window: wait for {preferred:.1f} or confirmation; pass if {pass_line:.1f}-"


def clean_scenario_label(scenario):
    """
    Scenario describes environment only. Action decides WATCH/STRIKE.
    Removes contradictions like 'BET NOW' with 'WATCH ONLY' scenario.
    """
    if not scenario:
        return "Market Evaluation"

    cleaned = scenario
    for bad in [" → WATCH ONLY", " → Watch", " Watch", " WATCH ONLY"]:
        cleaned = cleaned.replace(bad, "")
    cleaned = cleaned.replace("Predictive Market Move → Over", "Predictive Over Build")
    cleaned = cleaned.replace("Predictive Market Move → Under", "Predictive Under Build")
    return cleaned.strip()


def confidence_score(side, edge, scenario, scores, evidence, market_resistance):
    """
    Converts the signal stack into a confidence grade used for WATCH/STRIKE.
    """
    ae = abs(edge)
    confidence = 45

    if ae >= 2.0:
        confidence += 22
    elif ae >= 1.5:
        confidence += 16
    elif ae >= 1.0:
        confidence += 10
    elif ae >= 0.7:
        confidence += 5

    confidence += evidence.get("real_signal_count", 0) * 4

    if side == "OVER":
        confidence += scores.get("contact_quality", 0) * 0.08
        confidence += scores.get("pitcher_stress", 0) * 0.07
        confidence += scores.get("lineup_pressure", 0) * 0.06
        confidence += scores.get("starter_exit_probability", 0) * 0.06
        confidence += scores.get("contact_trend", 50) * 0.04
        confidence += scores.get("run_conversion", 0) * 0.14
        confidence += scores.get("pressure_to_runs", 0) * 0.12
        confidence += scores.get("pre_run_over_watch", 0) * 0.10
        confidence += scores.get("over_value", 0) * 0.05
        confidence += max(0, scores.get("predictive_market_move", 0)) * 0.10
        confidence += max(0, market_resistance) * 0.12
        confidence -= scores.get("fake_pressure", 0) * 0.16
        confidence -= scores.get("run_prevention", 0) * 0.06
        confidence -= scores.get("under_environment", 0) * 0.10
        confidence -= scores.get("blowout_kill", 0) * 0.12
    else:
        confidence += scores.get("dominance", 0) * 0.09
        confidence += scores.get("under_environment", 0) * 0.12
        confidence += scores.get("run_prevention", 0) * 0.14
        confidence += scores.get("fake_pressure", 0) * 0.10
        confidence += max(0, -market_resistance) * 0.12
        confidence += max(0, -scores.get("predictive_market_move", 0)) * 0.08
        confidence -= scores.get("current_inning_pressure", 0) * 0.08
        confidence -= scores.get("run_conversion", 0) * 0.06
        confidence -= scores.get("contact_quality", 0) * 0.08
        confidence -= scores.get("pitcher_stress", 0) * 0.06

    if "Watch" in scenario:
        confidence -= 4
    if "Strike" in scenario or "Opportunity" in scenario:
        confidence += 3

    return round(clamp(confidence))


def action_from_confidence(side, confidence, edge, scores=None):
    """
    V2.2.4 calibrated action engine.
    Projection finds the opportunity; confirmation decides whether it is actionable now.
    """
    scores = scores or {}
    inning = safe_int(scores.get("inning", 1), 1)
    projection = scores.get("projection_score", confidence)
    confirmation = scores.get("confirmation_score", confidence)

    if side == "OVER":
        min_edge = MIN_LATE_OVER_STRIKE_EDGE_RUNS if inning >= 7 else MIN_OVER_EDGE_RUNS
        min_confirm = MIN_LATE_OVER_CONFIRMATION_FOR_STRIKE if inning >= 7 else MIN_OVER_CONFIRMATION_FOR_STRIKE

        strike_ready = (
            edge >= min_edge
            and projection >= 65
            and confirmation >= min_confirm
            and scores.get("pressure_to_runs", 0) >= (70 if inning >= 7 else 60)
            and scores.get("run_conversion", 0) >= (62 if inning >= 7 else 55)
        )
        if strike_ready:
            return "STRIKE"

        watch_ready = (
            edge >= MIN_WATCH_EDGE_RUNS
            and projection >= 55
            and (
                confirmation >= 40
                or scores.get("pre_run_over_watch", 0) >= PRE_RUN_OVER_WATCH_SCORE
                or scores.get("predictive_market_move", 0) >= MIN_PREDICTIVE_MARKET_MOVE_FOR_WATCH
            )
        )
        return "WATCH" if watch_ready else "NO_PLAY"

    # UNDER
    min_edge = MIN_UNDER_STRIKE_EDGE_RUNS
    min_confirm = MIN_LATE_UNDER_CONFIRMATION_FOR_STRIKE if inning >= 7 else MIN_UNDER_CONFIRMATION_FOR_STRIKE

    strike_ready = (
        edge <= -min_edge
        and projection >= 62
        and confirmation >= min_confirm
        and scores.get("run_prevention", 0) >= 75
        and scores.get("current_inning_pressure", 0) <= 30
        and scores.get("contact_quality", 0) <= 40
    )
    if strike_ready:
        return "STRIKE"

    watch_ready = (
        edge <= -MIN_WATCH_EDGE_RUNS
        and projection >= 50
        and confirmation >= 50
    )
    return "WATCH" if watch_ready else "NO_PLAY"


def market_pressure(opening, live):
    if opening is None or live is None:
        return {"move": 0, "direction": "unknown", "abs_move": 0}
    move = round(live - opening, 1)
    if move >= 1:
        direction = "inflated"
    elif move <= -1:
        direction = "suppressed"
    else:
        direction = "stable"
    return {"move": move, "direction": direction, "abs_move": abs(move)}


def expected_future_runs(
    info,
    current_pressure,
    remaining_opp,
    stress,
    dominance,
    contact,
    bullpen,
    lineup,
    suppression,
    false_dom,
    contact_trend=50,
    tto=0,
    starter_exit=0,
    fake_pressure=0,
    market_resistance=0,
    blowout_kill=0,
    under_environment=0,
    run_conversion=0,
    run_prevention=0,
    predictive_market_move=0,
    pressure_to_runs=0,
    pre_run_over_watch=0,
):
    innings_left = innings_remaining_estimate(info)
    base_rate = innings_left * 0.95

    upward = 0
    downward = 0

    upward += (current_pressure / 100) * 1.00
    upward += (remaining_opp / 100) * 1.25
    upward += (stress / 100) * 0.85
    upward += (contact / 100) * 0.85
    upward += (bullpen / 100) * 0.70
    upward += (lineup / 100) * 0.55
    upward += (suppression / 100) * 0.35
    upward += (false_dom / 100) * 0.40
    upward += (max(0, contact_trend - 50) / 50) * 0.55
    upward += (tto / 100) * 0.50
    upward += (starter_exit / 100) * 0.45
    upward += (max(0, market_resistance) / 100) * 0.60
    upward += (run_conversion / 100) * 0.90
    upward += (pressure_to_runs / 100) * 0.75
    upward += (pre_run_over_watch / 100) * 0.55
    upward += (max(0, predictive_market_move) / 100) * 0.45

    downward += (dominance / 100) * 1.20
    downward += (fake_pressure / 100) * 0.75
    downward += (blowout_kill / 100) * 0.70
    downward += (under_environment / 100) * 0.95
    downward += (run_prevention / 100) * 0.90
    downward += (max(0, -market_resistance) / 100) * 0.60
    downward += (max(0, -predictive_market_move) / 100) * 0.35
    downward += max(0, 70 - current_pressure) * 0.007 if current_pressure < 40 else 0
    downward += max(0, 65 - remaining_opp) * 0.006 if remaining_opp < 45 else 0

    return round(max(0.2, base_rate + upward - downward), 1)


def projected_final_total(info, expected_future):
    return round(info["total_runs"] + expected_future, 1)


def classify_scenario(
    info,
    opening,
    live,
    current_pressure,
    remaining_opp,
    stress,
    dominance,
    contact,
    bullpen,
    lineup,
    suppression,
    false_dom,
    contact_trend=50,
    tto=0,
    starter_exit=0,
    fake_pressure=0,
    market_resistance=0,
    blowout_kill=0,
    under_environment=0,
):
    mp = market_pressure(opening, live)
    total_runs = info["total_runs"]

    # V2.2.1 predictive paths: watch before score/market fully moves.
    # These are not automatic bets; run conversion/prevention determines WATCH vs STRIKE later.
    if contact_trend >= 65 and stress >= 50 and market_resistance >= 0:
        return "Predictive Market Move → Over Watch"

    if lineup >= 65 and remaining_opp >= 55 and (stress >= 45 or contact >= 45) and fake_pressure < 55:
        return "Pre-Run Pressure Build → Over Watch"

    if fake_pressure >= 50 and under_environment >= 55:
        return "Predictive Market Move → Under Watch"

    # True UNDER paths first. This fixes the prior over-only behavior.
    if mp["direction"] == "inflated" and total_runs >= 3 and under_environment >= 55 and current_pressure <= 40:
        return "Fast Start → Inflated Total → Under Opportunity"

    if dominance >= 70 and contact <= 40 and current_pressure <= 35:
        return "Pitcher Control → Under Strike"

    if fake_pressure >= 55 and dominance >= 55:
        return "Fake Pressure → Under Opportunity"

    if mp["direction"] == "inflated" and dominance >= 55 and contact <= 45 and stress <= 50:
        return "Market Inflated / No Pressure → Under Watch"

    if blowout_kill >= 35:
        return "Blowout Kill Risk → Under Watch"

    # Over paths require more than scoreboard quietness.
    if mp["direction"] == "suppressed" and remaining_opp >= 45:
        if (stress >= 55 or contact >= 50 or contact_trend >= 65 or starter_exit >= 55 or tto >= 45):
            return "Slow Start + Real Pressure → Over Opportunity"
        if suppression >= 55:
            return "Run Suppression → WATCH ONLY"

    if false_dom >= 60 and fake_pressure < 45:
        return "False Dominance → Delayed Collapse Watch"

    if lineup >= 70 and remaining_opp >= 55 and fake_pressure < 50:
        return "Lineup Cycle Pressure"

    if bullpen >= 65 or starter_exit >= 70:
        return "Bullpen Cliff"

    if opening is not None and opening <= 8 and dominance >= 65 and current_pressure <= 35 and stress <= 45:
        return "Strong Pregame Under → Under Continuation"

    if opening is not None and opening >= 9 and (stress >= 60 or contact >= 60 or starter_exit >= 65):
        return "Strong Pregame Over → Over Continuation"

    if suppression >= 60:
        return "Run Suppression → WATCH ONLY"

    if under_environment >= 55:
        return "Dead Contact → Under Watch"

    return "Neutral / Watch"


def scenario_bias(scenario):
    # WATCH ONLY is intentionally not an automatic OVER bias.
    if any(x in scenario for x in ["Under", "Control", "Dead Contact", "Fake Pressure", "Inflated", "Blowout"]):
        return "UNDER"
    if any(x in scenario for x in ["Over Opportunity", "Collapse", "Lineup", "Bullpen", "Over Continuation"]):
        return "OVER"
    return "NONE"


def edge_grade(edge):
    ae = abs(edge)
    if ae >= 2.0:
        return "Rare"
    if ae >= 1.5:
        return "Strong"
    if ae >= 1.0:
        return "Playable"
    if ae >= 0.5:
        return "Watch"
    return "Noise"


def find_markets(odds_events, home, away):
    mlb_home = clean_team(home)
    mlb_away = clean_team(away)

    empty = {
        "total": {"point": None, "over_price": None, "under_price": None},
        "team_totals": [],
        "remaining_totals": [],
    }

    for ev in odds_events:
        odds_home = clean_team(ev.get("home_team"))
        odds_away = clean_team(ev.get("away_team"))

        if odds_home != mlb_home or odds_away != mlb_away:
            continue

        result = json.loads(json.dumps(empty))

        for book in ev.get("bookmakers", []):
            for market in book.get("markets", []):
                key = market.get("key")

                if key == "totals":
                    for out in market.get("outcomes", []):
                        if out.get("name") == "Over":
                            result["total"]["point"] = out.get("point")
                            result["total"]["over_price"] = out.get("price")
                        elif out.get("name") == "Under":
                            result["total"]["point"] = out.get("point")
                            result["total"]["under_price"] = out.get("price")

                elif key in ["team_totals", "alternate_team_totals"]:
                    grouped = {}
                    for out in market.get("outcomes", []):
                        team = out.get("description") or out.get("team") or out.get("name")
                        grouped.setdefault(team, {"team": team, "point": out.get("point"), "over_price": None, "under_price": None})
                        if out.get("name") == "Over":
                            grouped[team]["over_price"] = out.get("price")
                            grouped[team]["point"] = out.get("point")
                        elif out.get("name") == "Under":
                            grouped[team]["under_price"] = out.get("price")
                            grouped[team]["point"] = out.get("point")
                    result["team_totals"].extend([v for v in grouped.values() if v.get("point") is not None])

                elif key in ["remaining_totals", "live_totals", "game_remaining_totals"]:
                    rem = {"point": None, "over_price": None, "under_price": None}
                    for out in market.get("outcomes", []):
                        if out.get("name") == "Over":
                            rem["point"] = out.get("point")
                            rem["over_price"] = out.get("price")
                        elif out.get("name") == "Under":
                            rem["point"] = out.get("point")
                            rem["under_price"] = out.get("price")
                    if rem["point"] is not None:
                        result["remaining_totals"].append(rem)

        return result

    print(f"NO ODDS MATCH FOR: {away} at {home}")
    return empty



def live_evidence_report(info, p, q, traffic, scores, scenario, side=None):
    """
    Prevents false STRIKE alerts caused only by lineup pressure or full game opportunity
    before real live baseball evidence exists. V2.1 allows different OVER/UNDER evidence.
    """
    total_pitches = q.get("total_pitches", 0)
    balls_in_play = q.get("balls_in_play", 0)
    pitch_count = p.get("pitch_count", 0)
    batters_faced = p.get("batters_faced", 0)

    signals = []
    real_signal_count = 0

    if side == "UNDER":
        checks = [
            ("pitcher dominance", scores.get("dominance", 0) >= 55),
            ("dead contact", scores.get("contact_quality", 100) <= 35 and balls_in_play >= 4),
            ("low inning pressure", scores.get("current_inning_pressure", 100) <= 35),
            ("fake pressure", scores.get("fake_pressure", 0) >= 45),
            ("under environment", scores.get("under_environment", 0) >= 50),
            ("run prevention", scores.get("run_prevention", 0) >= 60),
            ("predictive under move", scores.get("predictive_market_move", 0) <= -MIN_PREDICTIVE_MARKET_MOVE_FOR_WATCH),
            ("market inflated", scores.get("market_resistance", 0) <= -20),
            ("limited remaining opportunity", scores.get("remaining_opportunity", 100) <= 40),
        ]
    else:
        checks = [
            ("pitcher stress", scores.get("pitcher_stress", 0) >= 50),
            ("contact quality", scores.get("contact_quality", 0) >= 50),
            ("rising contact trend", scores.get("contact_trend", 50) >= 65),
            ("current inning pressure", scores.get("current_inning_pressure", 0) >= 60),
            ("bullpen risk", scores.get("bullpen_risk", 0) >= 55),
            ("starter exit risk", scores.get("starter_exit_probability", 0) >= 55),
            ("run conversion", scores.get("run_conversion", 0) >= 60),
            ("pressure to runs", scores.get("pressure_to_runs", 0) >= MIN_OVER_RUN_CONVERSION_FOR_WATCH),
            ("pre-run over watch", scores.get("pre_run_over_watch", 0) >= PRE_RUN_OVER_WATCH_SCORE),
            ("predictive market move", scores.get("predictive_market_move", 0) >= MIN_PREDICTIVE_MARKET_MOVE_FOR_WATCH),
            ("times through order", scores.get("times_through_order", 0) >= 45),
            ("false dominance", scores.get("false_dominance", 0) >= 50),
            ("recent traffic", traffic.get("recent_baserunners", 0) >= 3),
            ("consecutive baserunners", traffic.get("consecutive_baserunners", 0) >= 2),
        ]

    for label, passed in checks:
        if passed:
            real_signal_count += 1
            signals.append(label)

    if pitch_count == 0 or batters_faced == 0:
        return {"ok": False, "reason": "no live pitching data yet", "real_signal_count": real_signal_count, "signals": signals}

    if scenario == "Neutral / Watch" and info.get("inning", 1) < MIN_INNING_FOR_NEUTRAL_ALERT:
        return {"ok": False, "reason": "neutral scenario is watch-only", "real_signal_count": real_signal_count, "signals": signals}

    # Do not force late OVERs in the 8th/9th unless they are very strong.
    if side == "OVER" and info.get("inning", 1) >= MAX_LATE_OVER_INNING and scores.get("current_inning_pressure", 0) < 65:
        return {"ok": False, "reason": "late over blocked without current pressure", "real_signal_count": real_signal_count, "signals": signals}

    if total_pitches < MIN_LIVE_PITCHES_FOR_STRIKE and balls_in_play < MIN_BALLS_IN_PLAY_FOR_STRIKE:
        return {"ok": False, "reason": "not enough pitches or balls in play", "real_signal_count": real_signal_count, "signals": signals}

    if real_signal_count < MIN_REAL_SIGNAL_COUNT:
        return {"ok": False, "reason": "not enough independent live signals", "real_signal_count": real_signal_count, "signals": signals}

    return {"ok": True, "reason": "live evidence confirmed", "real_signal_count": real_signal_count, "signals": signals}


def detect_total_opportunity(market, info, projected_total, scenario, scores, p, q, traffic, state=None):
    state = state or {"games": {}}
    live = market.get("point")
    over_price = market.get("over_price")
    under_price = market.get("under_price")

    if live is None:
        return None

    projected_total = adjusted_projection_for_time(info, live, projected_total)
    edge = round(projected_total - live, 1)
    bias = scenario_bias(scenario)

    candidates = []

    if edge >= MIN_WATCH_EDGE_RUNS and price_ok(over_price, edge):
        evidence = live_evidence_report(info, p, q, traffic, scores, scenario, side="OVER")
        if evidence["ok"]:
            scores["inning"] = safe_int(info.get("inning", 1), 1)
            scores = apply_winner_pattern_enhancements(state, info, "OVER", scores, live, projected_total)
            scores["projection_score"] = projection_score("OVER", edge, scores)
            scores["confirmation_score"] = confirmation_score("OVER", info, scores)
            confidence = confidence_score("OVER", edge, scenario, scores, evidence, scores.get("market_resistance", 0))
            confidence = min(confidence, max(scores["projection_score"], scores["confirmation_score"]))
            action = action_from_confidence("OVER", confidence, edge, scores)
            if action != "NO_PLAY":
                candidates.append({
                    "market_type": "Full Game Total",
                    "side": "OVER",
                    "line": live,
                    "price": over_price,
                    "edge": edge,
                    "edge_grade": edge_grade(edge),
                    "scenario": clean_scenario_label(scenario),
                    "scores": scores,
                    "projected_total": projected_total,
            "projection": projected_total,
                    "evidence": evidence,
                    "confidence": confidence,
                    "action": action,
                })

    if edge <= -MIN_WATCH_EDGE_RUNS and price_ok(under_price, abs(edge)):
        evidence = live_evidence_report(info, p, q, traffic, scores, scenario, side="UNDER")
        if evidence["ok"]:
            scores["inning"] = safe_int(info.get("inning", 1), 1)
            scores = apply_winner_pattern_enhancements(state, info, "UNDER", scores, live, projected_total)
            scores["projection_score"] = projection_score("UNDER", edge, scores)
            scores["confirmation_score"] = confirmation_score("UNDER", info, scores)
            confidence = confidence_score("UNDER", edge, scenario, scores, evidence, scores.get("market_resistance", 0))
            confidence = min(confidence, max(scores["projection_score"], scores["confirmation_score"]))
            action = action_from_confidence("UNDER", confidence, edge, scores)
            if action != "NO_PLAY":
                candidates.append({
                    "market_type": "Full Game Total",
                    "side": "UNDER",
                    "line": live,
                    "price": under_price,
                    "edge": edge,
                    "edge_grade": edge_grade(edge),
                    "scenario": clean_scenario_label(scenario),
                    "scores": scores,
                    "projected_total": projected_total,
            "projection": projected_total,
                    "evidence": evidence,
                    "confidence": confidence,
                    "action": action,
                })

    # Predictive market move WATCH: pressure building before market fully moves.
    if SEND_WATCH_ALERTS and not candidates:
        predictive = scores.get("predictive_market_move", 0)
        if predictive >= MIN_PREDICTIVE_MARKET_MOVE_FOR_WATCH and price_ok(over_price, abs(edge) if edge else MIN_WATCH_EDGE_RUNS):
            evidence = live_evidence_report(info, p, q, traffic, scores, "Predictive Market Move → Over Watch", side="OVER")
            if evidence["ok"]:
                watch_edge = max(edge, MIN_WATCH_EDGE_RUNS)
                scores["inning"] = safe_int(info.get("inning", 1), 1)
                scores = apply_winner_pattern_enhancements(state, info, "OVER", scores, live, projected_total)
                scores["projection_score"] = projection_score("OVER", watch_edge, scores)
                scores["confirmation_score"] = confirmation_score("OVER", info, scores)
                confidence = confidence_score("OVER", watch_edge, "Predictive Market Move → Over Watch", scores, evidence, scores.get("market_resistance", 0))
                confidence = min(confidence, max(scores["projection_score"], scores["confirmation_score"]))
                if confidence >= MIN_WATCH_CONFIDENCE:
                    candidates.append({
                        "market_type": "Full Game Total",
                        "side": "OVER",
                        "line": live,
                        "price": over_price,
                        "edge": round(watch_edge, 1),
                        "edge_grade": "Watch",
                        "scenario": clean_scenario_label("Predictive Market Move → Over Watch"),
                        "scores": scores,
                        "projected_total": projected_total,
            "projection": projected_total,
                        "evidence": evidence,
                        "confidence": confidence,
                        "action": "WATCH",
                    })
        elif predictive <= -MIN_PREDICTIVE_MARKET_MOVE_FOR_WATCH and price_ok(under_price, abs(edge) if edge else MIN_WATCH_EDGE_RUNS):
            evidence = live_evidence_report(info, p, q, traffic, scores, "Predictive Market Move → Under Watch", side="UNDER")
            if evidence["ok"]:
                watch_edge = min(edge, -MIN_WATCH_EDGE_RUNS)
                scores["inning"] = safe_int(info.get("inning", 1), 1)
                scores = apply_winner_pattern_enhancements(state, info, "UNDER", scores, live, projected_total)
                scores["projection_score"] = projection_score("UNDER", watch_edge, scores)
                scores["confirmation_score"] = confirmation_score("UNDER", info, scores)
                confidence = confidence_score("UNDER", watch_edge, "Predictive Market Move → Under Watch", scores, evidence, scores.get("market_resistance", 0))
                confidence = min(confidence, max(scores["projection_score"], scores["confirmation_score"]))
                if confidence >= MIN_WATCH_CONFIDENCE:
                    candidates.append({
                        "market_type": "Full Game Total",
                        "side": "UNDER",
                        "line": live,
                        "price": under_price,
                        "edge": round(watch_edge, 1),
                        "edge_grade": "Watch",
                        "scenario": clean_scenario_label("Predictive Market Move → Under Watch"),
                        "scores": scores,
                        "projected_total": projected_total,
            "projection": projected_total,
                        "evidence": evidence,
                        "confidence": confidence,
                        "action": "WATCH",
                    })

    # Dedicated V2.2.1 Pre-Run OVER WATCH:
    # This tries to catch the market move before runs fully arrive.
    if SEND_WATCH_ALERTS and not candidates and scores.get("pre_run_over_watch", 0) >= PRE_RUN_OVER_WATCH_SCORE:
        evidence = live_evidence_report(info, p, q, traffic, scores, "Pre-Run Pressure Build → Over Watch", side="OVER")
        if evidence["ok"]:
            watch_edge = max(edge, MIN_WATCH_EDGE_RUNS)
            scores["inning"] = safe_int(info.get("inning", 1), 1)
            scores = apply_winner_pattern_enhancements(state, info, "OVER", scores, live, projected_total)
            scores["projection_score"] = projection_score("OVER", watch_edge, scores)
            scores["confirmation_score"] = confirmation_score("OVER", info, scores)
            confidence = confidence_score("OVER", watch_edge, "Pre-Run Pressure Build → Over Watch", scores, evidence, scores.get("market_resistance", 0))
            confidence = min(confidence, max(scores["projection_score"], scores["confirmation_score"]))
            if confidence >= MIN_WATCH_CONFIDENCE and scores.get("pressure_to_runs", 0) >= MIN_OVER_RUN_CONVERSION_FOR_WATCH:
                candidates.append({
                    "market_type": "Full Game Total",
                    "side": "OVER",
                    "line": live,
                    "price": over_price,
                    "edge": round(watch_edge, 1),
                    "edge_grade": "Watch",
                    "scenario": clean_scenario_label("Pre-Run Pressure Build → Over Watch"),
                    "scores": scores,
                    "projected_total": projected_total,
            "projection": projected_total,
                    "evidence": evidence,
                    "confidence": confidence,
                    "action": "WATCH",
                })

    if not candidates:
        return None

    # Scenario bias can filter weak conflicts, but not strong confidence/edge.
    filtered = []
    for c in candidates:
        if bias != "NONE" and bias != c["side"] and abs(c["edge"]) < STRONG_EDGE_RUNS and c["confidence"] < 72:
            continue
        if c["action"] == "WATCH" and not SEND_WATCH_ALERTS:
            continue
        filtered.append(c)

    if not filtered:
        return None

    # Prefer STRIKE over WATCH, then highest confidence.
    filtered.sort(key=lambda x: (1 if x["action"] == "STRIKE" else 0, x["confidence"], abs(x["edge"])), reverse=True)
    return filtered[0]


def should_alert(state_game, opportunity):
    now_ts = time.time()
    alerts = state_game.setdefault("alerts", [])

    key = f"{opportunity['market_type']}|{opportunity['side']}"
    scenario = opportunity["scenario"]
    edge = abs(opportunity["edge"])
    line = opportunity.get("line")
    price = opportunity.get("price")
    action = opportunity.get("action", "STRIKE")

    side_alerts = [a for a in alerts if a.get("key") == key]
    strike_count = sum(1 for a in side_alerts if a.get("action") == "STRIKE")
    if action == "STRIKE" and strike_count >= MAX_ALERTS_PER_GAME_SIDE:
        # Allow one more only if the line materially improves or edge jumps.
        best_edge = max([a.get("edge_abs", 0) for a in side_alerts], default=0)
        best_line = side_alerts[-1].get("line") if side_alerts else None
        line_improved = False
        if best_line is not None:
            if opportunity["side"] == "OVER":
                line_improved = line <= best_line - LINE_IMPROVEMENT_TO_REPEAT
            else:
                line_improved = line >= best_line + LINE_IMPROVEMENT_TO_REPEAT
        if not line_improved and edge < best_edge + EDGE_IMPROVEMENT_TO_REPEAT:
            return False

    for a in reversed(alerts):
        if a.get("key") != key:
            continue

        seconds_since = now_ts - a.get("ts", 0)
        same_scenario = a.get("scenario") == scenario
        same_line = a.get("line") == line
        same_action = a.get("action") == action
        edge_improved = edge >= a.get("edge_abs", 0) + EDGE_IMPROVEMENT_TO_REPEAT

        # Do not resend the same side/same line just because price moved slightly.
        if seconds_since < ALERT_COOLDOWN_SECONDS and same_line and same_action and not edge_improved:
            return False

        # Do not spam same scenario inside cooldown unless edge materially improves.
        if seconds_since < ALERT_COOLDOWN_SECONDS and same_scenario and same_action and not edge_improved:
            return False

    alerts.append({
        "ts": now_ts,
        "key": key,
        "scenario": clean_scenario_label(scenario),
        "edge_abs": edge,
        "line": line,
        "price": price,
        "action": action,
        "confidence": opportunity.get("confidence"),
    })
    return True


def describe_reasons(info, p, q, traffic, hitters, scores, scenario):
    reasons = []

    reasons.append(f"{info['base_state']['label']}, {info['outs']} out(s), {info['inning_state']} {info['inning']}")

    if scores["current_inning_pressure"] >= 65:
        reasons.append(f"Current inning pressure is elevated ({scores['current_inning_pressure']}/100)")
    elif scores["current_inning_pressure"] <= 30:
        reasons.append(f"Current inning pressure is low ({scores['current_inning_pressure']}/100)")

    if scores["remaining_opportunity"] >= 65:
        reasons.append(f"Remaining scoring opportunity is strong ({scores['remaining_opportunity']}/100)")
    elif scores["remaining_opportunity"] <= 35:
        reasons.append(f"Remaining scoring opportunity is limited ({scores['remaining_opportunity']}/100)")

    if scores["pitcher_stress"] >= 60:
        outs = p.get("outs_recorded", 0)
        ppo = round(p["pitch_count"] / outs, 2) if outs else p["pitch_count"]
        reasons.append(f"Pitcher stress is high: {p['pitch_count']} pitches, {ppo} pitches/out")
    elif scores["dominance"] >= 65:
        reasons.append(f"Pitcher appears in control: strike {q['strike_pct']}%, whiff {q['whiff_pct']}%, CSW {q['csw_pct']}%")

    if scores["contact_quality"] >= 55:
        reasons.append(f"Contact quality is dangerous: {q['hard_hit']} hard-hit, {q['barrels']} barrel(s), max EV {q['max_ev']}")

    if traffic["recent_baserunners"] >= 3:
        reasons.append(f"Recent traffic: {traffic['recent_baserunners']} baserunners in recent plate appearances")

    if traffic["consecutive_baserunners"] >= 2:
        reasons.append(f"Consecutive baserunners signal inning stress ({traffic['consecutive_baserunners']} straight)")

    if scores["lineup_pressure"] >= 65:
        reasons.append(f"Lineup pressure: {format_hitters(hitters)}")

    if scores["bullpen_risk"] >= 60:
        reasons.append(f"Bullpen/transition risk is elevated ({scores['bullpen_risk']}/100)")

    if scores.get("starter_exit_probability", 0) >= 60:
        reasons.append(f"Starter exit risk is high ({scores['starter_exit_probability']}/100)")

    if scores.get("times_through_order", 0) >= 45:
        reasons.append(f"Times-through-order pressure is building ({scores['times_through_order']}/100)")

    if scores.get("fake_pressure", 0) >= 50:
        reasons.append(f"Fake pressure filter is active ({scores['fake_pressure']}/100)")

    if scores.get("under_environment", 0) >= 55:
        reasons.append(f"Under environment is active ({scores['under_environment']}/100)")

    if scores.get("pressure_to_runs", 0) >= MIN_OVER_RUN_CONVERSION_FOR_WATCH:
        reasons.append(f"Pressure-to-runs supports OVER ({scores['pressure_to_runs']}/100)")

    if scores.get("pre_run_over_watch", 0) >= PRE_RUN_OVER_WATCH_SCORE:
        reasons.append(f"Pre-run OVER watch is active ({scores['pre_run_over_watch']}/100)")

    if scores.get("run_conversion", 0) >= 60:
        reasons.append(f"Run conversion supports OVER ({scores['run_conversion']}/100)")

    if scores.get("run_prevention", 0) >= 60:
        reasons.append(f"Run prevention supports UNDER ({scores['run_prevention']}/100)")

    if abs(scores.get("predictive_market_move", 0)) >= MIN_PREDICTIVE_MARKET_MOVE_FOR_WATCH:
        direction = "OVER" if scores.get("predictive_market_move", 0) > 0 else "UNDER"
        reasons.append(f"Predictive market move signal supports {direction} ({scores['predictive_market_move']}/100)")

    if scores.get("market_resistance", 0) >= 25:
        reasons.append(f"Market resistance supports OVER (+{scores['market_resistance']})")
    elif scores.get("market_resistance", 0) <= -25:
        reasons.append(f"Market resistance supports UNDER ({scores['market_resistance']})")

    if q["velo_drop"] >= 1.0:
        reasons.append(f"Velocity drop detected: {q['velo_drop']} mph")
    if q["spin_drop"] >= 150:
        reasons.append(f"Spin drop detected: {q['spin_drop']} rpm")
    if q["movement_drop"] >= 2:
        reasons.append(f"Movement drop detected: {q['movement_drop']}")

    if "Predictive Market Move" in scenario:
        reasons.append("Interpretation: leading indicators are building before the market fully adjusts")
    elif "Slow Start" in scenario:
        reasons.append("Interpretation: scoreboard is quiet, but pressure suggests scoring may be delayed")
    elif "Inflated" in scenario:
        reasons.append("Interpretation: live total may have overreacted to early scoring")
    elif "False Dominance" in scenario:
        reasons.append("Interpretation: pitcher may be surviving traffic rather than controlling the game")
    elif "Bullpen Cliff" in scenario:
        reasons.append("Interpretation: starter-to-bullpen transition may change the scoring environment")
    elif "Lineup Cycle" in scenario:
        reasons.append("Interpretation: upcoming hitters improve the scoring environment")

    return reasons[:9]


def format_alert(label, start_label, info, market_context, opportunity, p, q, traffic, hitters, flags):
    scores = opportunity["scores"]
    reasons = describe_reasons(info, p, q, traffic, hitters, scores, opportunity["scenario"])
    reason_text = "\n".join([f"• {r}" for r in reasons])

    flag_text = " | ".join(flags) if flags else "No pitch-type red flags"

    edge_sign = "+" if opportunity["edge"] > 0 else ""
    price_text = opportunity["price"] if opportunity["price"] is not None else "N/A"
    action = opportunity.get("action", "STRIKE")
    instruction = "BET NOW" if action == "STRIKE" else "WATCH ONLY - wait for better price/confirmation"
    entry_guidance = best_entry_guidance(
        opportunity.get("side"),
        opportunity.get("line"),
        market_context.get("opening_total"),
        opportunity.get("edge", 0),
        action,
        scores,
    )
    history_note = historical_pattern_note(info, opportunity)

    return (
        f"SHIFT MLB V2.3.3 SELF-LEARNING TRACKER {action}\n\n"
        f"{label}\n"
        f"Start: {start_label}\n\n"
        f"Instruction:\n"
        f"{instruction}\n\n"
        f"Scenario:\n"
        f"{opportunity['scenario']}\n\n"
        f"Market:\n"
        f"{opportunity['market_type']}\n\n"
        f"PLAY:\n"
        f"{opportunity['side']} {opportunity['line']} ({price_text})\n"
        f"Price Label: {market_label(opportunity['price'])}\n"
        f"Edge Grade: {opportunity['edge_grade']}\n"
        f"Confidence: {opportunity.get('confidence', 'N/A')}/100\n\n"
        f"Open: {market_context.get('opening_total')}\n"
        f"Live Line: {opportunity['line']}\n"
        f"Proj: {opportunity['projected_total']}\n"
        f"Edge: {edge_sign}{opportunity['edge']} runs\n"
        f"Entry Guidance: {entry_guidance}\n"
        f"{history_note}\n\n"
        f"Score: {info['away_runs']}-{info['home_runs']}\n"
        f"Inning: {info['inning_state']} {info['inning']}\n"
        f"Base/Out: {info['base_state']['label']}, {info['outs']} out(s)\n\n"
        f"Scores:\n"
        f"CIP: {scores['current_inning_pressure']}/100\n"
        f"RO: {scores['remaining_opportunity']}/100\n"
        f"Stress: {scores['pitcher_stress']}/100\n"
        f"Dom: {scores['dominance']}/100\n"
        f"Contact: {scores['contact_quality']}/100\n"
        f"Contact Trend: {scores.get('contact_trend', 0)}/100\n"
        f"Lineup: {scores['lineup_pressure']}/100\n"
        f"Bullpen Risk: {scores['bullpen_risk']}/100\n"
        f"Starter Exit: {scores.get('starter_exit_probability', 0)}/100\n"
        f"Times Through Order: {scores.get('times_through_order', 0)}/100\n"
        f"Fake Pressure: {scores.get('fake_pressure', 0)}/100\n"
        f"Under Environment: {scores.get('under_environment', 0)}/100\n"
        f"Conv: {scores.get('run_conversion', 0)}/100\n"
        f"P2R: {scores.get('pressure_to_runs', 0)}/100\n"
        f"PreOver: {scores.get('pre_run_over_watch', 0)}/100\n"
        f"OVal: {scores.get('over_value', 0)}/100\n"
        f"Prev: {scores.get('run_prevention', 0)}/100\n"
        f"PredMove: {scores.get('predictive_market_move', 0)}/100\n"
        f"Threat: {scores.get('threat_index', 0)}/100\n"
        f"Stack: {scores.get('signal_stack', 0)} ({', '.join(scores.get('signal_stack_labels', []))})\n"
        f"Lag: {scores.get('market_lag', 0)}/100\n"
        f"ConvAccel: {scores.get('conv_acceleration', 0)}/100\n"
        f"P2RBoost: {scores.get('p2r_boost', 0)}\n"
        f"Market Resistance: {scores.get('market_resistance', 0)}\n"
        f"Run Suppression: {scores['run_suppression']}/100\n"
        f"False Dominance: {scores['false_dominance']}/100\n"
        f"Live Evidence: {opportunity.get('evidence', {}).get('real_signal_count', 0)} signal(s) - {opportunity.get('evidence', {}).get('reason', 'unknown')}\n\n"
        f"Why:\n"
        f"{reason_text}\n\n"
        f"Pitcher:\n"
        f"{info['pitcher_name']} ({info.get('pitcher_hand') or '?'})\n"
        f"Pitch Count: {p['pitch_count']}\n"
        f"Hits/Walks/K: {p['hits']}/{p['walks']}/{p['strikeouts']}\n"
        f"Batters Faced: {p['batters_faced']}\n\n"
        f"Pitch Quality:\n"
        f"Strike% {q['strike_pct']} | Whiff% {q['whiff_pct']} | Zone% {q['zone_pct']} | CSW% {q['csw_pct']}\n"
        f"VeloDrop {q['velo_drop']} | SpinDrop {q['spin_drop']} | MoveDrop {q['movement_drop']} | ReleaseDrift {q['release_drift']}\n"
        f"Avg EV {q['avg_ev']} | Recent EV {q.get('recent_avg_ev', 0)} | EV Trend {q.get('ev_trend', 0)} | Max EV {q['max_ev']} | HH {q['hard_hit']} | Barrels {q['barrels']}\n\n"
        f"Current/Upcoming Hitters:\n"
        f"{format_hitters(hitters)}\n\n"
        f"Pitch-Type Flags:\n"
        f"{flag_text}"
    )


def determine_next_sleep(any_live, any_near_strike):
    if any_near_strike:
        return FAST_POLL_SECONDS
    if any_live:
        return ACTIVE_POLL_SECONDS
    return SLOW_POLL_SECONDS


def main():
    state = load_state()
    build_learning_summary()

    while True:
        any_live = False
        any_near_strike = False

        try:
            games = get_schedule()

            # Credit-smart odds usage:
            # Only call the Odds API when at least one game is inside the pregame window
            # or already active. If all games are too far away, stay dormant and skip odds.
            needs_odds = False
            for sg in games:
                sg_pk = str(sg["gamePk"])
                if is_final_locked_today(state, sg_pk):
                    continue

                sg_status = schedule_status(sg)
                if is_final_status(sg_status):
                    sg_label = schedule_label(sg)
                    sg_score = schedule_final_score(sg)
                    mark_final_locked_today(
                        state,
                        sg_pk,
                        sg_label,
                        sg_status,
                        sg_score,
                    )
                    if sg_score:
                        grade_completed_strikes(sg_pk, sg_label, sg_score)
                    continue

                st = parse_start_time(sg)
                if st is None or should_fetch_feed(st):
                    needs_odds = True
                    break

            odds = get_odds() if needs_odds else []
            if not needs_odds:
                print("ODDS SKIPPED: all games outside pregame window.")

            print(f"\n--- SHIFT V2 CHECK {now_local().strftime('%I:%M:%S %p')} ---")

            for g in games:
                game_pk = str(g["gamePk"])
                start_time = parse_start_time(g)

                if is_final_locked_today(state, game_pk):
                    print(f"SKIP FINAL | {schedule_label(g)} | already final-locked for {today()}")
                    continue

                g_status = schedule_status(g)
                if is_final_status(g_status):
                    g_label = schedule_label(g)
                    g_score = schedule_final_score(g)
                    mark_final_locked_today(
                        state,
                        game_pk,
                        g_label,
                        g_status,
                        g_score,
                    )
                    if g_score:
                        grade_completed_strikes(game_pk, g_label, g_score)
                    print(f"FINAL LOCKED | {g_label} | Status {g_status} | Score {g_score or 'Unknown'} | no more tracking today")
                    save_state(state)
                    continue

                if game_pk not in state["games"]:
                    state["games"][game_pk] = {
                        "opening_total": None,
                        "alerts": [],
                    }

                state_game = state["games"][game_pk]

                if start_time and not should_fetch_feed(start_time):
                    home = g.get("teams", {}).get("home", {}).get("team", {}).get("name", "Home")
                    away = g.get("teams", {}).get("away", {}).get("team", {}).get("name", "Away")
                    print(f"DORMANT | {away} at {home} | Start {start_time.strftime('%I:%M %p')} AZ | Too early")
                    continue

                feed = get_feed(game_pk)
                info = parse_game(feed, g)

                label = f"{info['away']} at {info['home']}"
                start_label = info["start_time"].strftime("%I:%M %p AZ") if info["start_time"] else "Unknown"
                mode = "ACTIVE" if info["status"] == "Live" else "FINAL" if is_final_status(info["status"]) else "DORMANT"

                if is_final_status(info["status"]):
                    final_score = f"{info['away_runs']}-{info['home_runs']}"
                    mark_final_locked_today(
                        state,
                        game_pk,
                        label,
                        info["status"],
                        final_score,
                    )
                    grade_completed_strikes(game_pk, label, final_score)
                    print(f"FINAL LOCKED | {label} | Score {final_score} | no more tracking today")
                    save_state(state)
                    continue

                markets = find_markets(odds, info["home"], info["away"])
                live_total = markets["total"]["point"]

                if state_game["opening_total"] is None and live_total:
                    state_game["opening_total"] = live_total

                opening_total = state_game["opening_total"]

                p = pitcher_box(feed, info["pitcher_id"])
                q = live_statcast_quality(feed, info["pitcher_id"])
                traffic = traffic_metrics(feed, info["pitcher_id"])
                hitters = upcoming_hitters(feed, info, 4)

                lineup_pressure = lineup_pressure_score(info, hitters)
                stress = pitcher_stress_score(p, q, traffic)
                dominance = pitcher_dominance_score(p, q, traffic)
                contact = contact_quality_score(q, traffic)
                bullpen = bullpen_risk_score(info, p)
                remaining_opp = remaining_opportunity_score(info, lineup_pressure, bullpen)
                current_pressure = current_inning_pressure_score(info, lineup_pressure, stress, contact)
                suppression = run_suppression_score(info, p, q, traffic, contact)
                false_dom = false_dominance_score(dominance, stress, contact, traffic)

                contact_trend = contact_trend_score(q)
                tto = times_through_order_score(info, p, hitters)
                starter_exit = starter_exit_probability(info, p, stress, bullpen)
                fake_pressure = fake_pressure_score(info, p, q, traffic, dominance, contact, current_pressure)
                market_res = market_resistance_score(opening_total, live_total, info, current_pressure, contact, dominance)
                blowout = blowout_kill_score(info)
                under_env = under_environment_score(info, p, q, traffic, dominance, contact, current_pressure, remaining_opp, fake_pressure, blowout)
                run_conversion = run_conversion_score(info, p, q, traffic, hitters, current_pressure, remaining_opp, stress, contact, lineup_pressure, contact_trend, tto, starter_exit, fake_pressure)
                run_prevention = run_prevention_score(info, p, q, traffic, dominance, contact, current_pressure, remaining_opp, fake_pressure, under_env, blowout)

                # Temporary score dict for over predictive calculation.
                # IMPORTANT: initialize these BEFORE using them in dictionaries.
                pressure_to_runs = 0
                pre_run_over = 0
                over_value = 0
                predictive_move = 0

                base_scores_for_over = {
                    "current_inning_pressure": current_pressure,
                    "remaining_opportunity": remaining_opp,
                    "pitcher_stress": stress,
                    "dominance": dominance,
                    "contact_quality": contact,
                    "contact_trend": contact_trend,
                    "lineup_pressure": lineup_pressure,
                    "bullpen_risk": bullpen,
                    "starter_exit_probability": starter_exit,
                    "times_through_order": tto,
                    "fake_pressure": fake_pressure,
                    "market_resistance": market_res,
                    "blowout_kill": blowout,
                    "under_environment": under_env,
                    "run_conversion": run_conversion,
                    "run_prevention": run_prevention,
                    "pressure_to_runs": 0,
                    "pre_run_over_watch": 0,
                    "over_value": 0,
                    "predictive_market_move": 0,
                }
                pressure_to_runs = pressure_to_runs_score(info, p, q, traffic, hitters, base_scores_for_over)
                base_scores_for_over["pressure_to_runs"] = pressure_to_runs
                pre_run_over = pre_run_over_watch_score(opening_total, live_total, info, p, q, traffic, base_scores_for_over)
                base_scores_for_over["pre_run_over_watch"] = pre_run_over

                # Temporary score dict for predictive market-move calculation.
                pre_scores = {
                    "current_inning_pressure": current_pressure,
                    "remaining_opportunity": remaining_opp,
                    "pitcher_stress": stress,
                    "dominance": dominance,
                    "contact_quality": contact,
                    "contact_trend": contact_trend,
                    "lineup_pressure": lineup_pressure,
                    "bullpen_risk": bullpen,
                    "starter_exit_probability": starter_exit,
                    "times_through_order": tto,
                    "fake_pressure": fake_pressure,
                    "market_resistance": market_res,
                    "blowout_kill": blowout,
                    "under_environment": under_env,
                    "run_conversion": run_conversion,
                    "run_prevention": run_prevention,
                    "pressure_to_runs": pressure_to_runs,
                    "pre_run_over_watch": pre_run_over,
                    "over_value": over_value,
                }
                predictive_move = predictive_market_move_score(opening_total, live_total, info, p, q, traffic, pre_scores)
                pre_scores["predictive_market_move"] = predictive_move
                provisional_edge = 0
                if live_total is not None:
                    # Use current score vs live total as a conservative placeholder before full projection.
                    # Full edge is calculated later by detect_total_opportunity after expected future runs.
                    provisional_edge = info.get("total_runs", 0) - live_total
                over_value = over_value_score(opening_total, live_total, provisional_edge, pre_scores)
                pre_scores["over_value"] = over_value
                pre_scores["pre_run_over_watch"] = pre_run_over
                pre_scores["pressure_to_runs"] = pressure_to_runs
                pre_scores["predictive_market_move"] = predictive_move

                expected_future = expected_future_runs(
                    info,
                    current_pressure,
                    remaining_opp,
                    stress,
                    dominance,
                    contact,
                    bullpen,
                    lineup_pressure,
                    suppression,
                    false_dom,
                    contact_trend,
                    tto,
                    starter_exit,
                    fake_pressure,
                    market_res,
                    blowout,
                    under_env,
                    run_conversion,
                    run_prevention,
                    predictive_move,
                    pressure_to_runs,
                    pre_run_over,
                )

                projected_total = projected_final_total(info, expected_future)

                scenario = classify_scenario(
                    info,
                    opening_total,
                    live_total,
                    current_pressure,
                    remaining_opp,
                    stress,
                    dominance,
                    contact,
                    bullpen,
                    lineup_pressure,
                    suppression,
                    false_dom,
                    contact_trend,
                    tto,
                    starter_exit,
                    fake_pressure,
                    market_res,
                    blowout,
                    under_env,
                )

                scores = {
                    "current_inning_pressure": current_pressure,
                    "remaining_opportunity": remaining_opp,
                    "pitcher_stress": stress,
                    "dominance": dominance,
                    "contact_quality": contact,
                    "contact_trend": contact_trend,
                    "lineup_pressure": lineup_pressure,
                    "bullpen_risk": bullpen,
                    "starter_exit_probability": starter_exit,
                    "times_through_order": tto,
                    "fake_pressure": fake_pressure,
                    "market_resistance": market_res,
                    "blowout_kill": blowout,
                    "under_environment": under_env,
                    "run_conversion": run_conversion,
                    "run_prevention": run_prevention,
                    "pressure_to_runs": pressure_to_runs,
                    "pre_run_over_watch": pre_run_over,
                    "over_value": over_value,
                    "predictive_market_move": predictive_move,
                    "run_suppression": suppression,
                    "false_dominance": false_dom,
                }

                if info["status"] == "Live":
                    any_live = True

                opportunity = detect_total_opportunity(
                    markets["total"],
                    info,
                    projected_total,
                    scenario,
                    scores,
                    p,
                    q,
                    traffic,
                    state,
                )

                edge_for_sleep = abs(opportunity["edge"]) if opportunity else 0
                if edge_for_sleep >= 0.7:
                    any_near_strike = True

                flags = pitch_type_red_flags(q)

                print(
                    f"{mode} | {label} | {info['inning_state']} {info['inning']} | "
                    f"Score {info['away_runs']}-{info['home_runs']} | Base {info['base_state']['label']} {info['outs']} out | "
                    f"Open {opening_total} Live {live_total} Projected {projected_total} EFR {expected_future} | "
                    f"Scenario {scenario} | "
                    f"CIP {current_pressure} RO {remaining_opp} Stress {stress} Dom {dominance} Contact {contact} "
                    f"Trend {contact_trend} Lineup {lineup_pressure} Bullpen {bullpen} Exit {starter_exit} TTO {tto} "
                    f"Fake {fake_pressure} UnderEnv {under_env} Conv {run_conversion} Prev {run_prevention} PredMove {predictive_move} MarketRes {market_res} Supp {suppression} FalseDom {false_dom} | "
                    f"Pitcher {info['pitcher_name']} PC {p['pitch_count']} H/W/K {p['hits']}/{p['walks']}/{p['strikeouts']} | "
                    f"Next {format_hitters(hitters)}"
                )

                if info["status"] != "Live":
                    save_state(state)
                    continue

                if opportunity and should_alert(state_game, opportunity):
                    market_context = {"opening_total": opening_total}
                    msg = format_alert(
                        label,
                        start_label,
                        info,
                        market_context,
                        opportunity,
                        p,
                        q,
                        traffic,
                        hitters,
                        flags,
                    )
                    send_text(msg)
                    log_strike_history(
                        info,
                        opportunity,
                        {
                            "opening_total": opening_total,
                            "live_total": live_total,
                        },
                    )
                    record_strike_lock(state, info, opportunity)

                save_state(state)

        except Exception as e:
            print("ERROR:", repr(e))

        sleep_seconds = determine_next_sleep(any_live, any_near_strike)
        print(f"Sleeping {sleep_seconds} seconds...\n")
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()

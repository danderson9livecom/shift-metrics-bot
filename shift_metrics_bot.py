import os
import time
import json
import math
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from twilio.rest import Client

"""
SHIFT MLB V2

Professional live MLB totals monitor.

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

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
ALERT_TO_NUMBER = os.getenv("ALERT_TO_NUMBER", "")

SLOW_POLL_SECONDS = int(os.getenv("SLOW_POLL_SECONDS", "300"))
ACTIVE_POLL_SECONDS = int(os.getenv("ACTIVE_POLL_SECONDS", "45"))
FAST_POLL_SECONDS = int(os.getenv("FAST_POLL_SECONDS", "20"))

PREGAME_WINDOW_MINUTES = int(os.getenv("PREGAME_WINDOW_MINUTES", "45"))

MIN_EDGE_RUNS = float(os.getenv("MIN_EDGE_RUNS", "0.9"))
STRONG_EDGE_RUNS = float(os.getenv("STRONG_EDGE_RUNS", "1.4"))

MAX_PRICE_FAVORITE = int(os.getenv("MAX_PRICE_FAVORITE", "-140"))
MAX_PRICE_DOG = int(os.getenv("MAX_PRICE_DOG", "110"))

ALERT_COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN_SECONDS", "720"))
EDGE_IMPROVEMENT_TO_REPEAT = float(os.getenv("EDGE_IMPROVEMENT_TO_REPEAT", "0.7"))

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


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"date": today(), "games": {}}
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
    except Exception:
        return {"date": today(), "games": {}}
    if state.get("date") != today():
        return {"date": today(), "games": {}}
    return state


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def send_text(msg):
    print("\n" + msg + "\n")
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, ALERT_TO_NUMBER]):
        print("TEXT NOT SENT: Missing Twilio variables.")
        return
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(body=msg, from_=TWILIO_FROM_NUMBER, to=ALERT_TO_NUMBER)
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
        print(f"ODDS EVENTS RETURNED: {len(data)} | Markets: {ODDS_MARKETS}")
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


def expected_future_runs(info, current_pressure, remaining_opp, stress, dominance, contact, bullpen, lineup, suppression, false_dom):
    innings_left = innings_remaining_estimate(info)
    base_rate = innings_left * 0.95

    upward = 0
    downward = 0

    upward += (current_pressure / 100) * 1.05
    upward += (remaining_opp / 100) * 1.35
    upward += (stress / 100) * 0.95
    upward += (contact / 100) * 0.90
    upward += (bullpen / 100) * 0.85
    upward += (lineup / 100) * 0.65
    upward += (suppression / 100) * 0.70
    upward += (false_dom / 100) * 0.45

    downward += (dominance / 100) * 1.25
    downward += max(0, 70 - current_pressure) * 0.007 if current_pressure < 40 else 0
    downward += max(0, 65 - remaining_opp) * 0.006 if remaining_opp < 45 else 0

    return round(max(0.2, base_rate + upward - downward), 1)


def projected_final_total(info, expected_future):
    return round(info["total_runs"] + expected_future, 1)


def classify_scenario(info, opening, live, current_pressure, remaining_opp, stress, dominance, contact, bullpen, lineup, suppression, false_dom):
    mp = market_pressure(opening, live)
    total_runs = info["total_runs"]

    if mp["direction"] == "suppressed" and (suppression >= 50 or stress >= 60 or contact >= 55) and remaining_opp >= 45:
        return "Slow Start → Over Opportunity"

    if mp["direction"] == "inflated" and total_runs >= 4 and dominance >= 55 and stress <= 45 and contact <= 45:
        return "Fast Start → Inflated Total → Under Opportunity"

    if opening is not None and opening <= 8 and dominance >= 65 and current_pressure <= 35 and stress <= 45:
        return "Strong Pregame Under → Under Continuation"

    if opening is not None and opening >= 9 and (stress >= 55 or contact >= 55 or bullpen >= 55 or lineup >= 60):
        return "Strong Pregame Over → Over Continuation"

    if false_dom >= 55:
        return "False Dominance → Delayed Collapse Watch"

    if lineup >= 70 and remaining_opp >= 55:
        return "Lineup Cycle Pressure"

    if bullpen >= 65:
        return "Bullpen Cliff"

    if suppression >= 60:
        return "Run Suppression → Over Watch"

    if dominance >= 70 and current_pressure <= 30:
        return "Pitcher Control → Under Watch"

    return "Neutral / Watch"


def scenario_bias(scenario):
    if any(x in scenario for x in ["Over", "Collapse", "Lineup", "Bullpen", "Suppression"]):
        return "OVER"
    if any(x in scenario for x in ["Under", "Control"]):
        return "UNDER"
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


def detect_total_opportunity(market, info, projected_total, scenario, scores):
    live = market.get("point")
    over_price = market.get("over_price")
    under_price = market.get("under_price")

    if live is None:
        return None

    edge = round(projected_total - live, 1)
    bias = scenario_bias(scenario)

    if edge >= MIN_EDGE_RUNS and price_ok(over_price, edge):
        side = "OVER"
        price = over_price
    elif edge <= -MIN_EDGE_RUNS and price_ok(under_price, abs(edge)):
        side = "UNDER"
        price = under_price
    else:
        return None

    if bias != "NONE" and bias != side and abs(edge) < STRONG_EDGE_RUNS:
        return None

    return {
        "market_type": "Full Game Total",
        "side": side,
        "line": live,
        "price": price,
        "edge": edge,
        "edge_grade": edge_grade(edge),
        "scenario": scenario,
        "scores": scores,
        "projected_total": projected_total,
    }


def should_alert(state_game, opportunity):
    now_ts = time.time()
    alerts = state_game.setdefault("alerts", [])

    key = f"{opportunity['market_type']}|{opportunity['side']}"
    scenario = opportunity["scenario"]
    edge = abs(opportunity["edge"])

    for a in reversed(alerts):
        if a.get("key") != key:
            continue

        seconds_since = now_ts - a.get("ts", 0)
        same_scenario = a.get("scenario") == scenario
        edge_improved = edge >= a.get("edge_abs", 0) + EDGE_IMPROVEMENT_TO_REPEAT

        if seconds_since < ALERT_COOLDOWN_SECONDS and same_scenario and not edge_improved:
            return False

    alerts.append({
        "ts": now_ts,
        "key": key,
        "scenario": scenario,
        "edge_abs": edge,
        "line": opportunity.get("line"),
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

    if q["velo_drop"] >= 1.0:
        reasons.append(f"Velocity drop detected: {q['velo_drop']} mph")
    if q["spin_drop"] >= 150:
        reasons.append(f"Spin drop detected: {q['spin_drop']} rpm")
    if q["movement_drop"] >= 2:
        reasons.append(f"Movement drop detected: {q['movement_drop']}")

    if "Slow Start" in scenario:
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

    return (
        f"SHIFT V2 STRIKE\n\n"
        f"{label}\n"
        f"Start: {start_label}\n\n"
        f"Scenario:\n"
        f"{opportunity['scenario']}\n\n"
        f"Market:\n"
        f"{opportunity['market_type']}\n\n"
        f"PLAY:\n"
        f"{opportunity['side']} {opportunity['line']} ({price_text})\n"
        f"Price Label: {market_label(opportunity['price'])}\n"
        f"Edge Grade: {opportunity['edge_grade']}\n\n"
        f"Opening/First Captured Total: {market_context.get('opening_total')}\n"
        f"Live Line: {opportunity['line']}\n"
        f"Projected Final: {opportunity['projected_total']}\n"
        f"Model Edge: {edge_sign}{opportunity['edge']} runs\n\n"
        f"Score: {info['away_runs']}-{info['home_runs']}\n"
        f"Inning: {info['inning_state']} {info['inning']}\n"
        f"Base/Out: {info['base_state']['label']}, {info['outs']} out(s)\n\n"
        f"Scores:\n"
        f"Current Inning Pressure: {scores['current_inning_pressure']}/100\n"
        f"Remaining Opportunity: {scores['remaining_opportunity']}/100\n"
        f"Pitcher Stress: {scores['pitcher_stress']}/100\n"
        f"Pitcher Dominance: {scores['dominance']}/100\n"
        f"Contact Quality: {scores['contact_quality']}/100\n"
        f"Lineup Pressure: {scores['lineup_pressure']}/100\n"
        f"Bullpen Risk: {scores['bullpen_risk']}/100\n"
        f"Run Suppression: {scores['run_suppression']}/100\n"
        f"False Dominance: {scores['false_dominance']}/100\n\n"
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
        f"Avg EV {q['avg_ev']} | Max EV {q['max_ev']} | HH {q['hard_hit']} | Barrels {q['barrels']}\n\n"
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
                mode = "ACTIVE" if info["status"] == "Live" else "FINAL" if info["status"] == "Final" else "DORMANT"

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
                )

                scores = {
                    "current_inning_pressure": current_pressure,
                    "remaining_opportunity": remaining_opp,
                    "pitcher_stress": stress,
                    "dominance": dominance,
                    "contact_quality": contact,
                    "lineup_pressure": lineup_pressure,
                    "bullpen_risk": bullpen,
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
                    f"Lineup {lineup_pressure} Bullpen {bullpen} Supp {suppression} FalseDom {false_dom} | "
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

                save_state(state)

        except Exception as e:
            print("ERROR:", repr(e))

        sleep_seconds = determine_next_sleep(any_live, any_near_strike)
        print(f"Sleeping {sleep_seconds} seconds...\n")
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()

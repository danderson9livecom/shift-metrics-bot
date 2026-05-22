import os
import time
import json
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()

TZ = ZoneInfo("America/Phoenix")
STATE_FILE = "shift_v11_state.json"

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
ALERT_TO_NUMBER = os.getenv("ALERT_TO_NUMBER", "")

SLOW_POLL_SECONDS = int(os.getenv("SLOW_POLL_SECONDS", "300"))
ACTIVE_POLL_SECONDS = int(os.getenv("ACTIVE_POLL_SECONDS", "60"))
FAST_POLL_SECONDS = int(os.getenv("FAST_POLL_SECONDS", "30"))

MIN_CONFIDENCE = int(os.getenv("MIN_CONFIDENCE", "85"))
PREGAME_WINDOW_MINUTES = int(os.getenv("PREGAME_WINDOW_MINUTES", "30"))


def now_local():
    return datetime.now(TZ)


def today():
    return now_local().strftime("%Y-%m-%d")


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"date": today(), "games": {}}

    with open(STATE_FILE, "r") as f:
        state = json.load(f)

    if state.get("date") != today():
        return {"date": today(), "games": {}}

    return state


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def send_text(msg):
    print("\n" + msg + "\n")

    if not all([
        TWILIO_ACCOUNT_SID,
        TWILIO_AUTH_TOKEN,
        TWILIO_FROM_NUMBER,
        ALERT_TO_NUMBER
    ]):
        return

    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    client.messages.create(
        body=msg,
        from_=TWILIO_FROM_NUMBER,
        to=ALERT_TO_NUMBER
    )


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
        return []

    url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "totals",
        "oddsFormat": "american"
    }

    r = requests.get(url, params=params, timeout=15)

    if r.status_code != 200:
        print("Odds API error:", r.text)
        return []

    return r.json()


def parse_start_time(game):
    raw = game.get("gameDate")
    if not raw:
        return None

    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return dt.astimezone(TZ)


def parse_game(feed, schedule_game):
    gd = feed.get("gameData", {})
    ld = feed.get("liveData", {})
    linescore = ld.get("linescore", {})

    home = gd.get("teams", {}).get("home", {}).get("name", "")
    away = gd.get("teams", {}).get("away", {}).get("name", "")
    status = gd.get("status", {}).get("abstractGameState", "")

    start_time = parse_start_time(schedule_game)

    inning = linescore.get("currentInning", 1)
    inning_state = linescore.get("inningState", "")
    outs = linescore.get("outs", 0)

    home_runs = linescore.get("teams", {}).get("home", {}).get("runs", 0) or 0
    away_runs = linescore.get("teams", {}).get("away", {}).get("runs", 0) or 0

    offense = linescore.get("offense", {})
    runners_on = sum(1 for b in ["first", "second", "third"] if offense.get(b))

    pitcher = linescore.get("defense", {}).get("pitcher", {})
    pitcher_name = pitcher.get("fullName", "Unknown")
    pitcher_id = pitcher.get("id")

    return {
        "home": home,
        "away": away,
        "status": status,
        "start_time": start_time,
        "inning": inning,
        "inning_state": inning_state,
        "outs": outs,
        "home_runs": home_runs,
        "away_runs": away_runs,
        "runners_on": runners_on,
        "pitcher_name": pitcher_name,
        "pitcher_id": pitcher_id
    }


def pitcher_box(feed, pitcher_id):
    empty = {
        "pitch_count": 0,
        "walks": 0,
        "strikeouts": 0,
        "runs": 0,
        "hits": 0,
        "innings": 0.0
    }

    if not pitcher_id:
        return empty

    box = feed.get("liveData", {}).get("boxscore", {})
    pid = f"ID{pitcher_id}"

    for side in ["home", "away"]:
        players = box.get("teams", {}).get(side, {}).get("players", {})
        if pid in players:
            p = players[pid].get("stats", {}).get("pitching", {})
            innings = str(p.get("inningsPitched", "0"))
            innings = innings.replace(".1", ".33").replace(".2", ".67")

            return {
                "pitch_count": int(p.get("numberOfPitches", 0) or 0),
                "walks": int(p.get("baseOnBalls", 0) or 0),
                "strikeouts": int(p.get("strikeOuts", 0) or 0),
                "runs": int(p.get("runs", 0) or 0),
                "hits": int(p.get("hits", 0) or 0),
                "innings": float(innings or 0)
            }

    return empty


def find_total(odds_events, home, away):
    for ev in odds_events:
        teams = ev.get("teams", [])

        if home in teams and away in teams:
            total = None
            over_price = None
            under_price = None

            for book in ev.get("bookmakers", []):
                for market in book.get("markets", []):
                    if market.get("key") != "totals":
                        continue

                    for out in market.get("outcomes", []):
                        if out.get("name") == "Over":
                            total = out.get("point")
                            over_price = out.get("price")
                        elif out.get("name") == "Under":
                            total = out.get("point")
                            under_price = out.get("price")

            return total, over_price, under_price

    return None, None, None


def pitcher_stress(p):
    score = 0

    if p["pitch_count"] >= 95:
        score += 35
    elif p["pitch_count"] >= 80:
        score += 25
    elif p["pitch_count"] >= 65:
        score += 15
    elif p["pitch_count"] >= 50:
        score += 8

    if p["walks"] >= 4:
        score += 20
    elif p["walks"] >= 3:
        score += 14
    elif p["walks"] >= 2:
        score += 8

    if p["hits"] >= 7:
        score += 18
    elif p["hits"] >= 5:
        score += 12
    elif p["hits"] >= 3:
        score += 6

    if p["runs"] >= 4:
        score += 18
    elif p["runs"] >= 2:
        score += 8

    return min(score, 100)


def bullpen_exposure(info, p):
    score = 0
    inning = int(info["inning"] or 1)

    if p["pitch_count"] >= 85:
        score += 30
    elif p["pitch_count"] >= 70:
        score += 20

    if inning >= 5 and p["pitch_count"] >= 75:
        score += 20

    if p["innings"] < 5 and inning >= 5:
        score += 20

    innings_left = max(0, 9 - inning)
    score += innings_left * 4

    return min(score, 100)


def base_runner_pressure(info):
    score = 0

    if info["runners_on"] == 3:
        score += 35
    elif info["runners_on"] == 2:
        score += 25
    elif info["runners_on"] == 1:
        score += 12

    if info["outs"] <= 1 and info["runners_on"] > 0:
        score += 15

    return min(score, 100)


def times_through_order(p):
    if p["innings"] >= 6:
        return 90
    if p["innings"] >= 5:
        return 75
    if p["innings"] >= 4:
        return 55
    return 25


def market_over_pressure(opening, live):
    if opening is None or live is None:
        return 0

    drop = opening - live

    if drop >= 3:
        return 100
    if drop >= 2.5:
        return 90
    if drop >= 2:
        return 80
    if drop >= 1.5:
        return 70
    if drop >= 1:
        return 55

    return 10


def market_under_pressure(opening, live):
    if opening is None or live is None:
        return 0

    rise = live - opening

    if rise >= 3:
        return 100
    if rise >= 2.5:
        return 90
    if rise >= 2:
        return 80
    if rise >= 1.5:
        return 70
    if rise >= 1:
        return 55

    return 10


def calculate_scores(info, p, opening_total, live_total):
    ps = pitcher_stress(p)
    bp = bullpen_exposure(info, p)
    br = base_runner_pressure(info)
    tto = times_through_order(p)

    over_market = market_over_pressure(opening_total, live_total)
    under_market = market_under_pressure(opening_total, live_total)

    over_score = round(
        over_market * 0.30 +
        ps * 0.25 +
        bp * 0.20 +
        tto * 0.10 +
        br * 0.15
    )

    under_score = round(
        under_market * 0.35 +
        (100 - ps) * 0.25 +
        (100 - bp) * 0.20 +
        (100 - br) * 0.10 +
        (100 - tto) * 0.10
    )

    return over_score, under_score, ps, bp, br, tto


def price_ok(price):
    if price is None:
        return True
    return -125 <= int(price) <= 110


def should_fetch_feed(start_time):
    if not start_time:
        return True

    minutes_until_start = (start_time - now_local()).total_seconds() / 60

    if minutes_until_start > PREGAME_WINDOW_MINUTES:
        return False

    return True


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
            odds = get_odds()

            print(f"\n--- SHIFT V1.1 CHECK {now_local().strftime('%I:%M:%S %p')} ---")

            for g in games:
                game_pk = str(g["gamePk"])
                start_time = parse_start_time(g)

                if game_pk not in state["games"]:
                    state["games"][game_pk] = {
                        "opening_total": None,
                        "alerts": []
                    }

                if start_time and not should_fetch_feed(start_time):
                    home = g.get("teams", {}).get("home", {}).get("team", {}).get("name", "Home")
                    away = g.get("teams", {}).get("away", {}).get("team", {}).get("name", "Away")

                    print(
                        f"DORMANT | {away} at {home} | "
                        f"Start {start_time.strftime('%I:%M %p')} AZ | "
                        f"Too early for live monitoring"
                    )
                    continue

                feed = get_feed(game_pk)
                info = parse_game(feed, g)
                p = pitcher_box(feed, info["pitcher_id"])

                label = f"{info['away']} at {info['home']}"
                start_label = info["start_time"].strftime("%I:%M %p AZ") if info["start_time"] else "Unknown"

                live_total, over_price, under_price = find_total(
                    odds,
                    info["home"],
                    info["away"]
                )

                if state["games"][game_pk]["opening_total"] is None and live_total:
                    state["games"][game_pk]["opening_total"] = live_total

                opening_total = state["games"][game_pk]["opening_total"]

                over_score, under_score, ps, bp, br, tto = calculate_scores(
                    info,
                    p,
                    opening_total,
                    live_total
                )

                status = info["status"]

                if status == "Live":
                    any_live = True
                    mode = "ACTIVE"
                elif status == "Final":
                    mode = "FINAL"
                else:
                    mode = "DORMANT"

                if (
                    live_total is not None
                    and opening_total is not None
                    and (
                        live_total <= opening_total - 1.0
                        or live_total >= opening_total + 1.5
                    )
                ):
                    any_near_strike = True

                print(
                    f"{mode} | {label} | Start {start_label} | "
                    f"{info['inning_state']} {info['inning']} | "
                    f"Score {info['away_runs']}-{info['home_runs']} | "
                    f"Open {opening_total} Live {live_total} | "
                    f"Pitcher {info['pitcher_name']} PC {p['pitch_count']} | "
                    f"OVER {over_score}% UNDER {under_score}%"
                )

                if status != "Live":
                    continue

                alerts = state["games"][game_pk]["alerts"]

                if (
                    over_score >= MIN_CONFIDENCE
                    and opening_total is not None
                    and live_total is not None
                    and live_total <= opening_total - 1.5
                    and price_ok(over_price)
                    and "OVER" not in alerts
                ):
                    msg = (
                        f"SHIFT STRIKE\n\n"
                        f"{label}\n"
                        f"Start: {start_label}\n\n"
                        f"PLAY: Over {live_total}\n"
                        f"Odds: {over_price}\n"
                        f"Confidence: {over_score}%\n\n"
                        f"Opening Total: {opening_total}\n"
                        f"Live Total: {live_total}\n"
                        f"Score: {info['away_runs']}-{info['home_runs']}\n"
                        f"Inning: {info['inning_state']} {info['inning']}\n\n"
                        f"Pitcher: {info['pitcher_name']}\n"
                        f"Pitch Count: {p['pitch_count']}\n"
                        f"Pitcher Stress: {ps}/100\n"
                        f"Bullpen Exposure: {bp}/100\n"
                        f"Base Runner Pressure: {br}/100\n"
                        f"Times Through Order: {tto}/100"
                    )

                    send_text(msg)
                    alerts.append("OVER")

                if (
                    under_score >= MIN_CONFIDENCE
                    and opening_total is not None
                    and live_total is not None
                    and live_total >= opening_total + 2
                    and price_ok(under_price)
                    and "UNDER" not in alerts
                ):
                    msg = (
                        f"SHIFT STRIKE\n\n"
                        f"{label}\n"
                        f"Start: {start_label}\n\n"
                        f"PLAY: Under {live_total}\n"
                        f"Odds: {under_price}\n"
                        f"Confidence: {under_score}%\n\n"
                        f"Opening Total: {opening_total}\n"
                        f"Live Total: {live_total}\n"
                        f"Score: {info['away_runs']}-{info['home_runs']}\n"
                        f"Inning: {info['inning_state']} {info['inning']}\n\n"
                        f"Pitcher: {info['pitcher_name']}\n"
                        f"Pitch Count: {p['pitch_count']}\n"
                        f"Pitcher Stress: {ps}/100\n"
                        f"Bullpen Exposure: {bp}/100\n"
                        f"Base Runner Pressure: {br}/100\n"
                        f"Times Through Order: {tto}/100"
                    )

                    send_text(msg)
                    alerts.append("UNDER")

                save_state(state)

        except Exception as e:
            print("ERROR:", repr(e))

        sleep_seconds = determine_next_sleep(any_live, any_near_strike)
        print(f"Sleeping {sleep_seconds} seconds...\n")
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()

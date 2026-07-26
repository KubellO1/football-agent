"""API Provider Health Check — zero-dependency (stdlib only)."""
import json
import os
import sys
from datetime import date
from urllib.request import Request, urlopen
from urllib.error import HTTPError

os.chdir(r"C:\Users\ruowa\Projects\football-agent")

# Manual .env parse
env = {}
with open(".env", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"')

API_FOOTBALL_KEY = env["API_FOOTBALL_KEY"]
API_FOOTBALL_BASE_URL = env["API_FOOTBALL_BASE_URL"]
ODDS_API_KEY = env["ODDS_API_KEY"]
ODDS_API_BASE_URL = env["ODDS_API_BASE_URL"]

print("=== API PROVIDER HEALTH CHECK ===")
print(f"Date: {date.today().isoformat()}")
print(f"API-Football URL: {API_FOOTBALL_BASE_URL}")
print(f"Odds API URL: {ODDS_API_BASE_URL}")
print()

# --- API-Football ---
print("--- API-Football v3 ---")
try:
    req = Request(f"{API_FOOTBALL_BASE_URL}/status", headers={"x-apisports-key": API_FOOTBALL_KEY})
    resp = urlopen(req, timeout=15)
    data = json.loads(resp.read())
    errors = data.get("errors", [])
    sub = data.get("response", {}).get("subscription", {})
    plan = sub.get("plan", "?")
    remaining = resp.headers.get("x-ratelimit-requests-remaining", "?")
    if errors:
        print(f"  [AUTH ERROR] errors={errors}")
    else:
        print(f"  CONNECTED — Plan: {plan}, Quota remaining: {remaining}")
except HTTPError as e:
    body = e.read().decode()[:200]
    print(f"  HTTP {e.code}: {body}")
except Exception as e:
    print(f"  ERROR: {e}")

# Get today's fixtures
try:
    today = date.today().isoformat()
    req = Request(f"{API_FOOTBALL_BASE_URL}/fixtures?date={today}", headers={"x-apisports-key": API_FOOTBALL_KEY})
    resp = urlopen(req, timeout=15)
    data = json.loads(resp.read())
    fixtures = data.get("response", [])
    print(f"  Today's fixtures ({today}): {len(fixtures)}")
    if fixtures:
        for f in fixtures[:5]:
            home = f["teams"]["home"]["name"]
            away = f["teams"]["away"]["name"]
            league = f["league"]["name"]
            status = f["fixture"]["status"]["short"]
            print(f"    {home} vs {away} [{league}] — {status}")
except Exception as e:
    print(f"  Fixtures fetch ERROR: {e}")

print()

# --- The Odds API ---
print("--- The Odds API v4 ---")
for sport_key in ["soccer_fifa_world_cup", "soccer_world_cup", "soccer_epl"]:
    try:
        url = f"{ODDS_API_BASE_URL}/sports/{sport_key}/odds?apiKey={ODDS_API_KEY}&regions=eu"
        req = Request(url)
        resp = urlopen(req, timeout=15)
        data = json.loads(resp.read())
        remaining = resp.headers.get("x-requests-remaining", "?")
        used = resp.headers.get("x-requests-used", "?")
        print(f"  [{sport_key}]: {len(data)} events, used={used}, remaining={remaining}")
        if data:
            for event in data[:3]:
                print(f"    {event.get('home_team')} vs {event.get('away_team')}")
                for book in event.get("bookmakers", [])[:2]:
                    markets = [m["key"] for m in book.get("markets", [])]
                    print(f"      {book['title']}: markets={markets}")
        break  # Stop after first successful key
    except HTTPError as e:
        body = e.read().decode()[:200]
        if "OUT_OF_USAGE" in body:
            print(f"  [{sport_key}]: QUOTA EXHAUSTED — monthly limit reached")
        elif "401" in str(e.code):
            print(f"  [{sport_key}]: AUTH FAILED — API key invalid")
        else:
            print(f"  [{sport_key}]: HTTP {e.code} — {body[:100]}")
    except Exception as e:
        print(f"  [{sport_key}]: ERROR: {e}")

print()
print("=== HEALTH CHECK COMPLETE ===")

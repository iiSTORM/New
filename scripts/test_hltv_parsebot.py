#!/usr/bin/env python3
"""
Tests parse.bot's HLTV API (a managed wrapper around the real hltv.org)
against real data, to confirm exact field shapes before committing to
anything paid. Specifically need to see:
  - get_match_details: is "kd" a ratio (e.g. 1.35) or a "K-D" string
    (e.g. "22-15") we can split into real kills/deaths? Is there an
    assists field at all, or just kd/adr/rating?
  - get_player_stats / get_team_stats: exact field names, and whether the
    "days" window parameter can realistically stand in for "current split"
    vs "historical split" the way gol.gg's actual season boundaries did.
  - get_upcoming_matches / get_team_rankings: shape, for schedule/region
    selection design.

Costs about 18 credits total (well within the 200/month free tier):
  get_results(1) + get_match_details(2) + get_player_stats(5) +
  get_team_stats(2) + get_upcoming_matches(5) + get_team_rankings(3)

Setup:
  1. Sign up at https://parse.bot and grab a free API key.
  2. export PARSE_API_KEY=your_key_here
  3. pip install requests
  4. python scripts/test_hltv_parsebot.py > hltv_parsebot_output.txt

Then share hltv_parsebot_output.txt.
"""
import json
import os
import sys

import requests

API_KEY = os.environ.get("PARSE_API_KEY")
BASE = "https://api.parse.bot/scraper/b3500f47-4f4d-4f28-b85d-7e73293b70d1"

HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}


def call(endpoint, params=None, label=None):
    url = f"{BASE}/{endpoint}"
    print("=" * 60)
    print(label or endpoint)
    print(f"  GET {url}  params={params}")
    print("=" * 60)
    try:
        resp = requests.get(url, headers=HEADERS, params=params or {}, timeout=20)
        print(f"  status: {resp.status_code}")
        # Credit usage is reported in response headers per the docs.
        credit_headers = {k: v for k, v in resp.headers.items() if "credit" in k.lower()}
        if credit_headers:
            print(f"  credit headers: {credit_headers}")
        if resp.status_code == 200:
            data = resp.json()
            print(json.dumps(data, indent=2, default=str)[:5000])
            print()
            return data
        else:
            print(f"  body: {resp.text[:500]}\n")
    except Exception as e:
        print(f"  ! request failed: {type(e).__name__}: {e}\n")
    return None


def main():
    if not API_KEY:
        print("! PARSE_API_KEY environment variable not set. "
              "Run: export PARSE_API_KEY=your_key_here", file=sys.stderr)
        sys.exit(1)

    results = call("get_results", {"limit": 5}, "get_results(limit=5)")

    match_id = None
    if results and isinstance(results, dict):
        items = results.get("results", [])
        if items:
            match_id = items[0].get("match_id")
            print(f"Using match_id={match_id!r} (from the most recent real result) for get_match_details below\n")

    if match_id:
        call("get_match_details", {"match_id": match_id},
             f"get_match_details(match_id={match_id!r}) — THE CRITICAL ONE, check 'stats' array shape")
    else:
        print("! No match_id found in get_results — skipping get_match_details\n")

    call("get_player_stats", {"days": 30, "limit": 10}, "get_player_stats(days=30, limit=10)")
    call("get_team_stats", {"days": 30, "limit": 10}, "get_team_stats(days=30, limit=10)")
    call("get_upcoming_matches", {}, "get_upcoming_matches()")
    call("get_team_rankings", {}, "get_team_rankings()")


if __name__ == "__main__":
    main()

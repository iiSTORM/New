#!/usr/bin/env python3
"""
Follow-up to the first parse.bot test run. That run confirmed get_results,
get_upcoming_matches, get_team_rankings, and get_player_stats all work with
real data — but two things are still unresolved:

  1. A bug in THIS script (not the API): every response is wrapped as
     {"status": "success", "data": {...}}, and the first version looked for
     "results" at the top level instead of inside "data" — which is why
     get_match_details never actually ran despite real match_ids being
     right there in the response. Fixed below.
  2. get_team_stats timed out at 20s — bumped to 45s here to see if it's
     just slow rather than broken.

The real target this run: get_match_details. get_player_stats already
confirmed "kd" is a ratio (e.g. "1.37"), not raw kill/death counts — the
critical open question is whether get_match_details has the same
limitation (in which case we'd reconstruct real counts algebraically from
kd + kd_diff, like the player_stats endpoint allows) or gives raw counts
directly per map.

Costs about 7 credits this run (get_results 1 + get_match_details 2 +
get_team_stats ~2-5), reusing the same env var setup as before:
    python scripts/test_hltv_parsebot.py > hltv_parsebot_output_2.txt 2>&1
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
        resp = requests.get(url, headers=HEADERS, params=params or {}, timeout=45)
        print(f"  status: {resp.status_code}")
        credit_headers = {k: v for k, v in resp.headers.items() if "credit" in k.lower()}
        if credit_headers:
            print(f"  credit headers: {credit_headers}")
        if resp.status_code == 200:
            envelope = resp.json()
            # Every response is wrapped as {"status": "success", "data": {...}}
            # — unwrap it here so callers just get the real payload.
            data = envelope.get("data", envelope) if isinstance(envelope, dict) else envelope
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

    call("get_team_stats", {"days": 30, "limit": 10}, "get_team_stats(days=30, limit=10)")


if __name__ == "__main__":
    main()

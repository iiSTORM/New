#!/usr/bin/env python3
"""
Follow-up to the second parse.bot test run, which revealed something
important: get_match_details returned "stats": [] — completely empty —
for a real, genuinely completed match. Before concluding the endpoint is
broken, this rules out one explanation: that match was a very minor
qualifier ("Exort Fiesta Series 1"), and HLTV itself might just not track
detailed stats for lower-tier matches. This scans a larger batch of
results for a match involving a top-ranked team (using team names already
confirmed strong via get_team_stats: Virtus.pro, Betclic, Imperial,
Spirit, etc.) and tests get_match_details on that instead — a fairer test
of whether the field works at all when real stats coverage should exist.

Costs about 3 credits (get_results with a larger limit + one
get_match_details call):
    python scripts/test_hltv_parsebot_2.py > hltv_parsebot_output_3.txt 2>&1
"""
import json
import os
import sys

import requests

API_KEY = os.environ.get("PARSE_API_KEY")
BASE = "https://api.parse.bot/scraper/b3500f47-4f4d-4f28-b85d-7e73293b70d1"
HEADERS = {"X-API-Key": API_KEY} if API_KEY else {}

# Confirmed strong/notable teams from the previous get_team_stats run — a
# match involving any of these is far more likely to have real HLTV stats
# coverage than a minor regional qualifier.
NOTABLE_TEAMS = {
    "virtus.pro", "betclic", "imperial", "hotu", "spirit", "og",
    "bushido wildcats", "cybershoke", "1win", "metizport",
}


def call(endpoint, params=None, label=None):
    url = f"{BASE}/{endpoint}"
    print("=" * 60)
    print(label or endpoint)
    print(f"  GET {url}  params={params}")
    print("=" * 60)
    try:
        resp = requests.get(url, headers=HEADERS, params=params or {}, timeout=45)
    except Exception as e:
        print(f"  ! request failed: {type(e).__name__}: {e}\n")
        return None
    print(f"  status: {resp.status_code}")
    credit_headers = {k: v for k, v in resp.headers.items() if "credit" in k.lower()}
    if credit_headers:
        print(f"  credit headers: {credit_headers}")
    if resp.status_code != 200:
        print(f"  body: {resp.text[:500]}\n")
        return None
    envelope = resp.json()
    data = envelope.get("data", envelope) if isinstance(envelope, dict) else envelope
    return data


def main():
    if not API_KEY:
        print("! PARSE_API_KEY environment variable not set.", file=sys.stderr)
        sys.exit(1)

    results = call("get_results", {"limit": 50}, "get_results(limit=50) — scanning for a notable match")
    if not results:
        print("! get_results failed, can't continue")
        sys.exit(1)

    items = results.get("results", [])
    print(f"  {len(items)} results returned\n")

    notable_match = None
    for m in items:
        t1 = (m.get("team1") or "").lower()
        t2 = (m.get("team2") or "").lower()
        if t1 in NOTABLE_TEAMS or t2 in NOTABLE_TEAMS:
            notable_match = m
            break

    if not notable_match:
        print("! No match involving a known notable team found in the last 50 results — "
              "trying the first result anyway as a fallback")
        notable_match = items[0] if items else None

    if not notable_match:
        print("! No results at all — can't test get_match_details")
        sys.exit(1)

    print(f"Testing get_match_details on: {notable_match['team1']} vs {notable_match['team2']} "
          f"({notable_match['event']}), match_id={notable_match['match_id']}\n")

    details = call("get_match_details", {"match_id": notable_match["match_id"]},
                    f"get_match_details({notable_match['match_id']}) — notable-team test")
    if details:
        print(json.dumps(details, indent=2, default=str))
        stats = details.get("stats", [])
        print(f"\n>>> stats array length: {len(stats)}")
        if stats:
            print(">>> First stats entry, full shape:")
            print(json.dumps(stats[0], indent=2, default=str))


if __name__ == "__main__":
    main()

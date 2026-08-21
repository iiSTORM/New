#!/usr/bin/env python3
"""
Pulls the schedule (upcoming + recent match state) for each major region from
the public LoL Esports API — the same API lolesports.com's own website calls,
so it's CORS-friendly and doesn't need scraping/auth beyond the widely-
published public API key below.

Merges into data.json's "regions" structure alongside scrape_lcs.py's output.
"""
import json
import sys
import requests

API = "https://esports-api.lolesports.com/persisted/gw"
# Public key used by lolesports.com's own frontend — not a secret, but if
# Riot ever rotates it, grab the new one from the network tab on lolesports.com.
API_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
HEADERS = {"x-api-key": API_KEY}

REGION_KEYS = ["LCS", "LEC", "LCK", "LPL"]


def get_all_leagues():
    r = requests.get(f"{API}/getLeagues", headers=HEADERS, params={"hl": "en-US"}, timeout=20)
    r.raise_for_status()
    return r.json()["data"]["leagues"]


def find_league_id(leagues, name_contains):
    matches = [l for l in leagues if name_contains.lower() in l["name"].lower()]
    if not matches:
        raise RuntimeError(f"No league found matching '{name_contains}'. "
                            f"Available: {[l['name'] for l in leagues]}")
    # Prefer an exact match over things like "LCS Challengers" if present.
    exact = [l for l in matches if l["name"].strip().upper() == name_contains.upper()]
    return (exact or matches)[0]["id"]


def get_schedule(league_id):
    r = requests.get(f"{API}/getSchedule", headers=HEADERS,
                      params={"hl": "en-US", "leagueId": league_id}, timeout=20)
    r.raise_for_status()
    return r.json()["data"]["schedule"]["events"]


def main():
    leagues = get_all_leagues()
    print(f"Fetched {len(leagues)} leagues from LoL Esports API")

    regions = {}
    for region_key in REGION_KEYS:
        try:
            league_id = find_league_id(leagues, region_key)
            print(f"  {region_key} league id: {league_id}")
            events = get_schedule(league_id)
        except Exception as e:
            print(f"  ! {region_key} schedule fetch failed: {e}", file=sys.stderr)
            continue

        upcoming = []
        for e in events:
            if e.get("state") != "unstarted":
                continue
            match = e.get("match", {})
            teams = match.get("teams", [])
            if len(teams) != 2:
                continue
            upcoming.append({
                "date": e["startTime"],  # ISO 8601 UTC — app formats to local time
                "teamA": teams[0]["name"],
                "teamB": teams[1]["name"],
                "block": e.get("blockName", ""),
            })
        regions[region_key] = upcoming
        print(f"  {region_key}: {len(upcoming)} upcoming matches")

    with open("schedule.json", "w") as f:
        json.dump({"regions": regions}, f, indent=2)
    print(f"Wrote schedule.json for regions: {list(regions.keys())}")


if __name__ == "__main__":
    main()

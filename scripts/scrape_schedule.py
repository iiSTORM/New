#!/usr/bin/env python3
"""
Pulls the schedule (upcoming + recent match state) for each major region from
the public LoL Esports API — the same API lolesports.com's own website calls,
so it's CORS-friendly and doesn't need scraping/auth beyond the widely-
published public API key below.

Merges into data.json's "regions" structure alongside scrape_lcs.py's output.
"""
import json
import os
import sys
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from international import INTERNATIONAL_EVENTS, build_event, in_window, pick_tournament

API = "https://esports-api.lolesports.com/persisted/gw"
# Public key used by lolesports.com's own frontend — not a secret, but if
# Riot ever rotates it, grab the new one from the network tab on lolesports.com.
API_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
HEADERS = {"x-api-key": API_KEY}

REGION_KEYS = ["LCS", "LEC", "LCK", "LPL", "LCP", "CBLOL", "TCL"]


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


def get_tournaments(league_id):
    r = requests.get(f"{API}/getTournamentsForLeague", headers=HEADERS,
                     params={"hl": "en-US", "leagueId": league_id}, timeout=20)
    r.raise_for_status()
    return r.json()["data"]["leagues"][0]["tournaments"]


def get_standings(tournament_id):
    r = requests.get(f"{API}/getStandings", headers=HEADERS,
                     params={"hl": "en-US", "tournamentId": tournament_id}, timeout=20)
    r.raise_for_status()
    return r.json()["data"]


def upcoming_entry(e):
    """An unstarted schedule event as a fixture row, or None if it lacks two sides."""
    match = e.get("match", {})
    teams = match.get("teams", [])
    if len(teams) != 2:
        return None
    strategy = match.get("strategy") or {}
    return {
        "date": e["startTime"],  # ISO 8601 UTC — app formats to local time
        "teamA": teams[0]["name"],
        "teamB": teams[1]["name"],
        "block": e.get("blockName", ""),
        # A Demacia Cup Swiss round opens Bo1 and turns Bo3 the next day, so
        # the series length is per match, not per event.
        "best_of": strategy.get("count") if strategy.get("type") == "bestOf" else None,
        "match_id": match.get("id"),
    }


def scrape_international(leagues, now=None):
    """({region key: upcoming}, {region key: event}) for every event on now."""
    by_slug = {l.get("slug"): l for l in leagues}
    upcoming, events = {}, {}
    for region_key, slug in INTERNATIONAL_EVENTS.items():
        league = by_slug.get(slug)
        if not league:
            print(f"  {region_key}: no '{slug}' league in the API this run")
            continue
        try:
            tournament = pick_tournament(get_tournaments(league["id"]), now)
            if not tournament:
                continue
            standings = get_standings(tournament["id"])
            schedule = [e for e in get_schedule(league["id"])
                        if in_window(e.get("startTime"), tournament)]
        except Exception as e:
            print(f"  ! {region_key} event fetch failed: {e}", file=sys.stderr)
            continue
        event = build_event(slug, region_key, tournament, standings, schedule)
        rows = [r for r in (upcoming_entry(e) for e in schedule if e.get("state") == "unstarted") if r]
        upcoming[region_key] = rows
        events[region_key] = event
        n = sum(len(sec["matches"]) for st in event["stages"] for sec in st["sections"])
        print(f"  {region_key}: {tournament.get('slug')} — {len(event['stages'])} stage(s), "
              f"{n} matches, {len(rows)} upcoming")
    return upcoming, events


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

        print(f"  {region_key}: {len(events)} raw events from the API")
        state_counts = {}
        dropped_team_count = 0
        upcoming = []
        for e in events:
            state = e.get("state")
            state_counts[state] = state_counts.get(state, 0) + 1
            if state != "unstarted":
                continue
            row = upcoming_entry(e)
            if row is None:
                dropped_team_count += 1
                continue
            upcoming.append(row)
        print(f"    event states: {state_counts}")
        if dropped_team_count:
            print(f"    {dropped_team_count} 'unstarted' events dropped for not having exactly 2 teams "
                  f"(likely TBD bracket slots)")
        regions[region_key] = upcoming
        print(f"  {region_key}: {len(upcoming)} upcoming matches after filtering")

    print("International events:")
    intl_upcoming, events = scrape_international(leagues)
    regions.update(intl_upcoming)

    with open("schedule.json", "w") as f:
        # Written minified: these files are machine-generated and never read
        # by hand, and indent=2 was about two thirds of the bytes
        # (data.json: 7.5MB -> 2.4MB). GitHub serves them gzipped, so the
        # win on the wire is smaller (~535KB -> ~340KB), but the browser
        # still parses the full decompressed text, and every run commits a
        # whole fresh copy.
        json.dump({"generated_at": datetime.now(timezone.utc).isoformat(),
                   "regions": regions, "events": events}, f, separators=(",", ":"))
    print(f"Wrote schedule.json for regions: {list(regions.keys())}")


if __name__ == "__main__":
    main()

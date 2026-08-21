#!/usr/bin/env python3
"""Combines data.json (regions -> teams + past_matches) and schedule.json
(regions -> upcoming) into the final payload the app fetches. Team-name
spelling can differ slightly between gol.gg and the LoL Esports API (e.g.
"Cloud9" vs "Cloud9 KIA") — TEAM_NAME_MAP below normalizes them, add entries
per-region as mismatches turn up in the logs."""
import json
import sys

TEAM_NAME_MAP = {
    "Cloud9 KIA": "Cloud9",
    "Team Liquid Alienware": "Team Liquid",
    # add more if the scrapers report a mismatch
}


def normalize(name):
    return TEAM_NAME_MAP.get(name, name)


def main():
    with open("data.json") as f:
        data = json.load(f)
    try:
        with open("schedule.json") as f:
            schedule = json.load(f)
    except FileNotFoundError:
        schedule = {"regions": {}}

    for region_key, region_data in data.get("regions", {}).items():
        known_teams = set(region_data.get("teams", {}).keys())
        region_schedule = schedule.get("regions", {}).get(region_key, [])
        upcoming = []
        for m in region_schedule:
            a, b = normalize(m["teamA"]), normalize(m["teamB"])
            if a in known_teams and b in known_teams:
                upcoming.append({"date": m["date"], "teamA": a, "teamB": b, "block": m.get("block", "")})
            else:
                print(f"  ! {region_key}: dropped '{m['teamA']}' vs '{m['teamB']}' "
                      f"(not in known teams, possible name mismatch)", file=sys.stderr)
        region_data["upcoming_matches"] = upcoming
        print(f"{region_key}: merged {len(upcoming)}/{len(region_schedule)} upcoming matches")

    with open("data.json", "w") as f:
        json.dump(data, f, indent=2)


if __name__ == "__main__":
    main()

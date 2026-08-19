#!/usr/bin/env python3
"""Combines data.json (teams + past_matches) and schedule.json (upcoming)
into the final payload the app fetches. Team-name spelling can differ
slightly between gol.gg and the LoL Esports API (e.g. "Cloud9" vs
"Cloud9 KIA") — TEAM_NAME_MAP below normalizes them."""
import json

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
        schedule = {"upcoming": []}

    known_teams = set(data["teams"].keys())
    upcoming = []
    for m in schedule["upcoming"]:
        a, b = normalize(m["teamA"]), normalize(m["teamB"])
        if a in known_teams and b in known_teams:
            upcoming.append({"date": m["date"], "teamA": a, "teamB": b, "block": m.get("block", "")})

    data["upcoming_matches"] = upcoming
    with open("data.json", "w") as f:
        json.dump(data, f, indent=2)
    print(f"Merged {len(upcoming)} upcoming matches into data.json")


if __name__ == "__main__":
    main()

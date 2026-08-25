#!/usr/bin/env python3
"""Combines data.json (regions -> teams + past_matches) and schedule.json
(regions -> upcoming) into the final payload the app fetches.

Team-name spelling differs between gol.gg and the LoL Esports API in two
ways: pure capitalization (handled automatically via case-insensitive
matching below) and genuinely different names — e.g. the API includes a
city/sponsor prefix gol.gg drops ("Beijing JDG Esports" -> "JD Gaming"), or
the two sources just picked different official names ("Gen.G Esports" vs
"Gen.G"). Those need an explicit entry in TEAM_NAME_MAP (lowercase key ->
gol.gg's exact casing). Add more here as the logs report new mismatches —
"TBD vs TBD" entries are always correctly dropped (that match's teams
aren't determined yet, so it isn't projectable regardless)."""
import json
import sys

TEAM_NAME_MAP = {
    # LCS
    "cloud9 kia": "Cloud9",
    "team liquid alienware": "Team Liquid",
    # LCK
    "gen.g esports": "Gen.G",
    "nongshim red force": "Nongshim RedForce",  # gol.gg has no space in "RedForce"
    # LPL — API includes city/sponsor prefixes gol.gg doesn't use
    "xi'an team we": "Team WE",
    "shenzhen ninjas in pyjamas": "Ninjas in Pyjamas",
    "beijing jdg esports": "JD Gaming",
    "thunder talk gaming": "ThunderTalk Gaming",  # gol.gg has no space between Thunder/Talk
    "anyone's legend": "Anyone s Legend",  # gol.gg's own listing drops the apostrophe
    # CBLOL — confirmed against a real gol.gg game page titled "RED Canids
    # vs Los Grandes": the API uses "RED Kalunga" (drops "Canids") while
    # gol.gg drops the sponsor "Kalunga" instead; "LOS" is API shorthand
    # for the full "Los Grandes".
    "red kalunga": "RED Canids",
    "los": "Los Grandes",
    # TCL — confirmed against a real current TCL 2026 Summer team list:
    # "PCIFIC Esports" (unusual spelling, genuinely correct on both sides,
    # not a typo) and "SU Esports" (API adds sponsor prefix "Avella" that
    # gol.gg drops, same pattern as the LPL entries above).
    "avella su esports": "SU Esports",
}


def normalize(name):
    mapped = TEAM_NAME_MAP.get(name.lower())
    return mapped if mapped else name


def build_lookup(known_teams):
    """Case-insensitive lookup: API casing (e.g. 'KIWOOM DRX') resolves to
    gol.gg's exact casing (e.g. 'Kiwoom DRX') as long as the letters match."""
    return {t.lower(): t for t in known_teams}


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
        lookup = build_lookup(known_teams)
        region_schedule = schedule.get("regions", {}).get(region_key, [])
        upcoming = []
        dropped = 0
        for m in region_schedule:
            a_raw, b_raw = normalize(m["teamA"]), normalize(m["teamB"])
            a = lookup.get(a_raw.lower())
            b = lookup.get(b_raw.lower())
            if a and b:
                upcoming.append({"date": m["date"], "teamA": a, "teamB": b, "block": m.get("block", "")})
            else:
                dropped += 1
                if m["teamA"] != "TBD":  # don't spam the log with unscheduled placeholder matches
                    print(f"  ! {region_key}: dropped '{m['teamA']}' vs '{m['teamB']}' "
                          f"(no match in known teams — add to TEAM_NAME_MAP if this is a real team)",
                          file=sys.stderr)
        region_data["upcoming_matches"] = upcoming
        print(f"{region_key}: merged {len(upcoming)}/{len(region_schedule)} upcoming matches "
              f"({dropped} dropped)")

    with open("data.json", "w") as f:
        json.dump(data, f, indent=2)


if __name__ == "__main__":
    main()
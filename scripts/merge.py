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
    # Leviatán was originally a one-off CBLOL Cup guest team (LLA), but was
    # confirmed as a full 2026 CBLOL partner team for the whole season —
    # this entry matters beyond just the Cup now. LoL Esports API keeps
    # the accent ("LEVIATÁN"); gol.gg appears to drop it, per a real
    # indexed gol.gg game page titled "Leviatan vs RED Canids" (CBLOL Cup
    # 2026 Week 1) — inferred from that page title, not directly
    # confirmed against gol.gg's own stored team name, so double-check
    # this resolves cleanly on the next merge run.
    "leviatán": "Leviatan",
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


def merge_career_data(data):
    """Folds each player's decayed career baseline (from scrape_career.py's
    output) into their existing record in data.json. career_data.json is
    keyed by gol.gg's numeric player ID, but data.json's players are keyed
    by name within each team — so this builds a name-based lookup rather
    than an ID-based one, since data.json has no gol.gg IDs stored at all.

    Gracefully optional: if career_data.json doesn't exist yet (career
    history hasn't been run, or isn't wired into this particular
    workflow), every player just gets career=None and the rest of the
    pipeline is unaffected — same pattern as schedule.json being missing
    above."""
    try:
        with open("career_data.json") as f:
            career_data = json.load(f)
    except FileNotFoundError:
        print("! career_data.json not found — skipping career merge (every player gets career=None; "
              "run scrape_career.py first if this wasn't intentional)", file=sys.stderr)
        career_data = {}

    # Multiple gol.gg player IDs sharing an identical short handle across
    # different currently-tracked players is possible in principle (two
    # different pros both going by the same short name) though rare —
    # logged rather than silently overwritten so it's visible if it
    # happens.
    career_by_name = {}
    for player_id, record in career_data.items():
        name = record.get("name")
        if not name:
            continue
        if name in career_by_name and career_by_name[name][0] != player_id:
            print(f"  ! duplicate career-data name '{name}' (ids {career_by_name[name][0]} and {player_id}) "
                  f"— keeping the later one", file=sys.stderr)
        career_by_name[name] = (player_id, record.get("career"))

    matched = 0
    unmatched = []
    for region_key, region_data in data.get("regions", {}).items():
        for team_name, team in region_data.get("teams", {}).items():
            for player in team.get("players", []):
                name = player.get("name")
                entry = career_by_name.get(name)
                if entry and entry[1]:
                    player["career"] = entry[1]
                    matched += 1
                else:
                    player["career"] = None
                    unmatched.append(f"{region_key}/{team_name}/{name}")

    total = matched + len(unmatched)
    print(f"Merged career data: {matched}/{total} player(s) matched")
    if unmatched:
        shown = unmatched[:20]
        more = f" ... (+{len(unmatched) - 20} more)" if len(unmatched) > 20 else ""
        print(f"  ! unmatched (no career data — career=None, falls back to cur/hist only): {shown}{more}",
              file=sys.stderr)


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

    merge_career_data(data)

    with open("data.json", "w") as f:
        json.dump(data, f, indent=2)


if __name__ == "__main__":
    main()
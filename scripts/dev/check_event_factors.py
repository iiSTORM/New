#!/usr/bin/env python3
"""Do the class gap and the event pace still match the event being played?

CLASS_GAP_KILLS (1.4) and EVENT_PACE (Worlds 0.85) are measurements of one
event, applied to the next game of it. This re-measures both on every LoL
event match in data.json, the way they were measured: each player's kills
per map projected point in time with the factors already applied, against
what they did.

  pace   all players at an event in EVENT_PACE: actual / projected. 1.00
         means the factor is right; the factor that would have been right
         is the shipped one times that ratio.
  gap    LCK/LPL against LCS/LCP/CBLOL/TCL: the stronger side's ratio and
         the weaker side's. The factor that would have been right is the
         shipped one times sqrt(stronger / weaker) -- the same symmetric
         form the app applies.

Exits 1 when either implied factor sits further from the shipped one than
its tolerance, with enough maps behind it to mean anything, so the weekly
workflow goes red and says which. It never rewrites the constants.

    python scripts/dev/check_event_factors.py
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import optimize_weights as ow                      # noqa: E402
import search_weights_by_outcome as sw             # noqa: E402

PACE_TOLERANCE = 0.08
GAP_TOLERANCE = 0.3
MIN_PACE_MAPS = 20
MIN_GAP_MAPS = 8


def side_rows(data):
    """[(region, date, team, opponent, maps, {player: kills})] for event matches."""
    out = []
    for region, blob in data.items():
        teams = blob.get("teams") or {}
        if not any((t or {}).get("at_event") or (t or {}).get("from_home_region")
                   for t in teams.values()):
            continue
        for m in blob.get("past_matches") or []:
            games = [g for g in (m.get("per_game") or []) if len(g or {}) == 2]
            if not games:
                continue
            for team in games[0]:
                opp = next((t for t in games[0] if t != team), None)
                kills = {}
                for g in games:
                    for player, s in (g.get(team) or {}).items():
                        kills[player] = kills.get(player, 0) + (s.get("k") or 0)
                out.append((region, str(m.get("date"))[:10], team, opp, len(games), kills))
    return out


def measure(data, index, weights):
    """{"pace": {event: (ratio, maps)}, "gap": (stronger ratio, weaker ratio, maps)}."""
    pace, gap = {}, {"stronger": [0.0, 0.0], "weaker": [0.0, 0.0], "maps": 0}
    seen_gap, seen_pace = set(), set()
    for region, date, team, opp, maps, kills in side_rows(data):
        teams = data[region].get("teams") or {}
        rows = [{"player": p, "team": team, "opponent": opp, "match_date": date,
                 "maps": 1, "stat": "kills"} for p in kills]
        projected = {r["player"]: mu for r, mu, _ in
                     sw.project_all(rows, data, index, "lol", "kills", weights)}
        both = [(kills[p] / maps, projected[p]) for p in kills if p in projected]
        if len(both) < 4:
            continue
        actual, proj = sum(a for a, _ in both), sum(b for _, b in both)
        event = (teams.get(team) or {}).get("at_event")
        if event and event in ow.EVENT_PACE and event == (teams.get(opp) or {}).get("at_event"):
            got = pace.setdefault(event, [0.0, 0.0, 0])
            got[0] += actual
            got[1] += proj
            key = (event, date, tuple(sorted((team, opp))))
            if key not in seen_pace:           # each side is a row; a map is one map
                seen_pace.add(key)
                got[2] += maps
        mine = (teams.get(team) or {}).get("from_home_region")
        theirs = (teams.get(opp) or {}).get("from_home_region")
        side = ("stronger" if mine in ow.CLASS_TOP and theirs in ow.CLASS_BOTTOM else
                "weaker" if mine in ow.CLASS_BOTTOM and theirs in ow.CLASS_TOP else None)
        if side:
            gap[side][0] += actual
            gap[side][1] += proj
            key = (date, tuple(sorted((team, opp))))
            if key not in seen_gap:
                seen_gap.add(key)
                gap["maps"] += maps
    return {"pace": {e: (a / p, n) for e, (a, p, n) in pace.items() if p},
            "gap": ((gap["stronger"][0] / gap["stronger"][1] if gap["stronger"][1] else None),
                    (gap["weaker"][0] / gap["weaker"][1] if gap["weaker"][1] else None),
                    gap["maps"])}


def verdicts(got):
    """[(name, shipped, implied, maps, drifted)]."""
    out = []
    for event, (ratio, maps) in sorted(got["pace"].items()):
        shipped = ow.EVENT_PACE[event]
        implied = shipped * ratio
        out.append((f"EVENT_PACE {event}", shipped, implied, maps,
                    maps >= MIN_PACE_MAPS and abs(implied - shipped) > PACE_TOLERANCE))
    strong, weak, maps = got["gap"]
    if strong and weak:
        implied = ow.CLASS_GAP_KILLS * math.sqrt(strong / weak)
        out.append(("CLASS_GAP_KILLS", ow.CLASS_GAP_KILLS, implied, maps,
                    maps >= MIN_GAP_MAPS and abs(implied - ow.CLASS_GAP_KILLS) > GAP_TOLERANCE))
    return out


def main():
    data, index = sw.load("lol")
    if not data:
        print("no LoL data")
        return 0
    got = measure(data, index, ow.SHIPPED_WEIGHTS["lol"]["kills"])
    lines, bad = [], False
    for name, shipped, implied, maps, drifted in verdicts(got):
        bad |= drifted
        lines.append(f"- **{name}**: shipped {shipped}, the event so far implies {implied:.2f} "
                     f"over {maps} maps" + (" -- DRIFTED" if drifted else ""))
    if not lines:
        lines.append("no event matches with a pace factor or a class-gap pairing on file")
    text = "## event factors\n" + "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(text + "\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

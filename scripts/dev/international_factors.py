#!/usr/bin/env python3
"""The class gap and event pace, event by event, from lol_events_history.json.

Two questions the app's constants (CLASS_GAP_KILLS, EVENT_PACE) were
answered for on one event, asked of every event on file:

  pace   team kills per map at the event, by stage. Swiss and groups
         against knockouts inside one event needs no outside baseline, so
         it holds for 2025 events as much as 2026 ones.
  gap    in LCK/LPL against LCS/LCP/CBLOL/TCL maps, the stronger side's
         share of the kills, against the same share when top-region teams
         meet each other (which is 50% by construction) -- reported as the
         kill ratio stronger/weaker. The app's 1.4 each way implies about
         1.96 relative to home form; raw event ratios run above home-form
         ratios, so read the trend across events, not the level.

Team homes come from the leagues' rosters in data.json; a team that is not
in a league there now (renamed, relegated) is left out rather than guessed.

    python scripts/dev/international_factors.py
"""
import collections
import json
import os
import re
import statistics
import sys

TOP = {"LCK", "LPL"}
BOTTOM = {"LCS", "LCP", "CBLOL", "TCL"}
KNOCKOUT = re.compile(r"playoff|knockout|bracket|quarter|semi|final", re.I)


def norm(name):
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


def homes(data_path="data.json"):
    out = {}
    if not os.path.exists(data_path):
        return out
    for key, blob in (json.load(open(data_path)).get("regions") or {}).items():
        if key in ("Worlds", "MSI", "First Stand", "EWC", "Demacia Cup"):
            continue
        for team in (blob.get("teams") or {}):
            out.setdefault(norm(team), key)
    return out


def stage_of(match):
    label = f"{match.get('week') or ''} {match.get('stage') or ''} {match.get('tournament') or ''}"
    return "knockout" if KNOCKOUT.search(label) else "swiss/groups"


def map_kills(match):
    """[(team, kills, opponent, opponent kills)] per map, both orders."""
    out = []
    for one in match.get("per_game") or []:
        teams = list(one or {})
        if len(teams) != 2:
            continue
        k = [sum((p.get("k") or 0) for p in (one[t] or {}).values()) for t in teams]
        out.append((teams[0], k[0], teams[1], k[1]))
    return out


def summarise(events, home):
    rows = []
    for name, ev in sorted(events.items()):
        pace = collections.defaultdict(list)
        gap = []
        for m in ev.get("past_matches") or []:
            for a, ka, b, kb in map_kills(m):
                pace[stage_of(m)].append(ka + kb)
                ha, hb = home.get(norm(a)), home.get(norm(b))
                if ha in TOP and hb in BOTTOM:
                    gap.append((ka, kb))
                elif hb in TOP and ha in BOTTOM:
                    gap.append((kb, ka))
        row = {"event": name, "maps": sum(len(v) for v in pace.values())}
        for stage, kills in pace.items():
            row[stage] = (statistics.mean(kills), len(kills))
        if "knockout" in row and "swiss/groups" in row:
            row["knockout_vs_groups"] = row["knockout"][0] / row["swiss/groups"][0]
        if gap:
            strong, weak = sum(s for s, _ in gap), sum(w for _, w in gap)
            row["gap"] = (strong / max(weak, 1), len(gap))
        rows.append(row)
    return rows


def main(path="lol_events_history.json"):
    if not os.path.exists(path):
        print(f"no {path}: run scripts/backfill_lol_events.py (the 'Backfill past LoL "
              "international events' workflow) first")
        return 0
    events = json.load(open(path)).get("events") or {}
    rows = summarise(events, homes())
    print(f"{'event':<34} {'maps':>5} {'groups k/map':>13} {'knockout k/map':>15} "
          f"{'KO/groups':>9}  {'top v bottom kill ratio':>24}")
    for r in rows:
        g, k = r.get("swiss/groups"), r.get("knockout")
        print(f"{r['event']:<34} {r['maps']:>5} "
              + (f"{g[0]:>8.1f} ({g[1]:>3})" if g else f"{'':>13}") + " "
              + (f"{k[0]:>10.1f} ({k[1]:>3})" if k else f"{'':>15}") + " "
              + (f"{r['knockout_vs_groups']:>9.2f}" if "knockout_vs_groups" in r else f"{'':>9}")
              + (f"  {r['gap'][0]:>15.2f} over {r['gap'][1]:>3} maps" if "gap" in r else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))

#!/usr/bin/env python3
"""Past LoL international events, box scores and all, into lol_events_history.json.

data.json only carries the event that is on now. The two factors the model
applies at Worlds -- the class gap (LCK/LPL against the smaller leagues)
and the event pace -- were measured on this year's Demacia Cup and Swiss
stage alone, and whether they are properties of international play or of
three weeks in October is a question earlier events can answer.

Finished events only (last game more than EVENT_GRACE_DAYS ago), from
gol.gg's tournament list for each season in SEASONS. An event already in
the file is not fetched again: a finished event does not change. The file
is read by scripts/dev/international_factors.py and by nothing the app
loads, so it cannot move a projection on its own.

    python scripts/backfill_lol_events.py
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import scrape_lcs as lcs  # noqa: E402

OUT = "lol_events_history.json"
SEASONS = ("S15", "S16")       # 2025, 2026


def finished_events(rows, today):
    """[(our key, gol.gg name)] for finished international events, pure."""
    out = []
    for row in rows or []:
        name = str(row.get("trname") or "").strip()
        last = str(row.get("lastgame") or "")[:10]
        try:
            last_day = datetime.strptime(last, "%Y-%m-%d").date()
        except ValueError:
            continue
        if (today - last_day).days <= lcs.EVENT_GRACE_DAYS:
            continue
        for key, pattern in lcs.GOLGG_EVENTS.items():
            if pattern.search(name):
                out.append((key, name))
                break
    return sorted(set(out), key=lambda pair: pair[1])


def main():
    today = datetime.now(timezone.utc).date()
    try:
        with open(OUT) as f:
            stored = json.load(f)
    except FileNotFoundError:
        stored = {"events": {}}
    events = stored.setdefault("events", {})
    wanted = []
    for season in SEASONS:
        wanted += finished_events(lcs.golgg_tournament_rows(season), today)
    print(f"{len(wanted)} finished international tournament(s): {[n for _, n in wanted]}")
    fetched = 0
    for key, name in wanted:
        if (events.get(name) or {}).get("past_matches"):
            continue
        print(f"\n=== {name} ({key}) ===")
        try:
            past = lcs.scrape_matches([name], {}, name)
        except Exception as e:            # one event failing should not lose the rest
            print(f"! {name} failed: {e}", file=sys.stderr)
            continue
        if past:
            events[name] = {"event": key, "past_matches": past}
            fetched += 1
    stored["refreshed_at"] = datetime.now(timezone.utc).isoformat()
    with open(OUT, "w") as f:
        json.dump(stored, f, separators=(",", ":"))
    total = sum(len(e.get("past_matches") or []) for e in events.values())
    print(f"\n{fetched} event(s) fetched; {len(events)} on file, {total} series")
    lcs.report_requests("backfill")
    return 0 if events else 1


if __name__ == "__main__":
    sys.exit(main())

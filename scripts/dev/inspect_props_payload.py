#!/usr/bin/env python3
"""
Summarises a saved provider payload: which leagues it carries, and which
stat labels, and how each one lands in scrape_props.py's funnel.

WHY THIS EXISTS
---------------
When `scrape_props.py --dry-run` reports "0 raw" for one game and a few
hundred for another, the cause is not a broken parser -- it is that the
league string this repo asks for in GAMES is not the one the provider uses
for that game. The same goes for a pile of "unrecognised stat": the funnel
counts them but never says WHAT they were, which is the one thing needed to
tell "stats this app does not model" (correct) from "kills under a spelling
STAT_ALIASES does not know" (a silent hole).

Both answers are in the payload. It is ~2MB, far too large to read or paste
anywhere, so this prints only the distinct labels and their counts -- a
couple of dozen lines that are safe to share.

    python scripts/dev/inspect_props_payload.py saved.json
"""
import argparse
import collections
import json
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from props_match import STAT_ALIASES, parse_stat  # noqa: E402
from scrape_props import GAMES  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("payload", help="a saved provider payload")
    ap.add_argument("--show", type=int, default=30,
                    help="how many distinct labels to list (default 30)")
    args = ap.parse_args()

    with open(args.payload) as f:
        payload = json.load(f)

    players = {}
    for item in payload.get("included") or []:
        if item.get("type") in ("new_player", "player"):
            attrs = item.get("attributes") or {}
            players[str(item.get("id"))] = attrs.get("league")

    projections = [i for i in (payload.get("data") or [])
                   if i.get("type") == "projection"]
    print(f"{args.payload}: {Path(args.payload).stat().st_size:,} bytes, "
          f"{len(projections):,} projections, {len(players):,} players\n")

    leagues = collections.Counter()
    per_league_stats = collections.defaultdict(collections.Counter)
    for item in projections:
        attrs = item.get("attributes") or {}
        rel = ((item.get("relationships") or {}).get("new_player") or {}).get("data") or {}
        league = players.get(str(rel.get("id"))) or attrs.get("league") or "(unknown)"
        leagues[league] += 1
        per_league_stats[league][attrs.get("stat_type")] += 1

    # What this repo asks for, and whether anything answers to that name.
    print("LEAGUES IN THE PAYLOAD")
    print("  count  league                          this repo asks for")
    wanted = {g: cfg["leagues"] for g, cfg in GAMES.items()}
    for league, count in leagues.most_common():
        hits = [g for g, names in wanted.items()
                if str(league).strip().lower() in {n.lower() for n in names}]
        note = f"-> {', '.join(hits)}" if hits else ""
        print(f"  {count:5d}  {str(league)[:30]:30s}  {note}")

    # League ids, because the endpoint takes league_id and a filtered
    # response is a few hundred KB instead of forty-odd megabytes. The ids
    # are provider-side and not documented anywhere, but every payload
    # carries them: projections point at a league, and the league objects
    # sit in `included`.
    league_ids = {}
    for item in payload.get("included") or []:
        if item.get("type") == "league":
            name = (item.get("attributes") or {}).get("name")
            if name:
                league_ids[str(name)] = str(item.get("id"))
    if not league_ids:
        # Older payloads put the league only on the projection's
        # relationships, so fall back to reading it from there.
        for item in projections:
            rel = ((item.get("relationships") or {}).get("league") or {}).get("data") or {}
            attrs = item.get("attributes") or {}
            rel_id, name = rel.get("id"), attrs.get("league")
            if rel_id and name:
                league_ids.setdefault(str(name), str(rel_id))

    if league_ids:
        print("\n  LEAGUE IDS — the endpoint takes league_id, and one league is a")
        print("  few hundred KB where the unfiltered board is tens of megabytes:")
        for name, lid in sorted(league_ids.items()):
            wanted_here = any(str(name).strip().lower() in {n.lower() for n in names}
                              for names in (cfg["leagues"] for cfg in GAMES.values()))
            mark = "  <-- this app" if wanted_here else ""
            print(f"    {lid:>6}  {name}{mark}")
        print("\n  URLs to save from a browser, one per game this app tracks:")
        for game, cfg in sorted(GAMES.items()):
            lowered = {n.lower() for n in cfg["leagues"]}
            hit = next((lid for nm, lid in league_ids.items()
                        if str(nm).strip().lower() in lowered), None)
            if hit:
                print(f"    {game:9s} https://api.prizepicks.com/projections"
                      f"?league_id={hit}&per_page=250&single_stat=true")
            else:
                print(f"    {game:9s} (no league id found for {sorted(cfg['leagues'])})")
    else:
        print("\n  No league ids in this payload — it may predate them, or be a "
              "filtered response that dropped the league objects.")

    print("\n  configured, and whether the payload has it:")
    for game, names in sorted(wanted.items()):
        lowered = {n.lower() for n in names}
        found = [l for l in leagues if str(l).strip().lower() in lowered]
        state = f"found in {found}" if found else "NOT FOUND -- this is why it reports 0 raw"
        print(f"    {game:9s} accepts {sorted(names)}: {state}")

    # Stat labels only matter for leagues this app actually consumes.
    print("\nSTAT LABELS, for leagues this repo matched above")
    modelled = sorted(STAT_ALIASES)
    for league, count in leagues.most_common():
        if not any(str(league).strip().lower() in {n.lower() for n in names}
                   for names in wanted.values()):
            continue
        print(f"\n  {league} ({count} projections)")
        buckets = collections.defaultdict(list)
        for label, n in per_league_stats[league].most_common():
            stat, window = parse_stat(label)
            if stat is None:
                buckets["not modelled by this app"].append((n, label))
            elif window is None:
                buckets["MODELLED STAT, no map window stated"].append((n, label))
            else:
                buckets["matched"].append((n, label))
        for bucket in ("matched", "MODELLED STAT, no map window stated",
                       "not modelled by this app"):
            rows = buckets.get(bucket)
            if not rows:
                continue
            total = sum(n for n, _ in rows)
            print(f"    {bucket} ({total} projections, {len(rows)} labels):")
            for n, label in rows[:args.show]:
                print(f"      {n:5d}  {label}")
            if len(rows) > args.show:
                print(f"      ... and {len(rows) - args.show} more labels")

    print(f"\n  (this app models: {', '.join(modelled)})")
    return 0


if __name__ == "__main__":
    # Piping this into head or less is the obvious thing to do with it, and
    # the default Python handling of that turns a closed pipe into a
    # traceback that looks like the tool failed.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main())

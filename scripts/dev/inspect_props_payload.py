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

from props_match import COMBO_PATTERN, STAT_ALIASES, parse_stat  # noqa: E402
from scrape_props import GAMES  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("payload", nargs="+",
                    help="saved provider payloads, one per league is normal")
    ap.add_argument("--show", type=int, default=30,
                    help="how many distinct labels to list (default 30)")
    ap.add_argument("--payouts", action="store_true",
                    help="list every field a projection carries and flag anything "
                         "payout-shaped. The multiplier table in the Parlays tab is a "
                         "published default that cannot be fetched; this says whether "
                         "the provider ships the real number.")
    ap.add_argument("--names", type=int, default=0, metavar="N",
                    help="also list N player names per league. The fastest way "
                         "to tell 'the roster file is broken' from 'this slate "
                         "is teams this app does not track', which look "
                         "identical in the funnel.")
    args = ap.parse_args()

    if len(args.payload) > 1:
        for path in args.payload:
            print("=" * 72)
            inspect(path, args)
            print()
        return 0
    return inspect(args.payload[0], args)


# Anything whose NAME suggests it prices an entry. Deliberately generous: a
# false positive costs one line of output and a false negative costs the whole
# reason for looking.
PAYOUT_HINTS = ("payout", "multiplier", "multip", "odds", "price", "juice",
                "vig", "return", "flex", "power", "boost", "demon", "goblin",
                "discount", "adjusted")


def report_payout_fields(payload, show):
    """Every field a projection carries, and which of them look like a price.

    scrape_props.py keeps nine fields per projection and discards the rest, so
    a payout sitting in the payload would never reach props.json and nobody
    would know. The parlay view's multiplier table is currently a published
    default that cannot be verified from any feed -- if the provider ships the
    number, it should be read rather than assumed, and this is what says whether
    it does.
    """
    seen = collections.Counter()
    examples = {}
    for item in payload.get("data") or []:
        if item.get("type") != "projection":
            continue
        for key, value in (item.get("attributes") or {}).items():
            seen[key] += 1
            if key not in examples and value is not None:
                examples[key] = value
        for key in (item.get("relationships") or {}):
            seen[f"relationships.{key}"] += 1
    if not seen:
        print("  no projections in this payload, so nothing to say about payouts")
        return

    print(f"  {len(seen)} distinct field(s) across every projection:")
    for key, count in seen.most_common(show):
        sample = examples.get(key)
        shown = "" if sample is None else f"  e.g. {str(sample)[:48]!r}"
        print(f"    {key:34s} on {count:5d}{shown}")

    hits = [k for k in seen if any(h in k.lower() for h in PAYOUT_HINTS)]
    print()
    if hits:
        print(f"  PAYOUT-SHAPED FIELD(S): {sorted(hits)}")
        print("  If one of these carries the entry multiplier, PAYOUT_MULTIPLIERS in")
        print("  src/app.jsx should be read from it instead of defaulted.")
    else:
        print("  No payout-shaped field on any projection. The multiplier table cannot be")
        print("  derived from this payload and has to be entered by hand -- which the")
        print("  Parlays tab lets you do, per entry size, kept in the browser.")

    # The included side too: a league or a board object sometimes carries the
    # payout structure rather than the projection.
    other = collections.Counter()
    for item in payload.get("included") or []:
        kind = item.get("type")
        for key in (item.get("attributes") or {}):
            if any(h in key.lower() for h in PAYOUT_HINTS):
                other[f"{kind}.{key}"] += 1
    if other:
        print(f"  and on the included side: {dict(other)}")


def inspect(path, args):
    with open(path) as f:
        payload = json.load(f)
    args = argparse.Namespace(payload=path, show=args.show, names=args.names,
                              payouts=getattr(args, "payouts", False))

    players, player_info = {}, {}
    for item in payload.get("included") or []:
        if item.get("type") in ("new_player", "player"):
            attrs = item.get("attributes") or {}
            players[str(item.get("id"))] = attrs.get("league")
            player_info[str(item.get("id"))] = {
                "name": attrs.get("display_name") or attrs.get("name"),
                "team": attrs.get("team"),
                "league": attrs.get("league"),
            }

    projections = [i for i in (payload.get("data") or [])
                   if i.get("type") == "projection"]
    print(f"{args.payload}: {Path(args.payload).stat().st_size:,} bytes, "
          f"{len(projections):,} projections, {len(players):,} players\n")

    if args.payouts:
        report_payout_fields(payload, args.show)
        print()

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
            if COMBO_PATTERN.search(str(label or "")):
                # Counted apart from unmodelled stats: a combo is two or
                # more PLAYERS in one line, so it is refused for a reason
                # that has nothing to do with which stats this app knows.
                buckets["COMBO -- more than one player, refused"].append((n, label))
            elif stat is None:
                buckets["not modelled by this app"].append((n, label))
            elif window is None:
                buckets["MODELLED STAT, no map window stated"].append((n, label))
            else:
                buckets["matched"].append((n, label))
        for bucket in ("matched", "MODELLED STAT, no map window stated",
                       "COMBO -- more than one player, refused",
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

    if args.names:
        print("\n  WHO IS ON THE BOARD — compare these against the roster files.")
        print("  All of them unmatched means either a broken roster file or a")
        print("  slate of teams this app does not track, and the names say which.")
        by_league = collections.defaultdict(set)
        for item in projections:
            rel = ((item.get("relationships") or {}).get("new_player") or {}).get("data") or {}
            info = player_info.get(str(rel.get("id"))) or {}
            if info.get("league"):
                by_league[info["league"]].add((info.get("name"), info.get("team")))
        for league, count in leagues.most_common():
            if not any(str(league).strip().lower() in {n.lower() for n in names}
                       for names in wanted.values()):
                continue
            rows = sorted(x for x in by_league.get(league, ()) if x[0])
            print(f"\n    {league}: {len(rows)} players")
            for name, team in rows[:args.names]:
                print(f"      {str(name)[:24]:24s}  {team}")
            if len(rows) > args.names:
                print(f"      ... and {len(rows) - args.names} more")

    print(f"\n  (this app models: {', '.join(modelled)})")
    return 0


if __name__ == "__main__":
    # Piping this into head or less is the obvious thing to do with it, and
    # the default Python handling of that turns a closed pipe into a
    # traceback that looks like the tool failed.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    sys.exit(main())

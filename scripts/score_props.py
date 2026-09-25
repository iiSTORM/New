#!/usr/bin/env python3
"""
Grades every posted line in props_history.jsonl against what actually
happened, and reports how the market did.

WHY THIS EXISTS
---------------
The app backtests its projections against real results and reports the
average error in kills. That measures the model against the truth, which is
a different and much easier question than measuring it against a PRICE. A
model can sit closer to the truth than any human guess and still lose money
every week, because the line it is betting into is already close to the
truth and is charging vig for the privilege.

So this grades the other side first: for every line that was ever posted,
what did the player actually do over exactly that line's map window, and
did it land over or under. That produces the ground truth every later claim
has to rest on -- including "the projection was right and the line was
wrong", which cannot be asserted until "what the line did" is a measured
number rather than a feeling.

WHAT CAN AND CANNOT BE GRADED
-----------------------------
Every map window is its own question. A map-1 line, a maps-1-2 line and a
maps-1-3 line on the same series are three different bets that happen to
share a player, and each has to be settled against exactly the maps it
names. So the unit of truth here is per_game, a per-map breakdown: given
one, any window resolves by summing that many maps, and no window is
privileged over another.

A match without a per-map breakdown carries one series total instead, and
that total covers one specific window -- maps 1-2 for CS2 and Valorant by
construction, and whatever maps_counted records for LoL. Such a match can
settle a line over THAT window and nothing else. This is the older record
shape; matches gain their breakdown as they are re-scraped, so the bucket
shrinks on its own.

A window longer than the series actually ran is refused rather than
settled short. A maps-1-3 line on a series that ended 2-0 is a real case
and books differ on how they void it, so it is counted under its own
reason instead of being graded against a guess.

Anything ambiguous is refused and counted by reason, on the same principle
the matching uses: a wrong grade is worse than a missing one, because a
wrong grade becomes a claim.

    python scripts/score_props.py
    python scripts/score_props.py --history props_history.jsonl --json out.json
"""
import argparse
import collections
import json
import sys
from datetime import datetime, timezone

GAME_DATA = {
    "lol": "data.json",
    "cs2": "cs2_data.json",
    "valorant": "valorant_data.json",
}

# What a series total covers when the match carries no per-map breakdown
# and does not say. CS2 and Valorant fix their totals at maps 1-2 by
# construction; LoL records the real number in maps_counted, which is read
# in preference to this.
DEFAULT_TOTAL_WINDOW = 2

# CS2 records headshots; LoL and Valorant do not. A headshots line on
# those games therefore finds no value in the box score and is refused by
# the same path any missing player is -- there is no separate per-game
# gate here because the data's absence is the gate.
STAT_KEY = {"kills": "k", "deaths": "d", "assists": "a", "headshots": "hs"}


def utc_date(timestamp):
    """The calendar date a posted start time falls on, in UTC."""
    try:
        return datetime.fromisoformat(str(timestamp)).astimezone(timezone.utc).date()
    except (TypeError, ValueError):
        return None


def parse_time(timestamp):
    """A timestamp as an aware UTC datetime, or None.

    Aware on purpose: the provider writes an offset and bo3.gg writes
    +00:00, and subtracting a naive datetime from an aware one raises
    rather than comparing. A naive value is read as UTC, which is what
    both sources mean when they omit it.
    """
    try:
        when = datetime.fromisoformat(str(timestamp))
    except (TypeError, ValueError):
        return None
    return when.astimezone(timezone.utc) if when.tzinfo else when.replace(tzinfo=timezone.utc)


def load_history(path):
    records = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return None
    return records


def index_matches(regions):
    """{(team, date): [match, ...]} over every completed match."""
    index = collections.defaultdict(list)
    for region in (regions or {}).values():
        for match in region.get("past_matches") or []:
            date = None
            raw = match.get("date")
            if raw:
                try:
                    date = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
                except ValueError:
                    try:
                        date = datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
                    except ValueError:
                        date = None
            if date is None:
                continue
            for team in (match.get("teamA"), match.get("teamB")):
                if team:
                    index[(team, date)].append(match)
    return index


def total_window(match):
    """How many maps this match's series total sums over.

    Read off the match, never assumed. LoL records maps_counted, because
    its total follows the format -- a Bo3's runs through map 2 and a
    Bo5's through map 3. CS2 and Valorant record `games` instead, and
    "say so by omission" was wrong for CS2: 24 committed Bo1s carry a
    ONE-map total, and calling it a two-map total grades a maps 1-2 line
    against a single map. That is not a missing grade, it is a wrong one
    -- a guaranteed under on every player in the match -- which is the
    exact failure this file was written to avoid.
    """
    for field in ("maps_counted", "games"):
        recorded = match.get(field)
        if isinstance(recorded, int) and not isinstance(recorded, bool) and recorded > 0:
            return recorded
    return DEFAULT_TOTAL_WINDOW


def window_is_covered(match, maps):
    """Whether this stored match can settle a line over `maps` maps at
    all, before any particular player or stat is asked about.

    Two callers need this question at different depths. The grader goes
    on to look a player up and has distinct refusals to report. The CS2
    scraper only needs to know whether re-fetching a match it already
    holds would buy anything -- a match on record whose window the line
    names is NOT covered is exactly as ungradeable as no match at all,
    and treating it as settled is what left 202 map-1 lines stranded
    behind records the backfill kept skipping.

    Kept beside actual_over_window rather than reimplemented in the
    scraper, and pinned to it by a test, because two copies of "which
    windows does this record answer" is two copies that drift.
    """
    if not isinstance(maps, int) or maps <= 0:
        return False
    per_game = match.get("per_game")
    if isinstance(per_game, list) and per_game:
        return len(per_game) >= maps
    return maps == total_window(match)


def actual_over_window(match, team, player, stat, maps, game):
    """What the player actually did over exactly the line's maps.

    Returns (value, None) or (None, reason).
    """
    key = STAT_KEY.get(stat)
    if key is None:
        return None, "stat this app does not model"
    if not isinstance(maps, int) or maps <= 0:
        return None, "line does not name a map window"

    # The per-map breakdown first, because it answers every window and the
    # series total answers exactly one. A match that has both is graded
    # from the breakdown for the same reason: maps 1-3 and map 1 are
    # questions the total cannot be asked.
    per_game = match.get("per_game")
    if isinstance(per_game, list) and per_game:
        if len(per_game) < maps:
            # Settling a maps-1-3 line on a 2-0 sweep would invent the
            # third map's zero. Counted under its own reason so the size
            # of the bucket is visible rather than assumed.
            return None, f"series ran {len(per_game)} map(s), line was over {maps}"
        total = 0
        for game_stats in per_game[:maps]:
            entry = ((game_stats or {}).get(team) or {}).get(player)
            if not isinstance(entry, dict):
                return None, "player missing from a map's box score"
            if not isinstance(entry.get(key), int):
                return None, f"{stat} not recorded for this game"
            total += entry[key]
        return total, None

    # No breakdown: one total, covering one window. Refused rather than
    # approximated for any other -- grading a map-1 line against a two-map
    # total would manufacture a losing record out of nothing.
    covered = total_window(match)
    if maps != covered:
        return None, (f"{game} result is a maps 1-{covered} total with no per-map "
                      f"breakdown, line was over {maps}")

    entry = ((match.get("actual") or {}).get(team) or {}).get(player)
    if isinstance(entry, dict) and isinstance(entry.get(key), int):
        return entry[key], None
    # A player who is present but has no value for THIS stat is a
    # different problem from one who is absent, and lumping them together
    # sends whoever reads the refusal counts looking for a scraping gap
    # that is not there. A headshots line on a LoL match is the ordinary
    # case: nothing records headshots there.
    if isinstance(entry, dict):
        return None, f"{stat} not recorded for this game"
    return None, "player missing from the box score"


# How far a posted line's start time may sit from a match's own before
# they are not the same fixture. Providers and the source disagree by
# minutes over scheduled-versus-actual start; they do not disagree by
# hours, and two legs of a double-header are further apart than this.
DOUBLE_HEADER_TOLERANCE_HOURS = 4


def nearest_by_start_time(candidates, line_start):
    """The one match a posted line belongs to, or None if unsettled.

    None rather than a guess in every ambiguous case: no clock on the
    line, no clock on the results, nothing inside the tolerance, or two
    matches equally close. A wrong answer here does not look like an
    error downstream, it looks like a graded result.
    """
    want = parse_time(line_start)
    if want is None:
        return None
    timed = []
    for m in candidates:
        when = parse_time(m.get("start_time"))
        if when is not None:
            timed.append((abs((when - want).total_seconds()), m))
    if not timed:
        return None
    timed.sort(key=lambda pair: pair[0])
    if timed[0][0] > DOUBLE_HEADER_TOLERANCE_HOURS * 3600:
        return None
    if len(timed) > 1 and timed[1][0] - timed[0][0] < 60:
        return None  # two matches essentially equidistant: not settled
    return timed[0][1]


def grade(records, data_by_game):
    """Attach an outcome to every observation that can carry one."""
    graded, refused = [], collections.Counter()
    indexes = {g: index_matches(d.get("regions")) for g, d in data_by_game.items()}

    for rec in records:
        game = rec.get("game")
        index = indexes.get(game)
        if index is None:
            refused[f"no result data for {game}"] += 1
            continue
        date = utc_date(rec.get("start_time"))
        if date is None:
            refused["unreadable start time"] += 1
            continue

        candidates = index.get((rec.get("team"), date)) or []
        if not candidates:
            # The overwhelmingly common case early on: the match simply has
            # not been played or scraped yet.
            refused["no completed match on that date"] += 1
            continue
        if len(candidates) > 1:
            # Teams do play twice in a day in CS2 tournaments. A calendar
            # date cannot say which match a line belonged to -- but a
            # CLOCK can, and both sides carry one now: the posted line
            # has always had start_time, and the scraper stores the
            # match's full timestamp rather than truncating it to a day.
            #
            # Still refuses when the times cannot settle it, because
            # picking one would be a coin flip recorded as a result.
            match = nearest_by_start_time(candidates, rec.get("start_time"))
            if match is None:
                refused["team played more than once that day"] += 1
                continue
        else:
            match = candidates[0]
        value, reason = actual_over_window(
            match, rec.get("team"), rec.get("player"),
            rec.get("stat"), rec.get("maps"), game)
        if value is None:
            refused[reason] += 1
            continue

        line = rec.get("line")
        if not isinstance(line, (int, float)):
            refused["line is not a number"] += 1
            continue

        graded.append({
            **rec,
            "actual": value,
            "margin": round(value - line, 2),
            # A push is impossible on a half-point line and the provider
            # posts those almost exclusively, but a whole number does turn
            # up and silently calling it a loss would understate the book.
            "result": "over" if value > line else "under" if value < line else "push",
            "match_date": str(match.get("date")),
            "opponent": match.get("teamB") if match.get("teamA") == rec.get("team") else match.get("teamA"),
        })
    return graded, refused


def summarise(graded):
    """Per game and overall, with the numbers stated as counts not rates
    where the sample is too thin for a rate to mean anything."""
    out = {}
    by_game = collections.defaultdict(list)
    for row in graded:
        by_game[row["game"]].append(row)
    by_game["all"] = list(graded)

    for game, rows in sorted(by_game.items()):
        decided = [r for r in rows if r["result"] != "push"]
        overs = sum(1 for r in decided if r["result"] == "over")
        margins = [r["margin"] for r in rows]
        out[game] = {
            "graded": len(rows),
            "pushes": len(rows) - len(decided),
            "over": overs,
            "under": len(decided) - overs,
            "over_rate": round(overs / len(decided), 4) if decided else None,
            "mean_margin": round(sum(margins) / len(margins), 3) if margins else None,
            "median_margin": round(sorted(margins)[len(margins) // 2], 3) if margins else None,
        }
    return out


def window_label(maps):
    return "map 1" if maps == 1 else f"maps 1-{maps}"


def summarise_by_window(graded):
    """The same record, split by the window each line was posted over.

    Kept separate from summarise() rather than folded into it because a
    map-1 line and a maps-1-2 line are not the same bet and pooling them
    hides exactly the thing worth knowing: whether the model's edge is
    real on one window and imaginary on another. Sorted by game then
    window so the table reads in map order.
    """
    out = {}
    by_key = collections.defaultdict(list)
    for row in graded:
        maps = row.get("maps")
        if isinstance(maps, int) and maps > 0:
            by_key[(row["game"], maps)].append(row)
    for (game, maps), rows in sorted(by_key.items()):
        decided = [r for r in rows if r["result"] != "push"]
        overs = sum(1 for r in decided if r["result"] == "over")
        margins = [r["margin"] for r in rows]
        out[f"{game} {window_label(maps)}"] = {
            "game": game, "maps": maps,
            "graded": len(rows),
            "pushes": len(rows) - len(decided),
            "over": overs,
            "under": len(decided) - overs,
            "over_rate": round(overs / len(decided), 4) if decided else None,
            "mean_margin": round(sum(margins) / len(margins), 3) if margins else None,
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", default="props_history.jsonl")
    ap.add_argument("--json", dest="json_out",
                    help="write the graded rows and summary here")
    ap.add_argument("--min-sample", type=int, default=30,
                    help="below this many graded lines, rates are withheld "
                         "rather than printed (default 30)")
    args = ap.parse_args()

    records = load_history(args.history)
    if records is None:
        print(f"! no history at {args.history}. It is written by "
              f"scrape_props.py on every real run — see the README.",
              file=sys.stderr)
        return 1
    if not records:
        print(f"{args.history} is empty: no boards have been recorded yet.")
        return 0

    data_by_game = {}
    for game, path in GAME_DATA.items():
        try:
            with open(path) as f:
                data_by_game[game] = json.load(f)
        except (OSError, ValueError):
            print(f"  ! {path} unreadable — {game} lines cannot be graded",
                  file=sys.stderr)

    graded, refused = grade(records, data_by_game)
    print(f"{len(records)} observation(s) on file, {len(graded)} gradeable\n")

    if refused:
        print("NOT GRADED")
        for reason, count in refused.most_common():
            print(f"  {count:5d}  {reason}")
        print()

    if not graded:
        print("Nothing to report yet. The usual reason is simply that the "
              "matches these lines belong to have not been played and "
              "scraped yet; grade again tomorrow.")
        return 0

    summary = summarise(graded)
    print("HOW THE POSTED LINES LANDED")
    print("  graded  over  under  push   over rate   mean margin   game")
    for game, s in summary.items():
        rate = "        —" if s["graded"] < args.min_sample or s["over_rate"] is None \
            else f"{s['over_rate'] * 100:8.1f}%"
        print(f"  {s['graded']:6d}  {s['over']:4d}  {s['under']:5d}  {s['pushes']:4d}  "
              f"{rate}  {s['mean_margin']:11.2f}   {game}")

    by_window = summarise_by_window(graded)
    if by_window:
        print("\nBY MAP WINDOW")
        print("  graded  over  under  push   over rate   mean margin   window")
        for label, s in by_window.items():
            rate = "        —" if s["graded"] < args.min_sample or s["over_rate"] is None \
                else f"{s['over_rate'] * 100:8.1f}%"
            print(f"  {s['graded']:6d}  {s['over']:4d}  {s['under']:5d}  {s['pushes']:4d}  "
                  f"{rate}  {s['mean_margin']:11.2f}   {label}")

    thin = [g for g, s in summary.items() if s["graded"] < args.min_sample]
    if thin:
        print(f"\n  Rates withheld for {', '.join(thin)}: fewer than "
              f"{args.min_sample} graded lines, where a rate is noise wearing "
              f"a decimal point.")
    print("\n  A mean margin near zero is a market doing its job. This says "
          "nothing yet about\n  whether the MODEL beats it — that needs the "
          "projection as it stood when the line\n  was posted, which is the "
          "next piece.")

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump({"summary": summary, "by_window": by_window,
                       "graded": graded, "refused": dict(refused)}, f, indent=2)
        print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
LoL carries per_game, a per-map breakdown, so a line over maps 1-3 or over
map 1 resolves exactly by summing the maps the line names.

CS2 and Valorant store one total per series covering exactly maps 1-2,
because their scrapers deliberately collect the first two maps and no more.
That is precisely the window those providers post, so their lines grade
exactly -- and a line over any OTHER window cannot be graded at all rather
than being graded against the wrong maps.

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

# The windows each game's stored results can actually answer.
#   lol      — per_game is a list of maps, so any prefix window resolves.
#   cs2/val  — one series total covering exactly maps 1-2, and nothing finer.
GRADEABLE_WINDOWS = {"lol": None, "cs2": {2}, "valorant": {2}}

STAT_KEY = {"kills": "k", "deaths": "d", "assists": "a"}


def utc_date(timestamp):
    """The calendar date a posted start time falls on, in UTC."""
    try:
        return datetime.fromisoformat(str(timestamp)).astimezone(timezone.utc).date()
    except (TypeError, ValueError):
        return None


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


def actual_over_window(match, team, player, stat, maps, game):
    """What the player actually did over exactly the line's maps.

    Returns (value, None) or (None, reason).
    """
    key = STAT_KEY.get(stat)
    if key is None:
        return None, "stat this app does not model"

    allowed = GRADEABLE_WINDOWS.get(game)
    if allowed is not None and maps not in allowed:
        # Refused rather than approximated: grading a map-1 line against a
        # two-map total would manufacture a losing record out of nothing.
        return None, f"{game} results cover maps 1-2 only, line was over {maps}"

    per_game = match.get("per_game")
    if isinstance(per_game, list):
        if len(per_game) < maps:
            return None, f"series ran {len(per_game)} map(s), line was over {maps}"
        total = 0
        for game_stats in per_game[:maps]:
            entry = ((game_stats or {}).get(team) or {}).get(player)
            if not isinstance(entry, dict) or not isinstance(entry.get(key), int):
                return None, "player missing from a map's box score"
            total += entry[key]
        return total, None

    entry = ((match.get("actual") or {}).get(team) or {}).get(player)
    if isinstance(entry, dict) and isinstance(entry.get(key), int):
        return entry[key], None
    return None, "player missing from the box score"


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
            # Teams do play twice in a day in CS2 tournaments. Which match a
            # line belonged to is not recoverable from a calendar date, and
            # picking one would be a coin flip recorded as a result.
            refused["team played more than once that day"] += 1
            continue

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
            json.dump({"summary": summary, "graded": graded,
                       "refused": dict(refused)}, f, indent=2)
        print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

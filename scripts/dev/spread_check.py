#!/usr/bin/env python3
"""Is the model still using the signal that exists, or has it flattened?

The defect this exists to catch cannot be seen in MAE, and MAE is the only
thing the weight search has ever measured. Shrinking a projection toward the
league mean ALWAYS lowers absolute error when the signal is noisy -- that is
what shrinkage is for -- so a search that minimises MAE will happily choose a
model that barely distinguishes players. On this data it did:

    cs2/kills   sd(projection) 0.906   real between-player spread 1.914
                => the model used 47% of the signal that demonstrably exists,
                   and 57% of every projection was the league average

That is fatal for prop selection specifically, because ranking props is
ENTIRELY a question of between-player spread. A model with none is a constant,
its "edge" is just the line's own deviation, and ranking by edge ranks the
market's information rather than ours. Measured on 1105 graded CS2 props, the
model picked UNDER on 64% of them while overs and unders split 50/50.

Three numbers, then:

  spread ratio    sd(projection) / sd(each player's own long-run mean).
                  The denominator is the spread that genuinely exists between
                  players; a model tracking it lands near 1.00. NOT sd(actual),
                  which includes game-to-game noise no model should reproduce.
  pick balance    of the lines ever posted, how often the projection sits above
                  the line. A market near 50/50 that the model reads as 36/64
                  is a directional bias, whatever its error.
  MAE             kept in view, because a spread fix that wrecks the point
                  estimate is not a fix.

The signature is stark: every stat shipping shrink 0 has a ratio of 0.91-1.01,
and every stat shipping shrink above 0 has 0.47-0.67.

    python scripts/dev/spread_check.py
    python scripts/dev/spread_check.py --game cs2 --stat kills --set shrink=4
    python scripts/dev/spread_check.py --json ratios.json
"""
import argparse
import collections
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import optimize_weights as ow

SOURCES = {"lol": "data.json", "valorant": "valorant_data.json", "cs2": "cs2_data.json"}

# A player needs a real sample before their own mean is an estimate of
# anything; below this they contribute noise to the denominator instead.
MIN_GAMES_FOR_MEAN = 8

# The floor tests/test_model_spread.py holds the shipped weights to. Not 1.0:
# some shrinkage toward a prior is legitimate for a thin sample, and the point
# is to catch a model that has given up half its signal, not to forbid any
# regularisation at all.
MIN_SPREAD_RATIO = 0.55


def measure(game, stat, override=None, data=None):
    """{spread ratio, sds, MAE, n} for one game and stat under these weights."""
    weights = ow.SHIPPED_WEIGHTS.get(game, {}).get(stat)
    if not weights:
        return None
    weights = dict(weights)
    weights.update(override or {})
    if data is None:
        data = ow.load_region_data(SOURCES[game])
    if not data:
        return None

    key = ow.STAT_TYPES[stat]["key"]
    predictions, actuals = [], []
    per_player = collections.defaultdict(list)
    for region in data.values():
        teams = region.get("teams") or {}
        past = region.get("past_matches") or []
        if not teams or not past:
            continue
        for match in past:
            # Per MAP throughout, so a one-map and a three-map row are the
            # same measurement. A window mixed into the spread would show up
            # as signal the model does not have.
            maps = ow.maps_counted_for(match)
            for side in ("teamA", "teamB"):
                team = match[side]
                opponent = match["teamB"] if side == "teamA" else match["teamA"]
                if team not in teams:
                    continue
                for player in teams[team]["players"]:
                    actual = ow.get_actual_stat(match, team, player["name"], key)
                    if actual is None or actual == "unavailable":
                        continue
                    predicted, prior = ow.project_point_in_time(
                        past, teams, player, team, opponent, maps, weights,
                        match["date"], stat, match.get("patch"))
                    if predicted is None or (prior == 0 and not player.get("hist")):
                        continue
                    predictions.append(predicted / maps)
                    actuals.append(actual / maps)
                    per_player[(team, player["name"])].append(actual / maps)

    if len(predictions) < 200:
        return None
    means = [statistics.mean(v) for v in per_player.values()
             if len(v) >= MIN_GAMES_FOR_MEAN]
    if len(means) < 10:
        return None
    sd_predicted = statistics.stdev(predictions)
    sd_between = statistics.stdev(means)
    return {
        "n": len(predictions),
        "players": len(means),
        "sd_predicted": sd_predicted,
        "sd_between": sd_between,
        "sd_actual": statistics.stdev(actuals),
        "ratio": sd_predicted / sd_between if sd_between else None,
        "mae": statistics.mean(abs(p - a) for p, a in zip(predictions, actuals)),
        "shrink": weights.get("shrink"),
        "recencyHalfLife": weights.get("recencyHalfLife"),
    }


def pick_balance(game, stat, override=None, results_path="props_results.json"):
    """How often the projection sits above a line that was actually posted.

    None when the provider has never posted this stat, which is the honest
    answer and covers cs2/deaths, cs2/assists and valorant/assists entirely.
    """
    try:
        with open(results_path) as f:
            graded = json.load(f)["graded"]
    except (FileNotFoundError, KeyError):
        return None
    rows = [r for r in graded if r.get("game") == game and r.get("stat") == stat
            and r.get("result") != "push"]
    if len(rows) < 30:
        return None

    weights = ow.SHIPPED_WEIGHTS.get(game, {}).get(stat)
    if not weights:
        return None
    weights = dict(weights)
    weights.update(override or {})
    data = ow.load_region_data(SOURCES[game])
    if not data:
        return None

    index = {}
    for region in data.values():
        for match in region.get("past_matches") or ():
            for side in ("teamA", "teamB"):
                team = match[side]
                opponent = match["teamB"] if side == "teamA" else match["teamA"]
                for name in (match.get("actual") or {}).get(team) or {}:
                    index[(match.get("date"), team.lower(), name.lower())] = \
                        (match, team, opponent)
    teams_by_region = {rk: rd.get("teams") or {} for rk, rd in data.items()}

    above = hit = total = 0
    for row in rows:
        maps = row.get("maps")
        if not isinstance(maps, int):
            continue
        found = index.get((row.get("match_date"), str(row.get("team")).lower(),
                           str(row.get("player")).lower()))
        if not found:
            continue
        match, team, opponent = found
        region = next((rk for rk, t in teams_by_region.items() if team in t), None)
        if region is None:
            continue
        player = next((p for p in teams_by_region[region][team]["players"]
                       if p["name"].lower() == str(row["player"]).lower()), None)
        if player is None:
            continue
        predicted, _ = ow.project_point_in_time(
            data[region].get("past_matches") or [], teams_by_region[region], player,
            team, opponent, maps, weights, match["date"], stat, match.get("patch"))
        if predicted is None or abs(predicted - row["line"]) < 1e-9:
            continue
        total += 1
        over = predicted > row["line"]
        above += 1 if over else 0
        hit += 1 if over == (row["result"] == "over") else 0
    if total < 30:
        return None
    return {"lines": total, "picks_over": above / total, "picks_right": hit / total,
            "market_over": sum(1 for r in rows if r["result"] == "over") / len(rows)}


def parse_overrides(pairs):
    out = {}
    for pair in pairs or ():
        if "=" not in pair:
            raise SystemExit(f"--set wants name=value, got {pair!r}")
        name, value = pair.split("=", 1)
        out[name.strip()] = float(value)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", choices=sorted(SOURCES))
    ap.add_argument("--stat")
    ap.add_argument("--set", action="append", metavar="NAME=VALUE",
                    help="override a weight, e.g. --set shrink=4")
    ap.add_argument("--json", help="write the measured ratios here")
    ap.add_argument("--no-market", action="store_true",
                    help="skip the pick-balance pass over graded props")
    args = ap.parse_args(argv)

    override = parse_overrides(args.set)
    games = [args.game] if args.game else sorted(SOURCES)
    fitted = {}
    print(f"{'game/stat':18s} {'shrink':>6} {'hl':>4} {'sd(proj)':>9} {'real sd':>8} "
          f"{'ratio':>6} {'MAE/map':>8}   market")
    for game in games:
        data = ow.load_region_data(SOURCES[game])
        if not data:
            continue
        for stat in ow.STAT_TYPES:
            if args.stat and stat != args.stat:
                continue
            if not ow.stat_applies_to(stat, game):
                continue
            got = measure(game, stat, override, data)
            if not got:
                continue
            fitted.setdefault(game, {})[stat] = got
            flag = "" if got["ratio"] >= MIN_SPREAD_RATIO else "  << FLAT"
            market = ""
            if not args.no_market:
                balance = pick_balance(game, stat, override)
                market = ("no lines ever posted" if balance is None else
                          f"{balance['lines']} lines, projects over "
                          f"{balance['picks_over']:.0%} vs market {balance['market_over']:.0%}, "
                          f"picks right {balance['picks_right']:.0%}")
                if balance:
                    fitted[game][stat]["market"] = balance
            print(f"{game+'/'+stat:18s} {got['shrink']:>6} {got['recencyHalfLife']:>4} "
                  f"{got['sd_predicted']:>9.3f} {got['sd_between']:>8.3f} "
                  f"{got['ratio']:>6.2f} {got['mae']:>8.4f}   {market}{flag}")

    flat = [(g, s) for g, stats in fitted.items() for s, v in stats.items()
            if v["ratio"] < MIN_SPREAD_RATIO]
    print()
    if flat:
        print(f"{len(flat)} stat(s) below the {MIN_SPREAD_RATIO:.2f} spread floor: "
              f"{', '.join(f'{g}/{s}' for g, s in flat)}")
    else:
        print(f"every stat is at or above the {MIN_SPREAD_RATIO:.2f} spread floor")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(fitted, f, indent=2)
            f.write("\n")
        print(f"Wrote {args.json}")
    return 1 if flat else 0


if __name__ == "__main__":
    sys.exit(main())

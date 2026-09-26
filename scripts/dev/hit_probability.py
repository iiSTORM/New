#!/usr/bin/env python3
"""Can this model say how LIKELY a prop is to hit, not just which way?

Ranking the board by edge size puts the least-evidenced rows on top, because
a model with little to go on produces wilder numbers and wilder numbers are
bigger disagreements with the line. adjustEdge already shrinks for that. But a
shrunken edge is still a DISTANCE, and "how far from the line" is not "how
likely to clear it": +4 kills on a player who swings 12 either way is a
coin flip, and +2 on a metronome is close to a lock.

What turns a projection into a probability is the spread of the model's own
errors. So this measures that spread -- point-in-time, out of sample, per game
and stat and evidence bucket -- and then asks the only question that matters:
when the resulting number says 70%, do 70% of them land?

    python scripts/dev/hit_probability.py                # everything
    python scripts/dev/hit_probability.py --game cs2

Nothing here is shipped by running it. It prints the residual spread table
that src/app.jsx carries, and the calibration that says whether carrying it
is honest.
"""
import argparse
import bisect
import collections
import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import optimize_weights as ow
import evidence_vs_accuracy as ev
import edge_realization as er

SOURCES = {"lol": "data.json", "valorant": "valorant_data.json", "cs2": "cs2_data.json"}

# The same buckets edge_realization uses, so a row's evidence tier means one
# thing across the whole measurement apparatus.
BUCKETS = er.BUCKETS


def bucket_of(evidence):
    for lo, hi in BUCKETS:
        if lo <= evidence < hi:
            return (lo, hi)
    return BUCKETS[-1]


def residuals_by_bucket(rows):
    """{bucket: [actual - predicted]} over point-in-time rows."""
    out = {}
    for evidence, predicted, actual in rows:
        out.setdefault(bucket_of(evidence), []).append(actual - predicted)
    return out


def spread(residuals):
    """The residual scale, as a robust standard deviation.

    1.4826 * MAD rather than the sample sd: a single 40-kill map against a
    projection of 18 moves an sd a long way and a MAD barely at all, and the
    tail of this distribution is exactly where a stdev would be fitted to the
    outliers rather than to the body it has to describe.
    """
    if len(residuals) < 20:
        return None
    middle = statistics.median(residuals)
    mad = statistics.median([abs(r - middle) for r in residuals])
    return 1.4826 * mad if mad > 0 else None


def bias(residuals):
    """The median residual: how far the model sits from the truth, if it does."""
    return statistics.median(residuals) if residuals else 0.0


def normal_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def p_over(projection, line, scale, shift=0.0):
    """P(actual > line) for a projection with this residual scale.

    A normal on the residual, not on the count. The count is a bounded integer
    and its own distribution is not normal, but what this needs is the spread
    of the model's ERROR, which pools thousands of players and rounds and is
    much closer to one. empirical_p below is the version that assumes nothing,
    and the two are compared in the report.
    """
    if scale is None or scale <= 0:
        return None
    return 1.0 - normal_cdf((line - (projection + shift)) / scale)


def empirical_p(projection, line, residuals):
    """P(actual > line) read straight off the residual sample.

    No distributional assumption at all: count how often a residual of the
    observed size would have carried this projection past the line.
    """
    if len(residuals) < 20:
        return None
    need = line - projection
    beat = sum(1 for r in residuals if r > need)
    return beat / len(residuals)


def brier(pairs):
    """Mean squared error of a probability against its outcome.

    Lower is better; 0.25 is what you get by saying 50% to everything, so a
    number at or above that is a probability worth nothing.
    """
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs) if pairs else None


def calibration_table(pairs, edges=(0.5, 0.55, 0.6, 0.65, 0.7, 0.8, 1.01)):
    """[(lo, hi, n, mean predicted, observed rate)] over confidence bands."""
    out = []
    lo = edges[0]
    for hi in edges[1:]:
        band = [(p, o) for p, o in pairs if lo <= p < hi]
        if band:
            out.append((lo, hi, len(band),
                        sum(p for p, _ in band) / len(band),
                        sum(o for _, o in band) / len(band)))
        lo = hi
    return out


def wilson(hits, n, z=1.96):
    """A proportion's confidence interval, so a band of 9 rows cannot read as
    a measurement."""
    if not n:
        return (0.0, 1.0)
    phat = hits / n
    denom = 1 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    margin = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def report_game(game, args):
    data = ow.load_region_data(SOURCES[game])
    if not data:
        print(f"  {game}: no data file")
        return {}
    table = {}
    for stat in ow.STAT_TYPES:
        if not ow.stat_applies_to(stat, game):
            continue
        weights = ow.SHIPPED_WEIGHTS.get(game, {}).get(stat)
        if not weights:
            continue
        rows = er.rows_for(data, stat, weights)
        if not rows:
            continue
        by_bucket = residuals_by_bucket(rows)
        pooled = [r for rs in by_bucket.values() for r in rs]
        print(f"\n  {game}/{stat}  n={len(rows)}  pooled spread {spread(pooled)}"
              f"  median residual {bias(pooled):+.2f}")
        print(f"    {'evidence':>12}  {'n':>5}  {'spread':>7}  {'median':>7}  "
              f"{'|resid| p50':>11}  {'p90':>6}")
        entry = {}
        for lo, hi in BUCKETS:
            residuals = by_bucket.get((lo, hi)) or []
            if not residuals:
                continue
            s = spread(residuals)
            absr = sorted(abs(r) for r in residuals)
            label = f"{lo}-{'inf' if hi > 10**5 else hi}"
            print(f"    {label:>12}  {len(residuals):>5}  "
                  f"{('%.2f' % s) if s else '   -':>7}  {bias(residuals):>+7.2f}  "
                  f"{absr[len(absr)//2]:>11.1f}  {absr[int(len(absr)*0.9)]:>6.1f}")
            if s:
                entry[label] = {"n": len(residuals), "scale": round(s, 3),
                                "shift": round(bias(residuals), 3)}
        # Does the spread actually widen as evidence thins? If it does not,
        # bucketing by evidence buys nothing and one pooled number is honest.
        scales = [(lo, entry[f"{lo}-{'inf' if hi > 10**5 else hi}"]["scale"])
                  for lo, hi in BUCKETS
                  if f"{lo}-{'inf' if hi > 10**5 else hi}" in entry]
        if len(scales) > 1:
            thin, thick = scales[0][1], scales[-1][1]
            print(f"    thinnest bucket {thin:.2f} vs thickest {thick:.2f}  "
                  f"({'widens as evidence thins' if thin > thick else 'NO widening — bucketing buys nothing'})")
        table[stat] = entry
    return table


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", choices=sorted(SOURCES) + ["all"], default="all")
    ap.add_argument("--json", help="write the fitted table here")
    args = ap.parse_args(argv)

    games = sorted(SOURCES) if args.game == "all" else [args.game]
    fitted = {}
    for game in games:
        print(f"\n=== {game} " + "=" * 54)
        fitted[game] = report_game(game, args)

    if args.json:
        with open(args.json, "w") as f:
            json.dump(fitted, f, indent=2)
            f.write("\n")
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ============================================================
# The only test that matters: replay the graded props
# ============================================================

def index_matches(region_data):
    """{(date, lowered team, lowered player): (match, team, opponent)}."""
    index = {}
    for rd in region_data.values():
        for match in rd.get("past_matches") or ():
            for side in ("teamA", "teamB"):
                team = match[side]
                opp = match["teamB"] if side == "teamA" else match["teamA"]
                actual = (match.get("actual") or {}).get(team) or {}
                for player in actual:
                    index[(match.get("date"), team.lower(), player.lower())] = \
                        (match, team, opp)
    return index


def replay_graded(results_path, games):
    """(game, stat, maps, p_pick, hit) for every graded prop we can re-project.

    p_pick is the model's probability for the side it PICKED, so 0.5 is a
    coin flip and anything below it means the model bet against itself.
    """
    with open(results_path) as f:
        graded = json.load(f)["graded"]
    out, skipped = [], collections.Counter()
    for game in games:
        data = ow.load_region_data(SOURCES[game])
        if not data:
            continue
        index = index_matches(data)
        teams_by_region = {rk: rd.get("teams") or {} for rk, rd in data.items()}
        scales = fit_scales(data, game)
        for row in graded:
            if row.get("game") != game or row.get("result") == "push":
                skipped["other game or push"] += 1
                continue
            stat, maps = row.get("stat"), row.get("maps")
            weights = ow.SHIPPED_WEIGHTS.get(game, {}).get(stat)
            if not weights or not isinstance(maps, int):
                skipped["no weights or window"] += 1
                continue
            found = index.get((row.get("match_date"), str(row.get("team")).lower(),
                               str(row.get("player")).lower()))
            if not found:
                skipped["match not found"] += 1
                continue
            match, team, opp = found
            region = next((rk for rk, teams in teams_by_region.items() if team in teams), None)
            if region is None:
                skipped["team not rostered"] += 1
                continue
            player = next((p for p in teams_by_region[region][team]["players"]
                           if p["name"].lower() == str(row["player"]).lower()), None)
            if player is None:
                skipped["player not rostered"] += 1
                continue
            past = data[region].get("past_matches") or []
            predicted, prior = ow.project_point_in_time(
                past, teams_by_region[region], player, team, opp, maps, weights,
                match["date"], stat, match.get("patch"))
            if predicted is None:
                skipped["no projection"] += 1
                continue
            scale, shift = scale_for(scales, stat, maps)
            if scale is None:
                skipped["no fitted scale"] += 1
                continue
            p = p_over(predicted, row["line"], scale, shift)
            if p is None:
                skipped["no probability"] += 1
                continue
            went_over = row["result"] == "over"
            # The side the model picked, and whether that side landed.
            if p >= 0.5:
                out.append((game, stat, maps, p, 1 if went_over else 0))
            else:
                out.append((game, stat, maps, 1.0 - p, 0 if went_over else 1))
    return out, skipped


EXPONENT = 0.65  # how the residual scale grows with the map window; see below
MIN_WINDOW_ROWS = 40


def fit_scales(region_data, game):
    """{(stat, maps): (scale, shift)} measured per window, plus a maps=1 anchor.

    Measured per window rather than assumed, because the scale does not grow
    linearly with the window and it does not grow with its square root either:
    over lol/kills it goes 1.84 / 2.89 / 3.69 for one, two and three maps,
    where linear would predict 1.84 / 3.68 / 5.52 and sqrt 1.84 / 2.60 / 3.19.
    Maps are positively correlated -- a team winning fast plays short ones --
    so the truth sits between, at an exponent of about 0.65 across the seven
    game/stat pairs where both ends are observed.
    """
    fitted = {}
    for stat in ow.STAT_TYPES:
        if not ow.stat_applies_to(stat, game):
            continue
        weights = ow.SHIPPED_WEIGHTS.get(game, {}).get(stat)
        if not weights:
            continue
        by_maps = collections.defaultdict(list)
        for rd in region_data.values():
            teams, past = rd.get("teams") or {}, rd.get("past_matches") or []
            if not teams or not past:
                continue
            for match in past:
                maps = ow.maps_counted_for(match)
                for side in ("teamA", "teamB"):
                    team = match[side]
                    opp = match["teamB"] if side == "teamA" else match["teamA"]
                    if team not in teams:
                        continue
                    for player in teams[team]["players"]:
                        actual = ow.get_actual_stat(
                            match, team, player["name"], ow.STAT_TYPES[stat]["key"])
                        if actual is None or actual == "unavailable":
                            continue
                        pred, prior = ow.project_point_in_time(
                            past, teams, player, team, opp, maps, weights,
                            match["date"], stat, match.get("patch"))
                        if pred is None or (prior == 0 and not player.get("hist")):
                            continue
                        by_maps[maps].append(actual - pred)
        for maps, residuals in by_maps.items():
            if len(residuals) < MIN_WINDOW_ROWS:
                continue
            s = spread(residuals)
            if s:
                fitted[(stat, maps)] = (s, bias(residuals))
    return fitted


def scale_for(fitted, stat, maps):
    """The window's own measured scale, or the nearest one stretched to it."""
    if (stat, maps) in fitted:
        return fitted[(stat, maps)]
    anchors = [(m, v) for (s, m), v in fitted.items() if s == stat]
    if not anchors:
        return (None, 0.0)
    near, (scale, shift) = min(anchors, key=lambda kv: abs(kv[0] - maps))
    return (scale * (maps / near) ** EXPONENT, shift * maps / near)

#!/usr/bin/env python3
"""Does the esports model's probability mean what it says? Measured, then chosen.

The build plan's rule for phase 4 is "backtest every change against
props_results.json and report calibration before shipping", so this is the thing
that decides whether propedge/esports.py ships and with which parameters.

    python scripts/dev/backtest_esports.py
    python scripts/dev/backtest_esports.py --json backtest.json

TWO FREE PARAMETERS, and neither gets a hand-picked value:

  shrink_maps  -- how much evidence buys how much of the disagreement with the
                  line. reference/esports_projections.py hard-codes a flat 0.5.
  prior_weight -- how much of the final probability comes from the graded
                  record's measured under-bias rather than the projection. The
                  prototype hard-codes a flat 0.5 of that too.

HOW IT AVOIDS FITTING TO ITS OWN ANSWER. Every prop is replayed point in time --
the projection for a 20 September match is built only from matches before it --
and the grid is then chosen on the EARLIER half of the record and reported on
the later half, which the choice never saw. A parameter that only looks good on
the half it was picked on is a parameter that has learned this record.

The projection itself is the expensive part and does not depend on either
parameter, so it is computed once per prop and the grid sweeps over the cached
result. That is what makes a real sweep affordable at all.

Calibration is scored by the same propedge.analytics.calibration the live report
uses, on one-leg slips: the backtest and the tracker must not be able to
disagree about what "off by five points" means.
"""
import argparse
import collections
import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from propedge import analytics, esports             # noqa: E402
from propedge.money import to_cents                 # noqa: E402
from propedge.slips import Leg, Slip                # noqa: E402

SHRINK_GRID = (0.0, 4.0, 8.0, 12.0, 20.0, 40.0)
PRIOR_GRID = (0.0, 0.15, 0.35, 0.5, 0.75)


def replay(results_path, root=".", min_maps=esports.MIN_MAPS):
    """One cached record per graded prop: the projection, point in time.

    Neither the shrink nor the prior weight enters here, which is the whole
    point -- this is the slow half and it is computed once.
    """
    graded = json.loads(open(results_path).read())["graded"]
    model = esports.EsportsModel.from_files(root, graded, min_maps=min_maps)
    cached, skipped = [], collections.Counter()
    for row in graded:
        if row.get("result") not in ("over", "under"):
            skipped["pushed or ungraded"] += 1
            continue
        game, stat, maps = row.get("game"), row.get("stat"), row.get("maps")
        line, when = row.get("line"), row.get("match_date")
        if game not in esports.GAMES or not isinstance(maps, int) or line is None:
            skipped["no window or line"] += 1
            continue
        mean = model.project_mean(game, row.get("player"), stat, maps,
                                 row.get("team"), row.get("opponent"), when)
        if mean is None:
            skipped["not on a roster, or no rate for the stat"] += 1
            continue
        mu, evidence = mean
        if evidence < min_maps:
            skipped[f"under {min_maps} maps of history"] += 1
            continue
        sigma = esports.residual_scale(game, stat, maps, model.scales, model.exponent)
        if not sigma:
            skipped["no residual scale for this window"] += 1
            continue
        cached.append({
            "game": game, "stat": stat, "maps": maps, "line": line, "mu": mu,
            "sigma": sigma, "evidence": evidence, "when": when,
            "odds_type": row.get("odds_type") or "standard",
            "player": row.get("player"), "team": row.get("team") or "",
            "opponent": row.get("opponent") or "", "result": row["result"],
        })
    cached.sort(key=lambda r: str(r["when"]))
    return cached, skipped, model.rates


def score(cached, rates, shrink_maps, prior_weight, prior_shrink_n):
    """Slips carrying the model's probability and what happened, for scoring.

    Each prop becomes a one-leg slip. One leg is not a placeable entry, but
    calibration is a question about legs and this way it is answered by exactly
    the function that will answer it on the live record.
    """
    slips = []
    for row in cached:
        shrunk = esports.shrink_toward_line(row["mu"], row["line"], row["evidence"],
                                           shrink_maps)
        split = esports.outcome_probabilities(shrunk, row["sigma"], row["line"])
        if split is None:
            continue
        p_over, p_push, p_under = split
        decided = p_over + p_under
        if decided <= 0:
            continue
        prior = esports.prior_under(rates, row["game"], row["stat"], row["maps"],
                                   row["odds_type"], prior_shrink_n)
        if prior and prior_weight:
            p_prior, _ = prior
            conditional = ((1 - prior_weight) * (p_under / decided)
                           + prior_weight * p_prior)
        else:
            conditional = p_under / decided
        # Scored on the side the model would actually pick, conditional on the
        # prop being decided -- which is what a bet on it is.
        side = "under" if conditional >= 0.5 else "over"
        claimed = conditional if side == "under" else 1 - conditional
        leg = Leg(sport=row["game"], player=row["player"], team=row["team"],
                  opponent=row["opponent"], stat=row["stat"], line=row["line"],
                  side=side, maps=row["maps"], odds_type=row["odds_type"],
                  model_prob=claimed,
                  result="won" if row["result"] == side else "lost")
        slips.append(Slip(mode="power", stake_cents=to_cents("1"), legs=[leg],
                          placed_at=str(row["when"])))
    return slips


def brier(slips):
    legs = [leg for slip in slips for leg in slip.legs]
    if not legs:
        return None
    return statistics.mean((leg.model_prob - (1 if leg.result == "won" else 0)) ** 2
                           for leg in legs)


def separation(slips):
    """Top fifth against bottom fifth by claimed probability, on matches."""
    legs = [(slip, leg) for slip in slips for leg in slip.legs]
    if len(legs) < 10:
        return None
    legs.sort(key=lambda pair: -pair[1].model_prob)
    fifth = max(1, len(legs) // 5)
    def rate(rows):
        by_match = collections.defaultdict(list)
        for slip, leg in rows:
            by_match[analytics._match_key(slip, leg)].append(leg.result == "won")
        per = [sum(1 for w in v if w) / len(v) for v in by_match.values()]
        return statistics.mean(per) if per else None
    return (rate(legs[:fifth]), rate(legs[-fifth:]), fifth)


def projection_within_cell(cached, shrink_maps, min_legs=30):
    """Does the PROJECTION rank outcomes once the cell's own bias is held fixed?

    The sharpest question phase 4 can be asked, and the one the headline
    separation cannot answer. A model whose probability comes half from a
    measured per-cell under-rate will rank props partly by which cell they are
    in, and cells differ -- Valorant maps 1-2 went under 71.6% of the time
    against CS2 kills' 53.1%. So the whole-model separation can be entirely the
    cell, with the player model contributing nothing.

    Splitting WITHIN each (game, stat, maps) cell removes that: the prior is a
    constant inside a cell, so any gap here is the projection earning its keep.
    Reported per cell with its sample size, because the pattern matters more
    than the average -- a weighted mean dragged positive by two small cells
    while the two largest are flat or negative is the shape of noise, not edge.
    """
    by_cell = collections.defaultdict(list)
    for row in cached:
        shrunk = esports.shrink_toward_line(row["mu"], row["line"], row["evidence"],
                                           shrink_maps)
        split = esports.outcome_probabilities(shrunk, row["sigma"], row["line"])
        if split is None:
            continue
        p_over, p_push, p_under = split
        decided = p_over + p_under
        if decided <= 0:
            continue
        conditional = p_under / decided          # projection only: no prior
        side = "under" if conditional >= 0.5 else "over"
        claimed = conditional if side == "under" else 1 - conditional
        by_cell[(row["game"], row["stat"], row["maps"])].append(
            (claimed, row["result"] == side))
    out = []
    for cell, rows in by_cell.items():
        if len(rows) < min_legs:
            continue
        rows = sorted(rows, key=lambda r: -r[0])
        cut = len(rows) // 2
        top = sum(1 for _, won in rows[:cut] if won) / cut
        bottom = sum(1 for _, won in rows[cut:] if won) / len(rows[cut:])
        out.append({"cell": cell, "n": len(rows), "top_half": top,
                    "bottom_half": bottom, "gap": top - bottom})
    out.sort(key=lambda row: -row["n"])
    return out


def evaluate(cached, rates, shrink_maps, prior_weight, prior_shrink_n):
    slips = score(cached, rates, shrink_maps, prior_weight, prior_shrink_n)
    error, legs, matches = analytics.calibration_error(slips)
    got = {"shrink_maps": shrink_maps, "prior_weight": prior_weight,
           "legs": legs, "matches": matches, "calibration_error": error,
           "brier": brier(slips), "bands": analytics.calibration(slips)}
    gap = separation(slips)
    if gap and gap[0] is not None and gap[1] is not None:
        got["top_fifth"], got["bottom_fifth"], got["fifth_n"] = gap
    return got


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default="props_results.json")
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", help="write the numbers here as well")
    ap.add_argument("--prior-shrink", type=float, default=esports.PRIOR_SHRINK_N,
                    dest="prior_shrink")
    args = ap.parse_args(argv)

    cached, skipped, rates = replay(args.results, args.root)
    if not cached:
        print(f"nothing could be replayed. skipped: {dict(skipped)}")
        return 1
    half = len(cached) // 2
    early, late = cached[:half], cached[half:]
    print(f"{len(cached)} graded prop(s) replayed point in time"
          + (f"; skipped {dict(skipped)}" if skipped else ""))
    print(f"chosen on the first {len(early)} ({early[0]['when']} to "
          f"{early[-1]['when']}), reported on the last {len(late)} "
          f"({late[0]['when']} to {late[-1]['when']})\n")

    print(f"  {'shrink':>7} {'prior':>6} {'legs':>5} {'cal err':>8} {'brier':>7}  "
          f"{'top/bottom fifth':>18}")
    fits = []
    for shrink_maps in SHRINK_GRID:
        for prior_weight in PRIOR_GRID:
            got = evaluate(early, rates, shrink_maps, prior_weight, args.prior_shrink)
            fits.append(got)
            error = "n/a" if got["calibration_error"] is None else \
                f"{got['calibration_error']:.1%}"
            gap = (f"{got['top_fifth']:.1%} / {got['bottom_fifth']:.1%}"
                   if "top_fifth" in got else "too thin")
            print(f"  {shrink_maps:>7.0f} {prior_weight:>6.2f} {got['legs']:>5} "
                  f"{error:>8} {got['brier']:>7.4f}  {gap:>18}")

    scorable = [f for f in fits if f["calibration_error"] is not None]
    if not scorable:
        print("\nno grid point had enough legs to judge calibration on")
        return 1
    best = min(scorable, key=lambda f: (f["calibration_error"], f["brier"]))
    print(f"\nchosen on the first half: shrink_maps={best['shrink_maps']:.0f}, "
          f"prior_weight={best['prior_weight']:.2f} "
          f"(calibration off by {best['calibration_error']:.1%})")

    held = evaluate(late, rates, best["shrink_maps"], best["prior_weight"],
                    args.prior_shrink)
    print(f"\nHELD-OUT HALF, which the choice never saw:")
    print(f"  {held['legs']} legs across {held['matches']} matches")
    if held["calibration_error"] is None:
        print("  too few legs to judge calibration")
    else:
        print(f"  calibration off by {held['calibration_error']:.1%}, "
              f"Brier {held['brier']:.4f} against 0.25 for answering 50% to "
              "everything")
    if "top_fifth" in held:
        print(f"  most confident fifth {held['top_fifth']:.1%} against the least "
              f"confident {held['bottom_fifth']:.1%} ({held['fifth_n']} legs a side, "
              "clustered on the match)")
    print(f"\n  {'band':>10} {'n':>5} {'matches':>8} {'claimed':>8} {'realised':>9}"
          f"  {'95%':>13}")
    for band in held["bands"]:
        print(f"  {band.lo:.0%}-{band.hi:.0%}".ljust(13)
              + f"{band.n:>5} {band.matches:>8} {band.claimed:>8.1%} "
              f"{band.realised:>9.1%}  "
              + f"{band.interval[0]:.0%}-{band.interval[1]:.0%}".rjust(14)
              + ("" if band.straddles_its_claim else "  <- off"))

    within = projection_within_cell(late, best["shrink_maps"])
    print(f"\nDOES THE PROJECTION ITSELF RANK ANYTHING? Split within each cell, so")
    print(f"the market bias is constant and only the player model can move this.")
    print(f"  {'cell':<30} {'n':>5} {'likes it':>9} {'does not':>9}  {'gap':>7}")
    for row in within:
        print(f"  {str(row['cell']):<30} {row['n']:>5} {row['top_half']:>9.1%} "
              f"{row['bottom_half']:>9.1%}  {row['gap']:>+6.1%}")
    if within:
        legs = sum(row["n"] for row in within)
        weighted = sum(row["n"] * row["gap"] for row in within) / legs
        biggest = within[0]
        print(f"  weighted mean {weighted:+.1%} over {legs} legs, but read the "
              "pattern not the mean:")
        print(f"  the largest cell ({biggest['n']} legs) is {biggest['gap']:+.1%}. "
              "Large cells flat or negative with")
        print("  small cells strongly positive is what noise looks like, not edge.")

    if args.json:
        dump = lambda f: {k: v for k, v in f.items() if k != "bands"}
        with open(args.json, "w") as out:
            json.dump({"grid": [dump(f) for f in fits], "chosen": dump(best),
                       "held_out": dump(held),
                       "projection_within_cell": [
                           {**row, "cell": list(row["cell"])} for row in within],
                       "bands": [{"lo": b.lo, "hi": b.hi, "n": b.n,
                                  "matches": b.matches, "claimed": b.claimed,
                                  "realised": b.realised} for b in held["bands"]]},
                      out, indent=2)
            out.write("\n")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

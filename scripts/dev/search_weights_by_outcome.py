#!/usr/bin/env python3
"""Search the model's weights against OUTCOMES, not against absolute error.

The weights this app ships were chosen by minimising mean absolute error, and
that is the wrong objective for a betting model. It has already cost the project
once -- CS2 kills shipped with a shrink of 8 against a median sample of 6, so
57% of every projection was the league average -- and the fix then was to
unshrink one parameter. This is the general form of the same problem.

WHY MAE IS WRONG, CONCRETELY. Pulling a projection toward the league mean always
lowers absolute error when the signal is noisy. That is what shrinkage is FOR.
But ranking props is entirely a question of between-player spread: with none, the
projection is a constant, the "edge" is just the line's own deviation from
average, and ranking by edge ranks the market's information rather than ours. So
a search that minimises MAE will flatten the model until it barely distinguishes
players and report an improvement the whole way. Look at what it chose: the
`opponent` weight is 0.0 on every CS2 and Valorant stat. The model does not know
who you are playing, because knowing spreads projections apart and that costs
MAE.

WHY LOG-LOSS IS RIGHT. It is a proper scoring rule, so it cannot be gamed by
shading probabilities, and it rewards discrimination as well as calibration --
answering "50%" to everything scores ln 2 = 0.6931 and anything with real signal
beats it. More to the point it is THE SAME OBJECTIVE AS THE STAKING: minimising
log-loss is maximising expected log growth, which is what Kelly maximises. The
model is now being scored by the thing the bankroll actually experiences.

HOW IT AVOIDS FITTING ITSELF. Every prop is replayed point in time, the weights
are searched walk-forward across chronological folds, and a change is adopted
only when a MAJORITY of folds prefer it -- the discipline this repo already uses
for weights. The residual scale is re-measured on each fold's own fit data for
each candidate weight set, because a weight set that spreads projections further
has a different error, and scoring it with the shipped scale would punish it for
exactly the property we are trying to buy.

    python scripts/dev/search_weights_by_outcome.py --game cs2 --stat kills
    python scripts/dev/search_weights_by_outcome.py --all --json weights.json
"""
import argparse
import collections
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import optimize_weights as ow                      # noqa: E402
from propedge import analytics, esports            # noqa: E402

SOURCES = {"cs2": "cs2_data.json", "lol": "data.json",
           "valorant": "valorant_data.json"}
#: Same grid the MAE search uses, so a difference in the answer is a difference
#: in the OBJECTIVE and not in where each was allowed to look.
GRID = {
    "history": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
    "opponent": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0],
    "kp": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0],
    "recencyHalfLife": [2, 3, 4, 5, 6, 8, 10, 14, 20],
    "patchDiscount": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0],
    "career": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 1.0],
    "share": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    "shrink": [0.0, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0],
}
ORDER = ["opponent", "share", "career", "history", "recencyHalfLife", "shrink",
         "patchDiscount", "kp"]
#: Answering "50%" to everything. Any model worth running beats it.
COIN_FLIP_LOSS = math.log(2)
FOLDS = 3
#: A change has to help on more folds than it hurts AND by more than this in
#: mean log-loss, which is about the size of the noise on a few hundred props.
MIN_IMPROVEMENT = 0.0015


def load(game):
    data = ow.load_region_data(SOURCES[game])
    index = {}
    for region, blob in (data or {}).items():
        for team, info in (blob.get("teams") or {}).items():
            for record in info.get("players") or []:
                index.setdefault(str(record.get("name", "")).lower(), []).append(
                    (region, team, record))
    return data, index


def cell_rows(graded, game, stat):
    out = []
    for row in graded:
        if row.get("game") != game or row.get("stat") != stat:
            continue
        if row.get("result") not in ("over", "under"):
            continue
        if (row.get("odds_type") or "standard") != "standard":
            continue
        if not isinstance(row.get("maps"), int) or row.get("line") is None:
            continue
        out.append(row)
    out.sort(key=lambda r: str(r.get("match_date")))
    return out


def project_all(rows, data, index, game, stat, weights):
    """[(row, projection)] point in time, for one weight set."""
    out = []
    for row in rows:
        found = index.get(str(row.get("player")).lower()) or []
        if len(found) > 1:
            stated = str(row.get("team") or "").strip().lower()
            found = [f for f in found if f[1].strip().lower() == stated] or found
        if not found:
            continue
        region, team, record = found[0]
        blob = data[region]
        mu, evidence = ow.project_point_in_time(
            blob.get("past_matches") or [], blob.get("teams") or {}, record,
            team, row.get("opponent") or "", row["maps"], weights,
            row.get("match_date"), stat, row.get("patch"))
        if mu is None:
            continue
        out.append((row, mu, evidence))
    return out


def residual_scale_from(projected):
    """1.4826 x median absolute residual: the model's own error, re-measured.

    Per weight set and per fold, because a weight set that spreads projections
    further HAS a different error, and scoring it against the shipped scale
    would punish it for the spread we are trying to buy.
    """
    residuals = [abs(row["actual"] - mu) for row, mu, _ in projected
                 if row.get("actual") is not None]
    if len(residuals) < 20:
        return None
    return 1.4826 * statistics.median(residuals) or None


def log_loss(projected, sigma):
    """Mean -log P(what actually happened). Lower is better; ln 2 is a coin."""
    if not sigma:
        return None
    total, n = 0.0, 0
    for row, mu, _ in projected:
        split = esports.outcome_probabilities(mu, sigma, row["line"])
        if split is None:
            continue
        p_over, p_push, p_under = split
        decided = p_over + p_under
        if decided <= 0:
            continue
        p = (p_over if row["result"] == "over" else p_under) / decided
        total += -math.log(min(max(p, 1e-6), 1 - 1e-6))
        n += 1
    return total / n if n else None


def diagnostics(projected, sigma):
    """Pick accuracy and top/bottom separation, clustered on the match."""
    picks = []
    for row, mu, _ in projected:
        split = esports.outcome_probabilities(mu, sigma, row["line"])
        if split is None:
            continue
        p_over, p_push, p_under = split
        decided = p_over + p_under
        if decided <= 0:
            continue
        side = "over" if p_over >= p_under else "under"
        claimed = max(p_over, p_under) / decided
        picks.append((claimed, row["result"] == side,
                      (row.get("match_date"),
                       tuple(sorted((str(row.get("team")),
                                     str(row.get("opponent") or "?")))))))
    if not picks:
        return {}
    def rate(rows):
        by_match = collections.defaultdict(list)
        for _, won, cluster in rows:
            by_match[cluster].append(won)
        per = [sum(1 for w in v if w) / len(v) for v in by_match.values()]
        return statistics.mean(per) if per else None
    picks.sort(key=lambda p: -p[0])
    fifth = max(1, len(picks) // 5)
    return {"n": len(picks), "accuracy": rate(picks),
            "top_fifth": rate(picks[:fifth]), "bottom_fifth": rate(picks[-fifth:]),
            "matches": len({c for _, _, c in picks})}


def folds_of(rows, folds=FOLDS):
    """Walk-forward: fit on everything before a cut, score on the next slice."""
    size = len(rows) // (folds + 1)
    if size < 30:
        return []
    out = []
    for i in range(1, folds + 1):
        out.append((rows[:size * i], rows[size * i:size * (i + 1)]))
    return out


def score(weights, splits, data, index, game, stat):
    """(mean held-out log-loss, per-fold losses). None where it cannot be scored."""
    losses = []
    for fit_rows, test_rows in splits:
        fitted = project_all(fit_rows, data, index, game, stat, weights)
        sigma = residual_scale_from(fitted)
        if not sigma:
            return (None, [])
        tested = project_all(test_rows, data, index, game, stat, weights)
        loss = log_loss(tested, sigma)
        if loss is None:
            return (None, [])
        losses.append(loss)
    return ((statistics.mean(losses), losses) if losses else (None, []))


def search(game, stat, graded, data, index, start=None, passes=2, report=print):
    rows = cell_rows(graded, game, stat)
    splits = folds_of(rows)
    if not splits:
        report(f"  {game} {stat}: only {len(rows)} graded props — too few to search")
        return None
    weights = dict(start or ow.SHIPPED_WEIGHTS[game][stat])
    baseline, per_fold = score(weights, splits, data, index, game, stat)
    if baseline is None:
        report(f"  {game} {stat}: cannot be scored")
        return None
    report(f"  {game} {stat}: {len(rows)} props, {len(splits)} folds; shipped "
           f"weights score {baseline:.4f} against {COIN_FLIP_LOSS:.4f} for a coin")
    best = baseline
    params = [p for p in ORDER if p in weights]
    for sweep in range(passes):
        moved = False
        for param in params:
            current = weights[param]
            for candidate in GRID.get(param, []):
                if candidate == weights[param]:
                    continue
                trial = dict(weights)
                trial[param] = candidate
                got, fold_losses = score(trial, splits, data, index, game, stat)
                if got is None:
                    continue
                better = sum(1 for a, b in zip(fold_losses, per_fold) if a < b)
                # Fold MAJORITY and a real margin: one fold liking a change is
                # how a search learns a month of weather.
                if got < best - MIN_IMPROVEMENT and better > len(per_fold) / 2:
                    best, current, per_fold = got, candidate, fold_losses
                    moved = True
            if current != weights[param]:
                report(f"    {param}: {weights[param]} -> {current}  "
                       f"(log-loss {best:.4f})")
                weights[param] = current
        if not moved:
            break
    return {"game": game, "stat": stat, "rows": len(rows), "folds": len(splits),
            "shipped": dict(ow.SHIPPED_WEIGHTS[game][stat]), "found": weights,
            "shipped_loss": baseline, "found_loss": best,
            "coin_flip": COIN_FLIP_LOSS}


def compare(result, graded, data, index):
    """Held-out diagnostics for the shipped weights against the found ones."""
    rows = cell_rows(graded, result["game"], result["stat"])
    splits = folds_of(rows)
    out = {}
    for name, weights in (("shipped", result["shipped"]), ("found", result["found"])):
        merged = collections.Counter()
        accuracy, tops, bottoms, n = [], [], [], 0
        for fit_rows, test_rows in splits:
            fitted = project_all(fit_rows, data, index, result["game"],
                                 result["stat"], weights)
            sigma = residual_scale_from(fitted)
            if not sigma:
                continue
            tested = project_all(test_rows, data, index, result["game"],
                                 result["stat"], weights)
            got = diagnostics(tested, sigma)
            if not got:
                continue
            accuracy.append(got["accuracy"])
            tops.append(got["top_fifth"])
            bottoms.append(got["bottom_fifth"])
            n += got["n"]
        out[name] = {
            "n": n,
            "accuracy": statistics.mean(accuracy) if accuracy else None,
            "top_fifth": statistics.mean([t for t in tops if t is not None])
                         if any(t is not None for t in tops) else None,
            "bottom_fifth": statistics.mean([b for b in bottoms if b is not None])
                            if any(b is not None for b in bottoms) else None,
        }
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", default="cs2", choices=sorted(SOURCES))
    ap.add_argument("--stat", default="kills")
    ap.add_argument("--all", action="store_true", help="every game and stat with data")
    ap.add_argument("--results", default="props_results.json")
    ap.add_argument("--passes", type=int, default=2)
    ap.add_argument("--json", help="write the found weights here")
    args = ap.parse_args(argv)

    graded = json.load(open(args.results))["graded"]
    wanted = ([(g, s) for g in sorted(SOURCES) for s in ow.STAT_TYPES
               if ow.stat_applies_to(s, g)] if args.all
              else [(args.game, args.stat)])
    found = []
    for game in sorted({g for g, _ in wanted}):
        data, index = load(game)
        if not data:
            continue
        for g, stat in [(g, s) for g, s in wanted if g == game]:
            result = search(g, stat, graded, data, index, passes=args.passes)
            if not result:
                continue
            result["diagnostics"] = compare(result, graded, data, index)
            found.append(result)
            shipped, got = result["diagnostics"]["shipped"], result["diagnostics"]["found"]
            print(f"    held out, {got['n']} picks across the folds:")
            for name, row in (("shipped", shipped), ("found", got)):
                if row["accuracy"] is None:
                    continue
                print(f"      {name:<8} picks land {row['accuracy']:.1%}; "
                      f"top fifth {row['top_fifth']:.1%} against bottom "
                      f"{row['bottom_fifth']:.1%}")
            changed = {k: (result["shipped"][k], v) for k, v in result["found"].items()
                       if result["shipped"].get(k) != v}
            print(f"    log-loss {result['shipped_loss']:.4f} -> "
                  f"{result['found_loss']:.4f}"
                  + (f"; changed {changed}" if changed else "; no change adopted"))
    if args.json and found:
        with open(args.json, "w") as f:
            json.dump(found, f, indent=2)
            f.write("\n")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

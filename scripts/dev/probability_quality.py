#!/usr/bin/env python3
"""Three upgrades to the slate tool's probability, judged on props it never saw.

The slate tool turns a projection into P(under) with a normal curve around the
shrunk, realised mean. Three things could make that number better, and each is
an idea until held-out props say otherwise:

  count     -- kills are counts, not a bell curve. A negative binomial with the
               same mean and variance puts more mass in the right tail and less
               in the left, which matters most for lines far from the mean.
  calibrate -- the normal's sigma is the model's error at the window, measured
               once. If 70% claims land 62% of the time, a per-game logistic
               refit of the claim (Platt scaling) says so and corrects it.
  market    -- lines run high: unders land more often than half the time, and
               by different amounts per game, stat, window and odds type. The
               projection does not know that; a per-cell bias term fitted only
               on earlier props does.

Every variant is fitted walk-forward: the matches are cut into blocks by date,
each block is scored by a fit on the blocks before it, and nothing is ever
fitted on the props it is scored on. Scored by log-loss and Brier on decided
props on the STANDARD board only, fitted and scored: goblin and demon lines sit
far from the mean, which makes the projection look far more predictive on them
than on a line you can bet both ways, and a calibration fitted with them in is
overconfident on the standard board.

A variant ships only if it beats the shipped normal curve held out AND the gain
survives a bootstrap clustered on the match (95% interval above zero) -- the
rule LINE_REALIZATION already lives under. As first measured, 2026-11-02:
nothing does. The negative binomial lost in every game, because sigma is the
model's error, not the count's dispersion, and the bell is the right shape for
that. Per-game calibration and the market term lost by more, because the
under-bias they learn does not hold still (Valorant's reversed). A single slope
pooled across games, no intercept, came out near 0.6 -- the claims are too
confident -- and won by 0.0004, inside a 95% interval of -0.003 to +0.003.

    python scripts/dev/probability_quality.py
    python scripts/dev/probability_quality.py --json scripts/dev/probability_quality.json
"""
import argparse
import collections
import json
import math
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from propedge import esports                       # noqa: E402

FOLDS = 5
EPS = 1e-6
PRIOR_SHRINK_N = esports.PRIOR_SHRINK_N


# ---------------------------------------------------------------- distributions

def _nb_cdf(k, mean, var):
    """P(X <= k) for a negative binomial with this mean and variance, Poisson
    where the variance is not above the mean."""
    if k < 0:
        return 0.0
    k = int(math.floor(k))
    if var <= mean * 1.0001:
        log_p = -mean
        term = math.exp(log_p)
        total = term
        for i in range(1, k + 1):
            term *= mean / i
            total += term
        return min(1.0, total)
    r = mean * mean / (var - mean)
    p = r / (r + mean)                      # success probability
    log_term = r * math.log(p)              # P(X = 0)
    total = math.exp(log_term)
    for i in range(1, k + 1):
        log_term += math.log((i - 1 + r) / i) + math.log(1 - p)
        total += math.exp(log_term)
    return min(1.0, total)


def count_probabilities(mu, sigma, line):
    """(p_over, p_push, p_under) for a count with mean mu and sd sigma."""
    if not sigma or sigma <= 0 or mu <= 0:
        return None
    var = sigma * sigma
    if float(line).is_integer():
        below = _nb_cdf(line - 1, mu, var)
        at_or_below = _nb_cdf(line, mu, var)
        return (1 - at_or_below, at_or_below - below, below)
    below = _nb_cdf(line, mu, var)
    return (1 - below, 0.0, below)


def realised_mean(row, shrink_maps=esports.SHRINK_MAPS):
    shrunk = esports.shrink_toward_line(row["mu"], row["line"], row["evidence"], shrink_maps)
    return row["line"] + (shrunk - row["line"]) * esports.line_realization(row["game"], row["stat"])


def p_under(row, dist="normal"):
    """P(under | decided), as the slate tool would claim it."""
    mean = realised_mean(row)
    fn = esports.outcome_probabilities if dist == "normal" else count_probabilities
    split = fn(mean, row["sigma"], row["line"])
    if split is None:
        return None
    over, _, under = split
    decided = over + under
    return None if decided <= 0 else min(1 - EPS, max(EPS, under / decided))


# ---------------------------------------------------------------- fitting

def logit(p):
    p = min(1 - EPS, max(EPS, p))
    return math.log(p / (1 - p))


def sigmoid(z):
    return 1 / (1 + math.exp(-z)) if z > -700 else 0.0


def fit_logistic(xs, ys, ridge=1.0, prior=None, iters=25):
    """Weights for P(y) = sigmoid(w . x), by Newton's method with a ridge
    toward `prior` (the identity map when the first feature is a logit)."""
    dim = len(xs[0]) if xs else 0
    prior = prior or [0.0] * dim
    w = list(prior)
    for _ in range(iters):
        grad = [ridge * (w[j] - prior[j]) for j in range(dim)]
        hess = [[ridge if i == j else 0.0 for j in range(dim)] for i in range(dim)]
        for x, y in zip(xs, ys):
            p = sigmoid(sum(a * b for a, b in zip(w, x)))
            for i in range(dim):
                grad[i] += (p - y) * x[i]
                for j in range(dim):
                    hess[i][j] += p * (1 - p) * x[i] * x[j]
        step = _solve(hess, grad)
        w = [a - b for a, b in zip(w, step)]
        if max(abs(s) for s in step) < 1e-9:
            break
    return w


def _solve(a, b):
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        if abs(m[c][c]) < 1e-12:
            continue
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c])]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def cell(row):
    return (row["game"], row["stat"], row["maps"], row["odds_type"])


def cell_bias(train):
    """{cell: logit of the shrunk under-rate} from training props only."""
    counts = collections.defaultdict(lambda: [0, 0])
    for row in train:
        counts[cell(row)][0] += row["under"]
        counts[cell(row)][1] += 1
    return {c: logit(0.5 + (u / n - 0.5) * n / (n + PRIOR_SHRINK_N))
            for c, (u, n) in counts.items()}


# ---------------------------------------------------------------- variants

def features(row, variant, bias):
    z = row["z_" + ("count" if variant.startswith("count") else "normal")]
    if variant in ("normal", "count"):
        return None
    if variant.endswith("calibrated"):
        return [1.0, z]
    if variant.endswith("market"):
        return [z, bias.get(cell(row), 0.0)]
    return [1.0, z, bias.get(cell(row), 0.0)]       # calibrated + market


VARIANTS = ("normal", "count", "normal+slope", "normal+calibrated", "count+calibrated",
            "normal+market", "count+market", "normal+both", "count+both")
BOOTSTRAP = 2000


def predict(train, test, variant):
    """[(row, p_under)] for the test rows, fitted on the train rows."""
    base = "z_count" if variant.startswith("count") else "z_normal"
    if variant in ("normal", "count"):
        return [(row, sigmoid(row[base])) for row in test]
    if variant == "normal+slope":
        # one slope pooled across games, no intercept: confidence only
        k = fit_logistic([[row[base]] for row in train], [row["under"] for row in train],
                         prior=[1.0])[0] if len(train) >= 50 else 1.0
        return [(row, sigmoid(k * row[base])) for row in test]
    bias = cell_bias(train)
    out = []
    for game in {row["game"] for row in test}:
        tr = [row for row in train if row["game"] == game]
        te = [row for row in test if row["game"] == game]
        xs = [features(row, variant, bias) for row in tr]
        if len(tr) < 50:
            out += [(row, sigmoid(row[base])) for row in te]
            continue
        identity = [0.0, 1.0] if variant.endswith("calibrated") else \
            ([1.0, 0.0] if variant.endswith("market") else [0.0, 1.0, 0.0])
        w = fit_logistic(xs, [row["under"] for row in tr], prior=identity)
        out += [(row, sigmoid(sum(a * b for a, b in zip(w, features(row, variant, bias)))))
                for row in te]
    return out


def walk_forward(rows, variant, folds=FOLDS):
    matches = sorted({match_of(r) for r in rows})
    size = len(matches) // (folds + 1)
    order = {m: i for i, m in enumerate(matches)}
    out = []
    for i in range(1, folds + 1):
        lo, hi = size * i, (size * (i + 1) if i < folds else len(matches))
        train = [r for r in rows if order[match_of(r)] < lo]
        test = [r for r in rows if lo <= order[match_of(r)] < hi]
        # never fit on a date that is also being scored
        first = min(str(r["when"]) for r in test) if test else ""
        train = [r for r in train if str(r["when"]) < first]
        out += predict(train, test, variant)
    return out


def match_of(row):
    return (str(row["when"]), row["team"], row["opponent"])


def gain_interval(base, pairs, seed=1):
    """(mean, low, high) log-loss gain of `pairs` over `base`, clustered on match."""
    def ll(r, p):
        return -math.log(p if r["under"] else 1 - p)
    by_match = collections.defaultdict(list)
    for (r, p0), (_, p1) in zip(base, pairs):
        by_match[match_of(r)].append(ll(r, p0) - ll(r, p1))
    groups = list(by_match.values())
    mean = sum(map(sum, groups)) / sum(map(len, groups))
    rng = random.Random(seed)
    draws = []
    for _ in range(BOOTSTRAP):
        sample = [x for g in rng.choices(groups, k=len(groups)) for x in g]
        draws.append(sum(sample) / len(sample))
    draws.sort()
    return mean, draws[int(0.025 * BOOTSTRAP)], draws[int(0.975 * BOOTSTRAP) - 1]


def scores(pairs):
    if not pairs:
        return None
    ll = statistics.mean(-math.log(p if r["under"] else 1 - p) for r, p in pairs)
    br = statistics.mean((p - r["under"]) ** 2 for r, p in pairs)
    picked = [(p >= 0.5) == bool(r["under"]) for r, p in pairs]
    return {"n": len(pairs), "log_loss": ll, "brier": br, "hit": sum(picked) / len(picked)}


def bands(pairs, edges=(0.5, 0.55, 0.6, 0.65, 0.7, 1.01)):
    """Claimed against realised on the side picked."""
    rows = collections.defaultdict(list)
    for r, p in pairs:
        claim = max(p, 1 - p)
        won = (p >= 0.5) == bool(r["under"])
        for lo, hi in zip(edges, edges[1:]):
            if lo <= claim < hi:
                rows[(lo, hi)].append((claim, won))
    return [{"lo": lo, "hi": min(hi, 1.0), "n": len(v),
             "claimed": statistics.mean(c for c, _ in v),
             "realised": sum(1 for _, w in v if w) / len(v)}
            for (lo, hi), v in sorted(rows.items())]


def prepare(cached):
    rows = []
    for row in cached:
        pn, pc = p_under(row, "normal"), p_under(row, "count")
        if pn is None or pc is None:
            continue
        rows.append({**row, "under": 1 if row["result"] == "under" else 0,
                     "z_normal": logit(pn), "z_count": logit(pc)})
    rows.sort(key=lambda r: str(r["when"]))
    return rows


def evaluate(rows):
    rows = [r for r in rows if r["odds_type"] == "standard"]
    base = walk_forward(rows, "normal")
    out = {}
    for variant in VARIANTS:
        pairs = base if variant == "normal" else walk_forward(rows, variant)
        got = {"standard": scores(pairs),
               "by_game": {g: scores([(r, p) for r, p in pairs if r["game"] == g])
                           for g in sorted({r["game"] for r, _ in pairs})},
               "bands": bands(pairs)}
        if variant != "normal":
            mean, lo, hi = gain_interval(base, pairs)
            got["gain"] = {"mean": mean, "low": lo, "high": hi}
        out[variant] = got
    return out


def ships(results):
    """Variants whose held-out gain over the shipped curve is surely positive."""
    return [v for v, g in results.items() if g.get("gain") and g["gain"]["low"] > 0]


def main(argv=None):
    import backtest_esports
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default="props_results.json")
    ap.add_argument("--root", default=".")
    ap.add_argument("--json")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 when a variant now clears the bar, for the weekly workflow")
    args = ap.parse_args(argv)
    cached, _, _ = backtest_esports.replay(args.results, args.root)
    rows = prepare(cached)
    got = evaluate(rows)
    print(f"standard-board props scored walk-forward over {FOLDS} later blocks of matches\n")
    print(f"  {'variant':<20} {'n':>5} {'logloss':>8} {'brier':>7} {'hit':>6}"
          f"   gain over normal (95%, clustered on match)")
    for variant, g in got.items():
        s, gain = g["standard"], g.get("gain")
        print(f"  {variant:<20} {s['n']:>5} {s['log_loss']:>8.4f} {s['brier']:>7.4f} {s['hit']:>6.1%}"
              + (f"   {gain['mean']:+.4f} ({gain['low']:+.4f} to {gain['high']:+.4f})" if gain else ""))
    print("\n  standard board by game, log-loss (lower is better):")
    games = sorted(got["normal"]["by_game"])
    print("  " + " " * 20 + "".join(f"{g:>10}" for g in games))
    for variant, g in got.items():
        print(f"  {variant:<20}" + "".join(
            f"{g['by_game'][x]['log_loss']:>10.4f}" if g["by_game"].get(x) else f"{'':>10}"
            for x in games))
    print("  " + " " * 20 + "".join(f"{got['normal']['by_game'][x]['n']:>10}" for x in games)
          + "  (props)")
    print(f"\n  ships: {ships(got) or 'nothing -- the normal curve stays'}")
    for variant in ("normal", "normal+slope"):
        print(f"\n  {variant}: claimed vs realised on the side picked (standard board)")
        for b in got[variant]["bands"]:
            print(f"    {b['lo']:.0%}-{b['hi']:.0%}  n={b['n']:>5}  claimed {b['claimed']:.1%}"
                  f"  realised {b['realised']:.1%}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"folds": FOLDS, "ships": ships(got), "variants": got}, f, indent=2)
            f.write("\n")
    return 1 if args.check and ships(got) else 0


if __name__ == "__main__":
    sys.exit(main())

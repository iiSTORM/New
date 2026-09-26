#!/usr/bin/env python3
"""Does the model's confidence sort by OUTCOME yet?

This is the measurement the Parlays tab's gate turns on, run from the command
line so the answer is reproducible and can be checked on a schedule rather than
re-derived by hand every time.

WHY IT EXISTS. The app ranks props by standardised edge -- the disagreement with
the line, divided by the model's own error for that game, stat and window -- and
stacks the top of that ranking into parlays. Whether that ranking is worth
anything is a question the ranking cannot answer about itself. So the graded
record answers it: bucket every settled prop by the confidence the model had at
the time, and see whether the confident buckets actually landed more often.

When this was first run, over 1,328 graded props across 82 matches, they did not:

    band  1   0.56z   realised 43.9%
    band  5   0.19z   realised 52.3%
    band 10   0.01z   realised 50.7%
    top fifth 46.8% vs bottom fifth 52.1%   z = -1.22, noise
    confident half 42.3% against the rest at 49.4% -- below, not above

which is why the tab withholds its per-leg probabilities. Note the sign: the
ranking is not merely unproven, it currently points the wrong way, and 1,328
props is not enough to tell that apart from noise either. This script is how
that verdict gets revisited as data accrues, and it reads its thresholds OUT OF
src/app.jsx rather than keeping a second copy -- so what it reports is what the
app will do, not an approximation of it.

    python scripts/dev/decile_test.py
    python scripts/dev/decile_test.py --json decile.json --quiet

Exit status is 1 when the ranking still does not separate, so a scheduled run
can be read without parsing the output. That is NOT a failure of the code; it is
the current, honest answer.
"""
import argparse
import collections
import json
import math
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import optimize_weights as ow
import hit_probability as hp

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP = os.path.join(REPO_ROOT, "src", "app.jsx")
SOURCES = hp.SOURCES


# ============================================================
# The app's own numbers, read rather than copied
# ============================================================

def js_number(name, source):
    found = re.search(rf"const {name}\s*=\s*(-?\d+(?:\.\d+)?)", source)
    if not found:
        raise SystemExit(f"! {name} not found in src/app.jsx — has it been renamed?")
    return float(found.group(1))


def js_object(name, source):
    """A flat nested object literal out of app.jsx, as JSON."""
    start = source.index(f"const {name} = {{")
    brace = source.index("{", start)
    depth, end = 0, brace
    for end in range(brace, len(source)):
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
            if depth == 0:
                break
    body = source[brace:end + 1]
    body = re.sub(r"//[^\n]*", "", body)
    body = re.sub(r",(\s*[}\]])", r"\1", body)
    # Identifier-like keys AND bare numeric ones: RESIDUAL_SCALE keys its windows
    # by map count (`kills: { 1: 4.66, 2: 7.70 }`), which JSON will not take
    # unquoted. Quoting them here is why residual_scale() looks windows up by
    # str(maps).
    body = re.sub(r"([{,]\s*)([A-Za-z_$][A-Za-z0-9_$]*|\d+)\s*:", r'\1"\2":', body)
    return json.loads(body)


class Gate:
    """PARLAY_TIER_THRESHOLD, the row floor, and the residual scales, from the
    shipped app. One source of truth, so this cannot report a verdict the tab
    would not reach."""

    def __init__(self):
        with open(APP, encoding="utf-8") as f:
            source = f.read()
        self.threshold = js_number("PARLAY_TIER_THRESHOLD", source)
        self.min_rows = int(js_number("PARLAY_MIN_ROWS_PER_BAND", source))
        self.exponent = js_number("RESIDUAL_SCALE_EXPONENT", source)
        self.scales = js_object("RESIDUAL_SCALE", source)

    def residual_scale(self, game, stat, maps):
        by_window = (self.scales.get(game) or {}).get(stat)
        if not by_window or not isinstance(maps, int) or maps <= 0:
            return None
        exact = by_window.get(str(maps))
        if isinstance(exact, (int, float)):
            return float(exact)
        windows = [int(w) for w in by_window if int(w) > 0]
        if not windows:
            return None
        near = min(windows, key=lambda w: abs(w - maps))
        return by_window[str(near)] * (maps / near) ** self.exponent


# ============================================================
# Replay every settled prop through the shipped model
# ============================================================

def rows_for(results_path, gate, games):
    """One row per settled prop we can re-project: the confidence the model had
    and whether its pick landed."""
    with open(results_path) as f:
        graded = json.load(f)["graded"]
    out, skipped = [], collections.Counter()
    for game in games:
        data = ow.load_region_data(SOURCES[game])
        if not data:
            skipped["no data file"] += 1
            continue
        index = hp.index_matches(data)
        teams_by_region = {rk: rd.get("teams") or {} for rk, rd in data.items()}
        for row in graded:
            if row.get("game") != game or row.get("result") == "push":
                continue
            stat, maps = row.get("stat"), row.get("maps")
            weights = ow.SHIPPED_WEIGHTS.get(game, {}).get(stat)
            if not weights or not isinstance(maps, int):
                skipped["no weights or window"] += 1
                continue
            found = index.get((row.get("match_date"), str(row.get("team")).lower(),
                               str(row.get("player")).lower()))
            if not found:
                skipped["match not on file"] += 1
                continue
            match, team, opponent = found
            region = next((rk for rk, t in teams_by_region.items() if team in t), None)
            if region is None:
                skipped["team not rostered"] += 1
                continue
            player = next((p for p in teams_by_region[region][team]["players"]
                           if p["name"].lower() == str(row["player"]).lower()), None)
            if player is None:
                skipped["player not rostered"] += 1
                continue
            predicted, _ = ow.project_point_in_time(
                data[region].get("past_matches") or [], teams_by_region[region], player,
                team, opponent, maps, weights, match["date"], stat, match.get("patch"))
            if predicted is None:
                skipped["no projection"] += 1
                continue
            scale = gate.residual_scale(game, stat, maps)
            if not scale:
                skipped["no residual scale"] += 1
                continue
            edge = predicted - row["line"]
            if edge == 0:
                skipped["projection sits on the line"] += 1
                continue
            out.append({
                "game": game, "stat": stat, "maps": maps,
                "z": abs(edge) / scale,
                "won": (edge > 0) == (row["result"] == "over"),
                # Clustered on the MATCH, because ten props off one map are not
                # ten observations -- the same key groupByMatch uses in the app.
                "cluster": (game, row.get("match_date"),
                            tuple(sorted((str(row.get("team")),
                                          str(row.get("opponent") or "?"))))),
            })
    return out, skipped


def clustered(rows):
    """(mean, lo, hi, clusters) over per-match win rates, or None if too thin."""
    by_cluster = collections.defaultdict(list)
    for row in rows:
        by_cluster[row["cluster"]].append(row["won"])
    rates = [sum(1 for w in v if w) / len(v) for v in by_cluster.values()]
    if len(rates) < 2:
        return None
    mean = statistics.mean(rates)
    se = statistics.stdev(rates) / math.sqrt(len(rates))
    return {"mean": mean, "lo": mean - 1.96 * se, "hi": mean + 1.96 * se,
            "clusters": len(rates), "rows": len(rows)}


def deciles(rows, n=10):
    """Rows sorted most-confident first, cut into n bands."""
    ranked = sorted(rows, key=lambda r: -r["z"])
    step = max(1, len(ranked) // n)
    out = []
    for i in range(n):
        band = ranked[i * step:(i + 1) * step] if i < n - 1 else ranked[(n - 1) * step:]
        if band:
            out.append((i + 1, band))
    return out


def two_proportion_z(a, b):
    """Is the top band's win rate distinguishable from the bottom band's?

    On ROWS rather than clusters, and reported alongside the clustered
    intervals rather than instead of them: the row-level z is the optimistic
    version and saying so is the point.
    """
    wins = lambda rows: sum(1 for r in rows if r["won"])
    if not a or not b:
        return None
    p1, p2 = wins(a) / len(a), wins(b) / len(b)
    pool = (wins(a) + wins(b)) / (len(a) + len(b))
    se = math.sqrt(pool * (1 - pool) * (1 / len(a) + 1 / len(b)))
    return None if not se else (p1 - p2) / se


def verdict(rows, gate):
    """The app's own gate, applied here so the two cannot disagree."""
    big = [r for r in rows if r["z"] >= gate.threshold]
    small = [r for r in rows if r["z"] < gate.threshold]
    if len(big) < gate.min_rows or len(small) < gate.min_rows:
        return {"separated": False, "big": None, "small": None,
                "why": f"only {len(big)} prop(s) above the {gate.threshold} confidence "
                       f"threshold and {len(small)} below it; {gate.min_rows} a side is "
                       f"the floor"}
    big_rate, small_rate = clustered(big), clustered(small)
    if not big_rate or not small_rate:
        return {"separated": False, "big": big_rate, "small": small_rate,
                "why": "not enough distinct matches to cluster on"}
    separated = big_rate["lo"] > small_rate["mean"]
    return {
        "separated": separated, "big": big_rate, "small": small_rate,
        "why": (f"the confident half realises {big_rate['mean']:.1%} "
                f"(interval from {big_rate['lo']:.1%}), clear of the other half's "
                f"{small_rate['mean']:.1%}") if separated else
               (f"the confident half realises {big_rate['mean']:.1%} "
                f"({big_rate['lo']:.1%}–{big_rate['hi']:.1%}) against the other half's "
                f"{small_rate['mean']:.1%} — "
                + ("ordered, but not separated" if big_rate["mean"] > small_rate["mean"]
                   else "and is BELOW it")),
    }


def matches_needed(big, small, target=0.54):
    """Roughly how many more clusters before a real gap that size would show.

    Deliberately rough and labelled as such: it assumes the observed per-match
    spread holds and that the true confident-half rate is `target`. It is for
    answering "check back in a week or a month", not for planning.
    """
    if not big or not small:
        return None
    gap = target - small["mean"]
    if gap <= 0:
        return None
    # se at which the interval would clear: (target - small.mean) / 1.96
    se_needed = gap / 1.96
    se_now = (big["hi"] - big["mean"]) / 1.96
    if se_now <= 0 or se_needed <= 0:
        return None
    # se scales as 1/sqrt(clusters)
    return max(0, math.ceil(big["clusters"] * (se_now / se_needed) ** 2) - big["clusters"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default="props_results.json")
    ap.add_argument("--game", choices=sorted(SOURCES) + ["all"], default="all")
    ap.add_argument("--bands", type=int, default=10)
    ap.add_argument("--json", help="write the numbers here as well")
    ap.add_argument("--quiet", action="store_true", help="just the verdict")
    args = ap.parse_args(argv)

    gate = Gate()
    games = sorted(SOURCES) if args.game == "all" else [args.game]
    rows, skipped = rows_for(args.results, gate, games)
    if not rows:
        print(f"No settled props could be re-projected. Skipped: {dict(skipped)}")
        return 1

    overall = clustered(rows)
    got = verdict(rows, gate)
    ranked = sorted(rows, key=lambda r: -r["z"])
    fifth = max(1, len(ranked) // 5)
    top, bottom = ranked[:fifth], ranked[-fifth:]
    z = two_proportion_z(top, bottom)

    if not args.quiet:
        print(f"{len(rows)} settled prop(s) re-projected across {overall['clusters']} "
              f"matches" + (f"; skipped {dict(skipped)}" if skipped else ""))
        print(f"overall: picks land {overall['mean']:.1%} "
              f"({overall['lo']:.1%}–{overall['hi']:.1%}, clustered on the match)\n")
        print(f"  {'band':>5} {'n':>5} {'confidence':>11} {'realised':>9}  "
              f"{'clustered 95%':>16}")
        for number, band in deciles(rows, args.bands):
            rate = clustered(band)
            interval = (f"{rate['lo']:.0%}–{rate['hi']:.0%}" if rate else "too few matches")
            print(f"  {number:>5} {len(band):>5} {statistics.mean(r['z'] for r in band):>10.2f}z "
                  f"{sum(1 for r in band if r['won']) / len(band):>9.1%}  {interval:>16}")
        tw = sum(1 for r in top if r["won"]) / len(top)
        bw = sum(1 for r in bottom if r["won"]) / len(bottom)
        print(f"\n  top fifth {tw:.1%} vs bottom fifth {bw:.1%}"
              + (f"   z = {z:+.2f} ({'significant' if abs(z) > 1.96 else 'noise'})"
                 if z is not None else ""))

    print(f"\nVERDICT: the ranking {'SEPARATES' if got['separated'] else 'does not separate'}")
    print(f"  {got['why']}.")
    if got["separated"]:
        print("  The Parlays tab will show its per-leg probabilities.")
    else:
        need = matches_needed(got["big"], got["small"])
        print("  The Parlays tab withholds its per-leg probabilities, which is correct.")
        if need:
            print(f"  Roughly {need} more graded match(es) before a genuine 54% confident "
                  f"half would show — rough, and assumes today's per-match spread holds.")

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"rows": len(rows), "overall": overall, "verdict": got,
                       "top_fifth_vs_bottom_z": z,
                       "bands": [{"band": n, "n": len(b),
                                  "confidence": statistics.mean(r["z"] for r in b),
                                  "realised": sum(1 for r in b if r["won"]) / len(b)}
                                 for n, b in deciles(rows, args.bands)]}, f, indent=2)
            f.write("\n")
        print(f"\nWrote {args.json}")
    return 0 if got["separated"] else 1


if __name__ == "__main__":
    sys.exit(main())

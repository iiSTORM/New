#!/usr/bin/env python3
"""The simulation engine against the current projections, on the graded record.

For every match with graded standard props, point in time (only maps played
before the match date), the series is simulated RUNS times with the real
lineups and format, and each prop gets the engine's P(over | its window was
played). Three numbers per prop are scored on decided props by log-loss,
Brier and side hit-rate:

  slate     the current probability: the projection shrunk toward the line,
            LINE_REALIZATION applied, a normal curve (propedge.esports)
  engine    the simulation on its own
  anchored  the simulation's SHAPE moved to the slate's mean: what the engine
            adds if the projection keeps the say on how many

and the gain of each over the slate gets a 95% interval clustered on the
match -- the bar everything else in this repo has had to clear.

THE JOINT TEST is the one the engine was built for. For every pair of
graded props in one match, did the two land the same way (both over or both
under)? The current tool prices that with one measured correlation (0.143
same team, 0.043 opposing sides); the engine has a probability for it from
its own runs. Both are scored on the pairs, by log-loss.

    python scripts/dev/simulation_backtest.py
    python scripts/dev/simulation_backtest.py --runs 2000 --games lol cs2
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

from propedge import esports, joint                  # noqa: E402
from propedge import series_sim as sim                 # noqa: E402

EPS = 1e-4
BOOTSTRAP = 1000


def clip(p):
    return min(1 - EPS, max(EPS, p))


def slate_probability(model, row):
    mean = model.project_mean(row["game"], row["player"], row["stat"], row["maps"],
                              row.get("team"), row.get("opponent"), row["match_date"])
    if mean is None or mean[1] < esports.MIN_MAPS:
        return None
    got = model.probabilities(row["game"], row["stat"], row["maps"], mean[0], row["line"],
                              mean[1], "standard")
    if got is None:
        return None
    over, _, under, detail = got
    if over + under <= 0:
        return None
    return over / (over + under), detail.shrunk if detail else None, mean[0]


def engine_values(series_list, side, player, stat, window):
    """The player's window totals across runs, None where the window was not played."""
    return [sim.window_total(s, side, player, stat, window) for s in series_list]


def evaluate(games, runs, root=".", results="props_results.json", seed=1):
    graded = json.load(open(results))["graded"]
    model = esports.EsportsModel.from_files(root, graded)
    rows = [r for r in graded if (r.get("odds_type") or "standard") == "standard"
            and r.get("result") in ("over", "under") and r.get("game") in games
            and isinstance(r.get("maps"), int) and r.get("line") is not None
            and (r.get("map") in (None, 1))]
    by_match = collections.defaultdict(list)
    for r in rows:
        key = (r["game"], str(r.get("match_date"))[:10],
               tuple(sorted((sim.norm(r.get("team")), sim.norm(r.get("opponent"))))))
        by_match[key].append(r)
    libs = {g: sim.load_library(g, root) for g in games}
    scored, pairs, skipped = [], [], collections.Counter()
    for n_match, ((game, date, _), props) in enumerate(sorted(by_match.items())):
        lib = libs.get(game)
        if lib is None:
            skipped["no data"] += len(props)
            continue
        first = props[0]
        found = lib.series_on(first.get("team"), first.get("opponent"), date)
        if found is None:
            skipped["series not on file"] += len(props)
            continue
        team_a = found["names"].get(sim.norm(first.get("team")), first.get("team"))
        team_b = found["names"].get(sim.norm(first.get("opponent")), first.get("opponent"))
        ra = found["lineups"].get(sim.norm(team_a)) or []
        rb = found["lineups"].get(sim.norm(team_b)) or []
        fa, fb = sim.kill_factors(lib.regions, first.get("team"), first.get("opponent"))
        prepared = sim.prepare(lib, team_a, team_b, date, found["best_of"], ra, rb,
                               kill_factor_a=fa, kill_factor_b=fb)
        if prepared is None:
            skipped["under 4 maps of team history"] += len(props)
            continue
        series_list = list(sim.run(lib, prepared, runs, seed + n_match))
        per_prop = []
        for r in props:
            side = 0 if sim.norm(r.get("team")) == sim.norm(team_a) else 1
            roster = ra if side == 0 else rb
            pkey = sim.norm(r.get("player"))
            if pkey not in dict(roster):
                skipped["player not in the lineup"] += 1
                continue
            stat = sim.STAT_NAMES.get(r["stat"])
            if stat not in lib.stats:
                skipped["stat not simulated"] += 1
                continue
            vals = engine_values(series_list, side, pkey, stat, r["maps"])
            played = [v for v in vals if v is not None]
            decided = [v for v in played if v != r["line"]]
            slate = slate_probability(model, r)
            if len(decided) < 50 or slate is None:
                skipped["no slate probability or too few decided runs"] += 1
                continue
            p_slate, shrunk_mean, _ = slate
            p_engine = sum(1 for v in decided if v > r["line"]) / len(decided)
            # anchored: the engine's runs moved by the gap between its mean and
            # the slate's realised mean (shrunk and LINE_REALIZATION-scaled)
            target = r["line"] + (shrunk_mean - r["line"]) * esports.line_realization(game, r["stat"])
            shift = target - statistics.mean(played)
            moved = [v + shift for v in played if v + shift != r["line"]]
            p_anchor = sum(1 for v in moved if v > r["line"]) / max(1, len(moved))
            over = 1 if r["result"] == "over" else 0
            item = {"game": game, "stat": r["stat"], "match": (game, date, team_a, team_b),
                    "over": over, "slate": clip(p_slate), "engine": clip(p_engine),
                    "anchored": clip(p_anchor), "side": side, "vals": vals,
                    "line": r["line"], "shift": shift}
            per_prop.append(item)
            scored.append(item)
        for i in range(len(per_prop)):
            for j in range(i + 1, len(per_prop)):
                a, b = per_prop[i], per_prop[j]
                both = [(x, y) for x, y in zip(a["vals"], b["vals"])
                        if x is not None and y is not None and x != a["line"] and y != b["line"]]
                if len(both) < 50:
                    continue
                agree_engine = sum(1 for x, y in both if (x > a["line"]) == (y > b["line"])) / len(both)
                # the same pair with both players' runs moved to the slate's
                # means: the engine's correlation, the projection's levels
                moved = [(x + a["shift"], y + b["shift"]) for x, y in zip(a["vals"], b["vals"])
                         if x is not None and y is not None]
                moved = [(x, y) for x, y in moved if x != a["line"] and y != b["line"]]
                agree_anchored = (sum(1 for x, y in moved if (x > a["line"]) == (y > b["line"]))
                                  / max(1, len(moved)))
                rho = (joint.SAME_TEAM_CORRELATION if a["side"] == b["side"]
                       else joint.OPPOSING_TEAMS_CORRELATION)
                pa, pb = a["slate"], b["slate"]
                both_over = joint.group_hit_probability([pa, pb], rho)
                both_under = joint.group_hit_probability([1 - pa, 1 - pb], rho)
                agree_formula = (both_over or 0) + (both_under or 0)
                indep = pa * pb + (1 - pa) * (1 - pb)
                pairs.append({"match": a["match"], "same_team": a["side"] == b["side"],
                              "agree": 1 if a["over"] == b["over"] else 0,
                              "engine": clip(agree_engine), "anchored": clip(agree_anchored),
                              "formula": clip(agree_formula),
                              "independent": clip(indep)})
    for item in scored:
        item.pop("vals", None)
    return scored, pairs, skipped


def log_loss(items, key, outcome="over"):
    return statistics.mean(-math.log(i[key] if i[outcome] else 1 - i[key]) for i in items)


def brier(items, key, outcome="over"):
    return statistics.mean((i[key] - i[outcome]) ** 2 for i in items)


def hit(items, key):
    return statistics.mean(1 if (i[key] >= 0.5) == bool(i["over"]) else 0 for i in items)


def gain(items, base, other, outcome="over", seed=1):
    """(mean, low, high) log-loss gain of `other` over `base`, clustered on match."""
    by = collections.defaultdict(list)
    for i in items:
        ll0 = -math.log(i[base] if i[outcome] else 1 - i[base])
        ll1 = -math.log(i[other] if i[outcome] else 1 - i[other])
        by[i["match"]].append(ll0 - ll1)
    groups = list(by.values())
    mean = sum(map(sum, groups)) / sum(map(len, groups))
    rng = random.Random(seed)
    draws = sorted(sum(x for g in s for x in g) / sum(len(g) for g in s)
                   for s in ([rng.choice(groups) for _ in groups] for _ in range(BOOTSTRAP)))
    return mean, draws[int(0.025 * BOOTSTRAP)], draws[int(0.975 * BOOTSTRAP) - 1]


def report(scored, pairs, skipped):
    lines = [f"{len(scored)} graded standard props in "
             f"{len({i['match'] for i in scored})} matches simulated point in time"]
    if skipped:
        lines.append(f"skipped: {dict(skipped)}")
    lines.append("")
    lines.append(f"  {'cell':<18} {'n':>5}   log-loss slate / engine / anchored     "
                 f"hit slate / engine / anchored")
    cells = sorted({(i["game"], i["stat"]) for i in scored})
    for cell in cells + [("all", "")]:
        items = scored if cell[0] == "all" else [i for i in scored if (i["game"], i["stat"]) == cell]
        if len(items) < 30:
            continue
        lines.append(f"  {' '.join(cell).strip():<18} {len(items):>5}   "
                     f"{log_loss(items, 'slate'):.4f} / {log_loss(items, 'engine'):.4f} / "
                     f"{log_loss(items, 'anchored'):.4f}     "
                     f"{hit(items, 'slate'):.1%} / {hit(items, 'engine'):.1%} / {hit(items, 'anchored'):.1%}")
    verdict = {}
    for key in ("engine", "anchored"):
        m, lo, hi = gain(scored, "slate", key)
        verdict[key] = (m, lo, hi)
        lines.append(f"  {key} vs slate, log-loss gain {m:+.4f} (95% {lo:+.4f} to {hi:+.4f})"
                     + ("  <- BETTER" if lo > 0 else "  <- WORSE" if hi < 0 else ""))
    if pairs:
        lines.append("")
        lines.append(f"  same-match pairs: {len(pairs)} "
                     f"({sum(p['same_team'] for p in pairs)} same team)")
        for label, subset in (("same team", [p for p in pairs if p["same_team"]]),
                              ("opposing", [p for p in pairs if not p["same_team"]]), ("all", pairs)):
            if len(subset) < 30:
                continue
            actual = statistics.mean(p["agree"] for p in subset)
            lines.append(f"    {label:<10} landed the same way {actual:.1%}; predicted: engine "
                         f"{statistics.mean(p['engine'] for p in subset):.1%}, anchored "
                         f"{statistics.mean(p['anchored'] for p in subset):.1%}, formula "
                         f"{statistics.mean(p['formula'] for p in subset):.1%}, independent "
                         f"{statistics.mean(p['independent'] for p in subset):.1%}; log-loss "
                         f"engine {log_loss(subset, 'engine', 'agree'):.4f} / anchored "
                         f"{log_loss(subset, 'anchored', 'agree'):.4f} / formula "
                         f"{log_loss(subset, 'formula', 'agree'):.4f}")
        for key in ("engine", "anchored"):
            m, lo, hi = gain(pairs, "formula", key, "agree")
            verdict["pairs_" + key] = (m, lo, hi)
            lines.append(f"    {key} vs formula on pairs, log-loss gain {m:+.4f} "
                         f"(95% {lo:+.4f} to {hi:+.4f})"
                         + ("  <- BETTER" if lo > 0 else "  <- WORSE" if hi < 0 else ""))
        for game in sorted({p["match"][0] for p in pairs}):
            sub = [p for p in pairs if p["match"][0] == game]
            if len(sub) >= 100:
                m, lo, hi = gain(sub, "formula", "anchored", "agree")
                lines.append(f"      {game:<9} {len(sub):>6} pairs: anchored vs formula {m:+.4f} "
                             f"(95% {lo:+.4f} to {hi:+.4f})")
    return "\n".join(lines), verdict


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=1000)
    ap.add_argument("--games", nargs="+", default=list(sim.SOURCES))
    ap.add_argument("--json")
    ap.add_argument("--jitter", type=float, help="override series_sim.JITTER")
    args = ap.parse_args(argv)
    if args.jitter is not None:
        sim.JITTER = args.jitter
    scored, pairs, skipped = evaluate(args.games, args.runs)
    text, verdict = report(scored, pairs, skipped)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("```\n" + text + "\n```\n")
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"runs": args.runs, "verdict": verdict, "props": len(scored),
                       "pairs": len(pairs)}, f, indent=2)
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

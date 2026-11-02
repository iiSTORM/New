#!/usr/bin/env python3
"""Do AWPers' headshot lines miss in a way their headshot share predicts?

An AWP kill to the body is still a kill but not a headshot, so a team's
AWPer converts far fewer kills into headshots than its riflers (in this
data the lowest headshot shares, ~25-35%, against ~50-60% for riflers). The
model projects headshots from each player's own headshot rate, which carries
that already -- but the share also swings with how much an AWPer rifles in a
given series (eco rounds, a map they rifle on), which the rate averages away.

For each graded standard CS2 headshots prop, point in time:

    share = the player's headshots / kills over their maps before the prop
    left  = actual - the slate tool's realised mean

and the questions are (1) whether `left` / line moves with share, regressed
per prop and clustered on the match; (2) whether the model is worse on
AWPers, by mean absolute error and side hit rate, low-share fifth against
the rest. Kills props are the control for (1): an AWPer's KILL line should
show no share effect if the story is about conversion rather than form.

    python scripts/dev/awper_headshot_probe.py
"""
import bisect
import collections
import json
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

from propedge import esports                       # noqa: E402

BOOTSTRAP = 2000
MIN_KILLS = 60


def norm(name):
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


def player_maps(root="."):
    """{player: [(date, kills, headshots)]} sorted by date, from cs2 per-map rows."""
    out = collections.defaultdict(list)
    seen = set()
    regions = json.load(open(os.path.join(root, esports.SOURCES["cs2"]))).get("regions") or {}
    for blob in regions.values():
        for m in blob.get("past_matches") or []:
            date = str(m.get("date") or "")[:10]
            for i, one in enumerate(m.get("per_game") or []):
                for team, players in (one or {}).items():
                    for p, s in (players or {}).items():
                        if s.get("hs") is None:
                            continue
                        key = (norm(p), date, m.get("match_id"), i)
                        if key in seen:
                            continue
                        seen.add(key)
                        out[norm(p)].append((date, s.get("k") or 0, s["hs"]))
    for rows in out.values():
        rows.sort()
    return out


def share_before(maps, player, date):
    rows = maps.get(player) or []
    i = bisect.bisect_left([r[0] for r in rows], date)
    k = sum(r[1] for r in rows[:i])
    h = sum(r[2] for r in rows[:i])
    return h / k if k >= MIN_KILLS else None


def collect(results_path, root="."):
    graded = json.load(open(results_path))["graded"]
    model = esports.EsportsModel.from_files(root, graded)
    maps = player_maps(root)
    rows = []
    for r in graded:
        if r.get("game") != "cs2" or (r.get("odds_type") or "standard") != "standard":
            continue
        if r.get("actual") is None or r.get("stat") not in ("kills", "headshots"):
            continue
        date = str(r.get("match_date"))
        share = share_before(maps, norm(r.get("player")), date)
        if share is None or not isinstance(r.get("maps"), int):
            continue
        mean = model.project_mean("cs2", r.get("player"), r["stat"], r["maps"], r.get("team"),
                                  r.get("opponent"), date)
        if mean is None or mean[1] < esports.MIN_MAPS:
            continue
        mu, evidence = mean
        shrunk = esports.shrink_toward_line(mu, r["line"], evidence)
        realised = r["line"] + (shrunk - r["line"]) * esports.line_realization("cs2", r["stat"])
        rows.append({"stat": r["stat"], "share": share, "line": r["line"], "actual": r["actual"],
                     "mean": realised,
                     "match": (date, tuple(sorted((norm(r.get("team")), norm(r.get("opponent"))))))})
    return rows


def slope(rows):
    mid = statistics.mean(r["share"] for r in rows)
    den = sum((r["share"] - mid) ** 2 for r in rows)
    return sum((r["share"] - mid) * (r["actual"] - r["mean"]) / r["line"] for r in rows) / den


def interval(rows, seed=1):
    by = collections.defaultdict(list)
    for r in rows:
        by[r["match"]].append(r)
    groups = list(by.values())
    rng = random.Random(seed)
    draws = sorted(slope([r for g in rng.choices(groups, k=len(groups)) for r in g])
                   for _ in range(BOOTSTRAP))
    return draws[int(0.025 * BOOTSTRAP)], draws[int(0.975 * BOOTSTRAP) - 1]


def quality(rows):
    mae = statistics.mean(abs(r["actual"] - r["mean"]) / r["line"] for r in rows)
    decided = [r for r in rows if r["actual"] != r["line"]]
    hit = sum((r["mean"] > r["line"]) == (r["actual"] > r["line"]) for r in decided) / len(decided)
    under = sum(r["actual"] < r["line"] for r in decided) / len(decided)
    return mae, hit, under, len(rows)


def main(argv=None):
    rows = collect("props_results.json")
    for stat in ("headshots", "kills"):
        cell = [r for r in rows if r["stat"] == stat]
        lo, hi = interval(cell)
        cut = sorted(r["share"] for r in cell)[len(cell) // 5]
        awp = [r for r in cell if r["share"] <= cut]
        rest = [r for r in cell if r["share"] > cut]
        a, b = quality(awp), quality(rest)
        print(f"{stat}: {len(cell)} props; slope of miss/line on headshot share "
              f"{slope(cell):+.3f} (95% {lo:+.3f} to {hi:+.3f})")
        print(f"  lowest-share fifth (share <= {cut:.0%}): rel. error {a[0]:.3f}, model side "
              f"{a[1]:.1%}, went under {a[2]:.1%}, n={a[3]}")
        print(f"  everyone else:                 rel. error {b[0]:.3f}, model side "
              f"{b[1]:.1%}, went under {b[2]:.1%}, n={b[3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

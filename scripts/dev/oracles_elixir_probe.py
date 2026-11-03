#!/usr/bin/env python3
"""Does anything in Oracle's Elixir explain what the LoL model misses?

oracles_elixir/<year>.csv.gz (scripts/scrape_oracles_elixir.py) carries columns
gol.gg does not: gold / XP / CS differences at 15 minutes, damage share,
first-blood involvement, and per-team pace (combined kills per minute and
game length). Each is tested the way every other candidate input in this
repo has been:

  feature  the player's (or team's, or both teams') average over games
           strictly BEFORE the prop's date -- point in time
  left     actual - the slate tool's realised mean (after shrinking toward
           the line and LINE_REALIZATION), divided by the line

The feature is standardised, `left` is regressed on it, the slope gets a
95% interval clustered on the match, and a walk-forward fit (earlier
matches only) is scored on later ones. A feature is worth wiring into the
model only if the interval excludes 0 AND held-out error falls; this prints
which, if any, do. Graded LoL lines are few (about 500), so expect wide
intervals until the record grows -- which is why it runs weekly.

    python scripts/dev/oracles_elixir_probe.py
"""
import bisect
import collections
import csv
import gzip
import json
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

BOOTSTRAP = 1000
FOLDS = 5
MIN_GAMES = 5
WINDOW = 30            # most recent games averaged
STATS = ("kills",)     # LoL lines graded in numbers worth testing


def norm(name):
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


#: name -> (row filter, how to read the value). Player rows have a position
#: other than "team"; team rows describe the side as a whole.
PLAYER_FEATURES = {
    "golddiffat15": "golddiffat15", "xpdiffat15": "xpdiffat15",
    "csdiffat15": "csdiffat15", "damageshare": "damageshare",
    "killsat15": "killsat15", "earnedgoldshare": "earnedgoldshare",
}
TEAM_FEATURES = {"team_kpm": "team kpm", "team_golddiffat15": "golddiffat15",
                 "gamelength": "gamelength"}


DATA = "oracles_elixir"


def _rows(path):
    """Every row of one .csv.gz file, or of every year file in a directory."""
    paths = (sorted(os.path.join(path, n) for n in os.listdir(path) if n.endswith(".csv.gz"))
             if os.path.isdir(path) else [path])
    for p in paths:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            yield from csv.DictReader(f)


def load(path=DATA):
    """(player history, team history), each {key: sorted [(date, {feature: value})]}."""
    players, teams = collections.defaultdict(list), collections.defaultdict(list)
    for row in _rows(path):
        date = str(row.get("date") or "")[:10]
        if not date:
            continue
        if row.get("position") == "team":
            vals = {k: num(row.get(c)) for k, c in TEAM_FEATURES.items()}
            ck, gl = num(row.get("ckpm")), num(row.get("gamelength"))
            vals["game_kills"] = ck * gl / 60 if ck is not None and gl else None
            teams[norm(row.get("teamname"))].append((date, vals))
        else:
            vals = {k: num(row.get(c)) for k, c in PLAYER_FEATURES.items()}
            k, tk = num(row.get("kills")), num(row.get("teamkills"))
            vals["kill_share"] = k / tk if k is not None and tk else None
            fb = [num(row.get(c)) for c in ("firstbloodkill", "firstbloodassist")]
            vals["first_blood_part"] = (sum(x for x in fb if x) if any(x is not None for x in fb)
                                        else None)
            players[norm(row.get("playername"))].append((date, vals))
    for d in (players, teams):
        for rows in d.values():
            rows.sort(key=lambda r: r[0])
    return players, teams


def average_before(history, key, date, window=WINDOW):
    """{feature: mean} over the last `window` games before `date`, or None."""
    rows = history.get(key) or []
    i = bisect.bisect_left([r[0] for r in rows], date)
    recent = rows[max(0, i - window):i]
    if len(recent) < MIN_GAMES:
        return None
    out = {}
    for feature in recent[0][1]:
        vals = [r[1][feature] for r in recent if r[1].get(feature) is not None]
        if len(vals) >= MIN_GAMES:
            out[feature] = statistics.mean(vals)
    return out


def features_for(players, teams, player, team, opponent, date):
    """Every feature for one prop, point in time; missing ones left out."""
    out = {}
    mine = average_before(players, norm(player), date)
    if mine:
        out.update({f"player_{k}": v for k, v in mine.items()})
    t = average_before(teams, norm(team), date)
    o = average_before(teams, norm(opponent), date)
    if t:
        out.update({f"team_{k}": v for k, v in t.items()})
    if o:
        out.update({f"opp_{k}": v for k, v in o.items()})
    if t and o and t.get("game_kills") is not None and o.get("game_kills") is not None:
        out["pace_both"] = (t["game_kills"] + o["game_kills"]) / 2
    return out


def collect(results_path, root=".", oe_path=DATA):
    from propedge import esports
    graded = json.load(open(results_path))["graded"]
    model = esports.EsportsModel.from_files(root, graded)
    players, teams = load(oe_path)
    rows = []
    for r in graded:
        if r.get("game") != "lol" or r.get("stat") not in STATS or r.get("actual") is None:
            continue
        if (r.get("odds_type") or "standard") != "standard" or not isinstance(r.get("maps"), int):
            continue
        date = str(r.get("match_date"))
        mean = model.project_mean("lol", r.get("player"), r["stat"], r["maps"], r.get("team"),
                                  r.get("opponent"), date)
        if mean is None or mean[1] < esports.MIN_MAPS:
            continue
        feats = features_for(players, teams, r.get("player"), r.get("team"), r.get("opponent"), date)
        if not feats:
            continue
        mu, evidence = mean
        shrunk = esports.shrink_toward_line(mu, r["line"], evidence)
        realised = r["line"] + (shrunk - r["line"]) * esports.line_realization("lol", r["stat"])
        rows.append({"stat": r["stat"], "line": r["line"], "actual": r["actual"],
                     "mean": realised, "when": date, "features": feats,
                     "match": (date, tuple(sorted((norm(r.get("team")), norm(r.get("opponent"))))))})
    rows.sort(key=lambda r: r["when"])
    return rows


def slope(points):
    """Least-squares slope of y on standardised x; points are (x, y)."""
    if len(points) < 3:
        return 0.0
    xs = [p[0] for p in points]
    mx, sx = statistics.mean(xs), statistics.pstdev(xs) or 1.0
    den = sum(((x - mx) / sx) ** 2 for x in xs)
    return sum(((x - mx) / sx) * y for x, y in points) / den if den else 0.0


def test_feature(rows, feature, seed=1):
    pts = [(r["features"][feature], (r["actual"] - r["mean"]) / r["line"], r)
           for r in rows if feature in r["features"]]
    if len(pts) < 100:
        return None
    by = collections.defaultdict(list)
    for x, y, r in pts:
        by[r["match"]].append((x, y))
    groups = list(by.values())
    rng = random.Random(seed)
    draws = sorted(slope([p for g in rng.choices(groups, k=len(groups)) for p in g])
                   for _ in range(BOOTSTRAP))
    lo, hi = draws[int(0.025 * BOOTSTRAP)], draws[int(0.975 * BOOTSTRAP) - 1]
    # walk-forward: fit slope and centring on earlier matches, score later ones
    matches = sorted(by)
    size = len(matches) // (FOLDS + 1)
    base, adj = [], []
    if size >= 5:
        order = {m: i for i, m in enumerate(matches)}
        for i in range(1, FOLDS + 1):
            cut_lo, cut_hi = size * i, (size * (i + 1) if i < FOLDS else len(matches))
            test = [p for p in pts if cut_lo <= order[p[2]["match"]] < cut_hi]
            first = min(p[2]["when"] for p in test)
            train = [p for p in pts if p[2]["when"] < first]
            if len(train) < 50:
                continue
            xs = [p[0] for p in train]
            mx, sx = statistics.mean(xs), statistics.pstdev(xs) or 1.0
            b = slope([(p[0], p[1]) for p in train])
            for x, y, r in test:
                base.append(abs(r["actual"] - r["mean"]))
                adj.append(abs(r["actual"] - r["mean"] - b * (x - mx) / sx * r["line"]))
    wf = (statistics.mean(base), statistics.mean(adj)) if base else None
    return {"feature": feature, "n": len(pts), "matches": len(groups), "slope": slope(
        [(x, y) for x, y, _ in pts]), "low": lo, "high": hi, "walk_forward": wf,
        "helps": bool(wf and (lo > 0 or hi < 0) and wf[1] < wf[0])}


def main(argv=None):
    path = DATA
    if not os.path.isdir(path) or not any(n.endswith(".csv.gz") for n in os.listdir(path)):
        print(f"no {path} yet: run scripts/scrape_oracles_elixir.py "
              "(the \"Oracle's Elixir LoL game data\" workflow)")
        return 0
    rows = collect("props_results.json", oe_path=path)
    print(f"{len(rows)} graded LoL kills lines with Oracle's Elixir history behind them\n")
    names = sorted({f for r in rows for f in r["features"]})
    results = [got for got in (test_feature(rows, f) for f in names) if got]
    results.sort(key=lambda g: -abs(g["slope"]))
    print(f"  {'feature':<28} {'n':>4} {'slope/sd':>9}  {'95%':<17}  held-out MAE: model / +feature")
    for g in results:
        wf = g["walk_forward"]
        print(f"  {g['feature']:<28} {g['n']:>4} {g['slope']:>+9.3f}  "
              f"{g['low']:+.3f} to {g['high']:+.3f}"
              + (f"   {wf[0]:.3f} / {wf[1]:.3f}" if wf else "   too few matches")
              + ("   <- HELPS" if g["helps"] else ""))
    helping = [g["feature"] for g in results if g["helps"]]
    print(f"\n  worth wiring in: {helping or 'none yet'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

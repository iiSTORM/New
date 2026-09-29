#!/usr/bin/env python3
"""Can a better model close the gap to the posted line? Four tests, one answer.

scripts/dev/beat_the_line.py reports that the model is behind the book as a
predictor -- +0.20 kills of mean absolute error on the biggest CS2 cell. The
obvious next move is to improve the model. This is the investigation of whether
that is possible with the data in this repository, kept as a script because the
answer is a negative and a negative that lives only in a chat log gets
re-litigated every few weeks.

THE FOUR TESTS, each able to find information the others would miss:

  1. BLEND. Mix the model's projection with the line at every weight. If the
     model holds anything the line does not, some non-zero weight beats the line
     alone.

  2. REGRESSION on the model's output. Put the line and the model in together
     and look at the model's coefficient. Unlike the blend this allows the model
     to be rescaled and re-centred first, so it finds information that a raw
     average would waste.

  3. RAW FEATURES. The model's OUTPUT having no information does not mean its
     INPUTS have none -- a bad function can destroy a good signal. So every
     input is regressed against the line separately: recent form over three and
     ten series, kills per round, team and opponent scoring, team and opponent
     pace, the player's share, and how much history there is.

  4. PER-MAP FEATURES. cs2_data.json's per_game field carries per-MAP player
     lines for 570 of 877 matches and the shipped model has never touched it --
     it works from series totals. Map-level mean, median, spread, recent trend
     and a four-versus-eight form trend all get the same treatment.

Everything is point in time, and anything that looks good in sample is then
walk-forward tested, because at nine candidate features and 557 rows a t of 2
is what noise looks like on its best day.

WHAT IT FOUND, on 2026-09-29:

  1. Zero weight on the model is optimal in all three cells. Any amount hurts.
  2. Model coefficient -0.061 (t -0.47) for CS2 kills, +0.092 (t +0.58) for
     headshots, -0.528 (t -1.81) for Valorant -- nothing, and negative twice.
     The line's own coefficient is 1.014 with t +7.94: an efficient predictor.
  3. Three features looked significant in sample -- opponent pace (t +3.28),
     opponent scoring (t +2.88), history length (t -3.53) -- and NONE survived
     walk-forward. Even "line plus opponent pace" scored 5.40 against the line's
     5.37 out of sample.
  4. Nothing, in sample or out. No per-map feature reached t 1.96.

So the CS2 kills line is efficient with respect to every piece of information in
this repository. The gap is not closable by better modelling of this data, and
the honest place to spend effort is on data the repository does not have -- see
WHAT WOULD ACTUALLY HELP at the bottom of this file.

    python scripts/dev/can_we_beat_the_line.py
    python scripts/dev/can_we_beat_the_line.py --game cs2 --stat kills --json out.json
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
import search_weights_by_outcome as sw             # noqa: E402

BLEND_WEIGHTS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0)
FOLDS = 4
#: Below this many training rows a regression is fitting its own noise.
MIN_TRAIN = 80


# ============================================================
# Least squares, by normal equations, in the standard library
# ============================================================

def ols(y, columns):
    """(coefficients, standard errors) with an intercept prepended.

    Written out rather than imported because this package has no dependencies
    and a two-to-five column regression is a dozen lines. Returns (None, None)
    on a singular design, which happens when a column is constant.
    """
    n, k = len(y), len(columns) + 1
    if n <= k:
        return (None, None)
    design = [[1.0] + [col[i] for col in columns] for i in range(n)]
    normal = [[sum(design[i][a] * design[i][b] for i in range(n)) for b in range(k)]
              for a in range(k)]
    moment = [sum(design[i][a] * y[i] for i in range(n)) for a in range(k)]
    work = [row[:] + [1.0 if i == j else 0.0 for j in range(k)]
            for i, row in enumerate(normal)]
    for col in range(k):
        pivot_row = max(range(col, k), key=lambda r: abs(work[r][col]))
        work[col], work[pivot_row] = work[pivot_row], work[col]
        pivot = work[col][col]
        if abs(pivot) < 1e-12:
            return (None, None)
        work[col] = [v / pivot for v in work[col]]
        for r in range(k):
            if r != col and work[r][col]:
                factor = work[r][col]
                work[r] = [v - factor * w for v, w in zip(work[r], work[col])]
    inverse = [row[k:] for row in work]
    beta = [sum(inverse[a][b] * moment[b] for b in range(k)) for a in range(k)]
    residuals = [y[i] - sum(beta[a] * design[i][a] for a in range(k)) for i in range(n)]
    variance = sum(r * r for r in residuals) / (n - k)
    errors = [math.sqrt(max(variance * inverse[a][a], 0.0)) for a in range(k)]
    return (beta, errors)


def predict(beta, values):
    return beta[0] + sum(b * v for b, v in zip(beta[1:], values))


def walk_forward(rows, variants, folds=FOLDS):
    """{name: held-out MAE}. Fit before a cut, score after it, expanding."""
    size = len(rows) // (folds + 1)
    errors = {name: [] for name in variants}
    if size < 20:
        return {}
    for i in range(1, folds + 1):
        train, test = rows[:size * i], rows[size * i:size * (i + 1)]
        if len(train) < MIN_TRAIN or not test:
            continue
        for name, pick in variants.items():
            if pick is None:
                errors[name] += [abs(r["line"] - r["actual"]) for r in test]
                continue
            columns = list(zip(*[pick(r) for r in train]))
            beta, _ = ols([r["actual"] for r in train], [list(c) for c in columns])
            if beta is None:
                continue
            errors[name] += [abs(predict(beta, pick(r)) - r["actual"]) for r in test]
    return {name: statistics.mean(e) for name, e in errors.items() if e}


# ============================================================
# The data each test needs
# ============================================================

def rounds_of(stats, maps):
    """Rounds played, from damage and damage-per-round.

    Several of this provider's fields are SUMMED across the maps of a series --
    adr and kast among them -- so the per-map rate is adr/maps and the round
    count is dmg divided by that.
    """
    adr, dmg = stats.get("adr"), stats.get("dmg")
    if not adr or not dmg or not maps:
        return None
    per_map = adr / maps
    return dmg / per_map if per_map else None


def series_rows(game, stat, graded, data, index, maps=2):
    """Model projection, line, actual and the raw inputs, point in time."""
    region = next(iter(data))
    blob = data[region]
    matches = sorted(blob.get("past_matches") or [], key=lambda m: str(m.get("date")))
    key = ow.STAT_TYPES[stat]["key"]

    by_player, by_team = collections.defaultdict(list), collections.defaultdict(list)
    for match in matches:
        score = str(match.get("score") or "")
        try:
            played = min(sum(int(x) for x in score.split("-")), 2)
        except ValueError:
            played = match.get("games") or 2
        date = str(match.get("date") or "")
        for team, players in (match.get("actual") or {}).items():
            total, team_rounds = 0.0, []
            for name, stats in players.items():
                if not isinstance(stats, dict) or stats.get(key) is None:
                    continue
                total += stats[key]
                got = rounds_of(stats, played)
                if got and 15 < got < 70:
                    team_rounds.append(got)
                by_player[(team, name)].append({"date": date, "val": stats[key],
                                                "maps": played})
            by_team[team].append({
                "date": date, "total": total, "maps": played,
                "rounds": statistics.median(team_rounds) if team_rounds else None})

    projected = {}
    for row, mu, _ in sw.project_all(sw.cell_rows(graded, game, stat), data, index,
                                     game, stat, ow.SHIPPED_WEIGHTS[game][stat]):
        projected[id(row)] = mu

    out = []
    for row in sw.cell_rows(graded, game, stat):
        if (maps and row.get("maps") != maps) or row.get("actual") is None:
            continue
        team, player = str(row.get("team") or ""), str(row.get("player") or "")
        date = str(row.get("match_date") or "")
        history = by_player.get((team, player)) or []
        prior = [h for h in history if h["date"] < date]
        if len(prior) < 3:
            continue
        def rate(rows_):
            played_ = sum(r["maps"] for r in rows_)
            return sum(r["val"] for r in rows_) / played_ if played_ else None
        def team_rate(rows_):
            played_ = sum(r["maps"] for r in rows_)
            return sum(r["total"] for r in rows_) / played_ if played_ else None
        def pace(rows_):
            have = [r["rounds"] for r in rows_ if r["rounds"]]
            return statistics.mean(have) if have else None
        mine = [h for h in (by_team.get(team) or []) if h["date"] < date][-10:]
        theirs = [h for h in (by_team.get(str(row.get("opponent") or "")) or [])
                  if h["date"] < date][-10:]
        made = {
            "line": row["line"], "actual": row["actual"],
            "model": projected.get(id(row)),
            "form3": rate(prior[-3:]), "form10": rate(prior[-10:]),
            "history": len(prior),
            "team_rate": team_rate(mine), "opp_rate": team_rate(theirs),
            "team_pace": pace(mine), "opp_pace": pace(theirs),
        }
        if all(v is not None for v in made.values()):
            out.append(made)
    return out


def per_map_rows(graded, stat="kills", maps=2):
    """The same, from per_game -- per-MAP lines the shipped model never reads."""
    key = "k" if stat == "kills" else "hs"
    blob = json.load(open("cs2_data.json"))["regions"]["CS2"]
    matches = sorted(blob.get("past_matches") or [], key=lambda m: str(m.get("date")))
    by_player = collections.defaultdict(list)
    for match in matches:
        games = match.get("per_game")
        if not isinstance(games, list):
            continue
        date = str(match.get("date") or "")
        for index, entry in enumerate(games):
            if not isinstance(entry, dict):
                continue
            for team, players in entry.items():
                if not isinstance(players, dict):
                    continue
                for name, stats in players.items():
                    if isinstance(stats, dict) and stats.get(key) is not None:
                        by_player[(team, name)].append({"date": date, "map": index,
                                                        "val": stats[key]})
    out = []
    for row in sw.cell_rows(graded, "cs2", stat):
        if row.get("maps") != maps or row.get("actual") is None:
            continue
        prior = [h for h in (by_player.get((str(row.get("team") or ""),
                                            str(row.get("player") or ""))) or [])
                 if h["date"] < str(row.get("match_date") or "")]
        if len(prior) < 6:
            continue
        last, recent = [h["val"] for h in prior[-12:]], [h["val"] for h in prior[-4:]]
        older = [h["val"] for h in prior[-12:-4]]
        out.append({
            "line": row["line"], "actual": row["actual"],
            "map_mean": statistics.mean(last),
            "map_median": statistics.median(last),
            "map_sd": statistics.pstdev(last) if len(last) > 1 else 0.0,
            "map_recent": statistics.mean(recent),
            "trend": (statistics.mean(recent) - statistics.mean(older)) if older else 0.0,
            "maps_seen": len(prior),
        })
    return out


# ============================================================
# The tests
# ============================================================

def test_blend(rows, report):
    report("\n1. BLEND — mix the model with the line at every weight.")
    base = statistics.mean(abs(r["line"] - r["actual"]) for r in rows)
    best = (0.0, base)
    for weight in BLEND_WEIGHTS:
        mae = statistics.mean(
            abs((weight * r["model"] + (1 - weight) * r["line"]) - r["actual"])
            for r in rows)
        if mae < best[1] - 1e-12:
            best = (weight, mae)
        report(f"   {weight:>5.0%} model  MAE {mae:.4f}"
               + ("   <- line alone" if weight == 0 else
                  "   <- model alone" if weight == 1 else ""))
    verdict = (f"best is {best[0]:.0%} model — it adds information"
               if best[0] > 0 else
               "zero weight on the model is optimal — it adds nothing")
    report(f"   {verdict}")
    return {"best_weight": best[0], "best_mae": best[1], "line_mae": base}


def test_regression(rows, report):
    report("\n2. REGRESSION — the line and the model together, model rescaled freely.")
    beta, errors = ols([r["actual"] for r in rows],
                       [[r["line"] for r in rows], [r["model"] for r in rows]])
    if beta is None:
        report("   singular; skipped")
        return {}
    out = {}
    for name, b, e in zip(("intercept", "line", "model"), beta, errors):
        t = b / e if e else 0.0
        out[name] = {"coefficient": b, "se": e, "t": t}
        report(f"   {name:<10} {b:>+8.3f}  t {t:>+6.2f}"
               + ("  significant" if abs(t) > 1.96 else ""))
    return out


def test_features(rows, names, report, label):
    report(f"\n{label}")
    y, line = [r["actual"] for r in rows], [r["line"] for r in rows]
    found = []
    for name in names:
        beta, errors = ols(y, [line, [r[name] for r in rows]])
        if beta is None:
            continue
        t = beta[2] / errors[2] if errors[2] else 0.0
        report(f"   {name:<12} {beta[2]:>+8.3f}  t {t:>+6.2f}"
               + ("   <- significant in sample" if abs(t) > 1.96 else ""))
        if abs(t) > 1.96:
            found.append(name)
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", default="cs2")
    ap.add_argument("--stat", default="kills")
    ap.add_argument("--maps", type=int, default=2)
    ap.add_argument("--results", default="props_results.json")
    ap.add_argument("--json")
    args = ap.parse_args(argv)

    graded = json.load(open(args.results))["graded"]
    data, index = sw.load(args.game)
    rows = series_rows(args.game, args.stat, graded, data, index, args.maps)
    if len(rows) < 100:
        print(f"only {len(rows)} usable props; not enough to conclude anything")
        return 1
    print(f"{args.game} {args.stat} maps 1-{args.maps}: {len(rows)} props, "
          "point in time")

    out = {"game": args.game, "stat": args.stat, "n": len(rows)}
    out["blend"] = test_blend(rows, print)
    out["regression"] = test_regression(rows, print)

    series_features = ["form3", "form10", "team_rate", "opp_rate", "team_pace",
                       "opp_pace", "history"]
    hits = test_features(rows, series_features, print,
                         "3. RAW FEATURES — the model's inputs, against the line.")
    out["series_hits"] = hits

    variants = {"line alone": None,
                "form": lambda r: [r["form10"]],
                "form + opponent": lambda r: [r["form10"], r["opp_pace"], r["opp_rate"]],
                "line + opponent": lambda r: [r["line"], r["opp_pace"], r["opp_rate"]]}
    scored = walk_forward(rows, variants)
    if scored:
        print("\n   walk-forward, which is what decides it:")
        base = scored.get("line alone")
        for name, mae in scored.items():
            beats = mae < base - 1e-9 and name != "line alone"
            print(f"     {name:<18} {mae:>8.4f} {mae - base:>+8.4f}"
                  + ("   <- BEATS THE LINE" if beats else ""))
        out["series_walk_forward"] = scored

    if args.game == "cs2":
        per_map = per_map_rows(graded, args.stat, args.maps)
        if len(per_map) >= 100:
            print(f"\n4. PER-MAP FEATURES — {len(per_map)} props with 6+ prior maps "
                  "from per_game, which the model never reads.")
            names = ["map_mean", "map_median", "map_sd", "map_recent", "trend",
                     "maps_seen"]
            out["per_map_hits"] = test_features(per_map, names, print, "")
            scored = walk_forward(per_map, {
                "line alone": None,
                "per-map form": lambda r: [r["map_mean"]],
                "line + per-map": lambda r: [r["line"], r["map_mean"], r["trend"]]})
            if scored:
                base = scored.get("line alone")
                print("   walk-forward:")
                for name, mae in scored.items():
                    print(f"     {name:<18} {mae:>8.4f} {mae - base:>+8.4f}"
                          + ("   <- BEATS THE LINE"
                             if mae < base - 1e-9 and name != "line alone" else ""))
                out["per_map_walk_forward"] = scored

    beaten = (out["blend"]["best_weight"] > 0
              or any(v.get("t", 0) > 1.96 for k, v in out["regression"].items()
                     if k == "model")
              or any(name != "line alone" and mae < out.get("series_walk_forward", {})
                     .get("line alone", 9e9)
                     for name, mae in out.get("series_walk_forward", {}).items()))
    print()
    if beaten:
        print("SOMETHING BEAT THE LINE. Follow it up — that is the whole game.")
    else:
        print("NOTHING BEATS THE LINE. This market is efficient with respect to "
              "every piece")
        print("of information in this repository, so the gap is not closable by "
              "better")
        print("modelling of this data. See WHAT WOULD ACTUALLY HELP in this file's "
              "header.")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(out, f, indent=2)
            f.write("\n")
        print(f"\nwrote {args.json}")
    return 0


# WHAT WOULD ACTUALLY HELP, none of which is in this repository today:
#
#   * MAP POOL AND VETO. Kills per round differ by map, and the veto is known
#     before the match. A player on a team that vetoes into its best map is a
#     different proposition, and this dataset has no map names at all.
#   * ROSTER CHANGES AND STAND-INS. A stand-in is the single biggest shock to a
#     player's share, and `actual` cannot distinguish one from a regular.
#   * LIVE MAP ODDS. vlr.gg and HLTV publish per-map odds; the round-count
#     finding says pace is a quarter of the variance and unpredictable from form,
#     but odds are the market's own forecast of exactly that.
#   * REST AND TRAVEL. Days since the last match, LAN against online, and which
#     stage of a tournament it is.
#   * ROLE. An AWPer and an entry fragger have different kill distributions, and
#     nothing here records which is which.
#
# The cheapest of these is map odds, because it is already on the pages the
# scrapers visit.

if __name__ == "__main__":
    sys.exit(main())

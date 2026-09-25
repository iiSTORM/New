"""Does predicting a SHARE of the team's kills beat predicting kills?

The shipped model predicts a player's kills per map directly, then nudges
that number with multiplicative fudges for the opponent and for kill
participation. That treats "how many kills were there to go around" and
"how big a piece did this player take" as one quantity, when they are two,
and only the second is a stable property of the player.

The data says they really are two. In CS2 the coefficient of variation of
a player's raw series kills averages 0.235; of their share of their own
team's kills, 0.160 -- 32% steadier, and steadier for 111 of 131 players
with enough series to measure. Meanwhile team kills per series range from
29 to 237. The model currently has no term for that range at all.

So this measures the decomposition:

    kills = share x team_kills_over_the_window

against the shipped model, on identical rows and the same walk-forward
folds, for each game. Three pace estimators, deliberately including one
that cheats:

  own        the team's own recency-weighted kills per map
  opponent   the same, adjusted by how many kills this opponent has
             historically conceded per map, relative to the league
  ORACLE     the real team kill total for the match being predicted

The oracle is the point of the exercise. It cannot ship -- it reads the
result it is predicting -- but it separates two very different outcomes.
If the oracle is barely better than the shipped model, the decomposition
is wrong and no amount of work on pace estimation will rescue it. If the
oracle is far better, the decomposition is right and everything between
it and the shippable variants is the value of predicting pace well.

  python scripts/dev/experiment_kill_share.py
  python scripts/dev/experiment_kill_share.py --game cs2 --stat kills
"""
import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diagnose_calibration as dc  # noqa: E402
import optimize_weights as ow  # noqa: E402

ROOT = dc.ROOT
GAMES = dc.GAMES
HALF_LIFE = 6  # matches, for the share/pace recency weighting


def team_totals(match, team, stat_key):
    """Every recorded player's stat on one side, summed.

    Verified against CS2's own kp_denominator field, which this reproduces
    exactly on all 282 team-sides that carry it, and every team-side in all
    three games records a full five players -- so this is the team total,
    not a partial one.
    """
    side = (match.get("actual") or {}).get(team)
    if not side:
        return None
    total, seen = 0, 0
    for raw in side.values():
        if isinstance(raw, dict) and raw.get(stat_key) is not None:
            total += raw[stat_key]
            seen += 1
        elif isinstance(raw, (int, float)) and stat_key == "k":
            total += raw
            seen += 1
    return total if seen >= 5 else None


def prior(past_matches, team, cutoff):
    """This team's matches strictly before a date, oldest first."""
    out = [m for m in past_matches
           if m.get("date") and m["date"] < cutoff
           and (m.get("teamA") == team or m.get("teamB") == team)
           and m.get("actual")]
    out.sort(key=lambda m: m["date"])
    return out


def _weighted(pairs):
    """pairs of (value, weight) -> weighted mean, or None."""
    tw = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / tw if tw > 0 else None


def decayed(entries):
    """Recency weights over a list already sorted oldest-first."""
    n = len(entries)
    return [(v, 0.5 ** ((n - 1 - i) / HALF_LIFE)) for i, v in enumerate(entries)]


def share_rate(past_matches, team, player_name, stat_key, cutoff):
    """The player's recency-weighted share of their team's total."""
    vals = []
    for m in prior(past_matches, team, cutoff):
        got = ow.get_actual_stat(m, team, player_name, stat_key)
        if got is None or got == "unavailable":
            continue
        total = team_totals(m, team, stat_key)
        if not total:
            continue
        vals.append(got / total)
    return _weighted(decayed(vals)) if vals else None


def pace_rate(past_matches, team, stat_key, cutoff):
    """The team's recency-weighted total per MAP (not per series)."""
    vals = []
    for m in prior(past_matches, team, cutoff):
        total = team_totals(m, team, stat_key)
        if not total:
            continue
        vals.append(total / ow.maps_counted_for(m))
    return _weighted(decayed(vals)) if vals else None


def conceded_rate(past_matches, team, stat_key, cutoff):
    """What this team's OPPONENTS have totalled per map against them."""
    vals = []
    for m in prior(past_matches, team, cutoff):
        other = m["teamB"] if m["teamA"] == team else m["teamA"]
        total = team_totals(m, other, stat_key)
        if not total:
            continue
        vals.append(total / ow.maps_counted_for(m))
    return _weighted(decayed(vals)) if vals else None


def league_pace(past_matches, teams, stat_key, cutoff):
    vals = [p for p in (pace_rate(past_matches, t, stat_key, cutoff) for t in teams) if p]
    return statistics.fmean(vals) if vals else None


def collect(region_data, stat_type, variant):
    """Rows shaped exactly like optimize_weights' own, so the same fold
    machinery scores both and no difference can come from the split."""
    cfg = ow.STAT_TYPES[stat_type]
    key = cfg["key"]
    rows = []
    for region_key, rd in region_data.items():
        teams = rd.get("teams", {})
        past_matches = rd.get("past_matches", [])
        if not teams or not past_matches:
            continue
        cache = {}
        for match in past_matches:
            cutoff = match.get("date")
            if not cutoff:
                continue
            maps = ow.maps_counted_for(match)
            for side in ("teamA", "teamB"):
                team = match[side]
                opp = match["teamB"] if side == "teamA" else match["teamA"]
                if team not in teams:
                    continue
                if cutoff not in cache:
                    cache[cutoff] = league_pace(past_matches, teams, key, cutoff)
                league = cache[cutoff]
                own_pace = pace_rate(past_matches, team, key, cutoff)
                if own_pace is None:
                    continue

                if variant == "league":
                    # The estimator that follows from pace being noise: if a
                    # team's own history predicts its next pace no better
                    # than chance, the best available estimate of pace is
                    # the league's, and using the team's realised history
                    # instead just imports that noise into the multiplier.
                    if not league:
                        continue
                    window = league * maps
                elif variant == "oracle":
                    window = team_totals(match, team, key)
                    if window is None:
                        continue
                elif variant == "opponent":
                    conceded = conceded_rate(past_matches, opp, key, cutoff)
                    mult = (conceded / league) if (conceded and league) else 1.0
                    window = own_pace * mult * maps
                else:
                    window = own_pace * maps

                for player in teams[team]["players"]:
                    actual = ow.get_actual_stat(match, team, player["name"], key)
                    if actual is None or actual == "unavailable":
                        continue
                    share = share_rate(past_matches, team, player["name"], key, cutoff)
                    if share is None:
                        continue  # no prior grounding, same exclusion the baseline makes
                    rows.append((region_key, cutoff, share * window, actual))
    return rows


# ============================================================
# BLENDING, rather than replacing
# ============================================================
# The replacement results above are mostly worse, which is not the same
# as the signal being worthless: the variants throw away career and the
# history blend, and in CS2 career alone is worth 15-25%. What they show
# is that share x pace carries information the shipped model does not
# have -- clearest on deaths, where it beats the whole shipped model in
# CS2 and Valorant while using none of its machinery.
#
# So this layers it instead, exactly as career is layered: one blend
# weight, swept, scored out-of-sample on walk-forward folds. Both
# predictions are computed in the SAME pass over the same rows, so they
# cannot drift apart or be scored on different samples.


def collect_pairs(region_data, stat_type, weights, pace_variant="league"):
    """(region, date, shipped_prediction, share_prediction, actual) per row."""
    cfg = ow.STAT_TYPES[stat_type]
    key = cfg["key"]
    rows = []
    for region_key, rd in region_data.items():
        teams = rd.get("teams", {})
        past_matches = rd.get("past_matches", [])
        if not teams or not past_matches:
            continue
        cache = {}
        for match in past_matches:
            cutoff = match.get("date")
            if not cutoff:
                continue
            maps = ow.maps_counted_for(match)
            for side in ("teamA", "teamB"):
                team = match[side]
                opp = match["teamB"] if side == "teamA" else match["teamA"]
                if team not in teams:
                    continue
                if cutoff not in cache:
                    cache[cutoff] = league_pace(past_matches, teams, key, cutoff)
                league = cache[cutoff]
                if not league:
                    continue
                if pace_variant == "own":
                    own = pace_rate(past_matches, team, key, cutoff)
                    if own is None:
                        continue
                    window = own * maps
                else:
                    window = league * maps

                for player in teams[team]["players"]:
                    actual = ow.get_actual_stat(match, team, player["name"], key)
                    if actual is None or actual == "unavailable":
                        continue
                    predicted, prior_games = ow.project_point_in_time(
                        past_matches, teams, player, team, opp, maps, weights,
                        cutoff, stat_type, match.get("patch"))
                    if prior_games == 0 and not player.get("hist"):
                        continue  # the baseline's own cold-start exclusion
                    share = share_rate(past_matches, team, player["name"], key, cutoff)
                    if share is None:
                        continue
                    rows.append((region_key, cutoff, predicted, share * window, actual))
    return rows


def blended(rows, w):
    return [(r[0], r[1], (1 - w) * r[2] + w * r[3], r[4]) for r in rows]


def sweep(region_data, stat_type, weights, folds_k, pace_variant):
    rows = collect_pairs(region_data, stat_type, weights, pace_variant)
    if not rows:
        print(f"  {stat_type}: no rows")
        return
    folds = ow.fold_boundaries([r[1] for r in rows], folds_k)

    def score(w):
        b = blended(rows, w)
        return [v for v in (ow.mae_dated(ow.window_rows(b, lo, hi)) for lo, hi in folds) if v]

    base = score(0.0)
    base_mean = statistics.fmean(base)
    print(f"  --- {stat_type} --- n={len(rows)}  shipped OOS MAE {base_mean:.4f}"
          f"  (pace: {pace_variant})")
    best = (0.0, base_mean, len(base))
    for i in range(1, 11):
        w = i / 10
        vals = score(w)
        if not vals:
            continue
        mean = statistics.fmean(vals)
        wins = sum(1 for a, b in zip(base, vals) if b < a)
        flag = ""
        if mean < best[1]:
            best = (w, mean, wins)
        # Only a change that wins MOST folds is worth reporting as real;
        # the repo has been burned before by a mean that moved on one fold.
        if mean < base_mean and wins > len(base) / 2:
            flag = "  <-"
        print(f"      w={w:.1f}  OOS MAE {mean:7.4f}  {100 * (mean - base_mean) / base_mean:+6.2f}%"
              f"  wins {wins}/{len(base)}{flag}")
    if best[0] > 0:
        print(f"      best w={best[0]:.1f} at {100 * (best[1] - base_mean) / base_mean:+.2f}%"
              f" winning {best[2]}/{len(base)} folds")


def report(label, rows, folds):
    if not rows:
        print(f"  {label:26s} (no rows)")
        return None
    vals = [v for v in (ow.mae_dated(ow.window_rows(rows, lo, hi)) for lo, hi in folds) if v]
    if not vals:
        print(f"  {label:26s} (no scored folds)")
        return None
    mean = statistics.fmean(vals)
    print(f"  {label:26s} n={len(rows):5d}  OOS MAE {mean:7.4f}", end="")
    return mean, vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", choices=list(GAMES) + ["all"], default="all")
    ap.add_argument("--stat", choices=list(ow.STAT_TYPES) + ["all"], default="all")
    # Derived, not listed: a stat added to STAT_TYPES is immediately
    # available here rather than failing on an "invalid choice" from a
    # copy of the list that nobody remembered to update.
    ap.add_argument("--folds", type=int, default=6)
    ap.add_argument("--blend", action="store_true",
                    help="Sweep a blend weight for share x pace layered ON TOP of the "
                         "shipped model, instead of comparing it as a replacement.")
    ap.add_argument("--pace", choices=["league", "own"], default="league",
                    help="Which pace estimate the share is multiplied by.")
    args = ap.parse_args()

    table = dc.shipped_weights()
    games = list(GAMES) if args.game == "all" else [args.game]
    stats = list(ow.STAT_TYPES) if args.stat == "all" else [args.stat]

    for game in games:
        print(f"\n=== {game} " + "=" * 58)
        region_data = ow.load_region_data(os.path.join(ROOT, GAMES[game]))
        for stat in stats:
            weights = dict(table[game][stat])
            weights.setdefault("careerRamp", 0)
            ow.clear_point_in_time_caches()
            if args.blend:
                sweep(region_data, stat, weights, args.folds, args.pace)
                continue
            base = ow.collect_predictions_with_dates(region_data, stat, weights)

            variants = {name: collect(region_data, stat, name)
                        for name in ("own", "opponent", "league", "oracle")}

            # Score every variant on the SAME rows the baseline covers, so a
            # variant cannot look better by quietly predicting an easier set.
            shared = set(
                (r[0], r[1]) for r in base
            ).intersection(*[set((r[0], r[1]) for r in v) for v in variants.values()])
            keep = lambda rows: [r for r in rows if (r[0], r[1]) in shared]
            base, variants = keep(base), {k: keep(v) for k, v in variants.items()}
            if not base:
                print(f"  {stat}: no overlapping rows")
                continue

            folds = ow.fold_boundaries([r[1] for r in base], args.folds)
            print(f"\n  --- {stat} ---")
            ref = report("shipped model", base, folds)
            print()
            if not ref:
                continue
            for name, rows in variants.items():
                got = report(f"share x pace ({name})", rows, folds)
                if not got:
                    continue
                change = 100.0 * (got[0] - ref[0]) / ref[0]
                wins = sum(1 for a, b in zip(ref[1], got[1]) if b < a)
                print(f"   {change:+6.2f}%  wins {wins}/{len(ref[1])} folds")


if __name__ == "__main__":
    main()

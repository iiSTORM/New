#!/usr/bin/env python3
"""
Measures whether LoL's career tier is buying real predictive signal or
leaking future data into historical backtests.

THE CONCERN, precisely:

optimize_weights.project_point_in_time() filters almost everything to
strictly-before-cutoff data, but the LoL career path does not:

    career = player.get("career")
    career_rate = career.get(cfg["key"]) if career else None

That's a STATIC number computed once at scrape time from the player's
whole-season aggregate. For a backtested match played mid-season, that
aggregate includes games played AFTER the match being predicted. The
career weight for LoL sits at 0.8-0.85, so this is not a rounding
concern -- it is most of the prediction.

The existing in-code note argues the risk is "smaller in practice since
a whole-season aggregate dilutes any single prediction's overlap." That
argument was written BEFORE SEASON_HALF_LIFE was swept and landed at
0.05. At 0.05 a season one year back carries weight 0.5^20 -- i.e.
effectively zero -- so "career" collapses to approximately the player's
CURRENT-season aggregate alone (scrape_career.py's own docstring says
exactly this, measured: diffs of ~0.00-0.03 vs current-season-only).
There is therefore very little multi-season averaging left to do the
diluting. The dilution argument does not survive the later measurement.

THE TEST:

If career's benefit were real predictive signal, it should help roughly
evenly regardless of WHEN in the season a match was played. If the
benefit is leakage, it should be strongly larger for EARLY-season
matches -- an early match has nearly the whole season's future baked
into the career aggregate it is being scored against, while a late
match has almost none, because by then the aggregate is mostly genuine
past.

So: bucket every backtested prediction by how far through the season it
falls, and in each bucket measure MAE with career at its measured
weight vs career switched off. A gain that decays sharply from early to
late buckets is a leakage signature. A flat gain across buckets is
consistent with real signal.

This deliberately uses only data already on disk -- no re-scrape -- and
runs through optimize_weights' own collect_predictions(), so it measures
the real model path rather than a parallel reimplementation.

Usage:
    python scripts/diagnose_lol_career_leakage.py
Requires: data.json (with career data already merged in).
"""
import sys
from pathlib import Path

# scripts/dev/ (sibling dev modules, e.g. optimize_weights) and
# scripts/ (production modules, e.g. scrape_career) both need to be
# importable regardless of the cwd this is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from optimize_weights import (  # noqa: E402
    load_region_data, STAT_TYPES, project_point_in_time, get_actual_stat,
)

DATA_PATH = "data.json"
BUCKETS = 4  # quartiles of the season timeline

# Real measured LoL weights.
MEASURED_LOL_WEIGHTS = {
    "kills":   {"history": 0.4, "opponent": 0.2, "kp": 0.1, "recencyHalfLife": 20, "patchDiscount": 1.0, "career": 0.8},
    "deaths":  {"history": 0.8, "opponent": 0.2, "kp": 0.3, "recencyHalfLife": 2,  "patchDiscount": 1.0, "career": 0.85},
    "assists": {"history": 0.2, "opponent": 0.2, "kp": 0.4, "recencyHalfLife": 20, "patchDiscount": 1.0, "career": 0.85},
}


def collect_dated_predictions(region_data, stat_type, weights):
    """Deliberately a line-for-line mirror of
    optimize_weights.collect_predictions, with the match date carried
    through as the only addition -- including its get_actual_stat()
    helper, its "unavailable" guard, and its cold-start exclusion. Those
    filters are not incidental: dropping any of them would change which
    predictions are counted, and the MAE numbers below would then no
    longer be comparable to the real backtest this is meant to be
    auditing."""
    cfg = STAT_TYPES[stat_type]
    out = []
    for rd in region_data.values():
        teams = rd.get("teams", {})
        past_matches = rd.get("past_matches", [])
        if not teams or not past_matches:
            continue
        for match in past_matches:
            date = match.get("date")
            if not date:
                continue
            for side in ("teamA", "teamB"):
                team = match[side]
                opp = match["teamB"] if side == "teamA" else match["teamA"]
                if team not in teams:
                    continue
                for player in teams[team]["players"]:
                    actual = get_actual_stat(match, team, player["name"], cfg["key"])
                    if actual is None or actual == "unavailable":
                        continue
                    predicted, prior_games = project_point_in_time(
                        past_matches, teams, player, team, opp, 2, weights,
                        date, stat_type, match.get("patch"),
                    )
                    if prior_games == 0 and not player.get("hist"):
                        continue  # true cold start — matches the real backtest's own exclusion
                    out.append((date, predicted, actual))
    return out


def mae_of(rows):
    if not rows:
        return None
    return sum(abs(p - a) for _, p, a in rows) / len(rows)


def main():
    if not Path(DATA_PATH).exists():
        print(f"! Need {DATA_PATH} — run the normal LoL pipeline first.", file=sys.stderr)
        sys.exit(1)

    region_data = load_region_data(DATA_PATH)
    if not region_data:
        print(f"! {DATA_PATH} loaded but had no regions.", file=sys.stderr)
        sys.exit(1)

    # Sanity check: does career data actually exist here? If it was never
    # merged, every "career on/off" comparison below would be identical
    # and the diagnostic would silently report "no leakage" for the wrong
    # reason.
    have_career = sum(
        1
        for rd in region_data.values()
        for team in rd.get("teams", {}).values()
        for p in team.get("players", [])
        if p.get("career")
    )
    if not have_career:
        print("! No player in data.json has a 'career' field — career data was never merged in, "
              "so this diagnostic has nothing to measure. Run scrape_career.py + merge.py first.",
              file=sys.stderr)
        sys.exit(1)
    print(f"{have_career} players carry career data.\n")

    for stat_type, weights in MEASURED_LOL_WEIGHTS.items():
        career_weight = weights["career"]
        print(f"=== {stat_type.upper()} (measured career weight {career_weight}) ===")

        with_career = collect_dated_predictions(region_data, stat_type, weights)
        without_career = collect_dated_predictions(
            region_data, stat_type, {**weights, "career": 0.0}
        )
        if not with_career:
            print("  no predictions to evaluate\n")
            continue

        dates = sorted({d for d, _, _ in with_career})
        if len(dates) < BUCKETS:
            print(f"  only {len(dates)} distinct match dates — too few to bucket meaningfully\n")
            continue
        # Equal-count date quantiles, so each bucket holds a comparable
        # number of matches rather than a comparable span of calendar
        # time (schedules are not evenly distributed).
        edges = [dates[int(len(dates) * (i + 1) / BUCKETS) - 1] for i in range(BUCKETS)]

        def bucket_of(date):
            for i, edge in enumerate(edges):
                if date <= edge:
                    return i
            return BUCKETS - 1

        print(f"  {'season quartile':<18} {'n':>6}  {'MAE career ON':>14}  {'MAE career OFF':>15}  {'gain':>8}")
        gains = []
        for b in range(BUCKETS):
            on = [r for r in with_career if bucket_of(r[0]) == b]
            off = [r for r in without_career if bucket_of(r[0]) == b]
            mae_on, mae_off = mae_of(on), mae_of(off)
            if mae_on is None or mae_off is None or not mae_off:
                continue
            gain = (mae_off - mae_on) / mae_off * 100
            gains.append(gain)
            label = f"Q{b + 1} ({'earliest' if b == 0 else 'latest' if b == BUCKETS - 1 else 'mid'})"
            print(f"  {label:<18} {len(on):>6}  {mae_on:>14.4f}  {mae_off:>15.4f}  {gain:>7.1f}%")

        if len(gains) >= 2:
            early, late = gains[0], gains[-1]
            print(f"\n  earliest-quartile gain {early:.1f}%  vs  latest-quartile gain {late:.1f}%")
            drop = early - late
            if max(gains) <= 0:
                print("  => Career is not helping at all here — MAE is WORSE with it on, in every\n"
                      "     quartile. That is a different problem from leakage and the leakage\n"
                      "     read below does not apply. Check that career data actually merged\n"
                      "     correctly (right players, right scale) before drawing conclusions.")
            elif early > 0 and drop > max(3.0, 0.4 * early):
                print("  => LEAKAGE SIGNATURE. Career helps markedly more on early-season matches,\n"
                      "     which is exactly what future-data contamination looks like: an early\n"
                      "     match is being scored against an aggregate that is mostly its own\n"
                      "     future. The measured LoL career gains are likely overstated, and the\n"
                      "     fix is the one already proven on CS2 — store raw per-game history\n"
                      "     (scrape_career.py already parses per-game dates, it just discards them\n"
                      "     in season_aggregate()) and compute the baseline point-in-time.")
            elif drop < -3.0:
                # NOTE: deliberately keyed on the trend (late >> early),
                # NOT on `early > 0`. An earlier version required a
                # positive early gain here and so misreported the real
                # kills/assists result -- where the early quartile is
                # slightly NEGATIVE and the late quartile strongly
                # positive -- as "roughly flat". That is the clearest
                # possible not-leakage shape and it was being hidden.
                print("  => No leakage signature — career helps MORE as the season progresses,\n"
                      "     the opposite of the contamination pattern. Consistent with career\n"
                      "     acting as a real, independently-sourced estimate that gets better as\n"
                      "     its own sample fills in.")
                if early < 0:
                    print("     Note: the earliest quartile gain is NEGATIVE — career is actively\n"
                          "     hurting early-season predictions while helping later ones. The\n"
                          "     career weight is currently a fixed constant regardless of how much\n"
                          "     current-season data exists, so this is real headroom: a weight that\n"
                          "     scales with sample maturity should beat a flat one.")
            else:
                print("  => Gain is roughly flat across the season, which is consistent with real\n"
                      "     signal rather than leakage. Worth still fixing the architecture on\n"
                      "     principle, but it is not silently inflating the measured numbers.")
        print()


if __name__ == "__main__":
    main()
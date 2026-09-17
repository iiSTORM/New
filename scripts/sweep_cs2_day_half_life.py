#!/usr/bin/env python3
"""
Backtests CS2_CAREER_DAY_HALF_LIFE — the last real parameter in the CS2
career pipeline that was never actually measured. It was set to 60 days
as a plain guess, explicitly flagged as such in scrape_cs2_career.py's
own comment, and never validated. This matters because the equivalent
LoL parameter (SEASON_HALF_LIFE) turned out to sit nowhere near its
initial guess once swept for real -- so an unmeasured decay constant is
a genuine open risk, not a safe default.

No re-scraping needed. Unlike the LoL case (where the career figure was
a per-season aggregate that had to be rebuilt per candidate),
cs2_data.json already stores each player's RAW per-game history under
"career_games", and optimize_weights.point_in_time_cs2_career_rate()
applies the day-decay at COMPUTE time from that raw list. So sweeping
the constant is a pure recomputation over data already on disk.

Method: override optimize_weights' module-level constant per candidate,
then measure real MAE through optimize_weights.evaluate() -- reusing the
actual backtesting path the app's numbers come from, rather than a
parallel reimplementation that could quietly drift from it.

Two deliberate choices worth stating:

1. Every OTHER weight is held fixed at its already-measured CS2 value
   (see MEASURED_CS2_WEIGHTS) rather than re-optimized per candidate.
   That isolates the half-life question -- "does the career signal
   itself get better or worse at this decay rate" -- instead of
   conflating it with a full re-search. It also uses the real measured
   CS2 weights, NOT optimize_weights.DEFAULT_WEIGHTS, so the sweep runs
   at the model's actual operating point.

2. The candidate list spans from very short (3 days ~ "only the last
   week counts") to effectively infinite (36500 ~ "flat average of the
   whole career history, no decay at all"). Both ends are real
   hypotheses worth testing, and bracketing them is what makes an
   interior optimum meaningful rather than an artifact of a too-narrow
   range.

Usage:
    python scripts/sweep_cs2_day_half_life.py
Requires: cs2_data.json (already produced by the normal pipeline, with
career data merged in via scrape_cs2_career.py + merge_cs2_career_data).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import optimize_weights  # noqa: E402  -- imported as a module so the constant can be overridden
from optimize_weights import evaluate, load_region_data  # noqa: E402

CS2_DATA_PATH = "cs2_data.json"

CANDIDATE_HALF_LIVES = [3, 7, 14, 21, 30, 45, 60, 90, 120, 180, 270, 365, 36500]

# The real, already-measured CS2 weights (from optimize_weights.py
# --game cs2), held fixed so this sweep isolates the half-life question.
# Deliberately NOT DEFAULT_WEIGHTS: those are LoL-shaped and would put
# the sweep at an operating point the CS2 model never actually uses.
MEASURED_CS2_WEIGHTS = {
    "kills":   {"history": 0.3, "opponent": 0.0, "kp": 0.1, "recencyHalfLife": 10, "patchDiscount": 0.4, "career": 1.0},
    "deaths":  {"history": 0.3, "opponent": 0.0, "kp": 0.3, "recencyHalfLife": 2,  "patchDiscount": 0.4, "career": 1.0},
    "assists": {"history": 0.3, "opponent": 0.0, "kp": 0.1, "recencyHalfLife": 10, "patchDiscount": 0.4, "career": 1.0},
}

STAT_KEY = {"kills": "k", "deaths": "d", "assists": "a"}


def career_games_stats(region_data):
    """How much raw career history actually exists to decay over. If the
    median player only has a handful of games inside any plausible
    window, the sweep's result is thin regardless of what MAE says --
    worth surfacing rather than reporting a confident optimum over
    almost no data."""
    counts = []
    for region in region_data.values():
        for team in region.get("teams", {}).values():
            for player in team.get("players", []):
                games = player.get("career_games") or []
                if games:
                    counts.append(len(games))
    if not counts:
        return None
    counts.sort()
    return {
        "players_with_history": len(counts),
        "median_games": counts[len(counts) // 2],
        "min_games": counts[0],
        "max_games": counts[-1],
    }


def main():
    if not Path(CS2_DATA_PATH).exists():
        print(f"! Need {CS2_DATA_PATH} — run the normal CS2 pipeline first.", file=sys.stderr)
        sys.exit(1)

    region_data = load_region_data(CS2_DATA_PATH)
    if not region_data:
        print(f"! {CS2_DATA_PATH} loaded but had no regions.", file=sys.stderr)
        sys.exit(1)

    stats = career_games_stats(region_data)
    if not stats:
        print("! No player in cs2_data.json has a 'career_games' field — career data was never "
              "merged in. Run scrape_cs2_career.py then scrape_cs2.py before sweeping.", file=sys.stderr)
        sys.exit(1)
    print(f"{stats['players_with_history']} players with raw career history "
          f"(median {stats['median_games']} games, range {stats['min_games']}-{stats['max_games']}).")
    print(f"Sweeping {len(CANDIDATE_HALF_LIVES)} candidate half-lives per stat, no re-scraping.\n")

    original = optimize_weights.CS2_CAREER_DAY_HALF_LIFE
    summary = {}
    try:
        for stat_type, weights in MEASURED_CS2_WEIGHTS.items():
            print(f"=== {stat_type.upper()} (all other weights held at measured CS2 values) ===")
            results = []
            for half_life in CANDIDATE_HALF_LIVES:
                optimize_weights.CS2_CAREER_DAY_HALF_LIFE = half_life
                mae = evaluate(region_data, stat_type, weights)
                if mae is None:
                    print(f"  half_life={half_life:>6}  (no data to evaluate)")
                    continue
                results.append((half_life, mae))
                label = f"{half_life:>6}"
                if half_life == original:
                    label += "  <- current"
                if half_life >= 36500:
                    label += "  (~no decay)"
                print(f"  half_life={label}  MAE={mae:.4f}")
            if not results:
                print("  ! nothing evaluated for this stat\n")
                continue
            best_half_life, best_mae = min(results, key=lambda r: r[1])
            current = next((m for hl, m in results if hl == original), None)
            summary[stat_type] = (best_half_life, best_mae, current)
            print(f"  best: half_life={best_half_life}  MAE={best_mae:.4f}")
            if current is not None:
                delta = (current - best_mae) / current * 100 if current else 0
                if best_half_life == original:
                    print(f"  the current guess ({original}) is already optimal in this range.")
                else:
                    print(f"  vs current ({original}): MAE {current:.4f} -> {best_mae:.4f} "
                          f"({delta:+.1f}% improvement)")
            print()
    finally:
        # Always restore, so importing this module can't leave the shared
        # constant mutated for anything else in the same process.
        optimize_weights.CS2_CAREER_DAY_HALF_LIFE = original

    print("=== SUMMARY ===")
    for stat_type, (best_hl, best_mae, current) in summary.items():
        note = ""
        if current is not None and current > 0:
            note = f"  ({(current - best_mae) / current * 100:+.1f}% vs current {original}d)"
        print(f"  {stat_type:<8} best half_life={best_hl:<6} MAE={best_mae:.4f}{note}")

    # Honest read of the shape, not just the argmin. An optimum pinned to
    # either end of the swept range isn't really an optimum -- it means
    # the true value is outside what was tested, or that the parameter is
    # collapsing to a degenerate case ("only the newest game counts" /
    # "decay doesn't matter at all"). Worth saying out loud rather than
    # letting a bare number imply more confidence than it earns.
    lo, hi = CANDIDATE_HALF_LIVES[0], CANDIDATE_HALF_LIVES[-1]
    pinned = [s for s, (hl, _, _) in summary.items() if hl in (lo, hi)]
    if pinned:
        print(f"\n  ! {', '.join(pinned)} landed on a range endpoint — the real optimum may sit "
              f"outside {lo}-{hi} days, or the career signal may be degenerate for that stat "
              f"(at {lo}d it is essentially 'most recent games only'; at {hi}d it is a flat "
              f"undecayed average). Worth widening the range or checking the career signal "
              f"directly before acting on it.")


if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
Tests whether mixing preseason / cup / play-in events into the SAME
history pool as regular season and playoffs actually helps the model, or
whether the extra volume costs more in noise than it buys in coverage.

Why this is a real question rather than tidiness: tournament discovery
took the LoL pool from 241 to 967 series, and a lot of what it added is
not the same kind of competition. `LCS 2026 Lock-In`, `TCL 2026
Kickoff`, `LCK Cup 2026` and `CBLOL Cup 2026` are preseason or side
events -- different stakes, often experimental drafts and rosters. That
is structurally the same concern already confirmed as a real problem in
CS2's career pipeline, where career data drawn from every match
regardless of tier was measuring a different population than the
tier-filtered current-form data it was blended with.

It also plausibly explains something odd in the latest re-measurement:
patchDiscount collapsed to 0.0 for kills and deaths. A pool spanning a
whole year across many patches makes "same patch" rare, so any discount
throws away most of the history -- but part of that year is events we
might not want in the pool at all.

EXPERIMENT DESIGN -- the important part:

Naively filtering matches would change BOTH what the model learns from
AND which matches it is scored on, making the MAEs incomparable (a
smaller, easier evaluation set can look "better" while the model got
worse). So this holds the EVALUATION SET FIXED -- always the regular
season and playoff matches we actually care about predicting -- and
varies only the HISTORY POOL each prediction is allowed to draw on.
Every variant is therefore scored on exactly the same targets.

Requires the `stage` field that scrape_lcs.py now writes
(classify_tournament_stage). If data.json predates that, this exits with
a clear message rather than silently comparing identical pools.

Usage:
    python scripts/experiment_stage_filtering.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from optimize_weights import (  # noqa: E402
    load_region_data, STAT_TYPES, project_point_in_time, get_actual_stat,
    clear_point_in_time_caches,
)

DATA_PATH = "data.json"

# Stages we always want to PREDICT (the evaluation targets). Kept fixed
# across every variant so the MAEs are directly comparable.
EVAL_STAGES = {"regular_season", "playoffs", "finals"}

# The history-pool variants under test.
POOL_VARIANTS = {
    "everything (current behaviour)": None,  # None = no filtering at all
    "exclude preseason": {"preseason"},
    "exclude preseason + cup": {"preseason", "cup"},
    "exclude preseason + cup + play-in": {"preseason", "cup", "play-in"},
    "regular season + playoffs only": {"preseason", "cup", "play-in"},  # same set; kept for clarity below
}

MEASURED_LOL_WEIGHTS = {
    "kills":   {"history": 0.4, "opponent": 0.4, "kp": 0.1, "recencyHalfLife": 8, "patchDiscount": 0.0, "career": 0.4},
    "deaths":  {"history": 0.3, "opponent": 0.2, "kp": 0.3, "recencyHalfLife": 8, "patchDiscount": 0.0, "career": 0.5},
    "assists": {"history": 0.4, "opponent": 0.4, "kp": 0.2, "recencyHalfLife": 8, "patchDiscount": 0.3, "career": 0.3},
}


def evaluate_with_pool(region_data, stat_type, weights, excluded_stages):
    """MAE over a FIXED evaluation set, where each prediction may only
    draw on a history pool with `excluded_stages` removed."""
    cfg = STAT_TYPES[stat_type]
    errors = []
    for rd in region_data.values():
        teams = rd.get("teams", {})
        all_matches = rd.get("past_matches", [])
        if not teams or not all_matches:
            continue
        if excluded_stages:
            pool = [m for m in all_matches
                    if (m.get("stage") or "regular_season") not in excluded_stages]
        else:
            pool = all_matches
        for match in all_matches:
            # Evaluation targets never vary between variants.
            if (match.get("stage") or "regular_season") not in EVAL_STAGES:
                continue
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
                        pool, teams, player, team, opp, 2, weights,
                        date, stat_type, match.get("patch"),
                    )
                    if prior_games == 0 and not player.get("hist"):
                        continue
                    errors.append(abs(predicted - actual))
    if not errors:
        return None, 0
    return sum(errors) / len(errors), len(errors)


def main():
    if not Path(DATA_PATH).exists():
        print(f"! Need {DATA_PATH}.", file=sys.stderr)
        sys.exit(1)
    region_data = load_region_data(DATA_PATH)

    stage_counts = {}
    for rd in region_data.values():
        for m in rd.get("past_matches", []):
            s = m.get("stage")
            stage_counts[s if s else "(missing)"] = stage_counts.get(s if s else "(missing)", 0) + 1

    if set(stage_counts) <= {"(missing)"}:
        print("! No match in data.json carries a 'stage' field, so every pool variant below would\n"
              "  be identical and this experiment is meaningless. Re-run scrape_lcs.py (which now\n"
              "  writes stage via classify_tournament_stage) and merge.py first.", file=sys.stderr)
        sys.exit(1)

    print("Match pool by stage:")
    for stage, n in sorted(stage_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {stage:<18} {n}")
    off_stage = sum(n for s, n in stage_counts.items() if s in ("preseason", "cup", "play-in"))
    total = sum(stage_counts.values())
    print(f"\n{off_stage}/{total} matches ({off_stage / total * 100:.0f}%) are preseason/cup/play-in — "
          f"that is the volume under test.\n")

    # Deduplicate variants that resolve to the same exclusion set, so the
    # output doesn't imply more independent conditions than there are.
    seen, variants = {}, []
    for label, excl in POOL_VARIANTS.items():
        key = frozenset(excl) if excl else None
        if key in seen:
            continue
        seen[key] = label
        variants.append((label, excl))

    for stat_type, weights in MEASURED_LOL_WEIGHTS.items():
        print(f"=== {stat_type.upper()} (measured weights, fixed evaluation set) ===")
        results = []
        for label, excl in variants:
            # Each variant builds its own filtered pools. The caches pin
            # whatever they key on (so results can never collide across
            # pools), which means stale pools would otherwise be held
            # alive for the whole run — clearing here bounds that.
            clear_point_in_time_caches()
            mae, n = evaluate_with_pool(region_data, stat_type, weights, excl)
            if mae is None:
                print(f"  {label:<36} (nothing evaluated)")
                continue
            results.append((label, mae, n))
            print(f"  {label:<36} MAE={mae:.4f}  n={n}")
        if len(results) >= 2:
            baseline = results[0][1]
            best_label, best_mae, _ = min(results, key=lambda r: r[1])
            if best_label == results[0][0]:
                print(f"  -> keeping everything is best; the extra events are net POSITIVE "
                      f"(or at least harmless) for {stat_type}.")
            else:
                delta = (baseline - best_mae) / baseline * 100
                verdict = "a real effect" if delta >= 1.0 else "within noise — not worth acting on"
                print(f"  -> best: '{best_label}' at {best_mae:.4f} vs {baseline:.4f} "
                      f"({delta:+.1f}%) — {verdict}")
        print()

    print("Note: n should be identical across variants within a stat. If it is not, the\n"
          "evaluation set moved and the comparison is invalid — report that rather than\n"
          "trusting the MAEs.")


if __name__ == "__main__":
    main()
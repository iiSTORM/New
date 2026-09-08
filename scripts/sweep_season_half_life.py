#!/usr/bin/env python3
"""
Backtests SEASON_HALF_LIFE — the one real parameter in the career-history
pipeline that was never actually measured (unlike the model's `career`
weight itself, which went through a full optimize_weights.py search).
No new scraping needed: career_data.json already has each player's real
per-season aggregates cached, and decayed_career_baseline() is a pure
function of (season_aggregates, half_life) — this just recomputes
"career" for every candidate half-life from that same cached data, folds
each candidate into a working copy of data.json, and measures real MAE
via optimize_weights.py's own evaluate() — reusing its actual backtesting
logic rather than a separate reimplementation that could quietly drift
from what the app actually does.

The model's `career` WEIGHT is held fixed at its already-measured value
for each stat during this sweep — this isolates the half-life question
cleanly (does the CAREER DATA ITSELF get better or worse with a
different decay rate) rather than re-optimizing every weight
simultaneously, which would conflate two different questions.

Usage:
    python scripts/sweep_season_half_life.py
Requires: data.json, career_data.json (both already present from the
normal pipeline).
"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from scrape_career import decayed_career_baseline, CURRENT_SEASON  # noqa: E402
from optimize_weights import evaluate, load_region_data, DEFAULT_WEIGHTS  # noqa: E402

DATA_PATH = "data.json"
CAREER_DATA_PATH = "career_data.json"

CANDIDATE_HALF_LIVES = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 6.0]

# The model's own career weight, already measured for real via
# optimize_weights.py — held fixed here so this sweep isolates the
# half-life question specifically, not re-searching everything at once.
MEASURED_CAREER_WEIGHT = {
    "kills": 0.8,
    "deaths": 0.85,
    "assists": 0.85,
}


def build_region_data_with_career(base_region_data, career_by_name, half_life):
    """Returns a deep-copied region_data with every player's "career"
    field recomputed from their cached season_aggregates at the given
    half-life — base data.json is never mutated."""
    region_data = copy.deepcopy(base_region_data)
    for region in region_data.values():
        for team in region.get("teams", {}).values():
            for player in team.get("players", []):
                name = player.get("name")
                season_aggregates = career_by_name.get(name)
                if not season_aggregates:
                    player["career"] = None
                    continue
                player["career"] = decayed_career_baseline(season_aggregates, CURRENT_SEASON, half_life_override=half_life)
    return region_data


def main():
    if not Path(DATA_PATH).exists() or not Path(CAREER_DATA_PATH).exists():
        print(f"! Need both {DATA_PATH} and {CAREER_DATA_PATH} — run the normal pipeline first.", file=sys.stderr)
        sys.exit(1)

    base_region_data = load_region_data(DATA_PATH)
    with open(CAREER_DATA_PATH) as f:
        career_data = json.load(f)
    career_by_name = {record["name"]: record["season_aggregates"] for record in career_data.values() if record.get("name")}

    print(f"Loaded {len(career_by_name)} players' cached season data — sweeping {len(CANDIDATE_HALF_LIVES)} "
          f"candidate half-lives per stat, no re-scraping.\n")

    for stat_type, career_weight in MEASURED_CAREER_WEIGHT.items():
        print(f"=== {stat_type.upper()} (career weight held fixed at {career_weight}, as already measured) ===")
        weights = {**DEFAULT_WEIGHTS, "career": career_weight}
        results = []
        for half_life in CANDIDATE_HALF_LIVES:
            region_data = build_region_data_with_career(base_region_data, career_by_name, half_life)
            mae = evaluate(region_data, stat_type, weights)
            results.append((half_life, mae))
            print(f"  half_life={half_life:>4}  MAE={mae:.4f}")
        best_half_life, best_mae = min(results, key=lambda r: r[1])
        print(f"  best: half_life={best_half_life}  MAE={best_mae:.4f}\n")


if __name__ == "__main__":
    main()
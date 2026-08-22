#!/usr/bin/env python3
"""
Backtests the kill-projector model against real historical results and
searches for the weight combination that minimizes prediction error.

This is a direct, function-for-function Python port of the model in
kill-projector.jsx — same recency decay, same point-in-time logic, same
patch-awareness, same fallbacks. The point is to measure "how accurate is
what's actually deployed" and find better weights for it, not to build a
separate idealized model that the app doesn't actually run.

Usage (run from the repo root, where data.json / valorant_data.json live):
    python scripts/optimize_weights.py
    python scripts/optimize_weights.py --stat deaths
    python scripts/optimize_weights.py --game valorant

Outputs:
  1. Baseline MAE (mean absolute error, in kills/deaths/assists per player
     per 2-game window) using the app's current DEFAULT_WEIGHTS.
  2. A coordinate-descent search over the weight space, reporting the best
     weights found and the resulting MAE.
  3. A per-region breakdown so you can see if any one region is dragging
     the average down or behaving very differently from the rest.
"""
import argparse
import json
import re
import sys
from pathlib import Path

# ============================================================
# STAT TYPES — mirrors STAT_TYPES in kill-projector.jsx exactly.
# ============================================================
STAT_TYPES = {
    "kills": {"key": "k", "oppBasis": "d", "useKP": True, "laneSpecific": True},
    "deaths": {"key": "d", "oppBasis": "k", "useKP": False, "laneSpecific": True},
    "assists": {"key": "a", "oppBasis": "d", "useKP": True, "laneSpecific": False},
}

DEFAULT_WEIGHTS = {
    "history": 0.3, "opponent": 1.0, "kp": 0.3,
    "recencyHalfLife": 6, "patchDiscount": 0.4,
}


# ============================================================
# Direct ports of the JS model functions
# ============================================================

def get_actual_stat(match, team, player_name, stat_key):
    raw = (match.get("actual") or {}).get(team, {}).get(player_name)
    if raw is None:
        return None  # undefined equivalent: player didn't appear in this match
    if isinstance(raw, (int, float)):
        return raw if stat_key == "k" else "unavailable"  # legacy kills-only format
    return raw.get(stat_key)


def parse_patch(patch_str):
    if not patch_str:
        return None
    m = re.match(r"^(\d+)\.(\d+)", patch_str)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def compare_patch(a, b):
    if not a or not b:
        return 0
    return a[0] - b[0] if a[0] != b[0] else a[1] - b[1]


def latest_patch(past_matches, cutoff_date):
    best = None
    for m in past_matches:
        if cutoff_date is not None and (not m.get("date") or m["date"] >= cutoff_date):
            continue
        p = parse_patch(m.get("patch"))
        if p and (not best or compare_patch(p, best) > 0):
            best = p
    return best


def recency_weighted_rate(past_matches, team, player_name, stat_key, cutoff_date, half_life,
                           reference_patch, patch_discount):
    entries = []
    for m in past_matches:
        if cutoff_date is not None and (not m.get("date") or m["date"] >= cutoff_date):
            continue
        if not m.get("actual") or team not in (m.get("teamA"), m.get("teamB")) or team not in m["actual"]:
            continue
        val = get_actual_stat(m, team, player_name, stat_key)
        if val is None or val == "unavailable":
            continue
        entries.append({"date": m.get("date") or "", "val": val, "patch": parse_patch(m.get("patch"))})
    if not entries:
        return None, 0
    entries.sort(key=lambda e: e["date"])
    n = len(entries)
    flat = half_life >= 20
    weighted_sum, weighted_games = 0.0, 0.0
    for idx, e in enumerate(entries):
        matches_ago = (n - 1) - idx
        weight = 1.0 if flat else 0.5 ** (matches_ago / half_life)
        if reference_patch and e["patch"] and compare_patch(e["patch"], reference_patch) != 0:
            weight *= (1 - patch_discount)
        weighted_sum += e["val"] * weight
        weighted_games += 2 * weight
    rate = weighted_sum / weighted_games if weighted_games > 0 else None
    return rate, n * 2


def team_stat_per_game(teams, team_name, stat_key):
    players = teams[team_name]["players"]
    distinct_roles = len(set(p.get("role") for p in players))
    divisor = distinct_roles if distinct_roles > 1 else (min(5, len(players)) or 1)
    return sum(p["cur"][stat_key] for p in players) / divisor


def league_avg_stat(teams, stat_key):
    names = list(teams.keys())
    return sum(team_stat_per_game(teams, t, stat_key) for t in names) / len(names)


def point_in_time_team_stat(past_matches, team, stat_key, cutoff_date):
    total, games = 0.0, 0
    for m in past_matches:
        if not m.get("date") or m["date"] >= cutoff_date:
            continue
        opp = m["teamB"] if m["teamA"] == team else (m["teamA"] if m["teamB"] == team else None)
        if not opp or not m.get("actual"):
            continue
        source_team = opp if stat_key == "d" else team
        source_data = m["actual"].get(source_team)
        if not source_data:
            continue
        for player_name in source_data:
            val = get_actual_stat(m, source_team, player_name, "k")
            if isinstance(val, (int, float)):
                total += val
        games += 2
    return total / games if games > 0 else None


def point_in_time_league_avg_stat(past_matches, teams, stat_key, cutoff_date):
    rates = [r for r in (point_in_time_team_stat(past_matches, t, stat_key, cutoff_date) for t in teams) if r is not None]
    return sum(rates) / len(rates) if rates else None


# ============================================================
# Lane-specific opponent adjustment — mirrors the same feature in
# kill-projector.jsx. A player's kills/deaths are compared against the
# specific opponent in their own role, not a team-wide average. Assists
# stay team-wide (STAT_TYPES["assists"]["laneSpecific"] = False) since the
# diagnostic found assists had the strongest team-wide signal of the three.
# ============================================================

def point_in_time_player_names_stat(past_matches, team, player_names, stat_key, cutoff_date):
    total, games = 0.0, 0
    for m in past_matches:
        if not m.get("date") or m["date"] >= cutoff_date:
            continue
        if not m.get("actual") or team not in m["actual"]:
            continue
        for name in player_names:
            val = get_actual_stat(m, team, name, stat_key)
            if isinstance(val, (int, float)):
                total += val
                games += 2
    rate = total / games if games > 0 else None
    return rate, games


def point_in_time_league_avg_for_role(past_matches, teams, role, stat_key, cutoff_date):
    rates = []
    for team_name, team_data in teams.items():
        role_names = [p["name"] for p in team_data["players"] if p.get("role") == role]
        if not role_names:
            continue
        r, _ = point_in_time_player_names_stat(past_matches, team_name, role_names, stat_key, cutoff_date)
        if r is not None:
            rates.append(r)
    return sum(rates) / len(rates) if rates else None


def lane_opponent_multiplier(past_matches, teams, player, opponent_team, opp_strength, opp_basis_key, cutoff_date):
    role = player.get("role")
    if not role or opponent_team not in teams:
        return None
    opponent_role_names = [p["name"] for p in teams[opponent_team]["players"] if p.get("role") == role]
    if not opponent_role_names:
        return None
    opp_stat, _ = point_in_time_player_names_stat(past_matches, opponent_team, opponent_role_names, opp_basis_key, cutoff_date)
    league_avg = point_in_time_league_avg_for_role(past_matches, teams, role, opp_basis_key, cutoff_date)
    if opp_stat is None or not league_avg:
        return None
    return 1 + opp_strength * (opp_stat / league_avg - 1)


def resolve_opponent_multiplier(teams, past_matches, player, opponent_team, opp_strength, cfg, cutoff_date):
    if cfg["laneSpecific"]:
        lane_mult = lane_opponent_multiplier(past_matches, teams, player, opponent_team, opp_strength, cfg["oppBasis"], cutoff_date)
        if lane_mult is not None:
            return lane_mult
    opp_stat_pt = point_in_time_team_stat(past_matches, opponent_team, cfg["oppBasis"], cutoff_date)
    league_avg_pt = point_in_time_league_avg_stat(past_matches, teams, cfg["oppBasis"], cutoff_date)
    opp_stat = opp_stat_pt if opp_stat_pt is not None else team_stat_per_game(teams, opponent_team, cfg["oppBasis"])
    league_avg = league_avg_pt if league_avg_pt is not None else league_avg_stat(teams, cfg["oppBasis"])
    return 1 + opp_strength * (opp_stat / league_avg - 1)


def kp_multiplier(player, history_weight, kp_strength):
    cur_kp = player["cur"]["kp"]
    if not cur_kp:
        return 1.0
    hist_kp = player["hist"]["kp"] if player.get("hist") else cur_kp
    blended_kp = history_weight * hist_kp + (1 - history_weight) * cur_kp
    team_avg_kp = 66.0
    relative = blended_kp / team_avg_kp
    return 1 + kp_strength * (relative - 1)


def project_point_in_time(past_matches, teams, player, team, opponent_team, games, weights,
                           cutoff_date, stat_type, match_patch):
    cfg = STAT_TYPES[stat_type]
    ref_patch = parse_patch(match_patch)
    pt_rate, pt_games = recency_weighted_rate(
        past_matches, team, player["name"], cfg["key"], cutoff_date,
        weights["recencyHalfLife"], ref_patch, weights["patchDiscount"]
    )
    hist_rate = player["hist"][cfg["key"]] if player.get("hist") else None

    if pt_rate is not None:
        base = weights["history"] * hist_rate + (1 - weights["history"]) * pt_rate if hist_rate is not None else pt_rate
    else:
        base = hist_rate if hist_rate is not None else player["cur"][cfg["key"]]

    opp_mult = resolve_opponent_multiplier(teams, past_matches, player, opponent_team, weights["opponent"], cfg, cutoff_date)

    kp_mult = kp_multiplier(player, weights["history"], weights["kp"]) if cfg["useKP"] else 1.0

    per_game = base * opp_mult * kp_mult
    return per_game * games, pt_games


# ============================================================
# Backtest harness
# ============================================================

def load_region_data(path, region_keys=None):
    """Loads a data.json/valorant_data.json file. Returns {region: {teams, past_matches}}."""
    if not Path(path).exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    regions = data.get("regions", {})
    if region_keys:
        regions = {k: v for k, v in regions.items() if k in region_keys}
    return regions


def collect_predictions(region_data, stat_type, weights):
    """Returns list of (region, predicted, actual) for every player-match
    where a real actual value exists — i.e. every point the model is
    actually trying to predict, evaluated point-in-time."""
    cfg = STAT_TYPES[stat_type]
    results = []
    for region_key, rd in region_data.items():
        teams = rd.get("teams", {})
        past_matches = rd.get("past_matches", [])
        if not teams or not past_matches:
            continue
        for match in past_matches:
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
                        match["date"], stat_type, match.get("patch")
                    )
                    if prior_games == 0 and not player.get("hist"):
                        continue  # true cold start with zero grounding — not a fair test of the model
                    results.append((region_key, predicted, actual))
    return results


def mae(results):
    if not results:
        return None
    return sum(abs(p - a) for _, p, a in results) / len(results)


def evaluate(region_data, stat_type, weights):
    return mae(collect_predictions(region_data, stat_type, weights))


def coordinate_descent(region_data, stat_type, start_weights, passes=3):
    candidates = {
        "history": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
        "opponent": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0],
        "kp": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0],
        "recencyHalfLife": [2, 3, 4, 5, 6, 8, 10, 14, 20],
        "patchDiscount": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0],
    }
    cfg = STAT_TYPES[stat_type]
    params = ["history", "opponent", "recencyHalfLife", "patchDiscount"]
    if cfg["useKP"]:
        params.append("kp")

    weights = dict(start_weights)
    best_mae = evaluate(region_data, stat_type, weights)
    print(f"    starting MAE: {best_mae:.4f}" if best_mae is not None else "    no data to evaluate")
    if best_mae is None:
        return weights, best_mae

    for p in range(passes):
        improved = False
        for param in params:
            best_val = weights[param]
            for cand in candidates[param]:
                trial = dict(weights)
                trial[param] = cand
                m = evaluate(region_data, stat_type, trial)
                if m is not None and m < best_mae:
                    best_mae = m
                    best_val = cand
                    improved = True
            weights[param] = best_val
        print(f"    pass {p + 1}: MAE {best_mae:.4f}  weights={weights}")
        if not improved:
            break
    return weights, best_mae


def per_region_breakdown(region_data, stat_type, weights):
    preds = collect_predictions(region_data, stat_type, weights)
    by_region = {}
    for region, p, a in preds:
        by_region.setdefault(region, []).append((region, p, a))
    print(f"    per-region MAE (n = sample size):")
    for region, items in sorted(by_region.items()):
        print(f"      {region:20s} MAE={mae(items):.4f}  n={len(items)}")


# ============================================================
# Opponent-signal diagnosis — the search kept zeroing out `opponent`
# across every stat type, which is a strong enough pattern to actually
# investigate rather than just accept. Two live hypotheses:
#   1. The opponent's point-in-time estimate is noisy early in a split
#      (few prior games backing it), adding variance without real signal.
#   2. The opponent side uses a flat average while the player's own side
#      is recency-weighted — that asymmetry alone could be hurting it.
# This measures whether the opponent-strength signal actually correlates
# with real outcomes at all, and whether that correlation improves once
# the opponent's estimate has more games behind it (confirming #1) or
# stays flat regardless of sample size (pointing elsewhere, e.g. #2 or
# a genuinely weak effect in this data).
# ============================================================

def correlation(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    varx = sum((x - mx) ** 2 for x in xs)
    vary = sum((y - my) ** 2 for y in ys)
    if varx == 0 or vary == 0:
        return None
    return cov / (varx * vary) ** 0.5


def collect_opponent_diagnosis_rows(region_data, stat_type, weights):
    """For each player-match, computes the opponent signal exactly the way
    the model actually would (lane-specific first if this stat uses it,
    falling back to team-wide) — not a separate hardcoded team-wide-only
    calculation. Tracks which path was used per row so the report can show
    how often lane-specific data was actually available versus falling
    back, alongside the correlation itself."""
    cfg = STAT_TYPES[stat_type]
    rows = []
    for region_key, rd in region_data.items():
        teams = rd.get("teams", {})
        past_matches = rd.get("past_matches", [])
        if not teams or not past_matches:
            continue
        for match in past_matches:
            for side in ("teamA", "teamB"):
                team = match[side]
                opp = match["teamB"] if side == "teamA" else match["teamA"]
                if team not in teams or opp not in teams:
                    continue

                for player in teams[team]["players"]:
                    actual = get_actual_stat(match, team, player["name"], cfg["key"])
                    if actual is None or actual == "unavailable":
                        continue
                    own_rate, _ = recency_weighted_rate(
                        past_matches, team, player["name"], cfg["key"], match["date"],
                        weights["recencyHalfLife"], None, 0
                    )
                    if own_rate is None:
                        own_rate = player["hist"][cfg["key"]] if player.get("hist") else None
                    if own_rate is None:
                        continue

                    used_lane = False
                    opp_stat, league_avg, opp_games = None, None, 0
                    if cfg["laneSpecific"]:
                        role = player.get("role")
                        if role:
                            opponent_role_names = [p["name"] for p in teams[opp]["players"] if p.get("role") == role]
                            if opponent_role_names:
                                opp_stat, opp_games = point_in_time_player_names_stat(
                                    past_matches, opp, opponent_role_names, cfg["oppBasis"], match["date"]
                                )
                                league_avg = point_in_time_league_avg_for_role(past_matches, teams, role, cfg["oppBasis"], match["date"])
                                if opp_stat is not None and league_avg:
                                    used_lane = True
                    if not used_lane:
                        opp_stat = point_in_time_team_stat(past_matches, opp, cfg["oppBasis"], match["date"])
                        league_avg_pt = point_in_time_league_avg_stat(past_matches, teams, cfg["oppBasis"], match["date"])
                        league_avg = league_avg_pt if league_avg_pt is not None else league_avg_stat(teams, cfg["oppBasis"])
                        opp_games = 8  # team-wide estimates aggregate ~5x the data of a single role — treat as "stable" by default
                        if opp_stat is None:
                            opp_stat = team_stat_per_game(teams, opp, cfg["oppBasis"])
                    if not opp_stat or not league_avg:
                        continue

                    rows.append({
                        "opp_games": opp_games, "opp_ratio": opp_stat / league_avg,
                        "own_base": own_rate * 2, "actual": actual, "used_lane": used_lane,
                    })
    return rows


def diagnose_opponent_signal(region_data, stat_type, weights, threshold=8):
    rows = collect_opponent_diagnosis_rows(region_data, stat_type, weights)
    if not rows:
        print("  no data to diagnose")
        return
    n = len(rows)
    avg_opp_games = sum(r["opp_games"] for r in rows) / n
    lane_count = sum(1 for r in rows if r["used_lane"])
    print(f"  n={n} predictions, avg opponent-estimate sample size = {avg_opp_games:.1f} prior games")
    if STAT_TYPES[stat_type]["laneSpecific"]:
        print(f"  lane-specific comparison used for {lane_count}/{n} rows ({100*lane_count/n:.0f}%) "
              f"— the rest fell back to team-wide (no current opponent on record for that role, or thin data)")

    def report(subset, label):
        if len(subset) < 10:
            print(f"  {label}: too few samples ({len(subset)}) to report")
            return
        residual = [r["actual"] - r["own_base"] for r in subset]
        opp_dev = [r["opp_ratio"] - 1 for r in subset]
        corr = correlation(opp_dev, residual)
        corr_str = f"{corr:+.3f}" if corr is not None else "undefined"
        print(f"  {label}: n={len(subset)}, corr(opponent deviation, prediction residual) = {corr_str}")

    print(f"  --- {stat_type} ---")
    report(rows, "ALL matches")
    report([r for r in rows if r["opp_games"] < threshold], f"opponent estimate < {threshold} prior games (noisy)")
    report([r for r in rows if r["opp_games"] >= threshold], f"opponent estimate >= {threshold} prior games (stable)")
    print(f"  (positive correlation = signal is real and pointing the expected direction; "
          f"near zero = no usable signal at that sample size; negative = backwards)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stat", choices=["kills", "deaths", "assists", "all"], default="all")
    ap.add_argument("--game", choices=["lol", "valorant", "all"], default="all")
    ap.add_argument("--data", default="data.json")
    ap.add_argument("--valorant-data", default="valorant_data.json")
    ap.add_argument("--diagnose-opponent", action="store_true",
                     help="Investigate whether the opponent-strength signal correlates with real "
                          "outcomes, and whether it improves once the opponent has more prior games "
                          "on record. Run this instead of the normal weight search.")
    ap.add_argument("--min-opp-games", type=int, default=8,
                     help="Threshold (in prior games) for splitting 'noisy' vs 'stable' opponent "
                          "estimates in --diagnose-opponent. Default 8 (~4 matches).")
    args = ap.parse_args()

    region_data = {}
    if args.game in ("lol", "all"):
        region_data.update(load_region_data(args.data))
    if args.game in ("valorant", "all"):
        region_data.update(load_region_data(args.valorant_data))

    if not region_data:
        print("No region data loaded — check --data / --valorant-data paths point at real files "
              "with populated 'teams' and 'past_matches'.", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded regions: {list(region_data.keys())}\n")

    stat_types = ["kills", "deaths", "assists"] if args.stat == "all" else [args.stat]

    if args.diagnose_opponent:
        for stat_type in stat_types:
            diagnose_opponent_signal(region_data, stat_type, DEFAULT_WEIGHTS, args.min_opp_games)
            print()
        return

    summary = []
    for stat_type in stat_types:
        print(f"=== {stat_type.upper()} ===")
        baseline_mae = evaluate(region_data, stat_type, DEFAULT_WEIGHTS)
        if baseline_mae is None:
            print("  no predictable player-matches for this stat (data not populated yet)\n")
            continue
        print(f"  baseline (current defaults) MAE: {baseline_mae:.4f}")
        print(f"  searching...")
        best_weights, best_mae = coordinate_descent(region_data, stat_type, DEFAULT_WEIGHTS)
        improvement_pct = 100 * (baseline_mae - best_mae) / baseline_mae if baseline_mae else 0
        print(f"  best weights found: {best_weights}")
        print(f"  best MAE: {best_mae:.4f}  ({improvement_pct:+.1f}% vs baseline)")
        per_region_breakdown(region_data, stat_type, best_weights)
        print()
        summary.append((stat_type, baseline_mae, best_mae, improvement_pct, best_weights))

    print("=== SUMMARY ===")
    for stat_type, base, best, pct, w in summary:
        print(f"  {stat_type:8s}  baseline={base:.4f}  best={best:.4f}  ({pct:+.1f}%)  weights={w}")


if __name__ == "__main__":
    main()

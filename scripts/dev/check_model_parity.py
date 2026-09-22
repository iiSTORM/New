"""Compare the JS model's predictions with the Python port's, row by row.

The whole measurement apparatus in this repo rests on one assumption:
that optimize_weights.py is a faithful port of the model in src/app.jsx.
Nothing enforced it. When they drift, every weight "measured" in Python
describes a model nobody runs, and the tuning is worse than useless
because it looks rigorous.

They had drifted, in two places at once, and neither was visible from
either side alone:

  - CS2_CAREER_DAY_HALF_LIFE was 60 in the app and 180 in the port and
    the scraper. Both copies carried a comment claiming to match the
    scraper; only one did. Career carries 15-25% of CS2's accuracy.
  - The same function computed elapsed days fractionally in JS and
    floored in Python. career_games carry a full timestamp while match
    dates are bare days, so that difference was live on every row.

Reads predictions produced by tests/model_parity.test.mjs and exits
non-zero if any row disagrees. Invoked by that test; run it directly
only to debug a failure.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diagnose_calibration as dc  # noqa: E402
import optimize_weights as ow  # noqa: E402

# Nine decimals of agreement. The JS side rounds to nine before writing,
# so this is "identical up to that rounding" rather than a tolerance
# anyone chose -- there is no honest reason for the two to differ at all.
TOLERANCE = 1e-6


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("predictions", help="JSON written by the JS side")
    args = ap.parse_args()

    with open(args.predictions) as f:
        js = json.load(f)
    table = dc.shipped_weights()

    failures, checked = [], 0
    for combo, rows in sorted(js.items()):
        game, stat = combo.split("|")
        region_data = ow.load_region_data(os.path.join(dc.ROOT, dc.GAMES[game]))
        ow.clear_point_in_time_caches()
        weights = dict(table[game][stat])
        weights.setdefault("careerRamp", 0)
        worst, worst_row = 0.0, None
        for region_key, match_index, team, player_name, js_value in rows:
            rd = region_data[region_key]
            teams, past = rd["teams"], rd["past_matches"]
            match = past[match_index]
            opp = match["teamB"] if match["teamA"] == team else match["teamA"]
            player = next(p for p in teams[team]["players"] if p["name"] == player_name)
            py_value, _ = ow.project_point_in_time(
                past, teams, player, team, opp, match.get("maps_counted", 2),
                weights, match["date"], stat, match.get("patch"))
            gap = abs(py_value - js_value)
            checked += 1
            if gap > worst:
                worst, worst_row = gap, (region_key, match["date"], team, player_name,
                                         js_value, py_value)
        status = "OK" if worst <= TOLERANCE else "DRIFT"
        print(f"  {combo:20s} n={len(rows):4d}  max |JS - Python| = {worst:.3e}  {status}")
        if worst > TOLERANCE:
            failures.append((combo, worst_row, worst))

    print(f"\n{checked} predictions compared across {len(js)} game/stat combinations.")
    if failures:
        print("\nThe two ports disagree. Every weight measured in Python describes")
        print("a model the app does not run until this is resolved.\n")
        for combo, row, gap in failures:
            region, date, team, player, js_value, py_value = row
            print(f"  {combo}: worst at {region} {date} {team} {player}")
            print(f"    JS {js_value:.6f}  Python {py_value:.6f}  gap {gap:.6f}")
        return 1
    print("The JS model and the Python port agree on every row.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

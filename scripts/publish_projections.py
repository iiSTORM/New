#!/usr/bin/env python3
"""Write the model's projections for the posted board, for the app to show.

Everything this project models has, until now, only existed inside a Python CLI
that runs on a laptop. The app shows an Edges tab built from its own copy of the
model, and nothing else -- so the slate, the calibration, the benchmark and the
rest were invisible to the person they were built for. This is the bridge: the
scrape workflow runs it, commits the result, and the app reads it like every
other data file.

WHAT IS IN IT AND WHAT IS DELIBERATELY NOT. Projections, lines, disagreements
and the model's own accuracy record go in: the build plan says model outputs may
be public, and they are the product. No stake, no bankroll, no bet: those are
private and live in a separate store that never touches this repo.

IT PUBLISHES THE BENCHMARK ALONGSIDE THE PROJECTIONS, which is the point. The
model is currently BEHIND the posted line as a predictor in every cell with
enough data, so a disagreement between the two is not evidence the line is
wrong. Shipping the projections without that number would be shipping an edge
the measurement does not support, and the app is built to show both together.

    python scripts/publish_projections.py
    python scripts/publish_projections.py --out model_projections.json
"""
import argparse
import collections
import json
import os
import statistics
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "dev"))

from propedge.esports import EsportsModel, GAMES     # noqa: E402

OUT = "model_projections.json"
#: How many maps of history before a projection is shown at all.
MIN_MAPS = 3


def benchmark(graded, model):
    """Model MAE against the posted line's, per cell, on the graded record.

    The honest headline: a model that cannot predict the outcome better than the
    line has no information the market lacks, so its disagreements are not
    edges. Computed here rather than imported so the published file is
    self-contained and the app never has to guess.
    """
    cells = collections.defaultdict(lambda: {"model": [], "line": []})
    for row in graded:
        if row.get("result") not in ("over", "under") or row.get("actual") is None:
            continue
        if (row.get("odds_type") or "standard") != "standard":
            continue
        game, stat, maps = row.get("game"), row.get("stat"), row.get("maps")
        if game not in GAMES or not isinstance(maps, int):
            continue
        mean = model.project_mean(game, row.get("player"), stat, maps,
                                  row.get("team"), row.get("opponent"),
                                  row.get("match_date"))
        if mean is None:
            continue
        mu, evidence = mean
        if evidence < MIN_MAPS:
            continue
        key = f"{game} {stat} {'map 1' if maps == 1 else f'maps 1-{maps}'}"
        cells[key]["model"].append(abs(mu - row["actual"]))
        cells[key]["line"].append(abs(row["line"] - row["actual"]))
    out = []
    for key, got in sorted(cells.items()):
        if len(got["model"]) < 30:
            continue
        model_mae = statistics.mean(got["model"])
        line_mae = statistics.mean(got["line"])
        # The gap is derived from the ROUNDED figures, not from the full
        # precision ones, so the file is internally consistent: a reader who
        # subtracts the two numbers printed beside it gets the number printed
        # after it. Off-by-a-thousandth is not a money bug, but a table whose
        # own arithmetic does not check out invites doubt about the rest.
        model_mae, line_mae = round(model_mae, 3), round(line_mae, 3)
        out.append({"cell": key, "n": len(got["model"]),
                    "model_mae": model_mae, "line_mae": line_mae,
                    "gap": round(model_mae - line_mae, 3),
                    "ahead": model_mae < line_mae})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", default="props.json")
    ap.add_argument("--results", default="props_results.json")
    ap.add_argument("--root", default=".")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--skip-benchmark", action="store_true",
                    help="projections only; the benchmark is the slow half")
    args = ap.parse_args(argv)

    if not os.path.exists(args.board):
        print(f"no board at {args.board}; nothing to project")
        return 0
    with open(args.board) as f:
        board = json.load(f)
    graded = []
    if os.path.exists(args.results):
        with open(args.results) as f:
            graded = json.load(f).get("graded") or []

    model = EsportsModel.from_files(args.root, graded, min_maps=MIN_MAPS)
    made = model.project_board(board)
    rows = []
    for projection in made:
        detail = projection.as_json()
        rows.append({
            "game": projection.sport, "player": projection.player,
            "team": projection.team, "opponent": projection.opponent,
            "stat": projection.stat, "maps": projection.maps,
            "line": projection.line, "side": projection.side,
            "odds_type": projection.odds_type,
            "start_time": projection.start_time,
            "p_win": round(projection.p_win, 4),
            "p_push": round(projection.p_push, 4),
            "confidence": projection.confidence,
            "scenarios": {k: round(v, 4) for k, v in projection.scenarios.items()},
            "reasons": list(projection.reasons),
            "prop_id": projection.prop_id,
        })
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "board_fetched_at": board.get("fetched_at"),
        "projections": rows,
        "benchmark": [] if args.skip_benchmark else benchmark(graded, model),
        "graded_props": len(graded),
        # Said in the file rather than only in the app, so it travels with the
        # numbers wherever they end up.
        "caveat": ("The model is measured against the posted line as a predictor. "
                   "Where it is behind, a disagreement between projection and "
                   "line is not evidence the line is wrong."),
    }
    with open(args.out, "w") as f:
        json.dump(payload, f, separators=(",", ":"), sort_keys=True)
        f.write("\n")
    ahead = sum(1 for row in payload["benchmark"] if row["ahead"])
    print(f"{len(rows)} projection(s) from {payload['board_fetched_at']}; "
          f"benchmark over {len(payload['benchmark'])} cell(s), "
          f"model ahead of the line in {ahead}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

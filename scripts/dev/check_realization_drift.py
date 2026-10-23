#!/usr/bin/env python3
"""Do the shipped LINE_REALIZATION factors still match the graded record?

The factors in src/app.jsx and propedge/esports.py are pinned to
line_realization.json and line_realization_slate.json, which are a
measurement taken on one day. The record keeps growing, and a factor that
was right at 1,981 props can be wrong at 4,000. This re-measures both on
the current record and exits 1 when a shipped factor has moved by more
than TOLERANCE, or a cell has gained or lost one, so the weekly workflow
goes red and says which.

It does not rewrite anything. A changed factor changes every edge on the
board; the re-run is one command (printed below) and the diff is worth a
look before it ships.

    python scripts/dev/check_realization_drift.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import line_realization as lr  # noqa: E402

#: Smaller moves than this are sampling wobble, not drift: a day of grading
#: moved the slate's CS2 kills factor 0.46 -> 0.52 inside a 0.12-0.77 interval.
TOLERANCE = 0.10
TARGETS = [("board", lr.board_claim, "line_realization.json", ""),
           ("slate", lr.slate_claim, "line_realization_slate.json", " --slate")]


def drift(shipped, fresh, tolerance=TOLERANCE):
    """[(game, stat, shipped factor or None, fresh factor or None)] that differ."""
    out = []
    for game in sorted(set(shipped) | set(fresh)):
        for stat in sorted(set(shipped.get(game, {})) | set(fresh.get(game, {}))):
            a, b = shipped.get(game, {}).get(stat), fresh.get(game, {}).get(stat)
            if a is None or b is None:
                if a != b:
                    out.append((game, stat, a, b))
            elif abs(a - b) > tolerance:
                out.append((game, stat, a, b))
    return out


def main(argv=None):
    graded = json.load(open("props_results.json"))["graded"]
    lines, bad = [], False
    for name, claim, path, flag in TARGETS:
        shipped = json.load(open(os.path.join(HERE, path)))["shipped"]
        fresh = lr.shipped_factors([lr.measure(g, s, graded, claim) for g, s in lr.CELLS])
        moved = drift(shipped, fresh)
        lines.append(f"## {name}: shipped {json.dumps(shipped)}")
        lines.append(f"fresh   {json.dumps(fresh)}")
        if moved:
            bad = True
            for game, stat, a, b in moved:
                lines.append(f"- **{game} {stat}**: shipped {a}, measured now {b}")
            lines.append(f"Re-measure: `python scripts/dev/line_realization.py{flag} "
                         f"--json scripts/dev/{path}`, then update the constants it pins.")
        else:
            lines.append("no drift beyond the tolerance")
    text = "\n".join(lines)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(text + "\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

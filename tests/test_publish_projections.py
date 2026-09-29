"""The file the app reads, and the one thing it must never omit.

publish_projections.py is the bridge between a model that ran on a laptop and a
page someone actually looks at. The risk it carries is not a crash -- it is
shipping projections that LOOK like picks. The model is currently behind the
posted line as a predictor in every measured cell, so a disagreement between
projection and line is not evidence the line is wrong, and the benchmark that
says so travels in the same file as the numbers it qualifies.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("requests")

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "publish_projections.py"


@pytest.fixture(scope="module")
def published(tmp_path_factory):
    out = tmp_path_factory.mktemp("published") / "model_projections.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=1800)
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(out.read_text())


def test_it_publishes_the_board(published):
    assert published["projections"], "no projections at all"
    assert published["generated_at"] and published["board_fetched_at"]


def test_every_row_has_what_the_app_reads(published):
    for row in published["projections"][:50]:
        for field in ("game", "player", "stat", "maps", "line", "side", "p_win",
                      "confidence", "reasons", "prop_id"):
            assert field in row, field
        assert 0 <= row["p_win"] <= 1
        assert row["side"] in ("over", "under")
        assert row["reasons"], "a projection with no reasoning is a number to trust blindly"


def test_prop_ids_are_unique(published):
    ids = [row["prop_id"] for row in published["projections"]]
    assert len(set(ids)) == len(ids)


def test_the_benchmark_travels_with_the_numbers(published):
    """The load-bearing one. Projections without the benchmark are an edge
    claim the measurement does not support."""
    assert "benchmark" in published
    for row in published["benchmark"]:
        for field in ("cell", "n", "model_mae", "line_mae", "gap", "ahead"):
            assert field in row, field
        assert row["ahead"] == (row["model_mae"] < row["line_mae"])
        assert round(row["model_mae"] - row["line_mae"], 3) == row["gap"]


def test_the_caveat_is_in_the_file_not_only_in_the_app(published):
    assert "behind" in published["caveat"] and "not evidence" in published["caveat"]


def test_no_money_anywhere_in_it(published):
    """This repo is public. Stakes and bankroll live in a private store."""
    blob = json.dumps(published).lower()
    for word in ("stake", "bankroll", "kelly", "payout", "wager"):
        assert word not in blob, f"{word!r} leaked into a public file"


def test_a_missing_board_is_not_a_failure(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--board", str(tmp_path / "nope.json"),
         "--out", str(tmp_path / "out.json")],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300)
    assert result.returncode == 0
    assert "nothing to project" in result.stdout


def test_the_committed_file_matches_what_the_script_produces(published):
    """The app reads the committed file, so a stale one is a silently wrong page."""
    committed = ROOT / "model_projections.json"
    if not committed.exists():
        pytest.skip("model_projections.json has not been generated yet")
    live = json.loads(committed.read_text())
    assert {r["prop_id"] for r in live["projections"]} == \
        {r["prop_id"] for r in published["projections"]}

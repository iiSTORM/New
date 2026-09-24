"""CS2 keeps enough history to rate a player, not just to chart one.

The two constants that decide how much CS2 match history exists were set
for the form chart -- eight matches per team, sixty matches fetched per
run -- on the reasoning that the model leans on the career tier rather
than this list. The second half was wrong where it counts.

Thinning the committed history and re-running the backtest on identical
rows gives kills MAE against the per-team cap:

    cap 2   324 matches   6.6326
    cap 3   388 matches   6.5949
    cap 4   427 matches   6.5243
    cap 6   470 matches   6.4864
    cap 8   481 matches   6.4339

Monotonic, -3.0%, still falling at 8. And the feed reliably returns 100
notable matches per run while MATCH_LIMIT processed 60, so forty already
filtered, already discovered matches were discarded every run.

These pin the DIRECTION rather than the exact values: the reason to keep
them high is measured, so a future change lowering them should have to
argue with a number.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
pytest.importorskip("aiohttp")
import scrape_cs2 as sc

SOURCE = (ROOT / "scripts" / "scrape_cs2.py").read_text(encoding="utf-8")


class TestRetention:
    def test_the_cap_clears_what_the_form_chart_draws(self):
        """Eight is the floor, being what the UI renders. Anything less
        and the chart is short regardless of the model."""
        assert sc.MATCHES_KEPT_PER_TEAM >= 8

    def test_and_goes_past_it_for_accuracy(self):
        """The measured curve is still falling at 8, so stopping there
        leaves the gain on the table."""
        assert sc.MATCHES_KEPT_PER_TEAM > 8

    def test_a_busy_team_actually_accumulates_that_far(self):
        """The constant is only worth raising if the merge honours it."""
        matches = [{"match_id": i, "teamA": "A", "teamB": "B",
                    "date": f"2026-01-{i:02d}", "score": "2-1"}
                   for i in range(1, 26)]
        kept = sc.merge_past_matches(matches, [])
        seen = sum(1 for m in kept if "A" in (m["teamA"], m["teamB"]))
        assert seen == sc.MATCHES_KEPT_PER_TEAM


class TestFetchLimit:
    def test_the_run_processes_what_the_feed_offers(self):
        """The feed returns 100 notable matches; processing 60 threw
        forty away after paying to find them."""
        import re
        m = re.search(r"MATCH_LIMIT = (\d+)", SOURCE)
        assert m, "MATCH_LIMIT is not where this test expects it"
        assert int(m.group(1)) >= 100

    def test_the_limit_still_exists(self):
        """Not removed: an unbounded fetch against a feed that can
        return anything is a different failure."""
        assert "MATCH_LIMIT" in SOURCE
        assert "tier_filtered[:MATCH_LIMIT]" in SOURCE


class TestTheReasonIsWrittenDown:
    def test_the_measurement_is_recorded_beside_the_constant(self):
        """A number raised for a reason nobody can find gets lowered
        again by the next person trying to make a file smaller."""
        assert "6.4339" in SOURCE, "the measured curve should sit with the constant"

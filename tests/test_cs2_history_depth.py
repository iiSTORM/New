"""How much CS2 history to keep, and why the obvious answer was wrong.

Two constants decide how much CS2 match history exists: how many matches
a run processes, and how many are kept per team. Raising both looked
obviously right and only one of them was.

THE TRAP. Thinning the committed history and re-scoring gives a clean
monotonic curve -- kills MAE 6.6326 at cap 2 down to 6.4339 at cap 8,
-3.0%, still falling. It is an artefact. Changing how much history
exists changes WHICH ROWS ARE SCOREABLE, because a row with no prior
history is dropped rather than predicted, so the row count moved from
1604 to 2094 underneath the comparison. Scored on the 1335 rows that
survive at every depth, deeper history is slightly WORSE:

    cap2=6.7220  cap3=6.7606  cap4=6.7415  cap6=6.7843  cap8=6.7846

That is the same selection-versus-treatment mistake as the career
coverage number earlier -- comparing populations and reading it as an
effect. The rule that catches it is the same one: hold the rows fixed
and change only the thing being tested.

So retention stays at 8. MATCH_LIMIT stays raised, on different grounds:
it adds BREADTH rather than depth, since the retention cap bounds depth
at 8 per team regardless, and CS2 regularly puts fixtures on the board
whose opponents carry no player data at all.
"""
import re
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
        """Eight is the floor, being what the UI renders."""
        assert sc.MATCHES_KEPT_PER_TEAM >= 8

    def test_and_is_not_raised_past_it_without_new_evidence(self):
        """Raising it was tried and measured negative on fixed rows. If
        this ever goes up again it should be because someone held the
        rows still and got a different answer."""
        assert sc.MATCHES_KEPT_PER_TEAM == 8

    def test_a_busy_team_accumulates_exactly_that_far(self):
        matches = [{"match_id": i, "teamA": "A", "teamB": "B",
                    "date": f"2026-01-{i:02d}", "score": "2-1"}
                   for i in range(1, 26)]
        kept = sc.merge_past_matches(matches, [])
        seen = sum(1 for m in kept if "A" in (m["teamA"], m["teamB"]))
        assert seen == sc.MATCHES_KEPT_PER_TEAM


class TestFetchLimit:
    def test_the_run_processes_what_the_feed_offers(self):
        """The feed returns 100 notable matches; processing 60 threw
        forty away after paying to discover them."""
        m = re.search(r"MATCH_LIMIT = (\d+)", SOURCE)
        assert m, "MATCH_LIMIT is not where this test expects it"
        assert int(m.group(1)) >= 100

    def test_the_limit_still_exists(self):
        """Not removed: an unbounded fetch against a feed that can
        return anything is a different failure."""
        assert "tier_filtered[:MATCH_LIMIT]" in SOURCE

    def test_it_is_justified_as_coverage_rather_than_accuracy(self):
        """The accuracy argument for it did not survive measurement, and
        the comment should not still be making it."""
        block = SOURCE[SOURCE.index("MATCH_LIMIT = 100") - 1400:
                       SOURCE.index("MATCH_LIMIT = 100")]
        assert "COVERAGE" in block or "BREADTH" in block


class TestTheCorrectionIsRecorded:
    def test_the_artefact_is_written_down_beside_the_constant(self):
        """The thinning curve is genuinely persuasive and genuinely
        wrong. Someone will rediscover it; the note is what stops them
        acting on it a second time."""
        assert "artefact" in SOURCE
        assert "6.7846" in SOURCE, "the fixed-row numbers should sit with the constant"

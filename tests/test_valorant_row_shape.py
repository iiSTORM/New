"""The row-shape report, and the promise that it changes nothing.

vlr.gg's stat table carries FK/FD -- first kills and first deaths -- the
only thing in this data describing HOW a player plays rather than how
much they produce. Every style proxy derivable from k/d/a was measured
against the model's errors and came back near zero (K/D ratio 0.07 at
best, champion pool 0.02), because style is already inside a player's own
rates. FK/FD is not, so it is worth having.

It is not parsed yet on purpose: the column order on that page has never
been seen from this environment, and a guessed regex that mis-binds is
worse than a missing field. These tests hold that line -- the report is
diagnostic only, and k/d/a parsing must be untouched by it.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "scripts" / "scrape_valorant.py"
pytestmark = pytest.mark.skipif(not SRC.exists(), reason="scraper not present")


@pytest.fixture(scope="module")
def source():
    return SRC.read_text(encoding="utf-8")


class TestTheReportIsDiagnosticOnly:
    def test_kda_is_still_read_from_the_same_two_patterns(self, source):
        """The report must not have become a third parse path."""
        assert "m = all_rounds_kda_re.search(row_text) or combined_kda_re.search(row_text)" in source

    def test_nothing_is_bound_from_the_shape_report(self, source):
        """note_row_shape only counts and samples. If it ever starts
        returning values into the stat slots, this stops being safe."""
        body = source[source.index("def note_row_shape("):source.index("    all_rounds_kda_re")]
        for forbidden in ('slot[', 'totals[', '"k"', '"d"', '"a"'):
            assert forbidden not in body, f"{forbidden} means it is parsing, not reporting"

    def test_the_reporter_is_actually_called_on_each_row(self, source):
        """Guards the obvious hole: every other test here passes just as
        happily when the call site is deleted and nothing is ever
        sampled. A mutation run found exactly that."""
        assert "        note_row_shape(row_text)\n" in source, \
            "note_row_shape is defined but never called"
        # And before the parse, so a row that fails both patterns is
        # still sampled -- those are the rows most worth seeing.
        call = source.index("note_row_shape(row_text)")
        parse = source.index("m = all_rounds_kda_re.search(row_text)")
        assert call < parse, "an unparseable row must still be sampled"

    def test_it_reports_once_per_run_not_once_per_row(self, source):
        assert "_ROW_SHAPE_REPORTED" in source
        assert "if row_shape_seen and not _ROW_SHAPE_REPORTED:" in source

    def test_the_sample_is_bounded(self, source):
        assert "ROW_SHAPE_SAMPLES = 4" in source
        assert "if len(row_shape_seen) < ROW_SHAPE_SAMPLES:" in source


class TestTheExistingPatternsStillWork:
    """Both real row shapes, from the comments that documented them."""

    TRIPLE = re.compile(r"(\d+)\s+\d+\s+\d+\s*/\s*(\d+)\s+\d+\s+\d+\s*/\s*(\d+)\s+\d+\s+\d+")
    COMBINED = re.compile(r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)")

    def test_a_triple_row_still_yields_kda(self):
        row = "PlayerOne PO 1.12 222 18 9 9 / 12 6 6 / 4 2 2 +6 74% 158 24% 3 1 1 / 2 1 1"
        assert self.TRIPLE.search(row).groups() == ("18", "12", "4")

    def test_a_combined_row_still_yields_kda(self):
        row = "PlayerTwo PT 0.95 222 11 / 15 / 1 -4"
        assert self.TRIPLE.search(row) is None, "the two shapes must stay disjoint"
        assert self.COMBINED.search(row).groups() == ("11", "15", "1")

    def test_the_shape_counter_sees_more_numbers_on_a_row_carrying_fk_fd(self):
        """The whole point of the report: a row with FK/FD has more
        numbers on it than one without, which is how the next change
        will know the columns are there to read."""
        count = lambda t: len(re.findall(r"-?\d+(?:\.\d+)?%?", t))
        without = "PlayerTwo PT 0.95 222 11 / 15 / 1 -4"
        with_fk = ("PlayerOne PO 1.12 222 18 9 9 / 12 6 6 / 4 2 2 "
                   "+6 74% 158 24% 3 1 1 / 2 1 1")
        assert count(with_fk) > count(without)

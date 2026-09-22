"""Capturing stats beyond kills/deaths/assists.

PrizePicks posts lines on more than the three stats this app models --
"MAPS 1-2 Headshots" turns up in a real payload -- and every one of those
is dropped, because there is no history to project from. The first thing
standing in the way is that the scraper reads three fields out of a row
that may well carry more.

What it may carry is not known here: bo3.gg's players_stats shape is read
in scrape_cs2.py and documented nowhere, and it already spells one field
"death" where you would expect "deaths". So the scraper tries several
spellings and reports what it actually saw, and these cover the part that
can be tested without the network -- what happens once a value arrives,
and, more importantly, what happens when one does not.
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")
pytest.importorskip("cs2api")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scrape_cs2 as sc  # noqa: E402


def row(**over):
    base = {"kills": 20, "death": 15, "assists": 5, "team": "A"}
    base.update(over)
    return base


def capture(source_row):
    """The real picker, not a copy of it. An earlier draft of this file
    reimplemented the logic here and passed while the shipped version had
    a bug the copy did not."""
    return sc.capture_extra_stats(
        source_row, {"k": source_row["kills"], "d": source_row["death"], "a": source_row["assists"]})


class TestCapture:
    def test_headshots_are_taken_when_the_source_offers_them(self):
        assert capture(row(headshots=9))["hs"] == 9

    def test_an_alternative_spelling_is_accepted(self):
        assert capture(row(hs=7))["hs"] == 7

    def test_the_first_listed_spelling_wins_when_several_are_present(self):
        got = capture(row(headshots=9, hs=1))
        assert got["hs"] == 9, "candidates are ordered most-specific first"

    def test_a_source_without_the_field_leaves_the_key_off(self):
        # Not a zero. A zero would read downstream as "played and got none",
        # which is the exact failure this file has been bitten by twice --
        # see the present-but-null notes in scrape_cs2.py.
        assert "hs" not in capture(row())

    def test_present_but_null_is_treated_as_absent(self):
        assert "hs" not in capture(row(headshots=None))

    def test_a_non_numeric_value_is_refused(self):
        assert "hs" not in capture(row(headshots="9"))

    def test_kills_deaths_assists_are_unaffected(self):
        got = capture(row(headshots=9))
        assert (got["k"], got["d"], got["a"]) == (20, 15, 5)


class TestSeriesTotals:
    """A series total must cover every map, or not exist.

    A total summed from the two maps of a Bo3 that happened to report the
    stat looks like a real low number rather than a gap, and a projection
    built on it under-predicts with no sign anything is wrong.
    """

    def combine(self, *maps):
        slot = {"k": 0, "d": 0, "a": 0}
        for m in maps:
            slot["k"] += m["k"]
            sc.accumulate_extra_stats(slot, m)
        return slot

    def test_every_map_reporting_gives_a_real_total(self):
        got = self.combine({"k": 20, "d": 1, "a": 1, "hs": 9}, {"k": 18, "d": 1, "a": 1, "hs": 7})
        assert got["hs"] == 16 and "hs_incomplete" not in got

    def test_one_map_missing_it_discards_the_total(self):
        got = self.combine({"k": 20, "d": 1, "a": 1, "hs": 9}, {"k": 18, "d": 1, "a": 1})
        assert "hs" not in got and got["hs_incomplete"] is True

    def test_the_gap_is_caught_whichever_map_it_is_in(self):
        got = self.combine({"k": 20, "d": 1, "a": 1}, {"k": 18, "d": 1, "a": 1, "hs": 7})
        assert "hs" not in got

    def test_kills_are_still_summed_across_an_incomplete_series(self):
        assert self.combine({"k": 20, "d": 1, "a": 1, "hs": 9}, {"k": 18, "d": 1, "a": 1})["k"] == 38


class TestReporting:
    def test_the_field_map_is_not_empty(self):
        # If this is ever emptied the scraper silently stops looking, and
        # the run's report would claim nothing is on offer.
        assert sc.EXTRA_STAT_FIELDS and "hs" in sc.EXTRA_STAT_FIELDS

    def test_the_report_runs_on_a_payload_that_has_nothing(self, capsys):
        sc.SOURCE_FIELDS_SEEN.clear()
        sc.report_source_fields({"past_matches": []})
        out = capsys.readouterr().out
        assert "cannot be projected" in out, "a missing field must say so, not stay quiet"

    def test_the_report_names_the_spelling_it_found(self, capsys):
        sc.SOURCE_FIELDS_SEEN.clear()
        sc.SOURCE_FIELDS_SEEN.update({"kills", "death", "assists", "headshots", "adr"})
        sc.report_source_fields({"past_matches": [
            {"actual": {"A": {"p": {"k": 1, "d": 1, "a": 1, "hs": 4}}}}]})
        out = capsys.readouterr().out
        assert "FOUND as 'headshots'" in out
        assert "captured on 1 player-series" in out
        assert "adr" in out, "fields the scraper does not read yet must still be reported"

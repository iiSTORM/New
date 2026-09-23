"""Reusing a completed series instead of re-fetching it.

A finished LoL box score does not change, and this scraper was rebuilding
every one of them twice a day: 970 matches at two page loads each is
roughly 1,940 requests to gol.gg per run, for data already sitting in
data.json. Its own comment calls that fetch "the dominant cost of the
whole scrape".

Every test here is about the cost of getting reuse WRONG, which is much
higher than the cost of being slow: a reused box score attached to the
wrong series, or a half-scraped one frozen in place forever, is a silent
wrong number feeding the model.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

pytest.importorskip("requests")
pytest.importorskip("bs4")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scrape_lcs as sl

TODAY = date(2026, 9, 22)


def entry(game_id=101, days_ago=30, actual=None, **over):
    base = {
        "base_game_id": game_id,
        "date": str(TODAY - timedelta(days=days_ago)),
        "teamA": "T1", "teamB": "GEN", "winner": "T1", "score": "2-0",
        "actual": {"T1": {"Faker": {"k": 4, "d": 1, "a": 7}}, "GEN": {}} if actual is None else actual,
    }
    base.update(over)
    return base


def region(*entries):
    return {"past_matches": list(entries)}


class TestWhatIsReused:
    def test_an_old_complete_series_is_reused(self):
        got = sl.reusable_past_matches(region(entry()), today=TODAY)
        assert list(got) == [101]

    def test_keyed_on_the_game_id(self):
        got = sl.reusable_past_matches(region(entry(game_id=555)), today=TODAY)
        assert 555 in got and got[555]["teamA"] == "T1"

    def test_an_empty_region_yields_nothing(self):
        assert sl.reusable_past_matches(region(), today=TODAY) == {}
        assert sl.reusable_past_matches(None, today=TODAY) == {}
        assert sl.reusable_past_matches({}, today=TODAY) == {}


class TestWhatIsRefetched:
    def test_a_recent_series_is_not_reused(self):
        """gol.gg finalises a page some time after the series ends, and
        this repo has already been bitten by a source reporting a finished
        match with fields still null."""
        for days in range(sl.REUSE_GRACE_DAYS):
            assert sl.reusable_past_matches(region(entry(days_ago=days)), today=TODAY) == {}, days

    def test_the_grace_window_has_an_edge_and_it_is_inclusive_of_older(self):
        assert sl.reusable_past_matches(
            region(entry(days_ago=sl.REUSE_GRACE_DAYS)), today=TODAY) != {}

    def test_a_match_played_today_is_never_reused(self):
        """Written with a literal 0 rather than derived from the constant.

        The tests above take their inputs FROM REUSE_GRACE_DAYS, so they
        pass for any value of it including zero -- a mutation setting it to
        0 sailed through all of them. This one states the requirement in
        its own terms: whatever the window is, a series that finished today
        has not been finalised yet and must be fetched.
        """
        assert sl.reusable_past_matches(region(entry(days_ago=0)), today=TODAY) == {}

    def test_yesterday_is_not_reused_either(self):
        assert sl.reusable_past_matches(region(entry(days_ago=1)), today=TODAY) == {}

    def test_the_window_is_a_real_window(self):
        assert sl.REUSE_GRACE_DAYS >= 2, (
            "a window shorter than this stops covering the case it exists for — "
            "a page gol.gg has not finished populating")

    def test_a_record_with_no_game_id_is_not_reused(self):
        """Everything written before ids were stored. One more full run,
        then permanently fast -- rather than guessing at a weaker key."""
        old = entry()
        del old["base_game_id"]
        assert sl.reusable_past_matches(region(old), today=TODAY) == {}

    def test_a_series_with_no_box_score_is_refetched(self):
        assert sl.reusable_past_matches(region(entry(actual={})), today=TODAY) == {}

    def test_a_series_whose_sides_are_all_empty_is_refetched(self):
        assert sl.reusable_past_matches(
            region(entry(actual={"T1": {}, "GEN": {}})), today=TODAY) == {}

    def test_a_malformed_box_score_is_refetched(self):
        assert sl.reusable_past_matches(region(entry(actual="unavailable")), today=TODAY) == {}

    def test_an_undateable_record_is_refetched(self):
        for bad in (None, "", "week 3", 12345):
            assert sl.reusable_past_matches(region(entry(date=bad)), today=TODAY) == {}, bad


class TestNoWrongReuse:
    def test_two_series_between_the_same_teams_on_one_day_stay_separate(self):
        """The reason the key is the game id and not (date, teams). A round
        robin can have these, and attaching one series' box score to the
        other would be silent and wrong."""
        a = entry(game_id=1, actual={"T1": {"Faker": {"k": 9, "d": 1, "a": 2}}, "GEN": {}})
        b = entry(game_id=2, actual={"T1": {"Faker": {"k": 1, "d": 7, "a": 3}}, "GEN": {}})
        got = sl.reusable_past_matches(region(a, b), today=TODAY)
        assert got[1]["actual"]["T1"]["Faker"]["k"] == 9
        assert got[2]["actual"]["T1"]["Faker"]["k"] == 1

    def test_the_entry_is_handed_back_untouched(self):
        original = entry()
        got = sl.reusable_past_matches(region(original), today=TODAY)
        assert got[101] == original

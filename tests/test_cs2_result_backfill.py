"""Fetching results for lines we already posted.

The loop was open at one end. This scraper takes the most recent
NOTABLE matches -- tier a/s or 3+ stars -- while the prop provider posts
lines on a far wider field, so a fixture could be projected, played, and
never scraped. The board could not learn whether its own edges won.

Measured on the committed history: of 724 CS2 lines old enough to grade,
646 were refused for "no completed match on that date", and 645 of those
were dated AFTER the latest match we held for that team. 63 missing
results across 51 teams unlock all 646 -- the record going from 8 graded
matches to about 70.

Which results are worth asking for is the part with logic in it, so that
part is pure and tested here. The fetch that follows reuses the roster
backfill's own proven path.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
pytest.importorskip("aiohttp")
import scrape_cs2 as sc

TODAY = "2026-09-24"


def line(team, date, player="p", game="cs2"):
    return {"game": game, "team": team, "player": player,
            "start_time": f"{date}T10:00:00+00:00", "stat": "kills"}


def played(team, date, opp="Other"):
    return {"teamA": team, "teamB": opp, "date": date, "match_id": f"{team}-{date}"}


class TestWhichTeamsToAsk:
    def test_a_played_fixture_with_no_result_is_wanted(self):
        got = sc.teams_awaiting_results([line("A", "2026-09-22")], [], {"A"}, TODAY)
        assert got == ["A"]

    def test_a_fixture_we_already_have_is_not(self):
        got = sc.teams_awaiting_results([line("A", "2026-09-22")],
                                        [played("A", "2026-09-22")], {"A"}, TODAY)
        assert got == []

    def test_a_result_on_a_different_date_does_not_count(self):
        """The whole failure mode: we hold matches for the team, just not
        the one the line was posted on."""
        got = sc.teams_awaiting_results([line("A", "2026-09-22")],
                                        [played("A", "2026-09-18")], {"A"}, TODAY)
        assert got == ["A"]

    def test_a_fixture_that_has_not_happened_is_not_missing(self):
        got = sc.teams_awaiting_results([line("A", "2026-09-27")], [], {"A"}, TODAY)
        assert got == []

    def test_today_still_counts(self):
        """A match played earlier today is exactly what the next run
        should pick up."""
        got = sc.teams_awaiting_results([line("A", TODAY)], [], {"A"}, TODAY)
        assert got == ["A"]

    def test_a_team_we_do_not_track_is_skipped(self):
        """Nothing to project them with, so a result buys nothing."""
        got = sc.teams_awaiting_results([line("Z", "2026-09-22")], [], {"A"}, TODAY)
        assert got == []

    def test_another_game_is_not_our_problem(self):
        got = sc.teams_awaiting_results([line("A", "2026-09-22", game="valorant")],
                                        [], {"A"}, TODAY)
        assert got == []

    def test_the_busiest_team_comes_first(self):
        """The run is capped, so the order decides how many answers a
        capped run buys.

        Names chosen so alphabetical order DISAGREES with count order --
        the first version used "Busy" and "Quiet", which sort the same
        way and let a plain sorted() pass."""
        rows = ([line("Aardvark", "2026-09-22")]
                + [line("Zebra", "2026-09-22", player=f"p{i}") for i in range(5)])
        got = sc.teams_awaiting_results(rows, [], {"Aardvark", "Zebra"}, TODAY)
        assert got == ["Zebra", "Aardvark"]

    def test_ties_are_ordered_predictably(self):
        rows = [line("B", "2026-09-22"), line("A", "2026-09-22")]
        assert sc.teams_awaiting_results(rows, [], {"A", "B"}, TODAY) == ["A", "B"]

    def test_a_team_is_asked_for_once_however_many_lines_wait(self):
        rows = [line("A", "2026-09-22", player=f"p{i}") for i in range(9)]
        assert sc.teams_awaiting_results(rows, [], {"A"}, TODAY) == ["A"]

    def test_both_sides_of_a_stored_match_count_as_having_it(self):
        got = sc.teams_awaiting_results([line("B", "2026-09-22")],
                                        [{"teamA": "A", "teamB": "B", "date": "2026-09-22"}],
                                        {"B"}, TODAY)
        assert got == []

    def test_a_timestamped_stored_date_still_matches(self):
        """Match dates arrive both ways; comparing a date to a timestamp
        would make every stored match look like a different day."""
        got = sc.teams_awaiting_results(
            [line("A", "2026-09-22")],
            [{"teamA": "A", "teamB": "X", "date": "2026-09-22T18:00:00+00:00"}],
            {"A"}, TODAY)
        assert got == []


class TestItNeverBreaksAScrape:
    def test_no_history_file_is_not_an_error(self, tmp_path):
        assert sc.load_props_history(str(tmp_path / "nope.jsonl")) == []

    def test_one_corrupt_line_does_not_lose_the_rest(self, tmp_path):
        f = tmp_path / "h.jsonl"
        f.write_text(json.dumps(line("A", "2026-09-22")) + "\n{ not json\n"
                     + json.dumps(line("B", "2026-09-22")) + "\n")
        assert len(sc.load_props_history(str(f))) == 2

    def test_blank_lines_are_skipped(self, tmp_path):
        f = tmp_path / "h.jsonl"
        f.write_text("\n" + json.dumps(line("A", "2026-09-22")) + "\n\n")
        assert len(sc.load_props_history(str(f))) == 1

    def test_malformed_rows_do_not_crash_the_selection(self):
        rows = [{}, {"game": "cs2"}, {"game": "cs2", "team": "A"},
                {"game": "cs2", "team": "A", "start_time": "nonsense"},
                line("A", "2026-09-22")]
        assert sc.teams_awaiting_results(rows, [], {"A"}, TODAY) == ["A"]

    def test_a_match_with_no_date_is_ignored_not_fatal(self):
        got = sc.teams_awaiting_results([line("A", "2026-09-22")],
                                        [{"teamA": "A", "teamB": "B"}], {"A"}, TODAY)
        assert got == ["A"]


class TestTheRunIsBounded:
    def test_there_is_a_cap(self):
        """51 teams needed results on the day this was written, and an
        unbounded per-team fetch is how a scrape step starts timing out."""
        assert isinstance(sc.MAX_RESULT_BACKFILL_TEAMS, int)
        assert 0 < sc.MAX_RESULT_BACKFILL_TEAMS <= 60

    def test_both_backfills_share_the_per_team_limit(self):
        """It used to be defined inside the roster backfill's own if
        block, so the results backfill could not see it."""
        assert isinstance(sc.BACKFILL_MATCHES_PER_TEAM, int)

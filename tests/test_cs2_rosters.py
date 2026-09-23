"""Who gets a roster out of the matches already fetched.

More than half of a real CS2 fixture list rendered with no data on either
side -- 47 of 81 upcoming matches -- while big names like ENCE and 3DMAX
showed nothing. None of that was missing data. Their player stats were
sitting in past_matches already: a backfilled match record carries stats
for BOTH teams, and the backfill built roster entries for only the team
it had gone looking for, discarding the opponent's every time. 58 teams
and 298 players were being thrown away at zero request cost.

These cover the builder directly, because the bug was never in the
fetching -- it was in what got read out of what had already been
fetched.
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")
pytest.importorskip("cs2api")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scrape_cs2 as sc  # noqa: E402


def match(team_a, team_b, players_a=("a1", "a2"), players_b=("b1", "b2"), games=2):
    """A processed match record, shaped as the scraper's own pass leaves it."""
    def side(names):
        return {n: {"k": 20, "d": 15, "a": 5, "kp_numerator": 20, "kp_denominator": 30}
                for n in names}
    return {"teamA": team_a, "teamB": team_b, "games": games,
            "actual": {team_a: side(players_a), team_b: side(players_b)}}


def build(matches, past=None, only=None):
    teams, color = {}, {"i": 0}
    sc.add_team_players(matches, past if past is not None else matches, teams, color, only)
    return teams


class TestBothSidesAreRead:
    def test_a_match_rosters_both_of_its_teams(self):
        teams = build([match("ENCE", "3DMAX")])
        assert sorted(teams) == ["3DMAX", "ENCE"]

    def test_the_opponent_of_a_backfilled_team_is_not_discarded(self):
        """The exact shape of the bug.

        The backfill goes looking for Target and gets a record that also
        carries Opponent's stats. Scoping the build to the backfill's own
        list is what dropped Opponent, so this asserts the unrestricted
        call keeps it.
        """
        teams = build([match("Target", "Opponent")])
        assert "Opponent" in teams
        assert [p["name"] for p in teams["Opponent"]["players"]] == ["b1", "b2"]

    def test_players_carry_the_rates_the_model_reads(self):
        teams = build([match("ENCE", "3DMAX")])
        player = teams["ENCE"]["players"][0]
        assert player["cur"]["k"] == 10.0   # 20 kills over 2 games
        assert player["name"] == "a1" and player["hist"] is None


class TestOnlyRestriction:
    """Still available, because the first pass has to be able to report
    which teams it covered before the backfill picks its targets."""

    def test_only_limits_which_teams_are_built(self):
        teams = build([match("Wanted", "Ignored")], only={"Wanted"})
        assert list(teams) == ["Wanted"]

    def test_no_restriction_means_every_team(self):
        teams = build([match("Wanted", "Ignored")], only=None)
        assert sorted(teams) == ["Ignored", "Wanted"]


class TestAggregation:
    def test_rates_span_every_match_on_record_not_just_the_scanned_slice(self):
        """A team rostered off one match is still rated over all of them.

        The backfill scans only the newly-appended tail, so passing the
        tail as the aggregation source too would rate every backfilled
        player off a single game.
        """
        all_past = [match("ENCE", "3DMAX"), match("ENCE", "Other")]
        teams = build(all_past[-1:], past=all_past)
        # 40 kills over 4 games across both matches, not 20 over 2.
        assert teams["ENCE"]["players"][0]["cur"]["g"] == 4

    def test_a_player_is_not_listed_twice_across_matches(self):
        teams = build([match("ENCE", "3DMAX"), match("ENCE", "Other")])
        assert [p["name"] for p in teams["ENCE"]["players"]] == ["a1", "a2"]

    def test_teams_get_distinct_colours(self):
        teams = build([match("A", "B"), match("C", "D")])
        assert len({t["color"] for t in teams.values()}) == 4


class TestDegenerateRecords:
    def test_a_side_with_no_player_stats_still_gets_an_entry(self):
        """The fixture is worth rendering even with an empty roster --
        the app draws the card and omits the projections."""
        m = match("ENCE", "3DMAX")
        m["actual"]["3DMAX"] = {}
        teams = build([m])
        assert teams["3DMAX"]["players"] == []

    def test_a_side_missing_from_actual_entirely_is_survivable(self):
        m = match("ENCE", "3DMAX")
        del m["actual"]["3DMAX"]
        teams = build([m])
        assert teams["3DMAX"]["players"] == []

    def test_no_matches_builds_nothing(self):
        assert build([]) == {}

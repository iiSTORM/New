"""Lending regional rosters to teams that have not played at an event yet.

An event's rosters are aggregated out of played matches, so a team whose
fixtures exist but whose first map has not been played arrives as a name
with nobody behind it, and every line posted on it has no player to
attach to.

Lending used to be all-or-nothing on the REGION -- only an event with no
teams at all got rosters -- and a real run caught what that costs.
Champions had played exactly one match, so its roster was the two teams
in it, and the other fourteen teams with fixtures, every one available
in its home region, were left unprojectable. An event fills up one match
at a time, so "has any teams" was never the same question as "has this
team".
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

pytest.importorskip("bs4", reason="scrape_valorant imports beautifulsoup4 at module level")
pytest.importorskip("aiohttp")
import scrape_valorant as sv


def team(*players):
    return {"color": "#fff", "players": [{"name": n, "cur": {"k": 15}} for n in players]}


def fixture(a, b):
    return {"teamA": a, "teamB": b, "date": "2026-09-24T09:00:00Z"}


def regions():
    return {
        "VCT Pacific": {"teams": {"T1": team("BuZz"), "Paper Rex": team("f0rsakeN")},
                        "past_matches": [], "upcoming_matches": []},
        "VCT EMEA": {"teams": {"Team Liquid": team("kamo")},
                     "past_matches": [], "upcoming_matches": []},
        "VCT Champions": {"teams": {}, "past_matches": [],
                          "upcoming_matches": [fixture("Team Liquid", "Paper Rex"),
                                               fixture("T1", "Some Qualifier")]},
    }


class TestLending:
    def test_teams_come_from_their_home_regions(self):
        r = regions()
        sv.lend_rosters_from_home_regions(r)
        assert sorted(r["VCT Champions"]["teams"]) == ["Paper Rex", "T1", "Team Liquid"]

    def test_the_players_come_with_them(self):
        """The point of the exercise: a line is posted on a player, and
        without the roster there is nobody for it to attach to."""
        r = regions()
        sv.lend_rosters_from_home_regions(r)
        names = [p["name"] for p in r["VCT Champions"]["teams"]["T1"]["players"]]
        assert names == ["BuZz"]

    def test_a_team_in_no_region_is_reported_not_invented(self):
        r = regions()
        report = sv.lend_rosters_from_home_regions(r)
        missing = [m for _, _, m in report][0]
        assert missing == ["Some Qualifier"]
        assert "Some Qualifier" not in r["VCT Champions"]["teams"]

    def test_it_is_flagged_so_the_provenance_is_not_lost(self):
        """These numbers are a regional season's, not this event's."""
        r = regions()
        sv.lend_rosters_from_home_regions(r)
        assert r["VCT Champions"]["rosters_from_home_regions"] is True

    def test_the_donor_is_not_aliased(self):
        """Copied, not shared: a later edit to one must not reach into the
        other, and both get serialised into the same file."""
        r = regions()
        sv.lend_rosters_from_home_regions(r)
        r["VCT Champions"]["teams"]["T1"]["players"].append({"name": "ghost"})
        assert [p["name"] for p in r["VCT Pacific"]["teams"]["T1"]["players"]] == ["BuZz"]

    def test_a_team_that_has_played_here_keeps_its_own_numbers(self):
        """The half of the old rule that was right. Once a team has
        played at the event, the event's own numbers are the better data
        and must not be overwritten with regional form."""
        r = regions()
        r["VCT Champions"]["teams"] = {"T1": team("SomeoneElse")}
        sv.lend_rosters_from_home_regions(r)
        assert [p["name"] for p in r["VCT Champions"]["teams"]["T1"]["players"]] == ["SomeoneElse"]
        assert "from_home_region" not in r["VCT Champions"]["teams"]["T1"]

    def test_its_opponents_are_still_lent_to(self):
        """The bug, exactly. One played match made the region non-empty
        and every other team with a fixture went unprojectable."""
        r = regions()
        r["VCT Champions"]["teams"] = {"T1": team("SomeoneElse")}
        r["VCT Champions"]["past_matches"] = [{"teamA": "T1", "teamB": "x"}]
        sv.lend_rosters_from_home_regions(r)
        assert sorted(r["VCT Champions"]["teams"]) == ["Paper Rex", "T1", "Team Liquid"]
        assert [p["name"] for p in r["VCT Champions"]["teams"]["Team Liquid"]["players"]] == ["kamo"]

    def test_a_borrowed_team_says_where_it_came_from(self):
        """Region-wide provenance stopped being enough the moment some
        teams here are the event's own and some are not."""
        r = regions()
        sv.lend_rosters_from_home_regions(r)
        assert r["VCT Champions"]["teams"]["T1"]["from_home_region"] == "VCT Pacific"
        assert r["VCT Champions"]["teams"]["Team Liquid"]["from_home_region"] == "VCT EMEA"

    def test_a_team_already_here_is_never_marked_borrowed(self):
        """It is in the donor index too -- every region contributes to
        it. Being present here is what decides, not who else has it."""
        r = regions()
        r["VCT Champions"]["teams"] = {"Paper Rex": team("Jinggg")}
        sv.lend_rosters_from_home_regions(r)
        assert "from_home_region" not in r["VCT Champions"]["teams"]["Paper Rex"]
        assert [p["name"] for p in r["VCT Champions"]["teams"]["Paper Rex"]["players"]] == ["Jinggg"]

    def test_the_donor_is_where_the_team_has_actually_played(self):
        """A team sits in more than one region whenever an international
        event is running, and the point of borrowing is a season of
        form. Whichever region came first in REGIONS order is not that."""
        # T1 sits in VCT Pacific, which iterates FIRST, with one match.
        # The deep entry is in the region that comes after it, so "first
        # region wins" and "deepest region wins" give different answers.
        r = regions()
        r["VCT Pacific"]["past_matches"] = [{"teamA": "T1", "teamB": "x", "date": "2026-01-01"}]
        r["VCT EMEA"]["teams"]["T1"] = team("TheRealT1")
        r["VCT EMEA"]["past_matches"] = [
            {"teamA": "T1", "teamB": "Gen.G", "date": f"2026-0{i}-01"} for i in range(1, 8)]
        sv.lend_rosters_from_home_regions(r)
        lent = r["VCT Champions"]["teams"]["T1"]
        assert lent["from_home_region"] == "VCT EMEA", "seven matches beats one"
        assert [p["name"] for p in lent["players"]] == ["TheRealT1"]

    def test_a_team_that_has_played_nowhere_is_still_lent(self):
        """Depth is a tie-break, not a requirement. A roster with no
        matches behind it is still better than no roster."""
        r = regions()
        sv.lend_rosters_from_home_regions(r)
        assert "Team Liquid" in r["VCT Champions"]["teams"]

    def test_only_regions_that_needed_something_are_reported(self):
        """The caller prints one line per entry. A region that was
        already complete has nothing to say."""
        r = regions()
        # Fixtures, but between two teams it already has: nothing to do.
        r["VCT Pacific"]["upcoming_matches"] = [fixture("T1", "Paper Rex")]
        report = sv.lend_rosters_from_home_regions(r)
        assert [key for key, _, _ in report] == ["VCT Champions"]

    def test_a_home_region_is_lent_to_as_well(self):
        """Lending is not a Champions special case. A regional event has
        fixtures too, and a team that has not played one of them yet is
        in exactly the same position."""
        r = regions()
        r["VCT Pacific"]["upcoming_matches"] = [fixture("T1", "Team Liquid")]
        sv.lend_rosters_from_home_regions(r)
        assert r["VCT Pacific"]["teams"]["Team Liquid"]["from_home_region"] == "VCT EMEA"
        assert "from_home_region" not in r["VCT Pacific"]["teams"]["T1"]

    def test_a_region_that_needs_nothing_is_not_flagged(self):
        r = regions()
        r["VCT Champions"]["teams"] = {"Team Liquid": team("a"), "Paper Rex": team("b"),
                                       "T1": team("c"), "Some Qualifier": team("d")}
        sv.lend_rosters_from_home_regions(r)
        assert "rosters_from_home_regions" not in r["VCT Champions"]

    def test_a_region_with_no_fixtures_is_left_alone(self):
        r = regions()
        r["VCT Champions"]["upcoming_matches"] = []
        sv.lend_rosters_from_home_regions(r)
        assert r["VCT Champions"]["teams"] == {}

    def test_tbd_is_not_a_team(self):
        r = regions()
        r["VCT Champions"]["upcoming_matches"].append(fixture("TBD", "TBD"))
        report = sv.lend_rosters_from_home_regions(r)
        assert "TBD" not in [m for _, _, m in report][0]

    def test_nothing_to_lend_is_not_an_error(self):
        r = {"E": {"teams": {}, "past_matches": [], "upcoming_matches": [fixture("A", "B")]}}
        sv.lend_rosters_from_home_regions(r)
        assert r["E"]["teams"] == {}
        assert "rosters_from_home_regions" not in r["E"]

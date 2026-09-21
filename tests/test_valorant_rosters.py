"""Lending regional rosters to an international event that has not started.

An event's rosters are aggregated out of played matches, so one whose
fixtures exist but whose first map has not been played arrives as a
schedule with nobody in it. A real run confirmed it: Champions returned
34 fixtures and 0 teams, which leaves every line posted on that event with
no player to attach to.
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
        sv.lend_rosters_to_eventless_regions(r)
        assert sorted(r["VCT Champions"]["teams"]) == ["Paper Rex", "T1", "Team Liquid"]

    def test_the_players_come_with_them(self):
        """The point of the exercise: a line is posted on a player, and
        without the roster there is nobody for it to attach to."""
        r = regions()
        sv.lend_rosters_to_eventless_regions(r)
        names = [p["name"] for p in r["VCT Champions"]["teams"]["T1"]["players"]]
        assert names == ["BuZz"]

    def test_a_team_in_no_region_is_reported_not_invented(self):
        r = regions()
        report = sv.lend_rosters_to_eventless_regions(r)
        missing = [m for _, _, m in report][0]
        assert missing == ["Some Qualifier"]
        assert "Some Qualifier" not in r["VCT Champions"]["teams"]

    def test_it_is_flagged_so_the_provenance_is_not_lost(self):
        """These numbers are a regional season's, not this event's."""
        r = regions()
        sv.lend_rosters_to_eventless_regions(r)
        assert r["VCT Champions"]["rosters_from_home_regions"] is True

    def test_the_donor_is_not_aliased(self):
        """Copied, not shared: a later edit to one must not reach into the
        other, and both get serialised into the same file."""
        r = regions()
        sv.lend_rosters_to_eventless_regions(r)
        r["VCT Champions"]["teams"]["T1"]["players"].append({"name": "ghost"})
        assert [p["name"] for p in r["VCT Pacific"]["teams"]["T1"]["players"]] == ["BuZz"]

    def test_a_region_mid_event_keeps_its_own_rosters(self):
        """Once a single map has been played, the event's own numbers are
        the better data and must not be overwritten with regional form."""
        r = regions()
        r["VCT Champions"]["teams"] = {"T1": team("SomeoneElse")}
        sv.lend_rosters_to_eventless_regions(r)
        assert [p["name"] for p in r["VCT Champions"]["teams"]["T1"]["players"]] == ["SomeoneElse"]
        assert "rosters_from_home_regions" not in r["VCT Champions"]

    def test_a_region_with_no_fixtures_is_left_alone(self):
        r = regions()
        r["VCT Champions"]["upcoming_matches"] = []
        sv.lend_rosters_to_eventless_regions(r)
        assert r["VCT Champions"]["teams"] == {}

    def test_tbd_is_not_a_team(self):
        r = regions()
        r["VCT Champions"]["upcoming_matches"].append(fixture("TBD", "TBD"))
        report = sv.lend_rosters_to_eventless_regions(r)
        assert "TBD" not in [m for _, _, m in report][0]

    def test_nothing_to_lend_is_not_an_error(self):
        r = {"E": {"teams": {}, "past_matches": [], "upcoming_matches": [fixture("A", "B")]}}
        sv.lend_rosters_to_eventless_regions(r)
        assert r["E"]["teams"] == {}
        assert "rosters_from_home_regions" not in r["E"]

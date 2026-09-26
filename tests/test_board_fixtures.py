"""Tests for inferring fixtures from the posted board.

The whole module exists to make invisible lines visible, so the failure it
has to be held against is not a crash: it is publishing a fixture that is
wrong, or one the app then refuses to hang the very lines on. Both are
silent. Hence the emphasis below on what it must REFUSE to do — never pair
two teams off a shared kickoff, never add a fixture for a game another
region already lists, never fill a hole it cannot fill uniquely.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import board_fixtures as bf
import infer_fixtures

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def stamp(hours):
    return (NOW + timedelta(hours=hours)).isoformat()


def region(teams, upcoming=()):
    """A region as the data files hold one: teams -> players, plus fixtures."""
    return {
        "teams": {name: {"players": [{"name": p} for p in players]}
                  for name, players in teams.items()},
        "upcoming_matches": [dict(m) for m in upcoming],
    }


def board(rows, fetched_at=None, game="lol"):
    by_player = {}
    for row in rows:
        by_player.setdefault(row["player"], []).append(row)
    return {"fetched_at": fetched_at or NOW.isoformat(), "props": {game: by_player}}


def row(player, team, when, region_name="LCS", stat="kills", maps=3):
    return {"player": player, "team": team, "start_time": when,
            "region": region_name, "stat": stat, "maps": maps, "line": 10.5}


class TestIsPlaceholder:
    @pytest.mark.parametrize("name", ["TBD", "tbd", " TBD ", "", "   ", "?", None])
    def test_recognises_a_placeholder(self, name):
        assert bf.is_placeholder(name)

    @pytest.mark.parametrize("name", ["LYON", "T1", "TBD Gaming"])
    def test_a_real_team_is_not_one(self, name):
        assert not bf.is_placeholder(name)


class TestParseStamp:
    def test_reads_an_offset(self):
        assert bf.parse_stamp("2026-09-26T05:00:00.000-04:00") == \
            datetime(2026, 9, 26, 9, 0, tzinfo=timezone.utc)

    def test_reads_utc(self):
        assert bf.parse_stamp("2026-09-26T20:00:00Z") == \
            datetime(2026, 9, 26, 20, 0, tzinfo=timezone.utc)

    def test_reads_a_space_separator(self):
        assert bf.parse_stamp("2026-09-26 20:00:00+00:00") == \
            datetime(2026, 9, 26, 20, 0, tzinfo=timezone.utc)

    def test_a_naive_stamp_is_read_as_utc(self):
        """Not as local time: the workflow runner's zone is not the data's."""
        assert bf.parse_stamp("2026-09-26T20:00:00").tzinfo == timezone.utc

    @pytest.mark.parametrize("raw", [None, "", "   ", "not a date", 20260926])
    def test_refuses_what_it_cannot_read(self, raw):
        assert bf.parse_stamp(raw) is None


class TestTeamIndex:
    def test_maps_a_handle_to_its_team(self):
        index = bf.team_index(region({"LYON": ["Bvoy", "Contractz"]}))
        assert index["bvoy"] == {"LYON"}

    def test_keeps_every_team_a_shared_handle_is_on(self):
        index = bf.team_index(region({"paiN": ["tatu"], "paiN Academy": ["tatu"]}))
        assert index["tatu"] == {"paiN", "paiN Academy"}

    def test_ignores_a_nameless_player(self):
        data = {"teams": {"LYON": {"players": [{"role": "top"}, {"name": "  "}]}}}
        assert bf.team_index(data) == {}

    def test_survives_a_region_with_no_teams(self):
        assert bf.team_index({}) == {}


class TestResolveTeam:
    def test_a_unique_handle_resolves_without_the_board_naming_the_team(self):
        """The point of keying on the player: the board says 'The Huns' and
        the roster says 'The Huns Esports', and no name map is needed."""
        index = bf.team_index(region({"The Huns Esports": ["Cozen"]}))
        assert bf.resolve_team(row("Cozen", "The Huns", stamp(1)), index) == "The Huns Esports"

    def test_a_shared_handle_is_settled_by_the_team_the_board_states(self):
        index = bf.team_index(region({"paiN": ["tatu"], "paiN Academy": ["tatu"]}))
        assert bf.resolve_team(row("tatu", "paiN", stamp(1)), index) == "paiN"
        assert bf.resolve_team(row("tatu", "paiN Academy", stamp(1)), index) == "paiN Academy"

    def test_the_tiebreak_is_case_insensitive(self):
        index = bf.team_index(region({"paiN": ["tatu"], "paiN Academy": ["tatu"]}))
        assert bf.resolve_team(row("tatu", "PAIN", stamp(1)), index) == "paiN"

    def test_refuses_a_shared_handle_the_board_does_not_settle(self):
        """Guessing here projects the line off the wrong history and prints
        an academy fixture as a main-roster one."""
        index = bf.team_index(region({"paiN": ["tatu"], "paiN Academy": ["tatu"]}))
        assert bf.resolve_team(row("tatu", "Furia", stamp(1)), index) is None

    def test_the_tiebreak_is_equality_and_not_containment(self):
        """Every roster pair this has to separate is a containment pair —
        paiN/paiN Academy, The Huns/The Huns Esports, Nemesis/NemNemesis — so
        a substring rule matches BOTH candidates and then answers with
        whichever the set yielded first."""
        for pair, stated, expected in (
                (("paiN", "paiN Academy"), "paiN", "paiN"),
                (("The Huns", "The Huns Esports"), "The Huns", "The Huns"),
                (("Nemesis", "NemNemesis"), "Nemesis", "Nemesis")):
            index = bf.team_index(region({name: ["shared"] for name in pair}))
            assert bf.resolve_team(row("shared", stated, stamp(1)), index) == expected

    def test_refuses_two_roster_entries_that_differ_only_in_case(self):
        """Nothing can choose between them, so naming one is a coin flip."""
        index = bf.team_index(region({"Nemesis": ["tex1y"], "NEMESIS": ["tex1y"]}))
        assert bf.resolve_team(row("tex1y", "nemesis", stamp(1)), index) is None

    def test_refuses_a_handle_this_app_does_not_roster(self):
        index = bf.team_index(region({"LYON": ["Bvoy"]}))
        assert bf.resolve_team(row("Faker", "T1", stamp(1)), index) is None

    def test_refuses_a_row_with_no_player(self):
        index = bf.team_index(region({"LYON": ["Bvoy"]}))
        assert bf.resolve_team({"team": "LYON"}, index) is None


class TestBoardSlots:
    def test_groups_a_kickoff_into_one_slot(self):
        regions = {"LCS": region({"LYON": ["Bvoy"], "Shopify Rebellion": ["Tomio"]})}
        by_region, counts = bf.board_slots(
            board([row("Bvoy", "LYON", stamp(6)),
                   row("Tomio", "Shopify Rebellion", stamp(6))])["props"],
            "lol", regions, NOW)
        assert len(by_region["LCS"]) == 1
        assert by_region["LCS"][0]["teams"] == {"LYON": 1, "Shopify Rebellion": 1}
        assert counts["slots"] == 1

    def test_counts_the_lines_behind_each_team(self):
        regions = {"LCS": region({"LYON": ["Bvoy"]})}
        by_region, _ = bf.board_slots(
            board([row("Bvoy", "LYON", stamp(6)),
                   row("Bvoy", "LYON", stamp(6), stat="assists")])["props"],
            "lol", regions, NOW)
        assert by_region["LCS"][0]["teams"] == {"LYON": 2}

    def test_separate_kickoffs_stay_separate(self):
        """A team playing twice in a day is normal, and one fixture for the
        pair of games would attach both boards to whichever is nearer."""
        regions = {"LCS": region({"K27": ["sdaim"]})}
        by_region, _ = bf.board_slots(
            board([row("sdaim", "K27", stamp(2)),
                   row("sdaim", "K27", stamp(8))])["props"],
            "lol", regions, NOW)
        assert [s["when"] for s in by_region["LCS"]] == [bf.parse_stamp(stamp(2)),
                                                         bf.parse_stamp(stamp(8))]

    def test_a_match_already_under_way_is_still_a_slot(self):
        regions = {"LCS": region({"LYON": ["Bvoy"]})}
        by_region, counts = bf.board_slots(
            board([row("Bvoy", "LYON", stamp(-1))])["props"], "lol", regions, NOW)
        assert len(by_region["LCS"]) == 1 and not counts["past"]

    def test_a_kickoff_long_gone_is_not(self):
        regions = {"LCS": region({"LYON": ["Bvoy"]})}
        hours = -(bf.BOARD_MAX_AGE_HOURS + 1)
        by_region, counts = bf.board_slots(
            board([row("Bvoy", "LYON", stamp(hours))])["props"], "lol", regions, NOW)
        assert by_region["LCS"] == [] and counts["past"] == 1

    def test_a_stale_board_is_no_evidence_at_all(self):
        """props.json is captured from a browser by hand, so it can sit still
        for days while the fixture list moves on underneath it."""
        regions = {"LCS": region({"LYON": ["Bvoy"]})}
        old = (NOW - timedelta(days=bf.MAX_BOARD_AGE_DAYS + 1)).isoformat()
        payload = board([row("Bvoy", "LYON", stamp(6))], fetched_at=old)
        by_region, counts = bf.board_slots(payload["props"], "lol", regions, NOW,
                                           fetched_at=payload["fetched_at"])
        assert by_region["LCS"] == [] and counts["stale_board"] == 1

    def test_a_board_with_no_capture_time_is_still_used(self):
        regions = {"LCS": region({"LYON": ["Bvoy"]})}
        by_region, counts = bf.board_slots(
            board([row("Bvoy", "LYON", stamp(6))])["props"], "lol", regions, NOW,
            fetched_at=None)
        assert len(by_region["LCS"]) == 1 and not counts["stale_board"]

    def test_a_row_labelled_with_another_known_region_is_not_this_ones(self):
        """Valorant rosters a Champions team under its home region too. A
        'VCT Pacific' line must not conjure a Champions fixture."""
        regions = {"VCT Pacific": region({"Paper Rex": ["Jinggg"]}),
                   "VCT Champions": region({"Paper Rex": ["Jinggg"]})}
        by_region, _ = bf.board_slots(
            board([row("Jinggg", "Paper Rex", stamp(6),
                       region_name="VCT Pacific")], game="valorant")["props"],
            "valorant", regions, NOW)
        assert by_region["VCT Champions"] == []
        assert len(by_region["VCT Pacific"]) == 1

    def test_a_row_labelled_with_an_unknown_region_is_judged_on_the_roster(self):
        regions = {"VCT Pacific": region({"Paper Rex": ["Jinggg"]}),
                   "VCT Champions": region({"Paper Rex": ["Jinggg"]})}
        by_region, _ = bf.board_slots(
            board([row("Jinggg", "Paper Rex", stamp(6),
                       region_name="VAL")], game="valorant")["props"],
            "valorant", regions, NOW)
        assert len(by_region["VCT Pacific"]) == 1
        assert len(by_region["VCT Champions"]) == 1

    def test_reports_rows_it_could_not_resolve(self):
        regions = {"LCS": region({"LYON": ["Bvoy"]})}
        by_region, counts = bf.board_slots(
            board([row("Faker", "T1", stamp(6))])["props"], "lol", regions, NOW)
        assert by_region["LCS"] == [] and counts["unresolved"] == 1

    def test_reports_a_row_with_no_kickoff(self):
        regions = {"LCS": region({"LYON": ["Bvoy"]})}
        by_region, counts = bf.board_slots(
            board([row("Bvoy", "LYON", None)])["props"], "lol", regions, NOW)
        assert by_region["LCS"] == [] and counts["undated"] == 1

    def test_a_game_with_no_board_yields_nothing(self):
        regions = {"LCS": region({"LYON": ["Bvoy"]})}
        by_region, counts = bf.board_slots({}, "lol", regions, NOW)
        assert by_region["LCS"] == [] and counts["slots"] == 0


class TestFixtureMatchesSlot:
    WINDOW = timedelta(hours=bf.BOARD_MATCH_WINDOW_HOURS)

    def test_a_clocked_fixture_inside_the_window_matches(self):
        assert bf.fixture_matches_slot("2026-09-26T20:00:00Z",
                                       bf.parse_stamp("2026-09-26T21:00:00Z"),
                                       self.WINDOW)

    def test_a_clocked_fixture_outside_the_window_does_not(self):
        assert not bf.fixture_matches_slot("2026-09-26T20:00:00Z",
                                          bf.parse_stamp("2026-09-27T01:00:00Z"),
                                          self.WINDOW)

    def test_a_bare_date_matches_at_day_resolution(self):
        """vlr.gg gives no kickoff. Read as midnight and compared on the
        clock, every Valorant fixture looks nine hours from its own board —
        which published twelve phantom fixtures beside the eight real ones."""
        assert bf.fixture_matches_slot("2026-09-26",
                                       bf.parse_stamp("2026-09-26T09:00:00Z"),
                                       self.WINDOW)

    def test_a_bare_date_on_another_day_does_not_match(self):
        assert not bf.fixture_matches_slot("2026-09-27",
                                          bf.parse_stamp("2026-09-26T09:00:00Z"),
                                          self.WINDOW)

    def test_an_unreadable_date_matches_nothing(self):
        assert not bf.fixture_matches_slot(None, NOW, self.WINDOW)
        assert not bf.fixture_matches_slot("soon", NOW, self.WINDOW)


class TestFillingAnUndecidedSide:
    def test_fills_the_hole_the_board_leaves_one_candidate_for(self):
        regions = {"LCS": region(
            {"LYON": ["Bvoy"], "Shopify Rebellion": ["Tomio"]},
            [{"date": stamp(6), "teamA": "TBD", "teamB": "LYON", "block": "Playoffs"}])}
        slots = [{"when": bf.parse_stamp(stamp(6)),
                  "teams": {"LYON": 13, "Shopify Rebellion": 13}}]
        counts, _ = bf.augment_regions(regions, {"LCS": slots})
        fixture = regions["LCS"]["upcoming_matches"][0]
        assert fixture["teamA"] == "Shopify Rebellion"
        assert fixture["teamB"] == "LYON"
        assert fixture["inferred"] == "board:opponent"
        assert counts["filled"] == 1 and not counts["added"]

    def test_fills_the_other_side_too(self):
        regions = {"LCS": region(
            {"LYON": ["Bvoy"], "Shopify Rebellion": ["Tomio"]},
            [{"date": stamp(6), "teamA": "LYON", "teamB": "TBD"}])}
        slots = [{"when": bf.parse_stamp(stamp(6)),
                  "teams": {"LYON": 1, "Shopify Rebellion": 1}}]
        bf.augment_regions(regions, {"LCS": slots})
        assert regions["LCS"]["upcoming_matches"][0]["teamB"] == "Shopify Rebellion"

    def test_keeps_the_block_the_schedule_gave_the_fixture(self):
        regions = {"LCS": region(
            {"LYON": ["Bvoy"], "Shopify Rebellion": ["Tomio"]},
            [{"date": stamp(6), "teamA": "TBD", "teamB": "LYON", "block": "Playoffs"}])}
        slots = [{"when": bf.parse_stamp(stamp(6)),
                  "teams": {"LYON": 1, "Shopify Rebellion": 1}}]
        bf.augment_regions(regions, {"LCS": slots})
        assert regions["LCS"]["upcoming_matches"][0]["block"] == "Playoffs"

    def test_refuses_a_hole_with_two_candidates(self):
        """Two other teams on the board at that kickoff means two concurrent
        games, and picking either prints a matchup that is a coin flip."""
        regions = {"LCS": region(
            {"LYON": ["Bvoy"], "Shopify Rebellion": ["Tomio"], "Disguised": ["Yeon"]},
            [{"date": stamp(6), "teamA": "TBD", "teamB": "LYON"}])}
        slots = [{"when": bf.parse_stamp(stamp(6)),
                  "teams": {"LYON": 1, "Shopify Rebellion": 1, "Disguised": 1}}]
        counts, _ = bf.augment_regions(regions, {"LCS": slots})
        assert regions["LCS"]["upcoming_matches"][0]["teamA"] == "TBD"
        assert counts["ambiguous"] == 1
        assert not counts["added"], "a second fixture here publishes the same game twice"

    def test_refuses_two_holes_with_two_candidates(self):
        regions = {"LCS": region(
            {"LYON": ["Bvoy"], "Disguised": ["Yeon"],
             "Shopify Rebellion": ["Tomio"], "Dignitas": ["Srtty"]},
            [{"date": stamp(6), "teamA": "TBD", "teamB": "LYON"},
             {"date": stamp(6), "teamA": "Disguised", "teamB": "TBD"}])}
        slots = [{"when": bf.parse_stamp(stamp(6)),
                  "teams": {"LYON": 1, "Disguised": 1,
                            "Shopify Rebellion": 1, "Dignitas": 1}}]
        counts, _ = bf.augment_regions(regions, {"LCS": slots})
        assert [m["teamA"] for m in regions["LCS"]["upcoming_matches"]] == ["TBD", "Disguised"]
        assert counts["ambiguous"] == 1 and not counts["added"]

    def test_a_hole_whose_named_side_the_board_does_not_place_here_is_left_alone(self):
        """A different game that merely kicks off nearby."""
        regions = {"LCS": region(
            {"Dignitas": ["Srtty"], "Shopify Rebellion": ["Tomio"]},
            [{"date": stamp(6), "teamA": "TBD", "teamB": "Dignitas"}])}
        slots = [{"when": bf.parse_stamp(stamp(6)),
                  "teams": {"Shopify Rebellion": 1}}]
        counts, _ = bf.augment_regions(regions, {"LCS": slots})
        assert regions["LCS"]["upcoming_matches"][0]["teamA"] == "TBD"
        assert counts["added"] == 1


class TestAddingAMissingFixture:
    def test_adds_one_fixture_per_team_with_the_opponent_undecided(self):
        regions = {"TCL": region({"SU Esports": ["Zeitnot"]})}
        slots = [{"when": bf.parse_stamp(stamp(3)), "teams": {"SU Esports": 2}}]
        counts, _ = bf.augment_regions(regions, {"TCL": slots})
        assert regions["TCL"]["upcoming_matches"] == [
            {"date": bf.stamp_text(bf.parse_stamp(stamp(3))), "teamA": "SU Esports",
             "teamB": "TBD", "block": "", "inferred": "board:team"}]
        assert counts["added"] == 1

    def test_never_pairs_two_teams_off_a_shared_kickoff(self):
        """The load-bearing refusal. The board carries no opponent and no
        match id, and a kickoff routinely holds several concurrent games —
        the CS2 slot at 09:00-04:00 on 2026-09-26 held six teams. A wrong
        opponent is printed as fact beside a projection; an undecided one
        falls back to a neutral opponent term, which the app does on
        purpose. So two uncovered teams are two fixtures, never one."""
        regions = {"CS2": region({"K27": ["sdaim"], "ex-RUBY": ["z1k4"]})}
        slots = [{"when": bf.parse_stamp(stamp(3)),
                  "teams": {"K27": 10, "ex-RUBY": 10}}]
        counts, _ = bf.augment_regions(regions, {"CS2": slots})
        added = regions["CS2"]["upcoming_matches"]
        assert counts["added"] == 2
        assert sorted(m["teamA"] for m in added) == ["K27", "ex-RUBY"]
        assert [m["teamB"] for m in added] == ["TBD", "TBD"]

    def test_adds_only_for_the_teams_that_have_no_fixture(self):
        regions = {"CS2": region(
            {"Nemesis": ["tex1y"], "Sashi": ["hades"],
             "EYEBALLERS": ["hyped"], "K27": ["sdaim"]},
            [{"date": stamp(3), "teamA": "Nemesis", "teamB": "Sashi"}])}
        slots = [{"when": bf.parse_stamp(stamp(3)),
                  "teams": {"Nemesis": 10, "Sashi": 10, "EYEBALLERS": 10, "K27": 10}}]
        counts, _ = bf.augment_regions(regions, {"CS2": slots})
        assert counts["added"] == 2
        assert sorted(m["teamA"] for m in regions["CS2"]["upcoming_matches"][1:]) == \
            ["EYEBALLERS", "K27"]

    def test_a_team_playing_twice_in_a_day_gets_both_fixtures(self):
        regions = {"CS2": region({"K27": ["sdaim"]})}
        slots = [{"when": bf.parse_stamp(stamp(2)), "teams": {"K27": 10}},
                 {"when": bf.parse_stamp(stamp(8)), "teams": {"K27": 10}}]
        counts, _ = bf.augment_regions(regions, {"CS2": slots})
        assert counts["added"] == 2

    def test_survives_a_region_whose_fixture_list_is_null(self):
        regions = {"TCL": {"teams": {"SU Esports": {"players": [{"name": "Zeitnot"}]}},
                           "upcoming_matches": None}}
        slots = [{"when": bf.parse_stamp(stamp(3)), "teams": {"SU Esports": 1}}]
        counts, _ = bf.augment_regions(regions, {"TCL": slots})
        assert counts["added"] == 1
        assert regions["TCL"]["upcoming_matches"][0]["teamA"] == "SU Esports"

    def test_adds_nothing_when_the_board_says_nothing(self):
        regions = {"TCL": region({"SU Esports": ["Zeitnot"]})}
        counts, _ = bf.augment_regions(regions, {"TCL": []})
        assert regions["TCL"]["upcoming_matches"] == [] and not counts["added"]


class TestNotPublishingAGameTwice:
    def test_a_fixture_in_another_region_already_covers_the_slot(self):
        """Valorant lists the Champions fixture under the event, while the
        board labels the lines with the players' home region. Judged per
        region, all eight fixtures looked missing."""
        regions = {
            "VCT Champions": region(
                {"Paper Rex": ["Jinggg"], "G2 Esports": ["leaf"]},
                [{"date": stamp(6), "teamA": "G2 Esports", "teamB": "Paper Rex"}]),
            "VCT Pacific": region({"Paper Rex": ["Jinggg"]}),
        }
        slots = [{"when": bf.parse_stamp(stamp(6)), "teams": {"Paper Rex": 3}}]
        counts, _ = bf.augment_regions(regions, {"VCT Pacific": slots})
        assert regions["VCT Pacific"]["upcoming_matches"] == []
        assert counts["already_covered"] == 1

    def test_a_bare_dated_fixture_on_the_same_day_already_covers_the_slot(self):
        regions = {"VCT Champions": region(
            {"Paper Rex": ["Jinggg"], "G2 Esports": ["leaf"]},
            [{"date": "2026-09-26", "teamA": "G2 Esports", "teamB": "Paper Rex"}])}
        slots = [{"when": bf.parse_stamp("2026-09-26T09:00:00Z"),
                  "teams": {"Paper Rex": 3}}]
        counts, _ = bf.augment_regions(regions, {"VCT Champions": slots})
        assert regions["VCT Champions"]["upcoming_matches"] == [
            {"date": "2026-09-26", "teamA": "G2 Esports", "teamB": "Paper Rex"}]
        assert counts["already_covered"] == 1

    def test_a_nearby_kickoff_in_another_region_still_counts_as_covered(self):
        """Coverage is a time window, not an exact stamp: the two sources
        need not agree to the minute about when the same game starts."""
        regions = {"VCT Champions": region(
            {"Paper Rex": ["Jinggg"], "G2 Esports": ["leaf"]},
            [{"date": stamp(6), "teamA": "G2 Esports", "teamB": "Paper Rex"}]),
            "VCT Pacific": region({"Paper Rex": ["Jinggg"]})}
        slots = [{"when": bf.parse_stamp(stamp(7)), "teams": {"Paper Rex": 3}}]
        counts, _ = bf.augment_regions(regions, {"VCT Pacific": slots})
        assert regions["VCT Pacific"]["upcoming_matches"] == []
        assert counts["already_covered"] == 1

    def test_one_team_raised_by_two_regions_gets_one_fixture(self):
        regions = {"VCT Pacific": region({"Paper Rex": ["Jinggg"]}),
                   "VCT Champions": region({"Paper Rex": ["Jinggg"]})}
        slot = {"when": bf.parse_stamp(stamp(6)), "teams": {"Paper Rex": 3}}
        counts, _ = bf.augment_regions(
            regions, {"VCT Pacific": [slot], "VCT Champions": [slot]})
        assert counts["added"] == 1
        added = (regions["VCT Pacific"]["upcoming_matches"]
                 + regions["VCT Champions"]["upcoming_matches"])
        assert len(added) == 1


class TestIdempotence:
    """The workflow writes its inferred fixtures into the file the app then
    reads and re-infers over. A second pass that added anything would double
    every fixture the first one found."""

    def test_a_second_pass_over_an_added_fixture_adds_nothing(self):
        regions = {"TCL": region({"SU Esports": ["Zeitnot"]})}
        slots = [{"when": bf.parse_stamp(stamp(3)), "teams": {"SU Esports": 1}}]
        first, _ = bf.augment_regions(regions, {"TCL": slots})
        assert first["added"] == 1
        second, _ = bf.augment_regions(regions, {"TCL": slots})
        assert not second["added"] and second["already_covered"] == 1
        assert len(regions["TCL"]["upcoming_matches"]) == 1

    def test_a_second_pass_over_a_filled_hole_adds_nothing(self):
        regions = {"LCS": region(
            {"LYON": ["Bvoy"], "Shopify Rebellion": ["Tomio"]},
            [{"date": stamp(6), "teamA": "TBD", "teamB": "LYON"}])}
        slots = [{"when": bf.parse_stamp(stamp(6)),
                  "teams": {"LYON": 1, "Shopify Rebellion": 1}}]
        first, _ = bf.augment_regions(regions, {"LCS": slots})
        assert first["filled"] == 1
        second, _ = bf.augment_regions(regions, {"LCS": slots})
        assert not second["filled"] and not second["added"]
        assert len(regions["LCS"]["upcoming_matches"]) == 1

    def test_a_second_pass_over_the_real_board_adds_nothing(self):
        """Run against the committed files, which is the shape the workflow
        actually hands the app: already inferred over once."""
        import json
        from datetime import datetime
        props_path = REPO_ROOT / "props.json"
        if not props_path.exists():
            pytest.skip("no props.json committed")
        props = json.loads(props_path.read_text())
        captured = bf.parse_stamp(props.get("fetched_at")) or NOW
        # Judged from when the board was captured, so a board that has since
        # gone past MAX_BOARD_AGE_DAYS does not turn this into a no-op that
        # passes by doing nothing.
        for game, filename in (("lol", "data.json"), ("cs2", "cs2_data.json"),
                               ("valorant", "valorant_data.json")):
            path = REPO_ROOT / filename
            if not path.exists():
                continue
            regions = (json.loads(path.read_text()).get("regions") or {})
            slots, _ = bf.board_slots(props.get("props") or {}, game, regions,
                                      captured, fetched_at=props.get("fetched_at"))
            first, _ = bf.augment_regions(regions, slots)
            before = {k: list(v.get("upcoming_matches") or []) for k, v in regions.items()}
            slots_again, _ = bf.board_slots(props.get("props") or {}, game, regions,
                                            captured, fetched_at=props.get("fetched_at"))
            second, _ = bf.augment_regions(regions, slots_again)
            after = {k: list(v.get("upcoming_matches") or []) for k, v in regions.items()}
            assert not second.get("filled") and not second.get("added"), \
                f"{game}: a second pass changed {dict(second)}"
            assert before == after, f"{game}: a second pass rewrote the fixture list"
            assert first.get("added") or first.get("filled") or first.get("already_covered"), \
                f"{game}: the first pass did nothing, so this proved nothing"


class TestAugmentRegionsHousekeeping:
    def test_does_not_disturb_a_region_the_board_says_nothing_about(self):
        untouched = [{"date": stamp(6), "teamA": "T1", "teamB": "Gen.G"}]
        regions = {"LCK": region({"T1": ["Faker"]}, untouched),
                   "TCL": region({"SU Esports": ["Zeitnot"]})}
        bf.augment_regions(regions, {"TCL": [
            {"when": bf.parse_stamp(stamp(3)), "teams": {"SU Esports": 1}}]})
        assert regions["LCK"]["upcoming_matches"] == untouched

    def test_reports_what_changed_per_region(self):
        regions = {"TCL": region({"SU Esports": ["Zeitnot"]}),
                   "LCS": region({"LYON": ["Bvoy"], "Shopify Rebellion": ["Tomio"]},
                                 [{"date": stamp(6), "teamA": "TBD", "teamB": "LYON"}])}
        _, by_region = bf.augment_regions(regions, {
            "TCL": [{"when": bf.parse_stamp(stamp(3)), "teams": {"SU Esports": 1}}],
            "LCS": [{"when": bf.parse_stamp(stamp(6)),
                     "teams": {"LYON": 1, "Shopify Rebellion": 1}}]})
        assert by_region["TCL"]["added"] == 1
        assert by_region["LCS"]["filled"] == 1

    def test_is_deterministic_whatever_order_the_slots_arrive_in(self):
        def run(slots):
            regions = {"CS2": region({"K27": ["sdaim"], "ex-RUBY": ["z1k4"]})}
            bf.augment_regions(regions, {"CS2": slots})
            return regions["CS2"]["upcoming_matches"]
        early = {"when": bf.parse_stamp(stamp(2)), "teams": {"K27": 1}}
        late = {"when": bf.parse_stamp(stamp(9)), "teams": {"ex-RUBY": 1}}
        assert run([early, late]) == run([late, early])


class TestStampText:
    def test_writes_utc_with_a_z_and_no_subseconds(self):
        assert bf.stamp_text(bf.parse_stamp("2026-09-26T05:00:00.000-04:00")) == \
            "2026-09-26T09:00:00Z"

    def test_matches_the_shape_the_schedule_sources_already_emit(self):
        assert bf.stamp_text(bf.parse_stamp("2026-09-26T20:00:00Z")) == \
            "2026-09-26T20:00:00Z"


class TestDescribe:
    def test_says_nothing_when_nothing_happened(self):
        assert bf.describe({}) == ""

    def test_leads_with_what_changed(self):
        text = bf.describe({"added": 2, "filled": 1, "already_covered": 5})
        assert text.startswith("1 filled, 2 added")

    def test_spells_the_keys_for_a_human(self):
        assert "1 already covered" in bf.describe({"already_covered": 1})


class TestConstantsStayInStepWithTheirSources:
    def test_the_match_window_equals_the_apps(self):
        """Equality is required in both directions. Wider here, and this
        treats a distant fixture as covering the slot while the app refuses
        to hang the lines on it — back to invisible. Narrower, and this adds
        a second fixture the app then splits the same lines across."""
        src = (REPO_ROOT / "src/app.jsx").read_text()
        found = re.search(r"const PROP_MATCH_WINDOW_HOURS\s*=\s*(\d+(?:\.\d+)?)", src)
        assert found, "PROP_MATCH_WINDOW_HOURS not found in src/app.jsx"
        assert float(found.group(1)) == float(bf.BOARD_MATCH_WINDOW_HOURS)

    def test_does_not_keep_a_fixture_the_cs2_scraper_would_have_dropped(self):
        src = (REPO_ROOT / "scripts/scrape_cs2.py").read_text()
        found = re.search(r"UPCOMING_MAX_AGE_HOURS\s*=\s*(\d+)", src)
        assert found, "UPCOMING_MAX_AGE_HOURS not found in scripts/scrape_cs2.py"
        assert bf.BOARD_MAX_AGE_HOURS <= int(found.group(1))


class TestInferForGame:
    def test_lands_the_stranded_lines_of_a_real_shaped_board(self):
        data = {"regions": {
            "LCS": region({"LYON": ["Bvoy"], "Shopify Rebellion": ["Tomio"]},
                          [{"date": stamp(6), "teamA": "TBD", "teamB": "LYON"}]),
            "TCL": region({"SU Esports": ["Zeitnot"]}),
        }}
        props = board([row("Bvoy", "LYON", stamp(6)),
                       row("Tomio", "Shopify Rebellion", stamp(6)),
                       row("Zeitnot", "SU Esports", stamp(3), region_name="TCL")])
        reported, counts = infer_fixtures.infer_for_game(data, props, "lol", NOW)
        assert counts["filled"] == 1 and counts["added"] == 1
        assert dict(reported)["LCS"]["filled"] == 1
        assert dict(reported)["TCL"]["added"] == 1
        assert data["regions"]["LCS"]["upcoming_matches"][0]["teamA"] == "Shopify Rebellion"

    def test_counts_an_unresolved_handle_once_for_the_game_not_once_per_region(self):
        data = {"regions": {"LCS": region({"LYON": ["Bvoy"]}),
                            "LEC": region({"G2": ["Caps"]}),
                            "LCK": region({"T1": ["Faker"]})}}
        props = board([row("Nobody", "Ghost Esports", stamp(6), region_name="???")])
        _, counts = infer_fixtures.infer_for_game(data, props, "lol", NOW)
        assert counts["unresolved"] == 1

    def test_a_game_the_board_does_not_cover_changes_nothing(self):
        data = {"regions": {"LCS": region({"LYON": ["Bvoy"]})}}
        _, counts = infer_fixtures.infer_for_game(data, board([]), "lol", NOW)
        assert not counts.get("added") and not counts.get("filled")


class TestMain:
    def test_a_missing_board_is_not_a_failure(self, tmp_path, capsys):
        assert infer_fixtures.main(["--props", str(tmp_path / "nope.json")]) == 0
        assert "no board" in capsys.readouterr().out

    def test_rejects_a_game_it_does_not_know(self):
        with pytest.raises(SystemExit):
            infer_fixtures.main(["quake"])

    def test_dry_run_leaves_the_file_alone(self, tmp_path, monkeypatch, capsys):
        data = {"regions": {"TCL": region({"SU Esports": ["Zeitnot"]})}}
        (tmp_path / "data.json").write_text(json.dumps(data))
        props = board([row("Zeitnot", "SU Esports", stamp(3), region_name="TCL")])
        (tmp_path / "props.json").write_text(json.dumps(props))
        monkeypatch.chdir(tmp_path)
        assert infer_fixtures.main(["lol", "--dry-run"]) == 0
        assert json.loads((tmp_path / "data.json").read_text()) == data
        assert "would write" in capsys.readouterr().out

    def test_writes_the_file_when_not_a_dry_run(self, tmp_path, monkeypatch):
        data = {"regions": {"TCL": region({"SU Esports": ["Zeitnot"]})}}
        (tmp_path / "data.json").write_text(json.dumps(data))
        props = board([row("Zeitnot", "SU Esports", stamp(3), region_name="TCL")])
        (tmp_path / "props.json").write_text(json.dumps(props))
        monkeypatch.chdir(tmp_path)
        assert infer_fixtures.main(["lol"]) == 0
        written = json.loads((tmp_path / "data.json").read_text())
        assert written["regions"]["TCL"]["upcoming_matches"][0]["teamA"] == "SU Esports"

    def test_a_missing_data_file_does_not_stop_the_other_games(self, tmp_path,
                                                              monkeypatch, capsys):
        (tmp_path / "props.json").write_text(json.dumps(board([])))
        monkeypatch.chdir(tmp_path)
        assert infer_fixtures.main([]) == 0
        assert "skipped" in capsys.readouterr().err

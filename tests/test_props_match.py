"""Matching betting props onto rostered players.

Every test here guards against a WRONG number rather than a missing one.
A prop that fails to match shows up as a gap; a prop matched to the wrong
player, stat or map window shows up as a confident edge that is simply
false, which is worse.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import props_match as pm


class TestNormalizeName:
    def test_case_and_punctuation_are_ignored(self):
        assert pm.normalize_name("ZywOo") == pm.normalize_name("zywoo")
        assert pm.normalize_name("Hans Sama") == pm.normalize_name("hans-sama")

    def test_accents_fold(self):
        assert pm.normalize_name("Leviatán") == pm.normalize_name("Leviatan")

    def test_digits_are_kept(self):
        """sh1ro and shiro are not obviously the same person, and merging
        two players silently is worse than failing to match one."""
        assert pm.normalize_name("sh1ro") != pm.normalize_name("shiro")

    @pytest.mark.parametrize("value", [None, "", 123, {}])
    def test_junk_gives_empty_not_an_exception(self, value):
        assert pm.normalize_name(value) == ""


class TestParseStat:
    @pytest.mark.parametrize("label,stat,maps", [
        ("MAPS 1-2 Kills", "kills", 2),
        ("Maps 1-3 Deaths", "deaths", 3),
        ("MAP 1 Assists", "assists", 1),
        ("maps 1–2 kills", "kills", 2),          # en dash
    ])
    def test_reads_stat_and_window(self, label, stat, maps):
        assert pm.parse_stat(label) == (stat, maps)

    @pytest.mark.parametrize("label", [
        "Kills (Combo)",
        # The dangerous one. This states a window, and the window patterns
        # are checked in order and break on the first hit, so "maps 1-2"
        # matched and the combo marker was never reached. 18 of these
        # passed as ordinary single-player kills lines in a real payload.
        "MAPS 1-2 Kills (Combo)",
        "MAPS 1-3 Kills (Combo)",
    ])
    def test_a_combo_is_not_a_stat_this_app_models(self, label):
        """A Combo projection is two or more PLAYERS added together, not a
        variant of one player's kills. A two-player line sits at roughly
        double a single player's, so comparing it against one player's
        projection reads as an enormous edge on the under."""
        assert pm.parse_stat(label) == (None, None)

    def test_unstated_window_is_none(self):
        assert pm.parse_stat("Kills") == ("kills", None)

    def test_unknown_stat_is_not_guessed(self):
        assert pm.parse_stat("MAPS 1-2 Flibbertigibbets")[0] is None

    def test_headshots_are_read_now_that_they_are_modelled(self):
        """Headshots used to be the example of a stat this app refuses. It
        stopped being one when a scrape run established that the source
        carries them and the weights were tuned on the resulting history."""
        assert pm.parse_stat("MAPS 1-2 Headshots") == ("headshots", 2)
        assert pm.parse_stat("MAP 1 Headshots") == ("headshots", 1)

    @pytest.mark.parametrize("value", [None, "", 42])
    def test_junk_is_handled(self, value):
        assert pm.parse_stat(value) == (None, None)


def roster(*players):
    return {"LCS": {"teams": {"T": {"players": [{"name": n} for n in players]}}}}


class TestRosterIndex:
    def test_indexes_by_normalized_handle(self):
        index, dupes = pm.build_roster_index(roster("Berserker", "Impact"))
        assert index[pm.normalize_name("berserker")][0][2] == "Berserker"
        assert dupes == []

    def test_a_handle_on_two_teams_keeps_both_and_says_so(self):
        """Kept rather than dropped, because the provider states a team on
        each prop and that usually settles it exactly. A real CS2 board had
        two of these and both named a team that appears verbatim in the
        roster file — dropping them would have thrown away attributable
        lines."""
        regions = {
            "LCS": {"teams": {"A": {"players": [{"name": "Zeus"}]}}},
            "LCK": {"teams": {"B": {"players": [{"name": "zeus"}]}}},
        }
        index, dupes = pm.build_roster_index(regions)
        assert len(index[pm.normalize_name("zeus")]) == 2
        assert dupes == [pm.normalize_name("zeus")]

    def test_the_same_player_listed_twice_on_one_team_is_not_ambiguous(self):
        regions = {"LCS": {"teams": {"A": {"players": [{"name": "Zeus"}, {"name": "Zeus"}]}}}}
        index, dupes = pm.build_roster_index(regions)
        assert dupes == [] and len(index[pm.normalize_name("zeus")]) == 1

    def test_empty_input(self):
        assert pm.build_roster_index({}) == ({}, [])


class TestMatchProps:
    INDEX = pm.build_roster_index(roster("Faker"))[0]

    def _prop(self, **kw):
        base = {"player_name": "Faker", "stat_label": "MAPS 1-2 Kills",
                "line": 4.5, "provider": "test", "start_time": "2026-09-20T10:00:00Z"}
        base.update(kw)
        return base

    def test_matches_a_clean_prop(self):
        matched, unmatched = pm.match_props([self._prop()], self.INDEX)
        assert unmatched == []
        assert matched[0]["player"] == "Faker"
        assert matched[0]["stat"] == "kills"
        assert matched[0]["maps"] == 2
        assert matched[0]["line"] == 4.5

    def test_unknown_player_is_reported_not_dropped(self):
        matched, unmatched = pm.match_props([self._prop(player_name="Nobody")], self.INDEX)
        assert matched == []
        assert unmatched[0]["reason"] == "player not on any roster"

    def test_a_combo_is_refused_and_named(self):
        """Reported as a combo rather than as an unrecognised stat: it is
        perfectly recognisable, it just belongs to more than one player,
        and a rising count of these means something different."""
        _, unmatched = pm.match_props([self._prop(stat_label="Kills (Combo)")], self.INDEX)
        assert unmatched[0]["reason"] == "combo line covers more than one player"

    def test_a_combo_with_a_stated_window_is_still_refused(self):
        """The regression that mattered: a stated window used to satisfy the
        window check before the combo marker was ever looked at."""
        _, unmatched = pm.match_props(
            [self._prop(stat_label="MAPS 1-2 Kills (Combo)", line=19.5)], self.INDEX)
        assert unmatched[0]["reason"] == "combo line covers more than one player"

    def test_an_unstated_window_is_still_refused(self):
        """Separately from combos: a plain "Kills" does not say how many
        maps, and guessing 2 would mis-scale every comparison on a Bo5."""
        _, unmatched = pm.match_props([self._prop(stat_label="Kills")], self.INDEX)
        assert unmatched[0]["reason"] == "map window not stated"

    def test_unknown_stat_is_refused(self):
        # A label nothing can make sense of. Headshots used to stand in for
        # this case and no longer can: it is refused for a different and
        # more specific reason now (see TestUnmodelledStats), which is the
        # point -- a parse failure and a stat we chose not to cover are not
        # the same problem and should not read the same in the funnel.
        _, unmatched = pm.match_props([self._prop(stat_label="Flibbertigibbets")], self.INDEX)
        assert unmatched[0]["reason"] == "unrecognised stat"

    def test_a_stat_we_do_not_model_is_still_refused(self):
        _, unmatched = pm.match_props([self._prop(stat_label="MAPS 1-2 Points")], self.INDEX)
        assert len(unmatched) == 1, "still refused — it just says why more precisely"

    def test_a_headshots_line_now_matches(self):
        matched, unmatched = pm.match_props(
            [self._prop(stat_label="MAPS 1-2 Headshots", line=12.5)], self.INDEX)
        assert unmatched == []
        assert matched[0]["stat"] == "headshots" and matched[0]["maps"] == 2

    def test_non_numeric_line_is_refused(self):
        _, unmatched = pm.match_props([self._prop(line="n/a")], self.INDEX)
        assert unmatched[0]["reason"] == "line is not a number"

    def test_every_prop_is_accounted_for(self):
        """Matched + unmatched must equal what went in — a silent drop rate
        is indistinguishable from the provider going down."""
        props = [self._prop(), self._prop(player_name="Nobody"),
                 self._prop(stat_label="Kills (Combo)"),
                 self._prop(stat_label="MAPS 1-2 Kills (Combo)"),
                 self._prop(stat_label="Kills"), self._prop(line=None)]
        matched, unmatched = pm.match_props(props, self.INDEX)
        assert len(matched) + len(unmatched) == len(props)

    def test_a_handle_on_two_teams_is_resolved_by_the_provider_team(self):
        """Without this the line lands on whichever match is nearest in
        time, which is the wrong team's opponent half the time."""
        regions = {
            "CS2": {"teams": {
                "ECSTATIC": {"players": [{"name": "Anlelele"}]},
                "Sashi": {"players": [{"name": "Anlelele"}]},
            }},
        }
        index, dupes = pm.build_roster_index(regions)
        assert dupes  # the handle is genuinely on two teams
        matched, unmatched = pm.match_props([{
            "player_name": "Anlelele", "stat_label": "MAPS 1-2 Kills",
            "line": 26.5, "team": "ECSTATIC", "provider": "t",
            "start_time": "2026-09-21T06:00:00-04:00"}], index)
        assert unmatched == []
        assert matched[0]["team"] == "ECSTATIC"

    def test_a_handle_on_two_teams_the_provider_does_not_settle_is_refused(self):
        regions = {
            "CS2": {"teams": {
                "ECSTATIC": {"players": [{"name": "Anlelele"}]},
                "Sashi": {"players": [{"name": "Anlelele"}]},
            }},
        }
        index, _ = pm.build_roster_index(regions)
        for team in (None, "", "Some Third Team"):
            _, unmatched = pm.match_props([{
                "player_name": "Anlelele", "stat_label": "MAPS 1-2 Kills",
                "line": 26.5, "team": team, "provider": "t",
                "start_time": "2026-09-21T06:00:00-04:00"}], index)
            assert unmatched[0]["reason"] == "handle is on more than one roster", team

    def test_no_props_is_not_an_error(self):
        assert pm.match_props([], self.INDEX) == ([], [])


class TestUnmodelledStats:
    """Stats the app reads fine and does not project.

    "MAPS 1-2 Headshots" is a working provider, a correctly parsed label,
    and a stat with no history behind it. Reporting that as "unrecognised
    stat" hides two things at once: a real parse failure gets excused as
    probably-just-headshots, and the size of what is not covered never
    surfaces.
    """

    def test_an_unmodelled_stat_is_named_rather_than_called_unrecognised(self):
        _, unmatched = pm.match_props(
            [{"player_name": "acoR", "stat_label": "Points", "line": 12.5}],
            {"acor": [("CS2", "Sashi", "acoR")]})
        assert len(unmatched) == 1
        assert unmatched[0]["reason"] == "points is not a stat this app projects yet"

    def test_headshots_left_this_table_when_it_gained_a_projection(self):
        """The journey this table exists to make possible, asserted once so
        the two tables cannot both claim it."""
        assert pm.unmodelled_stat("MAPS 1-2 Headshots") is None
        assert "headshots" in pm.STAT_ALIASES

    def test_a_window_prefix_does_not_hide_the_stat(self):
        assert pm.unmodelled_stat("MAP 1 Points") == "points"
        assert pm.unmodelled_stat("MAPS 1-3 Points") == "points"

    def test_a_genuinely_unreadable_label_still_says_unrecognised(self):
        _, unmatched = pm.match_props(
            [{"player_name": "acoR", "stat_label": "Flibbertigibbets", "line": 1.5}],
            {"acor": [("CS2", "Sashi", "acoR")]})
        assert unmatched[0]["reason"] == "unrecognised stat"

    def test_a_modelled_stat_is_not_swept_up(self):
        for label in ("MAPS 1-2 Kills", "Deaths", "MAPS 1-3 Assists"):
            assert pm.unmodelled_stat(label) is None

    def test_nothing_is_both_modelled_and_unmodelled(self):
        # The day headshots gets a projection it moves between these two,
        # and being in both would mean it is silently refused anyway.
        overlap = set(pm.STAT_ALIASES) & set(pm.UNMODELLED_STATS)
        assert not overlap, overlap

    def test_no_alias_collides_across_the_two_tables(self):
        modelled = {a for aliases in pm.STAT_ALIASES.values() for a in aliases}
        unmodelled = {a for aliases in pm.UNMODELLED_STATS.values() for a in aliases}
        assert not (modelled & unmodelled)

    def test_junk_input_is_not_an_error(self):
        assert pm.unmodelled_stat(None) is None
        assert pm.unmodelled_stat(123) is None
        assert pm.unmodelled_stat("") is None


class TestUnmatchedByTeam:
    """Splitting "player not on any roster" into the two things it means.

    A real run reported 309 of 361 CS2 props and 30 of 32 LoL props with
    that one reason. As a number it is unactionable, because it collapses
    a team this app does not cover (nothing to do but scrape more teams)
    with a team it does cover whose names are not lining up (lines sitting
    right there, kept out by a spelling).
    """

    REGIONS = {"LCS": {"teams": {"T1": {"players": [{"name": "Faker"}]},
                                 "GEN": {"players": [{"name": "Chovy"}]}}}}

    def refused(self, team, player, reason="player not on any roster"):
        return {"reason": reason, "team": team, "player_name": player}

    def test_a_tracked_team_is_separated_from_an_untracked_one(self):
        tracked, untracked = pm.unmatched_by_team(
            [self.refused("T1", "Zeus"), self.refused("Tier Three Squad", "nobody")],
            self.REGIONS)
        assert [t for t, _ in tracked] == ["T1"]
        assert [t for t, _ in untracked] == ["Tier Three Squad"]

    def test_the_players_are_named_so_the_mismatch_can_be_seen(self):
        tracked, _ = pm.unmatched_by_team(
            [self.refused("T1", "Zeus"), self.refused("T1", "Oner")], self.REGIONS)
        assert tracked[0][1] == ["Oner", "Zeus"]

    def test_team_matching_ignores_case(self):
        tracked, untracked = pm.unmatched_by_team(
            [self.refused("t1", "Zeus")], self.REGIONS)
        assert [t for t, _ in tracked] == ["t1"] and untracked == []

    def test_ordered_by_how_many_lines_are_being_lost(self):
        """Worst offender first, so the top of the list is where to look.

        The thin team is fed in FIRST on purpose. Built the other way
        round, insertion order alone produces the expected answer and the
        assertion passes even with the sort deleted -- which is how it was
        written at first, and a mutation run caught it.
        """
        props = ([self.refused("GEN", "one")]
                 + [self.refused("T1", f"p{i}") for i in range(3)])
        tracked, _ = pm.unmatched_by_team(props, self.REGIONS)
        assert [t for t, _ in tracked] == ["T1", "GEN"]

    def test_teams_losing_the_same_count_are_ordered_by_name(self):
        """Otherwise the list reshuffles between runs on equal counts."""
        props = [self.refused("T1", "Zeus"), self.refused("GEN", "one")]
        tracked, _ = pm.unmatched_by_team(props, self.REGIONS)
        assert [t for t, _ in tracked] == ["GEN", "T1"]

    def test_untracked_teams_are_ordered_the_same_way(self):
        props = ([self.refused("Small Org", "a")]
                 + [self.refused("Big Org", f"p{i}") for i in range(2)])
        _, untracked = pm.unmatched_by_team(props, self.REGIONS)
        assert [t for t, _ in untracked] == ["Big Org", "Small Org"]

    def test_other_refusals_are_not_swept_in(self):
        tracked, untracked = pm.unmatched_by_team(
            [self.refused("T1", "x", reason="combo line covers more than one player")],
            self.REGIONS)
        assert tracked == [] and untracked == []

    def test_a_prop_with_no_team_stated_is_still_counted(self):
        _, untracked = pm.unmatched_by_team([self.refused(None, "orphan")], self.REGIONS)
        assert untracked == [("(no team stated)", ["orphan"])]

    def test_duplicate_names_are_listed_once(self):
        tracked, _ = pm.unmatched_by_team(
            [self.refused("T1", "Zeus"), self.refused("T1", "Zeus")], self.REGIONS)
        assert tracked[0][1] == ["Zeus"]

    def test_empty_input_is_not_an_error(self):
        assert pm.unmatched_by_team([], self.REGIONS) == ([], [])
        assert pm.unmatched_by_team(None, None) == ([], [])

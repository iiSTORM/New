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

    def test_ambiguous_combo_window_is_refused(self):
        """'Kills (Combo)' does not say how many maps. Guessing 2 would
        silently mis-scale every comparison on a Bo5."""
        stat, maps = pm.parse_stat("Kills (Combo)")
        assert stat == "kills" and maps is None

    def test_unstated_window_is_none(self):
        assert pm.parse_stat("Kills") == ("kills", None)

    def test_unknown_stat_is_not_guessed(self):
        assert pm.parse_stat("MAPS 1-2 Headshots")[0] is None

    @pytest.mark.parametrize("value", [None, "", 42])
    def test_junk_is_handled(self, value):
        assert pm.parse_stat(value) == (None, None)


def roster(*players):
    return {"LCS": {"teams": {"T": {"players": [{"name": n} for n in players]}}}}


class TestRosterIndex:
    def test_indexes_by_normalized_handle(self):
        index, dupes = pm.build_roster_index(roster("Berserker", "Impact"))
        assert index[pm.normalize_name("berserker")][2] == "Berserker"
        assert dupes == []

    def test_a_handle_on_two_rosters_is_dropped_not_guessed(self):
        regions = {
            "LCS": {"teams": {"A": {"players": [{"name": "Zeus"}]}}},
            "LCK": {"teams": {"B": {"players": [{"name": "zeus"}]}}},
        }
        index, dupes = pm.build_roster_index(regions)
        assert pm.normalize_name("zeus") not in index
        assert dupes == [pm.normalize_name("zeus")]

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

    def test_ambiguous_window_is_refused(self):
        _, unmatched = pm.match_props([self._prop(stat_label="Kills (Combo)")], self.INDEX)
        assert unmatched[0]["reason"] == "map window not stated"

    def test_unknown_stat_is_refused(self):
        _, unmatched = pm.match_props([self._prop(stat_label="MAPS 1-2 Headshots")], self.INDEX)
        assert unmatched[0]["reason"] == "unrecognised stat"

    def test_non_numeric_line_is_refused(self):
        _, unmatched = pm.match_props([self._prop(line="n/a")], self.INDEX)
        assert unmatched[0]["reason"] == "line is not a number"

    def test_every_prop_is_accounted_for(self):
        """Matched + unmatched must equal what went in — a silent drop rate
        is indistinguishable from the provider going down."""
        props = [self._prop(), self._prop(player_name="Nobody"),
                 self._prop(stat_label="Kills (Combo)"), self._prop(line=None)]
        matched, unmatched = pm.match_props(props, self.INDEX)
        assert len(matched) + len(unmatched) == len(props)

    def test_no_props_is_not_an_error(self):
        assert pm.match_props([], self.INDEX) == ([], [])

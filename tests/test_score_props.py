"""Grading posted lines against what actually happened.

Every test here guards against a WRONG grade rather than a missing one. A
missing grade shows up as a smaller sample; a wrong grade becomes a claim,
and the whole point of the file being graded is that claims will be made
from it.
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import score_props as sc


def lol_match(date="2026-09-21", maps=3, kills=(5, 4, 6)):
    """A LoL series with a per-map breakdown, which is what lets a line
    over a named window resolve exactly."""
    return {
        "date": date, "teamA": "T1", "teamB": "GEN", "maps_counted": maps,
        "per_game": [{"T1": {"Faker": {"k": k, "d": 1, "a": 3}}} for k in kills[:maps]],
        "actual": {"T1": {"Faker": {"k": sum(kills[:maps]), "d": 3, "a": 9}}},
    }


def cs2_match(date="2026-09-21", kills=28):
    """CS2 stores one total covering exactly maps 1-2 and nothing finer."""
    return {"date": date, "teamA": "Sashi", "teamB": "FOKUS",
            "actual": {"Sashi": {"acoR": {"k": kills, "d": 20, "a": 5}}}}


def regions(*matches):
    return {"R": {"past_matches": list(matches)}}


def obs(**kw):
    base = {"game": "lol", "player": "Faker", "team": "T1", "stat": "kills",
            "maps": 2, "line": 8.5, "start_time": "2026-09-21T06:00:00-04:00"}
    base.update(kw)
    return base


class TestWindowResolution:
    def test_a_line_resolves_over_exactly_the_maps_it_names(self):
        """5 + 4 over maps 1-2, not 15 over the whole series. Grading a
        two-map line against a three-map total invents a win for the over
        on every single line."""
        match = lol_match(kills=(5, 4, 6))
        value, reason = sc.actual_over_window(match, "T1", "Faker", "kills", 2, "lol")
        assert value == 9 and reason is None

    def test_the_same_series_over_three_maps(self):
        value, _ = sc.actual_over_window(lol_match(kills=(5, 4, 6)), "T1", "Faker", "kills", 3, "lol")
        assert value == 15

    def test_map_one_alone(self):
        value, _ = sc.actual_over_window(lol_match(kills=(5, 4, 6)), "T1", "Faker", "kills", 1, "lol")
        assert value == 5

    def test_a_series_shorter_than_the_line_is_refused(self):
        """A Bo5 line over maps 1-3 on a series that ended 2-0 has no third
        map to count, and counting two would grade it against less than it
        covered."""
        value, reason = sc.actual_over_window(lol_match(maps=2), "T1", "Faker", "kills", 3, "lol")
        assert value is None and "2 map" in reason

    def test_cs2_grades_its_own_window(self):
        value, reason = sc.actual_over_window(cs2_match(kills=28), "Sashi", "acoR", "kills", 2, "cs2")
        assert value == 28 and reason is None

    @pytest.mark.parametrize("maps", [1, 3])
    def test_cs2_refuses_any_other_window(self, maps):
        """Its stored total covers maps 1-2 because the scraper collects
        exactly two. A map-1 line graded against it would lose almost
        always, and the record would look like a broken model."""
        value, reason = sc.actual_over_window(cs2_match(), "Sashi", "acoR", "kills", maps, "cs2")
        assert value is None and "maps 1-2 only" in reason

    def test_a_player_missing_from_a_map_is_refused(self):
        match = lol_match()
        del match["per_game"][1]["T1"]["Faker"]
        value, reason = sc.actual_over_window(match, "T1", "Faker", "kills", 2, "lol")
        assert value is None and "box score" in reason

    def test_a_stat_the_app_does_not_model(self):
        value, reason = sc.actual_over_window(lol_match(), "T1", "Faker", "headshots", 2, "lol")
        assert value is None and "model" in reason


class TestGrading:
    def data(self, game, *matches):
        return {game: {"regions": regions(*matches)}}

    def test_over_under_and_push(self):
        data = self.data("lol", lol_match(kills=(5, 4, 6)))
        rows, _ = sc.grade([obs(line=8.5), obs(line=9.5), obs(line=9.0)], data)
        assert [r["result"] for r in rows] == ["over", "under", "push"]
        assert [r["margin"] for r in rows] == [0.5, -0.5, 0.0]

    def test_a_whole_number_line_is_a_push_not_a_loss(self):
        """Half-point lines are the norm, so this is rare — and silently
        scoring it as a loss would quietly understate the book."""
        rows, _ = sc.grade([obs(line=9.0)], self.data("lol", lol_match(kills=(5, 4, 6))))
        assert rows[0]["result"] == "push"

    def test_a_team_playing_twice_that_day_is_refused(self):
        """Which match a line belonged to is not recoverable from a
        calendar date, and picking one is a coin flip recorded as a result."""
        data = self.data("lol", lol_match(kills=(5, 4, 6)), lol_match(kills=(9, 9, 9)))
        rows, refused = sc.grade([obs()], data)
        assert rows == [] and "more than once" in " ".join(refused)

    def test_an_unplayed_match_is_refused_not_guessed(self):
        rows, refused = sc.grade([obs(start_time="2026-12-25T06:00:00-04:00")],
                                 self.data("lol", lol_match()))
        assert rows == [] and "no completed match" in " ".join(refused)

    def test_the_opponent_is_recorded(self):
        rows, _ = sc.grade([obs()], self.data("lol", lol_match()))
        assert rows[0]["opponent"] == "GEN"

    def test_an_unreadable_start_time(self):
        rows, refused = sc.grade([obs(start_time="whenever")], self.data("lol", lol_match()))
        assert rows == [] and "unreadable" in " ".join(refused)


class TestSummary:
    def rows(self, results):
        return [{"game": "lol", "result": r, "margin": m} for r, m in results]

    def test_counts_and_rate(self):
        s = sc.summarise(self.rows([("over", 1.5), ("over", 0.5), ("under", -2.0)]))
        assert s["lol"]["over"] == 2 and s["lol"]["under"] == 1
        assert s["lol"]["over_rate"] == round(2 / 3, 4)

    def test_pushes_are_excluded_from_the_rate(self):
        """A push is not half a win; it is no bet."""
        s = sc.summarise(self.rows([("over", 1.0), ("push", 0.0)]))
        assert s["lol"]["graded"] == 2 and s["lol"]["pushes"] == 1
        assert s["lol"]["over_rate"] == 1.0

    def test_an_all_push_sample_has_no_rate_rather_than_a_crash(self):
        s = sc.summarise(self.rows([("push", 0.0)]))
        assert s["lol"]["over_rate"] is None

    def test_an_overall_row_covers_every_game(self):
        rows = [{"game": "lol", "result": "over", "margin": 1.0},
                {"game": "cs2", "result": "under", "margin": -1.0}]
        s = sc.summarise(rows)
        assert s["all"]["graded"] == 2 and s["all"]["mean_margin"] == 0.0


class TestHistoryLoading:
    def test_a_truncated_row_is_skipped(self, tmp_path):
        p = tmp_path / "h.jsonl"
        p.write_text('{"game":"lol"}\n{"game":"cs2"\n{"game":"valorant"}\n')
        assert [r["game"] for r in sc.load_history(str(p))] == ["lol", "valorant"]

    def test_a_missing_file_is_distinguishable_from_an_empty_one(self, tmp_path):
        """Different situations wanting different messages: nothing has run
        yet, versus it ran and recorded nothing."""
        assert sc.load_history(str(tmp_path / "nope.jsonl")) is None
        (tmp_path / "empty.jsonl").write_text("")
        assert sc.load_history(str(tmp_path / "empty.jsonl")) == []

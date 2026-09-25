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
    """An OLD-shaped CS2 record: one total covering exactly maps 1-2 and
    nothing finer. Records scraped before the per-map breakdown existed
    all look like this, and they age out only as matches are re-fetched,
    so the fallback they exercise has to keep working."""
    return {"date": date, "teamA": "Sashi", "teamB": "FOKUS",
            "actual": {"Sashi": {"acoR": {"k": kills, "d": 20, "a": 5}}}}


def cs2_match_per_map(date="2026-09-21", kills=(14, 14, 11)):
    """A CS2 record as the scraper writes one now: every map played, kept
    separately, with `actual` still summing exactly maps 1-2."""
    return {
        "date": date, "teamA": "Sashi", "teamB": "FOKUS",
        "per_game": [{"Sashi": {"acoR": {"k": k, "d": 10, "a": 2, "hs": 7}}}
                     for k in kills],
        "actual": {"Sashi": {"acoR": {"k": sum(kills[:2]), "d": 20, "a": 5}}},
    }


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
    def test_a_lone_total_refuses_any_other_window(self, maps):
        """An old-shaped record's total covers maps 1-2 and says nothing
        about any other window. A map-1 line graded against it would lose
        almost always, and the record would look like a broken model."""
        value, reason = sc.actual_over_window(cs2_match(), "Sashi", "acoR", "kills", maps, "cs2")
        assert value is None and "no per-map breakdown" in reason

    def test_cs2_map_one_resolves_from_the_breakdown(self):
        """The refusal above is not a fact about CS2, it is a fact about a
        record with no breakdown. Given one, a map-1 line is exact."""
        value, reason = sc.actual_over_window(
            cs2_match_per_map(kills=(14, 14, 11)), "Sashi", "acoR", "kills", 1, "cs2")
        assert value == 14 and reason is None

    def test_cs2_maps_one_to_three_resolves_from_the_breakdown(self):
        """The window nobody could grade before. A maps 1-3 line is its
        own market and settles over its own three maps."""
        value, reason = sc.actual_over_window(
            cs2_match_per_map(kills=(14, 14, 11)), "Sashi", "acoR", "kills", 3, "cs2")
        assert value == 39 and reason is None

    def test_cs2_maps_one_to_two_still_matches_the_stored_total(self):
        """The breakdown and the total have to agree on the window they
        overlap on, or one of the two is wrong and every record built on
        either is suspect."""
        match = cs2_match_per_map(kills=(14, 14, 11))
        from_breakdown, _ = sc.actual_over_window(match, "Sashi", "acoR", "kills", 2, "cs2")
        assert from_breakdown == match["actual"]["Sashi"]["acoR"]["k"] == 28

    def test_a_three_map_line_on_a_two_map_series_is_refused(self):
        """A CS2 series that ended 2-0 never played a third map. Settling
        a maps 1-3 line on two maps invents the third map's zero, so it is
        counted under its own reason instead."""
        value, reason = sc.actual_over_window(
            cs2_match_per_map(kills=(14, 14)), "Sashi", "acoR", "kills", 3, "cs2")
        assert value is None and "2 map" in reason

    def test_the_breakdown_beats_the_total_when_both_are_present(self):
        """Not a preference -- a requirement. The total answers exactly
        one window and the breakdown answers every window, so reaching for
        the total first would refuse questions the record can answer."""
        match = cs2_match_per_map(kills=(14, 14, 11))
        match["actual"]["Sashi"]["acoR"]["k"] = 999  # deliberately wrong
        value, _ = sc.actual_over_window(match, "Sashi", "acoR", "kills", 2, "cs2")
        assert value == 28

    def test_headshots_resolve_per_map(self):
        value, reason = sc.actual_over_window(
            cs2_match_per_map(), "Sashi", "acoR", "headshots", 2, "cs2")
        assert value == 14 and reason is None

    def test_a_lone_total_uses_the_window_the_match_records(self):
        """LoL's total follows the format: a Bo5's runs through map 3. A
        record that says so grades a maps 1-3 line and refuses a 1-2 one,
        which is the reverse of the CS2 default and the reason the window
        is read off the match rather than off the game."""
        match = lol_match(maps=3)
        del match["per_game"]
        value, reason = sc.actual_over_window(match, "T1", "Faker", "kills", 3, "lol")
        assert value == 15 and reason is None
        value, reason = sc.actual_over_window(match, "T1", "Faker", "kills", 2, "lol")
        assert value is None and "maps 1-3 total" in reason

    @pytest.mark.parametrize("maps", [0, -1, None, "2"])
    def test_a_line_with_no_usable_window_is_refused(self, maps):
        """maps reaches here straight off the provider's payload. A zero
        or a string would slice per_game into an empty list and grade
        every such line as a 0, which reads as a real result."""
        value, reason = sc.actual_over_window(
            cs2_match_per_map(), "Sashi", "acoR", "kills", maps, "cs2")
        assert value is None and "map window" in reason

    def test_a_player_missing_from_a_map_is_refused(self):
        match = lol_match()
        del match["per_game"][1]["T1"]["Faker"]
        value, reason = sc.actual_over_window(match, "T1", "Faker", "kills", 2, "lol")
        assert value is None and "box score" in reason

    def test_a_stat_the_app_does_not_model(self):
        value, reason = sc.actual_over_window(lol_match(), "T1", "Faker", "flibberts", 2, "lol")
        assert value is None and "model" in reason

    def test_a_modelled_stat_the_game_does_not_record(self):
        """Headshots are modelled, and LoL records none. That is a different
        refusal from a stat nothing models, and a different one again from a
        player who is simply absent -- reporting all three the same way sends
        whoever reads the counts hunting a scraping gap that is not there."""
        value, reason = sc.actual_over_window(lol_match(), "T1", "Faker", "headshots", 2, "lol")
        assert value is None and reason == "headshots not recorded for this game"


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


class TestDoubleHeaders:
    """Teams play twice in a day, and a calendar date cannot say which
    match a line belonged to.

    The grader refused those outright rather than coin-flip, which was
    right and cost 211 of the ungraded lines. Both sides carry a clock
    now: the posted line always had start_time, and the CS2 scraper
    stores the match's full timestamp instead of truncating it to a day.

    None is still the answer whenever the clocks cannot settle it. A
    wrong pick here does not surface as an error downstream -- it
    surfaces as a graded result, which is worse than no result.
    """

    @staticmethod
    def match(start, tag):
        return {"start_time": start, "tag": tag}

    def test_the_nearer_match_wins(self):
        got = sc.nearest_by_start_time(
            [self.match("2026-09-22T10:00:00+00:00", "morning"),
             self.match("2026-09-22T18:00:00+00:00", "evening")],
            "2026-09-22T17:40:00+00:00")
        assert got["tag"] == "evening"

    def test_and_so_does_the_earlier_one_when_it_is_nearer(self):
        got = sc.nearest_by_start_time(
            [self.match("2026-09-22T10:00:00+00:00", "morning"),
             self.match("2026-09-22T18:00:00+00:00", "evening")],
            "2026-09-22T10:15:00+00:00")
        assert got["tag"] == "morning"

    def test_offsets_are_respected_not_ignored(self):
        """The provider writes -04:00 and the source writes +00:00. Read
        as wall clock these are four hours apart and the wrong match
        wins."""
        # Candidates chosen so the two readings pick DIFFERENT matches:
        # read correctly the line is 14:00 UTC and 13:30 wins; read as
        # wall clock it is 10:00 and 09:30 wins.
        got = sc.nearest_by_start_time(
            [self.match("2026-09-22T09:30:00+00:00", "utc-morning"),
             self.match("2026-09-22T13:30:00+00:00", "utc-afternoon")],
            "2026-09-22T10:00:00-04:00")   # == 14:00 UTC
        assert got["tag"] == "utc-afternoon"

    def test_a_naive_timestamp_is_read_as_utc(self):
        got = sc.nearest_by_start_time(
            [self.match("2026-09-22T14:00:00", "naive")], "2026-09-22T14:05:00+00:00")
        assert got["tag"] == "naive"

    def test_nothing_within_tolerance_is_refused(self):
        assert sc.nearest_by_start_time(
            [self.match("2026-09-22T02:00:00+00:00", "dawn")],
            "2026-09-22T20:00:00+00:00") is None

    def test_two_matches_equally_close_are_refused(self):
        assert sc.nearest_by_start_time(
            [self.match("2026-09-22T12:00:00+00:00", "a"),
             self.match("2026-09-22T12:00:00+00:00", "b")],
            "2026-09-22T12:30:00+00:00") is None

    def test_a_line_with_no_clock_is_refused(self):
        assert sc.nearest_by_start_time(
            [self.match("2026-09-22T12:00:00+00:00", "a")], None) is None

    def test_results_with_no_clock_are_refused(self):
        """Records written before the scraper stored a timestamp. They
        must keep refusing rather than start guessing."""
        assert sc.nearest_by_start_time([{"tag": "old"}, {"tag": "older"}],
                                        "2026-09-22T12:00:00+00:00") is None

    def test_an_unparseable_clock_is_refused(self):
        assert sc.nearest_by_start_time(
            [self.match("2026-09-22T12:00:00+00:00", "a")], "whenever") is None

    def test_the_tolerance_is_tighter_than_a_double_header_gap(self):
        """Two legs of a double-header sit further apart than this, and
        scheduled-versus-actual start disagrees by minutes."""
        assert 1 <= sc.DOUBLE_HEADER_TOLERANCE_HOURS <= 6

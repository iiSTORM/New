"""Carrying matches between runs, so coverage accumulates.

The CS2 scraper rebuilt itself from nothing every run. The global feed is
one page -- pagination was tested and breaks the call -- so each run saw
the most recent ~100 matches and any team that had gone quiet since fell
out of the file completely: roster, history and all. That is why teams
churned between runs (two of them had props matched against them one day
and no roster the next) and why coverage sat at 51 teams against the 123
with fixtures no matter how discovery was tuned.

Merging makes it cumulative. The whole thing turns on a dedupe key, which
is why match_id is now stored: date+teams is not an identity, because two
teams can meet twice in one day.
"""
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")
pytest.importorskip("cs2api")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scrape_cs2 as sc  # noqa: E402


def m(mid, date, a="A", b="B", score="2-0", **extra):
    rec = {"match_id": mid, "date": date, "teamA": a, "teamB": b,
           "score": score, "winner": a, "games": 2,
           "actual": {a: {"p1": {"k": 20, "d": 15, "a": 5}}, b: {}}}
    rec.update(extra)
    return rec


class TestMatchKey:
    def test_the_source_id_is_the_identity(self):
        assert sc.match_key(m("abc", "2026-09-01")) == ("id", "abc")

    def test_the_same_match_from_two_runs_keys_the_same(self):
        assert sc.match_key(m("abc", "2026-09-01")) == sc.match_key(m("abc", "2026-09-02"))

    def test_a_record_with_no_id_falls_back_without_crashing(self):
        key = sc.match_key({"date": "2026-09-01", "teamA": "A", "teamB": "B", "score": "2-0"})
        assert key[0] == "legacy"

    def test_a_double_header_is_not_collapsed_into_one(self):
        """The reason date+teams was never allowed to be the key."""
        first = {"date": "2026-09-01", "teamA": "A", "teamB": "B", "score": "2-0"}
        second = {"date": "2026-09-01", "teamA": "A", "teamB": "B", "score": "1-2"}
        assert sc.match_key(first) != sc.match_key(second)

    def test_an_id_and_a_legacy_record_never_collide(self):
        assert sc.match_key(m("abc", "2026-09-01"))[0] == "id"
        assert sc.match_key({"date": "2026-09-01"})[0] == "legacy"


class TestMerge:
    def test_stored_matches_survive_a_run_that_did_not_see_them(self):
        """The churn fix, stated directly."""
        stored = [m("old", "2026-09-01", "Quiet", "Team")]
        merged = sc.merge_past_matches(stored, [m("new", "2026-09-02")])
        assert {x["match_id"] for x in merged} == {"old", "new"}

    def test_the_same_match_is_not_stored_twice(self):
        merged = sc.merge_past_matches([m("dup", "2026-09-01")], [m("dup", "2026-09-01")])
        assert len(merged) == 1

    def test_this_runs_copy_wins_over_the_stored_one(self):
        """A stored record can predate a fix to how stats are read -- the
        headshot capture landed exactly that way, so the re-fetched copy
        has to be the one kept."""
        old = m("x", "2026-09-01")
        new = m("x", "2026-09-01")
        new["actual"]["A"]["p1"]["hs"] = 9
        merged = sc.merge_past_matches([old], [new])
        assert merged[0]["actual"]["A"]["p1"].get("hs") == 9

    def test_newest_first(self):
        merged = sc.merge_past_matches(
            [m("a", "2026-09-01"), m("c", "2026-09-03")], [m("b", "2026-09-02")])
        assert [x["match_id"] for x in merged] == ["c", "b", "a"]

    def test_empty_inputs_are_not_an_error(self):
        assert sc.merge_past_matches([], []) == []
        assert sc.merge_past_matches(None, None) == []

    def test_a_malformed_record_is_dropped_rather_than_crashing(self):
        merged = sc.merge_past_matches([{"match_id": "junk"}], [m("ok", "2026-09-01")])
        assert [x["match_id"] for x in merged] == ["ok"]


class TestRetention:
    def test_a_team_keeps_only_its_most_recent_few(self):
        stored = [m(f"g{i}", f"2026-09-{i:02d}") for i in range(1, 21)]
        merged = sc.merge_past_matches(stored, [], per_team=8)
        assert len(merged) == 8
        assert merged[0]["match_id"] == "g20", "and it keeps the NEWEST, not the first seen"

    def test_a_quiet_team_is_not_evicted_by_a_busy_opponent(self):
        """Kept while EITHER side still has room.

        Without that rule the one match a small team has on file is
        dropped as soon as its opponent has a fuller schedule, and the
        small team loses its roster -- which is the bug this whole file
        exists to stop.
        """
        busy = [m(f"b{i}", f"2026-09-{i + 5:02d}", "Busy", "Other") for i in range(1, 9)]
        lone = [m("lone", "2026-09-01", "Busy", "Quiet")]
        merged = sc.merge_past_matches(busy + lone, [], per_team=8)
        assert "lone" in {x["match_id"] for x in merged}

    def test_the_cap_is_per_team_not_overall(self):
        a = [m(f"a{i}", f"2026-09-{i:02d}", "A", "B") for i in range(1, 9)]
        c = [m(f"c{i}", f"2026-09-{i:02d}", "C", "D") for i in range(1, 9)]
        merged = sc.merge_past_matches(a + c, [], per_team=8)
        assert len(merged) == 16

    def test_the_shipped_cap_is_enough_to_draw_the_form_chart(self):
        """The app charts the last 8. Keeping fewer would silently
        shorten every CS2 form chart in the product."""
        # Raised 8 -> 16 on measurement: thinning the committed history
        # and re-running the backtest gives a monotonic -3.0% on kills
        # MAE from cap 2 to cap 8, still falling at 8. The floor is what
        # the form chart draws; the reason to go past it is accuracy.
        assert sc.MATCHES_KEPT_PER_TEAM >= 8


class TestLoadingPrevious:
    def test_a_missing_file_is_not_fatal(self, tmp_path):
        assert sc.load_previous_matches(str(tmp_path / "nope.json")) == []

    def test_a_corrupt_file_is_not_fatal(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert sc.load_previous_matches(str(bad)) == []

    def test_a_real_file_comes_back(self, tmp_path):
        good = tmp_path / "cs2.json"
        good.write_text(json.dumps(
            {"regions": {"CS2": {"past_matches": [m("x", "2026-09-01")]}}}))
        assert [r["match_id"] for r in sc.load_previous_matches(str(good))] == ["x"]

    def test_a_file_with_no_cs2_region_is_not_fatal(self, tmp_path):
        p = tmp_path / "cs2.json"
        p.write_text(json.dumps({"regions": {}}))
        assert sc.load_previous_matches(str(p)) == []


class TestCoverageAccumulates:
    def test_merging_rosters_more_teams_than_the_feed_alone(self):
        """The end-to-end claim, on the builder the scraper actually uses."""
        stored = [m("old", "2026-09-01", "Gone Quiet", "Also Quiet")]
        fresh = [m("new", "2026-09-10", "Active", "Rival")]

        def teams_for(matches):
            teams, color = {}, {"i": 0}
            sc.add_team_players(matches, matches, teams, color)
            return set(teams)

        assert teams_for(fresh) == {"Active", "Rival"}
        assert teams_for(sc.merge_past_matches(stored, fresh)) == {
            "Active", "Rival", "Gone Quiet", "Also Quiet"}

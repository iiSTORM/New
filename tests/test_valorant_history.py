"""Valorant's per-player history surviving longer than one event.

The prior event was fetched, aggregated into the "hist" tier, and its
matches thrown away. That capped every player's per-match record at the
CURRENT event -- a median of 8 maps against LoL's 14 -- and reset it to
zero the day an event rolled over, which is exactly when the board is
busiest.

It matters because the relationship between evidence and accuracy was
measured and is steep right there: a projection made on 4 maps or fewer
realises 52% of its edge, one made on more than 12 realises 98%.

Kept in history_matches rather than appended to past_matches, because
past_matches answers "what has happened at THIS event" for standings and
the Past Results tab, where a previous split's games would be wrong.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
pytest.importorskip("bs4")
pytest.importorskip("aiohttp")
import scrape_valorant as sv


def m(mid, a="A", b="B", date=None, score=None):
    """One match.

    Date and score vary with the id unless given, so two different
    matches look different to the composite fallback key as well as to
    the id -- a fixture where every match shares a date and a score
    makes them one match under that key, and the tests stop testing
    what they say.
    """
    n = 0 if mid is None else int(mid)
    return {"match_id": mid, "teamA": a, "teamB": b,
            "date": date or f"2026-05-{n % 28 + 1:02d}",
            "score": score or f"2-{n % 3}", "actual": {}, "games": 2}


class TestIdentity:
    def test_the_source_id_is_the_key(self):
        assert sv.match_key(m(7)) == ("id", "7")

    def test_two_meetings_in_one_day_are_two_matches(self):
        """A double-header is real. date+teams alone would collapse them
        into one and silently halve that pair's history."""
        first = m(None, date="2026-05-01", score="2-0")
        second = m(None, date="2026-05-01", score="1-2")
        assert sv.match_key(first) != sv.match_key(second)

    def test_a_match_with_an_id_still_has_a_composite_identity(self):
        """The only thing a stored copy and a fetched copy of one match
        share across the transition to ids."""
        assert sv.legacy_key(m(7)) == sv.legacy_key({**m(7), "match_id": None})

    def test_a_record_without_an_id_still_has_an_identity(self):
        """Records written before match_id was stored. They age out on
        their own as the per-team cap rolls forward."""
        assert sv.match_key(m(None))[0] == "legacy"


class TestMerging:
    def test_last_run_s_matches_are_kept(self):
        got = sv.merge_history_matches([m(1)], [m(2)], [])
        assert sorted(x["match_id"] for x in got) == [1, 2]

    def test_a_match_is_not_stored_twice(self):
        got = sv.merge_history_matches([m(1)], [m(1)], [])
        assert len(got) == 1

    def test_this_run_s_copy_wins(self):
        """The stored one may predate a fix to how stats are read -- the
        opening-duel columns landed exactly that way."""
        old = m(1); old["actual"] = {"A": {"p": {"k": 1}}}
        new = m(1); new["actual"] = {"A": {"p": {"k": 1, "fk": 2}}}
        got = sv.merge_history_matches([old], [new], [])
        assert got[0]["actual"]["A"]["p"] == {"k": 1, "fk": 2}

    def test_a_match_in_the_current_event_is_not_also_history(self):
        """Every consumer concatenates the two lists. A match in both is
        counted twice, in the rate and in the evidence count."""
        got = sv.merge_history_matches([m(1), m(2)], [], [m(1)])
        assert [x["match_id"] for x in got] == [2]

    def test_a_stored_copy_from_before_ids_is_the_same_match(self):
        """What actually happened on the first run after match_id landed.
        Last run's file had no ids, so the stored copy keyed as legacy
        and the fresh copy keyed as id, the exclusion matched neither,
        and all 247 current matches ended up in BOTH lists."""
        stored = {**m(1), "match_id": None}
        assert sv.merge_history_matches([stored], [], [m(1)]) == []

    def test_it_holds_in_the_other_direction_too(self):
        """The mirror case: the CURRENT match is the one without an id,
        and the stored copy has one. Neither side of the transition can
        be assumed to be the identified one."""
        current_without_id = {**m(1), "match_id": None}
        assert sv.merge_history_matches([m(1)], [], [current_without_id]) == []

    def test_a_stored_copy_and_a_fetched_copy_collapse_to_one(self):
        """The same collision inside the pool itself. The one carrying
        the id wins, because it is the one this run fetched."""
        stored = {**m(1), "match_id": None, "actual": {"stale": True}}
        got = sv.merge_history_matches([stored], [m(1)], [])
        assert len(got) == 1
        assert got[0]["match_id"] == 1 and got[0]["actual"] == {}

    def test_newest_first(self):
        got = sv.merge_history_matches(
            [m(1, date="2026-01-01"), m(3, date="2026-03-01"), m(2, date="2026-02-01")], [], [])
        assert [x["match_id"] for x in got] == [3, 2, 1]

    def test_a_malformed_record_is_dropped_not_fatal(self):
        got = sv.merge_history_matches([{}, None, {"teamA": "A"}, m(1)], [], [])
        assert [x["match_id"] for x in got] == [1]


class TestTheCap:
    def test_a_team_stops_at_the_cap(self):
        history = [m(i, date=f"2026-01-{i:02d}") for i in range(1, 41)]
        got = sv.merge_history_matches(history, [], [], per_team=5)
        assert len(got) == 5
        assert [x["match_id"] for x in got] == [40, 39, 38, 37, 36], "newest kept"

    def test_the_current_event_counts_against_the_allowance(self):
        """Otherwise a team mid-season grows without bound while the cap
        only ever bites on teams that have not started."""
        current = [m(100 + i, date=f"2026-06-{i:02d}") for i in range(1, 5)]
        history = [m(i, date=f"2026-01-{i:02d}") for i in range(1, 41)]
        got = sv.merge_history_matches(history, [], current, per_team=5)
        assert len(got) == 1, "four already played, one slot left"

    def test_a_quiet_team_is_not_evicted_by_a_busy_opponent(self):
        """Kept while EITHER side still has room. A team with one match
        on file must not lose it because its opponent is full."""
        busy = [m(i, "A", "B", date=f"2026-01-{i:02d}") for i in range(1, 10)]
        quiet = [m(99, "A", "Quiet", date="2026-01-01")]
        got = sv.merge_history_matches(busy + quiet, [], [], per_team=3)
        assert 99 in [x["match_id"] for x in got]

    def test_the_cap_is_deep_enough_to_outlast_an_event(self):
        """A team plays 10-14 maps in a split. A cap below that would
        undo the whole point the first time an event rolled over."""
        assert sv.MATCHES_KEPT_PER_TEAM >= 24


class TestAccumulation:
    def test_last_run_s_current_event_becomes_this_run_s_history(self):
        """The rollover case, and the reason `previous` is both lists.
        Those matches were the current event's a run ago."""
        previous = {"VCT Pacific": {"history_matches": [], "past_matches": [m(1), m(2)]}}
        regions = {"VCT Pacific": {"history_matches": [], "past_matches": [m(9)]}}
        sv.accumulate_history(regions, previous)
        assert sorted(x["match_id"] for x in regions["VCT Pacific"]["history_matches"]) == [1, 2]
        assert [x["match_id"] for x in regions["VCT Pacific"]["past_matches"]] == [9], (
            "the current event's own list is untouched")

    def test_history_deepens_run_over_run(self):
        previous = {"R": {"history_matches": [m(1)], "past_matches": [m(2)]}}
        regions = {"R": {"history_matches": [m(3)], "past_matches": [m(4)]}}
        sv.accumulate_history(regions, previous)
        assert sorted(x["match_id"] for x in regions["R"]["history_matches"]) == [1, 2, 3]

    def test_a_region_the_previous_file_never_had_is_fine(self):
        regions = {"New": {"history_matches": [m(1)], "past_matches": []}}
        sv.accumulate_history(regions, {})
        assert [x["match_id"] for x in regions["New"]["history_matches"]] == [1]

    def test_the_report_says_what_grew(self):
        previous = {"R": {"history_matches": [m(1), m(2)], "past_matches": []}}
        regions = {"R": {"history_matches": [m(3)], "past_matches": []}}
        report = sv.accumulate_history(regions, previous)
        assert report == [("R", 1, 3)]


class TestLoadingLastRun:
    def test_a_missing_file_is_not_an_error(self, tmp_path):
        assert sv.load_previous_regions(str(tmp_path / "nope.json")) == {}

    def test_a_corrupt_file_is_not_an_error(self, tmp_path):
        bad = tmp_path / "v.json"
        bad.write_text("{ not json")
        assert sv.load_previous_regions(str(bad)) == {}, (
            "a corrupt file must cost a shallow run, never a crashed one")

    def test_a_real_file_round_trips(self, tmp_path):
        good = tmp_path / "v.json"
        good.write_text(json.dumps({"regions": {"R": {"past_matches": [m(1)]}}}))
        assert sv.load_previous_regions(str(good))["R"]["past_matches"][0]["match_id"] == 1

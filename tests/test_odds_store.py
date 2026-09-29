"""The accumulating odds store, whose whole job is not to lose anything."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import odds_store


ODDS = {"prices": {"A": 2.0, "B": 1.8}, "implied": {"A": 0.47, "B": 0.53}}
LATER = {"prices": {"A": 2.4, "B": 1.6}, "implied": {"A": 0.40, "B": 0.60}}


def test_a_missing_file_reads_as_an_empty_store():
    assert odds_store.load("does-not-exist.json") == {}


def test_a_corrupt_file_reads_as_empty_rather_than_raising(tmp_path):
    path = tmp_path / "odds.json"
    path.write_text("{not json")
    assert odds_store.load(str(path)) == {}


def test_the_first_sighting_is_kept_alongside_the_last():
    store = {}
    assert odds_store.record(store, "m1", ODDS, "2026-09-01T00:00:00Z") is True
    assert odds_store.record(store, "m1", LATER, "2026-09-02T00:00:00Z") is True
    entry = store["m1"]
    assert entry["first"] == ODDS and entry["first_seen"] == "2026-09-01T00:00:00Z"
    assert entry["last"] == LATER and entry["last_seen"] == "2026-09-02T00:00:00Z"


def test_an_out_of_order_capture_never_overwrites_a_later_one():
    """Because the later sighting is the one nearer kickoff."""
    store = {}
    odds_store.record(store, "m1", LATER, "2026-09-02T00:00:00Z")
    assert odds_store.record(store, "m1", ODDS, "2026-09-01T00:00:00Z") is False
    assert store["m1"]["last"] == LATER


def test_re_seeing_the_same_price_at_the_same_time_is_not_a_change():
    store = {}
    odds_store.record(store, "m1", ODDS, "2026-09-01T00:00:00Z")
    assert odds_store.record(store, "m1", ODDS, "2026-09-01T00:00:00Z") is False


def test_nothing_is_recorded_without_a_key_or_without_odds():
    store = {}
    assert odds_store.record(store, "", ODDS, "t") is False
    assert odds_store.record(store, "m1", {}, "t") is False
    assert store == {}


def test_extra_fields_are_stored_and_refreshed_but_never_blanked():
    store = {}
    odds_store.record(store, "m1", ODDS, "t1", teamA="Falcons", teamB="Vitality")
    odds_store.record(store, "m1", LATER, "t2", teamA="Team Falcons", teamB=None)
    assert store["m1"]["teamA"] == "Team Falcons"
    assert store["m1"]["teamB"] == "Vitality"


def test_a_run_that_saw_less_cannot_publish_its_smaller_view(tmp_path):
    """The one rule that makes this file different from every other one here.

    Every other JSON file in the repository is wholesale-regenerated and last
    writer wins. Here that would destroy prices that exist nowhere else --
    bo3.gg stops serving a pre-match price the moment the match starts.
    """
    path = str(tmp_path / "odds.json")
    full = {f"m{i}": {"first": ODDS, "last": ODDS, "first_seen": "t",
                      "last_seen": "t"} for i in range(5)}
    assert odds_store.save(full, path, "now") == 5

    with pytest.raises(ValueError, match="accumulates"):
        odds_store.save({"m1": full["m1"]}, path, "later")

    # And the file on disk is untouched by the refused write.
    assert len(odds_store.load(path)) == 5


def test_a_run_that_saw_the_same_or_more_writes_normally(tmp_path):
    path = str(tmp_path / "odds.json")
    store = {}
    odds_store.record(store, "m1", ODDS, "t1")
    odds_store.save(store, path, "now")
    reloaded = odds_store.load(path)
    assert reloaded == store
    odds_store.record(reloaded, "m2", LATER, "t2")
    assert odds_store.save(reloaded, path, "later") == 2
    assert set(odds_store.load(path)) == {"m1", "m2"}


def test_an_implausible_number_of_captures_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(odds_store, "MAX_MATCHES", 2)
    with pytest.raises(ValueError, match="MAX_MATCHES"):
        odds_store.save({"a": 1, "b": 2, "c": 3}, str(tmp_path / "o.json"), "now")


def test_the_written_file_carries_when_it_was_generated(tmp_path):
    path = str(tmp_path / "odds.json")
    store = {}
    odds_store.record(store, "m1", ODDS, "t1")
    odds_store.save(store, path, "2026-09-29T00:00:00Z")
    assert json.load(open(path))["generated_at"] == "2026-09-29T00:00:00Z"

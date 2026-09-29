"""Merging two odds stores, which must never lose a capture.

The scrape workflow's push-retry resets to origin and re-applies the run's own
snapshot. For every other file it writes that is correct. For this one it would
delete another run's captures of prices that no longer exist anywhere, and
nothing would look broken afterwards.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import merge_odds_history as moh

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def entry(first_seen, last_seen, first=1.5, last=1.6, **extra):
    return {"first_seen": first_seen, "last_seen": last_seen,
            "first": {"prices": {"A": first}}, "last": {"prices": {"A": last}},
            **extra}


def test_captures_only_one_side_has_are_kept():
    merged = moh.merge({"a": entry("t1", "t1")}, {"b": entry("t1", "t1")})
    assert set(merged) == {"a", "b"}


def test_the_earliest_first_and_the_latest_last_both_survive():
    mine = {"m": entry("2026-09-01", "2026-09-02", first=2.0, last=2.1)}
    theirs = {"m": entry("2026-09-03", "2026-09-04", first=3.0, last=3.1)}
    merged = moh.merge(mine, theirs)["m"]
    assert merged["first_seen"] == "2026-09-01"
    assert merged["first"]["prices"]["A"] == 2.0
    assert merged["last_seen"] == "2026-09-04"
    assert merged["last"]["prices"]["A"] == 3.1


def test_merging_is_symmetric_on_the_pair_that_matters():
    mine = {"m": entry("2026-09-01", "2026-09-02")}
    theirs = {"m": entry("2026-09-03", "2026-09-04")}
    one, other = moh.merge(mine, theirs)["m"], moh.merge(theirs, mine)["m"]
    assert one["first_seen"] == other["first_seen"]
    assert one["last_seen"] == other["last_seen"]


def test_a_missing_label_is_filled_in_from_the_other_side():
    mine = {"m": entry("t1", "t1", teamA="Falcons", teamB=None)}
    theirs = {"m": entry("t1", "t1", teamA=None, teamB="Vitality")}
    merged = moh.merge(mine, theirs)["m"]
    assert merged["teamA"] == "Falcons" and merged["teamB"] == "Vitality"


def test_junk_on_one_side_does_not_take_the_other_side_down():
    assert moh.merge({"m": "not a dict"}, {"m": entry("t", "t")})["m"]["first_seen"] == "t"
    assert moh.merge({"m": entry("t", "t")}, {"m": "not a dict"})["m"]["first_seen"] == "t"


def test_the_command_line_merges_two_files(tmp_path):
    mine, theirs, out = (str(tmp_path / n) for n in ("a.json", "b.json", "c.json"))
    json.dump({"captured": {"a": entry("t1", "t1")}}, open(mine, "w"))
    json.dump({"captured": {"b": entry("t1", "t1")}}, open(theirs, "w"))
    done = subprocess.run([sys.executable,
                           os.path.join(ROOT, "scripts", "merge_odds_history.py"),
                           mine, theirs, out], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert set(json.load(open(out))["captured"]) == {"a", "b"}


def test_the_command_line_refuses_the_wrong_number_of_arguments():
    done = subprocess.run([sys.executable,
                           os.path.join(ROOT, "scripts", "merge_odds_history.py")],
                          capture_output=True, text=True)
    assert done.returncode == 2


def test_the_merge_keeps_the_later_stamp_rather_than_dropping_it(tmp_path):
    """The committed file's only provenance runs through here.

    The workflow merges between the scraper writing the file and git
    committing it, so whatever this writes is what lands on main. The first
    committed copy read "generated_at: null" for exactly this reason.
    """
    mine, theirs, out = (str(tmp_path / n) for n in ("a.json", "b.json", "c.json"))
    json.dump({"generated_at": "2026-09-29T08:06:00Z",
               "captured": {"a": entry("t1", "t1")}}, open(mine, "w"))
    json.dump({"generated_at": "2026-09-28T21:00:00Z",
               "captured": {"b": entry("t1", "t1")}}, open(theirs, "w"))
    subprocess.run([sys.executable,
                    os.path.join(ROOT, "scripts", "merge_odds_history.py"),
                    mine, theirs, out], check=True, capture_output=True)
    assert json.load(open(out))["generated_at"] == "2026-09-29T08:06:00Z"


def test_a_merge_of_two_unstamped_files_is_still_written(tmp_path):
    mine, theirs, out = (str(tmp_path / n) for n in ("a.json", "b.json", "c.json"))
    json.dump({"captured": {"a": entry("t1", "t1")}}, open(mine, "w"))
    json.dump({"captured": {"b": entry("t1", "t1")}}, open(theirs, "w"))
    subprocess.run([sys.executable,
                    os.path.join(ROOT, "scripts", "merge_odds_history.py"),
                    mine, theirs, out], check=True, capture_output=True)
    written = json.load(open(out))
    assert written["generated_at"] is None
    assert set(written["captured"]) == {"a", "b"}

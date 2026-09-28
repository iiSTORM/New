"""Auto-grading esports legs from the scrapers' own graded record.

The interesting case is not the happy path, it is ambiguity: the same player can
have the same line on the same stat on two different days, and grading Tuesday's
slip off Wednesday's result is worse than leaving it pending. It is wrong, it
looks graded, and it moves the bankroll. So it refuses.
"""
import json

from propedge.autograde import autograde, candidates_for, index_graded
from propedge.slips import Leg, Slip
from propedge.store import Tracker


def row(player, actual, line=15.5, stat="headshots", maps=2, game="cs2",
        match_date="2026-09-28"):
    return {"game": game, "player": player, "stat": stat, "maps": maps,
            "line": line, "actual": actual, "result": "over",
            "match_date": match_date, "start_time": f"{match_date}T13:30:00-04:00",
            "team": "Alpha"}


def results_file(tmp_path, rows):
    path = tmp_path / "props_results.json"
    path.write_text(json.dumps({"graded": rows}))
    return str(path)


def leg(player, line=15.5, side="over", sport="cs2", stat="headshots", maps=2):
    return Leg(sport=sport, player=player, team="Alpha" if player == "P" else "Beta",
               stat=stat, line=line, side=side, maps=maps)


def tracker_with(tmp_path, *legs, placed_at="2026-09-28T20:00:00+00:00"):
    tracker = Tracker(tmp_path / "store.json")
    tracker.place(Slip(mode="power", stake_cents=100, multiplier="3",
                       legs=list(legs), placed_at=placed_at), force=True)
    return tracker


def test_a_matching_line_is_graded(tmp_path):
    tracker = tracker_with(tmp_path, leg("P"), leg("Q", side="under"))
    path = results_file(tmp_path, [row("P", 20), row("Q", 10)])
    graded, skipped = autograde(tracker, path)
    assert len(graded) == 2 and not skipped
    assert [l.result for l in tracker.slips[0].legs] == ["won", "won"]


def test_the_window_is_part_of_the_match(tmp_path):
    """maps 1-2 and maps 1-3 are different props on the same player and stat."""
    tracker = tracker_with(tmp_path, leg("P", maps=3), leg("Q", side="under"))
    path = results_file(tmp_path, [row("P", 20, maps=2), row("Q", 10)])
    graded, skipped = autograde(tracker, path)
    assert len(graded) == 1
    assert any("not in the graded record yet" in s for s in skipped)


def test_a_different_line_is_a_different_prop(tmp_path):
    tracker = tracker_with(tmp_path, leg("P", line=16.5), leg("Q"))
    graded, skipped = autograde(tracker, results_file(tmp_path, [row("P", 20)]))
    assert graded == []
    assert len(skipped) == 2


def test_two_graded_rows_that_disagree_are_left_alone(tmp_path):
    tracker = tracker_with(tmp_path, leg("P"), leg("Q"))
    path = results_file(tmp_path, [row("P", 20, match_date="2026-09-28"),
                                   row("P", 9, match_date="2026-09-20")])
    graded, skipped = autograde(tracker, path)
    # the slip's own date narrows it to one row, so it grades
    assert len(graded) == 1 and tracker.slips[0].legs[0].result == "won"


def test_rows_that_disagree_on_the_same_day_refuse(tmp_path):
    tracker = tracker_with(tmp_path, leg("P"), leg("Q"))
    path = results_file(tmp_path, [row("P", 20), row("P", 9)])
    graded, skipped = autograde(tracker, path)
    assert graded == []
    assert any("disagree" in s for s in skipped)


def test_a_non_esports_leg_is_left_for_manual_grading(tmp_path):
    tracker = tracker_with(tmp_path,
                           leg("Keeper", line=4, side="under", sport="soccer",
                               stat="goalie saves", maps=None),
                           leg("Q"))
    graded, skipped = autograde(tracker, results_file(tmp_path, [row("Q", 20)]))
    assert any("soccer is not auto-graded" in s for s in skipped)


def test_an_already_graded_leg_is_not_regraded(tmp_path):
    tracker = tracker_with(tmp_path, leg("P"), leg("Q"))
    tracker.slips[0].legs[0].grade(99)
    graded, _ = autograde(tracker, results_file(tmp_path, [row("P", 20), row("Q", 20)]))
    assert len(graded) == 1
    assert tracker.slips[0].legs[0].actual == 99


def test_a_settled_slip_is_not_touched(tmp_path):
    tracker = tracker_with(tmp_path, leg("P"), leg("Q"))
    tracker.slips[0].status = "lost"
    graded, skipped = autograde(tracker, results_file(tmp_path, [row("P", 20)]))
    assert (graded, skipped) == ([], [])


def test_candidates_are_narrowed_by_date(tmp_path):
    index = index_graded([row("P", 20, match_date="2026-09-28"),
                          row("P", 9, match_date="2026-09-20")])
    assert len(candidates_for(leg("P"), index)) == 2
    assert len(candidates_for(leg("P"), index, "2026-09-20")) == 1


def test_the_real_graded_file_indexes_without_error():
    """A shape check against the file the scrapers actually write."""
    from propedge.autograde import load_graded
    rows = load_graded("props_results.json")
    index = index_graded(rows)
    assert len(rows) > 100
    assert index, "no graded row produced a key — has props_results.json changed?"

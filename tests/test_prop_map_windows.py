"""Which map a single-map line is for, and exact duplicate lines.

The board of 2026-10-03 11:00 UTC carried 29 CS2 lines twice, identical in
every field, all of them single-map lines. The provider posts "MAP 3 Kills"
as well as "MAP 1 Kills" (four of the former in one saved payload), and the
parser filed every single-map label as maps=1 with nothing saying which map,
so a Map 1 and a Map 2 line at the same number were the same row -- and a
Map 3 line was graded against map 1's box score.
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import props_match as pm
import score_props as sp
from propedge.esports import EsportsModel


@pytest.mark.parametrize("label,first,window", [
    ("MAP 1 Kills", 1, 1),
    ("MAP 2 Kills", 2, 1),
    ("MAP 3 Kills", 3, 1),
    ("MAP 3 Headshots", 3, 1),
    ("MAPS 1-2 Kills", 1, 2),
    ("MAPS 1-3 Kills", 1, 3),
    ("Maps 1-2 Deaths", 1, 2),
    ("Kills", 1, None),
])
def test_first_map_and_window(label, first, window):
    assert pm.first_map(label) == first
    assert pm.parse_stat(label)[1] == window


def raw(label, line, odds="standard"):
    return {"player_name": "Nertz", "stat_label": label, "line": line, "team": "G2",
            "start_time": "2026-10-03T12:30:00.000-04:00", "odds_type": odds,
            "provider": "prizepicks"}


INDEX = {"nertz": [("CS2", "G2", "Nertz")]}


def test_a_map_three_line_says_so_and_a_map_one_line_is_unchanged():
    matched, _ = pm.match_props([raw("MAP 1 Kills", 15.5), raw("MAP 3 Kills", 15.5)], INDEX,
                                game="cs2", alias_index={})
    one, three = matched
    assert "map" not in one and one["maps"] == 1
    assert three["map"] == 3 and three["maps"] == 1
    assert pm.dedupe_rows(matched)[1] == 0          # different maps: both kept


def test_exact_copies_are_kept_once_and_near_copies_are_not():
    rows, _ = pm.match_props([raw("MAP 1 Kills", 15.5), raw("MAP 1 Kills", 15.5),
                              raw("MAP 1 Kills", 15.5, "demon"), raw("MAP 1 Kills", 16.5)],
                             INDEX, game="cs2", alias_index={})
    kept, dropped = pm.dedupe_rows(rows)
    assert dropped == 1 and len(kept) == 3


def test_prop_ids_tell_map_one_from_map_three_and_keep_old_ids():
    base = {"game": "cs2", "player": "Nertz", "stat": "kills", "maps": 1, "line": 15.5,
            "odds_type": "standard", "start_time": "2026-10-03T12:30:00.000-04:00"}
    old = EsportsModel.prop_id(base)
    assert old == "cs2:nertz:kills:1:15.5:standard:2026-10-03T12:30:00.000-04:00"
    assert EsportsModel.prop_id({**base, "map": 3}) != old


def box(*maps):
    return {"per_game": [{"G2": {"Nertz": {"k": k}}} for k in maps],
            "actual": {"G2": {"Nertz": {"k": maps[0] + maps[1]}}}}


def test_a_map_three_line_is_graded_on_map_three():
    match = box(10, 20, 30)
    assert sp.actual_over_window(match, "G2", "Nertz", "kills", 1, "cs2") == (10, None)
    assert sp.actual_over_window(match, "G2", "Nertz", "kills", 1, "cs2", first_map=3) == (30, None)


def test_a_map_three_line_on_a_sweep_is_refused_not_zeroed():
    value, reason = sp.actual_over_window(box(10, 20), "G2", "Nertz", "kills", 1, "cs2", first_map=3)
    assert value is None and "map 3" in reason


def test_without_a_per_map_breakdown_a_later_map_cannot_be_graded():
    match = {"actual": {"G2": {"Nertz": {"k": 30}}}, "games": 2}
    value, reason = sp.actual_over_window(match, "G2", "Nertz", "kills", 1, "cs2", first_map=2)
    assert value is None and "map 2" in reason


def test_window_labels():
    assert sp.window_label(1) == "map 1"
    assert sp.window_label(1, 3) == "map 3"
    assert sp.window_label(2) == "maps 1-2"

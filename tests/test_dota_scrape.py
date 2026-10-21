"""The Dota 2 scraper's pure parts: positions, box scores, series.

Built on hand-made OpenDota-shaped game records, since the API is not
reachable from the test runner. Each test pins a rule the other three
games learned the hard way (see the module docstring of scrape_dota.py).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scrape_dota as sd  # noqa: E402

LANES = [(1, 600), (2, 450), (3, 300), (3, 80), (1, 40)]   # (lane_role, last hits): pos 1..5


def side(radiant, names, kills, lanes=LANES, base_id=0):
    return [{"account_id": base_id + i, "name": n, "isRadiant": radiant, "kills": k,
             "deaths": 3, "assists": 7, "last_hits": lh, "gold_per_min": 300 + lh,
             "lane_role": lane}
            for i, (n, k, (lane, lh)) in enumerate(zip(names, kills, lanes))]


RAD = ["Yatoro", "Larl", "Collapse", "Mira", "Miposhka"]
DIRE = ["Ame", "NothingToSay", "Faith_bian", "XinQ", "xNova"]


def detail(match_id, start, radiant_win=True, rad_kills=(10, 8, 5, 2, 1), dire_kills=(6, 5, 4, 2, 1)):
    return {"match_id": match_id, "start_time": start, "radiant_win": radiant_win,
            "radiant_team": {"team_id": 1, "name": "Team Spirit"},
            "dire_team": {"team_id": 2, "name": "Xtreme Gaming"},
            "players": side(True, RAD, rad_kills) + side(False, DIRE, dire_kills, base_id=10)}


def built(match_id, start, **kw):
    box, radiant, dire = sd.game_box(detail(match_id, start, **kw), {}, {})
    winner = radiant if kw.get("radiant_win", True) else dire
    return {"match_id": match_id, "start_time": start, "box": box,
            "radiant": radiant, "dire": dire, "winner": winner}


def test_positions_from_lanes_and_farm():
    pos = sd.assign_positions(side(True, RAD, (1, 1, 1, 1, 1)))
    assert [pos[i] for i in range(5)] == [1, 2, 3, 4, 5]


def test_positions_fall_back_to_farm_when_lanes_do_not_add_up():
    trilane = [(1, 600), (2, 450), (1, 30), (1, 20), (3, 300)]   # three safe, one off
    pos = sd.assign_positions(side(True, RAD, (1, 1, 1, 1, 1), lanes=trilane))
    assert [pos[i] for i in range(5)] == [1, 2, 4, 5, 3]


def test_a_game_a_player_short_is_refused():
    d = detail(1, 1_760_000_000)
    d["players"] = d["players"][:-1]
    assert sd.game_box(d, {}, {}) is None


def test_a_game_without_team_ids_is_refused():
    d = detail(1, 1_760_000_000)
    d["dire_team"] = {}
    assert sd.game_box(d, {}, {}) is None


def test_series_total_is_maps_1_2_and_per_game_keeps_every_map():
    games = [built(103, 1_760_007_200, radiant_win=False), built(101, 1_760_000_000), built(102, 1_760_003_600)]
    s = sd.build_series("s9", games, 5, "The International")
    assert s["game_ids"] == [101, 102, 103]                   # map order is start order
    assert len(s["per_game"]) == 3 and s["games"] == 2
    assert s["actual"]["Team Spirit"]["Yatoro"]["k"] == 20    # maps 1 and 2 only
    assert s["per_game"][2]["Team Spirit"]["Yatoro"] == {"k": 10, "d": 3, "a": 7, "lh": 600, "gpm": 900, "pos": 1}
    assert s["score"] == "2-1" and s["winner"] == "Team Spirit" and s["best_of"] == 5
    assert s["start_time"].startswith("2025-10-09T") and s["date"] == "2025-10-09"
    kp = s["actual"]["Team Spirit"]["Yatoro"]
    assert kp["kp_numerator"] == 2 * (10 + 7) and kp["kp_denominator"] == 2 * 26


def test_a_bo1_covers_one_map():
    s = sd.build_series("m5", [built(5, 1_760_000_000)], 1, "DreamLeague")
    assert s["games"] == 1 and s["score"] == "1-0"


def test_only_finished_series_count():
    now = 1_760_100_000
    one_win = [{"winner": "A", "start_time": now - 3600}]
    assert not sd.is_finished(one_win, 3, now)                # 1-0 in a Bo3, an hour ago
    assert sd.is_finished(one_win + [{"winner": "A", "start_time": now - 1800}], 3, now)
    assert sd.is_finished(one_win, 1, now)
    assert sd.is_finished([{"winner": "A", "start_time": now - 13 * 3600}], 3, now)   # abandoned


def test_rosters_rates_and_usual_position():
    past = [sd.build_series("s1", [built(1, 1_760_000_000), built(2, 1_760_003_600)], 3, "X")]
    teams = sd.build_teams(past)
    spirit = {p["name"]: p for p in teams["Team Spirit"]["players"]}
    assert set(spirit) == set(RAD)
    assert spirit["Yatoro"]["role"] == "POS1" and spirit["Miposhka"]["role"] == "POS5"
    assert spirit["Yatoro"]["cur"]["g"] == 2 and spirit["Yatoro"]["cur"]["k"] == 10
    assert round(spirit["Yatoro"]["cur"]["kp"], 1) == round(17 / 26 * 100, 1)


def test_merge_keeps_a_fresh_copy_and_caps_history_per_team():
    old = [dict(sd.build_series(f"s{i}", [built(i, 1_760_000_000 + i * 86400)], 1, "X")) for i in range(20)]
    merged = sd.merge_past_matches(old[:15], old[10:], per_team=12)
    assert len(merged) == 12 and merged[0]["match_id"] == "dota-s19"
    assert sd.stored_game_ids(merged) == set(range(8, 20))

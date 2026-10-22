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
    one_win = [{"winner": "A"}]
    assert not sd.is_finished(one_win, 3)                     # 1-0 in a Bo3
    assert sd.is_finished(one_win * 2, 3)
    assert sd.is_finished(one_win, 1)
    assert not sd.is_finished([], 1)


def summary(mid, sid, start, rad=1, dire=2, radiant_win=True, series_type=1, league=7):
    return {"match_id": mid, "series_id": sid, "start_time": start, "radiant_team_id": rad,
            "dire_team_id": dire, "radiant_win": radiant_win, "series_type": series_type,
            "leagueid": league}


def test_a_bo3_split_across_series_ids_is_put_back_together():
    """Chandogs v Team Revenant, 2026-10-20: one Bo3, two series ids."""
    t = 1_760_900_000
    got = sd.group_series([summary(1, 100, t), summary(2, 101, t + 4200, rad=2, dire=1, radiant_win=False)])
    assert [(k, bo, [g["match_id"] for g in gs]) for k, bo, gs in got] == [("s100", 3, [1, 2])]


def test_a_finished_series_and_a_far_later_one_are_not_merged():
    t = 1_760_900_000
    done = [summary(1, 100, t), summary(2, 100, t + 3600)]                 # 2-0, finished
    rematch = [summary(3, 200, t + 7200)]
    far = [summary(4, 300, t + 20 * 3600)]
    got = sd.group_series(done + rematch + far)
    assert [k for k, _, _ in got] == ["s100", "s200", "s300"]


def test_other_teams_or_leagues_are_never_merged():
    t = 1_760_900_000
    got = sd.group_series([summary(1, 100, t), summary(2, 101, t + 3600, rad=3),
                           summary(3, 102, t + 3600, league=8)])
    assert len(got) == 3


def test_a_stored_series_that_never_finished_is_dropped():
    assert sd.series_complete({"score": "2-1", "best_of": 3})
    assert not sd.series_complete({"score": "1-0", "best_of": 3})
    assert sd.series_complete({"score": "1-0", "best_of": 1})


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


NOW = 1_761_000_000
TABLE = f"""
<table class="wikitable infobox_matches_content">
 <tr><td class="team-left"><span data-highlightingclass="Team Spirit"><a title="Team Spirit">Spirit</a></span></td>
     <td class="versus"><abbr title="Best of 3">Bo3</abbr></td>
     <td class="team-right"><span data-highlightingclass="Xtreme Gaming"><a title="Xtreme Gaming">XG</a></span></td></tr>
 <tr><td class="match-filler"><span class="timer-object" data-timestamp="{NOW + 7200}">soon</span>
     <a href="/dota2/PGL/Wallachia" title="PGL Wallachia Season 6">PGL</a></td></tr>
</table>
<table class="wikitable infobox_matches_content">
 <tr><td class="team-left"><span data-highlightingclass="TBD">TBD</span></td><td class="team-right"><span data-highlightingclass="Tundra Esports">T</span></td></tr>
 <tr><td><span class="timer-object" data-timestamp="{NOW + 9000}"></span></td></tr>
</table>
<table class="wikitable infobox_matches_content">
 <tr><td><span data-highlightingclass="Team Liquid">L</span></td><td><span data-highlightingclass="Nigma Galaxy">N</span></td></tr>
 <tr><td><span class="timer-object" data-timestamp="{NOW + 30 * 86400}"></span></td></tr>
</table>"""
DIVS = f"""
<div data-switch-group-container="countdown"><div class="">
<div class="match-info"><span class="match-info-countdown"><span class="timer-object" data-format="full"
  data-timestamp="{NOW + 3600}">October 22, 2026 - 11:00 <abbr title="Singapore Time (UTC+8)">SGT</abbr></span></span>
 <div class="match-info-header">
  <div class="match-info-header-opponent match-info-header-opponent-left"><div class="block-team flipped">
    <span class="team-template-image-icon"><a href="/dota2/PARIVISION" title="PARIVISION"><img alt=""/></a></span>
    <span class="name"><a href="/dota2/PARIVISION" title="PARIVISION">PARI</a></span></div></div>
  <div class="match-info-header-scoreholder"><span class="match-info-header-scoreholder-lower">(Bo5)</span></div>
  <div class="match-info-header-opponent"><div class="block-team">
    <span class="name"><a href="/dota2/index.php?title=Cloud_Dawning&amp;action=edit&amp;redlink=1"
      title="Cloud Dawning (page does not exist)">Cloud Dawning</a></span></div></div></div>
 <div class="match-info-tournament"><a href="/dota2/BLAST/Slam/8" title="BLAST Slam VIII">BLAST Slam</a></div></div>
<div class="match-info"><span class="timer-object" data-timestamp="{NOW + 3600}"></span>
 <div class="match-info-header-opponent"><span class="name">PARIVISION</span></div>
 <div class="match-info-header-opponent"><span class="name">Cloud Dawning</span></div></div>
<div class="match-info"><span class="timer-object" data-timestamp="{NOW + 5000}"></span>
 <div class="match-info-header-opponent"><span class="name">TBD</span></div>
 <div class="match-info-header-opponent"><span class="name">Team Liquid</span></div></div>
</div></div>"""


def test_the_schedule_reads_the_table_layout():
    rows = sd.parse_schedule(TABLE, NOW)
    assert rows == [(NOW + 7200, "Team Spirit", "Xtreme Gaming", "PGL Wallachia Season 6", 3)]   # TBD and 30 days out dropped


def test_the_schedule_reads_the_div_layout():
    # The live layout: one row although the match is listed in two filter
    # views, full names from link titles rather than the short tag shown
    # ("PARI"), a red link's title without its suffix, a TBD slot dropped.
    assert sd.parse_schedule(DIVS, NOW) == [(NOW + 3600, "PARIVISION", "Cloud Dawning", "BLAST Slam VIII", 5)]


def test_fixtures_resolve_to_tracked_names_and_need_one_tracked_side():
    rows = [(NOW, "Team Spirit", "Xtreme Gaming", "PGL", 3), (NOW, "Nobody", "Also Nobody", "PGL", 3),
            (NOW, "Team Spirit", "Someone", "1win Streamers League #4", 3)]
    got = sd.upcoming_from_schedule(rows, {"Spirit", "Xtreme Gaming"})
    assert [(m["teamA"], m["teamB"], m["best_of"]) for m in got] == [("Spirit", "Xtreme Gaming", 3)]
    assert got[0]["date"].startswith("2025-10-20T")


def test_fixture_names_drop_disambiguators_and_use_the_explicit_map():
    rows = [(NOW, "LEGION (stack)", "1w Team", "DreamLeague", 3)]
    got = sd.upcoming_from_schedule(rows, {"LEGION", "1win"})
    assert [(m["teamA"], m["teamB"]) for m in got] == [("LEGION", "1win")]


def test_a_failed_schedule_fetch_keeps_unstarted_fixtures():
    from datetime import datetime, timezone
    iso = lambda ts: datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    prev = [{"date": iso(NOW - 60), "teamA": "A", "teamB": "B"}, {"date": iso(NOW + 3600), "teamA": "C", "teamB": "D"}]
    assert [m["teamA"] for m in sd.carry_upcoming(prev, NOW)] == ["C"]


def test_a_refused_game_says_why():
    assert "fetched" in sd.box_problem(None, {})
    d = detail(1, 1_760_000_000)
    d["players"][0]["account_id"] = None
    assert sd.game_box(d, {}, {}) is None and "anonymous" in sd.box_problem(d, {})
    d = detail(1, 1_760_000_000)
    d["players"] = d["players"][:-1]
    assert "4 players" in sd.box_problem(d, {})


def test_a_series_with_an_unregistered_side_is_skipped_before_fetching():
    t = 1_760_900_000
    assert sd.has_registered_teams([summary(1, 100, t), summary(2, 100, t + 3600)])
    assert not sd.has_registered_teams([summary(1, 100, t), summary(2, 100, t + 3600, dire=None)])

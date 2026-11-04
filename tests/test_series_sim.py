"""The series simulator: its arithmetic, its consistency, and its summaries."""
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "dev"))

from propedge import series_sim as sim  # noqa: E402


def match(date, a, b, winner, score, maps):
    """A past series in data.json's shape; maps = [(a kills each, b kills each)]."""
    per_game = []
    for ka, kb in maps:
        per_game.append({a: {f"{a}{i}": {"k": ka, "d": kb, "a": ka} for i in range(5)},
                         b: {f"{b}{i}": {"k": kb, "d": ka, "a": kb} for i in range(5)}})
    return {"date": date, "teamA": a, "teamB": b, "winner": winner, "score": score,
            "per_game": per_game}


def library():
    past = []
    for day in range(1, 25):
        d = f"2026-10-{day:02d}"
        if day % 3:
            past.append(match(d, "Alpha", "Beta", "Alpha", "2-0", [(5, 2), (4, 3)]))
        else:
            past.append(match(d, "Alpha", "Beta", "Beta", "1-2", [(5, 2), (2, 4), (3, 5)]))
    return sim.Library("lol", {"LCK": {"past_matches": past, "teams": {}}})


def test_series_probability_and_its_inverse():
    assert sim.series_probability(0.5, 3) == pytest.approx(0.5)
    assert sim.series_probability(0.6, 3) == pytest.approx(0.6 ** 2 * (3 - 2 * 0.6))
    for bo in (1, 3, 5):
        assert sim.map_probability_from_series(sim.series_probability(0.63, bo), bo) == pytest.approx(0.63, abs=1e-6)


def test_a_bookmaker_price_beats_elo_and_is_turned_into_a_map_price():
    lib = library()
    p, source = sim.fixture_map_probability(lib, "alpha", "beta", "2026-11-01", 3,
                                            {"implied": {"Alpha": 0.648, "Beta": 0.352}})
    assert source == "odds" and sim.series_probability(p, 3) == pytest.approx(0.648, abs=1e-6)
    assert sim.fixture_map_probability(lib, "alpha", "beta", "2026-11-01", 3)[1] == "elo"


def test_every_run_is_a_legal_series_and_kills_mirror_deaths():
    lib = library()
    prepared = sim.prepare(lib, "Alpha", "Beta", "2026-11-01", 5)
    for series in sim.run(lib, prepared, runs=300, seed=3):
        wins = [sum(1 for m in series if m[0]), sum(1 for m in series if not m[0])]
        assert max(wins) == 3 and min(wins) < 3 and 3 <= len(series) <= 5
        for a_won, (la, lb), (ka, kb), _ in series:
            assert sum(v["k"] for v in la.values()) == ka
            assert sum(v["d"] for v in lb.values()) == ka      # A's kills are B's deaths
            assert sum(v["k"] for v in lb.values()) == kb


def test_history_is_point_in_time():
    lib = library()
    assert all(m.date < "2026-10-10" for m in lib.maps_before("alpha", "2026-10-10"))
    assert sim.prepare(lib, "Alpha", "Beta", "2026-10-02", 3) is None   # one series before


def test_a_window_that_was_not_played_is_void():
    lib = library()
    prepared = sim.prepare(lib, "Alpha", "Beta", "2026-11-01", 3)
    series = next(sim.run(lib, prepared, runs=1, seed=1))
    player = prepared[0].roster_a[0][0]
    assert sim.window_total(series[:2], 0, player, "k", 3) is None
    assert sim.window_total(series, 0, player, "k", 1) == series[0][1][0][player]["k"]


def test_the_summary_adds_up():
    lib = library()
    got = sim.summarise(lib, sim.prepare(lib, "Alpha", "Beta", "2026-11-01", 3), runs=500)
    assert sum(got["series"]["scores"].values()) == pytest.approx(1)
    assert sum(got["series"]["maps_played"].values()) == pytest.approx(1)
    assert got["maps"][0]["played"] == 1 and got["maps"][2]["played"] < 1
    assert sum(p["mvp"] for p in got["players"]) == pytest.approx(1)
    k1 = got["players"][0]["stats"]["k"]["1"]
    assert sum(k1["hist"]["counts"]) == 500
    over, push, under = sim.p_over(k1["hist"], 2.5)
    assert over + push + under == pytest.approx(1) and push == 0


def test_a_stand_in_takes_the_line_of_the_player_he_replaced():
    lib = library()
    roster = [("alpha0", "Alpha0"), ("alpha1", "Alpha1"), ("alpha2", "Alpha2"),
              ("alpha3", "Alpha3"), ("newguy", "NewGuy")]
    prepared = sim.prepare(lib, "Alpha", "Beta", "2026-11-01", 3, roster_a=roster)
    series = next(sim.run(lib, prepared, runs=1, seed=2))
    assert "newguy" in series[0][1][0] and "alpha4" not in series[0][1][0]


def test_best_of_is_read_from_the_record():
    assert sim.best_of_for({"best_of": 5}) == 5
    assert sim.best_of_for({"series_format": "Bo3"}) == 3
    assert sim.best_of_for({"score": "3-1"}) == 5
    assert sim.best_of_for({"score": "1-0"}) == 1

"""The NFL simulator, generalised from a model written for one fixture.

The prototype baked the teams, the quarterback who might be pulled and the sign
of the spread into the code, so the tests that matter most here are the ones
that would catch a sign flipping when the model meets its second game.

The simulator is also the first thing in this project that produces per-
simulation outcomes, which is what lets the builder price two legs on one
quarterback's attempts from the games where both got fed rather than from an
average. That alignment -- index i is the same simulated game for every player
-- is the property everything downstream rests on, so it is tested directly.
"""
import json
import math
import random
import statistics
from pathlib import Path

import pytest

from propedge import nfl
from propedge.nfl import GameSetup, TeamSetup

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "nfl"


@pytest.fixture(scope="module")
def game():
    return GameSetup.from_json(json.loads(
        (FIXTURES / "game_phi_at_chi.json").read_text()))


@pytest.fixture(scope="module")
def simulated(game):
    return nfl.simulate(game, runs=3000, seed=11)


def small_game(**kw):
    home = TeamSetup(code="H", quarterback="HQB", plays=60, pass_rate=0.5,
                     rushers={"HRB": 0.9, "other": 0.1},
                     targets={"HWR": 0.6, "HTE": 0.2, "other": 0.2})
    away = TeamSetup(code="A", quarterback="AQB", plays=60, pass_rate=0.5,
                     rushers={"ARB": 0.9, "other": 0.1},
                     targets={"AWR": 0.6, "other": 0.4})
    return GameSetup(home=home, away=away, **kw)


# ------------------------------------------------------------ stat names

@pytest.mark.parametrize("given,want", [
    ("Receiving Yards", nfl.REC_YARDS), ("receiving yards", nfl.REC_YARDS),
    ("Rec Yards", nfl.REC_YARDS), ("Receptions", nfl.RECEPTIONS),
    ("Recs", nfl.RECEPTIONS), ("Rushing Yards", nfl.RUSH_YARDS),
    ("Carries", nfl.RUSH_ATTEMPTS), ("Passing Yards", nfl.PASS_YARDS),
    ("Rushing + Receiving Yards", nfl.COMBINED_YARDS),
])
def test_a_boards_spelling_reaches_the_models_stat(given, want):
    assert nfl.stat_key(given) == want


def test_an_unknown_stat_is_passed_through_not_guessed():
    assert nfl.stat_key("Longest Reception") == "Longest Reception"


# --------------------------------------------------------------- the draws

def test_binomial_has_the_right_mean_and_bounds():
    rng = random.Random(4)
    draws = [nfl._binomial(rng, 20, 0.3) for _ in range(4000)]
    assert all(0 <= d <= 20 for d in draws)
    assert statistics.mean(draws) == pytest.approx(6.0, abs=0.25)


def test_binomial_edges():
    rng = random.Random(4)
    assert nfl._binomial(rng, 0, 0.5) == 0
    assert nfl._binomial(rng, 10, 0) == 0
    assert nfl._binomial(rng, 10, 1) == 10


def test_the_normal_branch_agrees_with_the_exact_one():
    """n*p above 30 switches to a normal approximation; it must not shift."""
    rng = random.Random(5)
    draws = [nfl._binomial(rng, 200, 0.5) for _ in range(3000)]
    assert statistics.mean(draws) == pytest.approx(100, abs=1.5)
    assert 0 <= min(draws) and max(draws) <= 200


def test_poisson_has_the_right_mean():
    rng = random.Random(6)
    draws = [nfl._poisson(rng, 4.3) for _ in range(4000)]
    assert statistics.mean(draws) == pytest.approx(4.3, abs=0.2)
    assert min(draws) >= 0


def test_a_dirichlet_draw_is_a_simplex():
    rng = random.Random(7)
    for _ in range(50):
        shares = nfl._dirichlet(rng, [3, 2, 1])
        assert sum(shares) == pytest.approx(1.0)
        assert all(s >= 0 for s in shares)


def test_the_multinomial_hands_out_exactly_the_pot():
    """The point of a shared pot: one quarterback's attempts are a fixed number,
    so drawing each receiver's count independently would not add up."""
    rng = random.Random(8)
    for _ in range(200):
        counts = nfl._multinomial(rng, 30, [0.5, 0.3, 0.2])
        assert sum(counts) == 30
        assert all(c >= 0 for c in counts)


def test_the_multinomial_respects_the_shares():
    rng = random.Random(9)
    totals = [0, 0, 0]
    for _ in range(500):
        for i, c in enumerate(nfl._multinomial(rng, 30, [0.6, 0.3, 0.1])):
            totals[i] += c
    shares = [t / sum(totals) for t in totals]
    assert shares[0] == pytest.approx(0.6, abs=0.03)
    assert shares[2] == pytest.approx(0.1, abs=0.03)


def test_carry_yards_have_a_fat_right_tail():
    """A rushing distribution without breakaway runs prices the over wrong."""
    rng = random.Random(10)
    draws = [nfl._carry_yards(rng, 1, 4.3) for _ in range(6000)]
    assert statistics.mean(draws) == pytest.approx(4.3, abs=0.5)
    assert statistics.median(draws) < statistics.mean(draws)   # right-skewed
    assert max(draws) > 30


def test_reception_yards_cannot_go_backwards():
    rng = random.Random(11)
    draws = [nfl._reception_yards(rng, 1, 12.0) for _ in range(3000)]
    assert min(draws) > 0
    assert statistics.mean(draws) == pytest.approx(12.0, rel=0.15)


# ------------------------------------------------------------- simulation

def test_the_same_seed_gives_the_same_game():
    one = nfl.simulate(small_game(), runs=200, seed=3)
    two = nfl.simulate(small_game(), runs=200, seed=3)
    assert one["HWR"][nfl.REC_YARDS] == two["HWR"][nfl.REC_YARDS]


def test_a_different_seed_gives_a_different_game():
    one = nfl.simulate(small_game(), runs=200, seed=3)
    two = nfl.simulate(small_game(), runs=200, seed=4)
    assert one["HWR"][nfl.REC_YARDS] != two["HWR"][nfl.REC_YARDS]


def test_every_player_has_one_value_per_run(simulated):
    lengths = {len(values) for stats in simulated.values()
               for values in stats.values()}
    assert lengths == {3000}, "index i must be the same simulated game for everyone"


def test_receptions_never_exceed_targets(simulated):
    for player, stats in simulated.items():
        if nfl.TARGETS not in stats:
            continue
        for targets, caught in zip(stats[nfl.TARGETS], stats[nfl.RECEPTIONS]):
            assert caught <= targets


def test_combined_yards_are_the_sum_of_their_parts(simulated):
    stats = simulated["Saquon Barkley"]
    for i in range(0, 3000, 250):
        assert stats[nfl.COMBINED_YARDS][i] == pytest.approx(
            stats[nfl.RUSH_YARDS][i] + stats[nfl.REC_YARDS][i])


def test_the_spread_sign_is_the_home_teams():
    """The prototype carried one team's spread under that team's name, which is
    exactly what flips when the model meets its second fixture."""
    home_favoured = nfl.simulate(small_game(home_spread=-10), runs=600, seed=5)
    assert statistics.mean(home_favoured["_margin"]["home_margin"]) < -5


def test_trailing_teams_throw_more():
    """Game script: the losing side passes, and that is where a garbage-time
    receiving line comes from."""
    losing = nfl.simulate(small_game(home_spread=14, margin_sd=1), runs=800, seed=6)
    winning = nfl.simulate(small_game(home_spread=-14, margin_sd=1), runs=800, seed=6)
    assert (statistics.mean(losing["HQB"][nfl.PASS_ATTEMPTS])
            > statistics.mean(winning["HQB"][nfl.PASS_ATTEMPTS]))


def test_a_winning_quarterback_kneels():
    """Kneels are rush attempts at about a yard lost, which is why a winning
    quarterback's rushing line is not his running."""
    kneeling = nfl.simulate(small_game(home_spread=-21, margin_sd=1,
                                       kneel_probability=1.0), runs=600, seed=7)
    never = nfl.simulate(small_game(home_spread=-21, margin_sd=1,
                                    kneel_probability=0.0), runs=600, seed=7)
    assert (statistics.mean(kneeling["HQB"][nfl.RUSH_ATTEMPTS])
            > statistics.mean(never["HQB"][nfl.RUSH_ATTEMPTS]))
    assert (statistics.mean(kneeling["HQB"][nfl.RUSH_YARDS])
            < statistics.mean(never["HQB"][nfl.RUSH_YARDS]))


def test_pulling_the_quarterback_cuts_his_workload():
    home = TeamSetup(code="H", quarterback="HQB", targets={"HWR": 1.0},
                     qb_pull_probability=1.0)
    away = TeamSetup(code="A", quarterback="AQB", targets={"AWR": 1.0})
    pulled = nfl.simulate(GameSetup(home=home, away=away), runs=500, seed=8)
    steady = nfl.simulate(small_game(), runs=500, seed=8)
    assert (statistics.mean(pulled["HQB"][nfl.PASS_ATTEMPTS])
            < statistics.mean(steady["HQB"][nfl.PASS_ATTEMPTS]))


def test_an_early_exit_takes_a_players_share_away():
    with_exit = nfl.simulate(small_game(exit_probability={"HWR": 1.0}),
                             runs=600, seed=9)
    without = nfl.simulate(small_game(), runs=600, seed=9)
    assert (statistics.mean(with_exit["HWR"][nfl.TARGETS])
            < statistics.mean(without["HWR"][nfl.TARGETS]))


def test_the_other_bucket_never_becomes_a_player(simulated):
    assert nfl.OTHER not in simulated


def test_plays_stay_inside_a_football_game():
    out = nfl.simulate(small_game(home_spread=0, margin_sd=40), runs=400, seed=12)
    total = [a + b for a, b in zip(out["HQB"][nfl.PASS_ATTEMPTS],
                                   out["HQB"][nfl.RUSH_ATTEMPTS])]
    assert max(total) < 90


# ---------------------------------------------------------- probabilities

def test_probability_over_splits_a_push():
    assert nfl.probability_over([1, 2, 3, 4], 2) == pytest.approx(0.625)
    assert nfl.probability_over([1, 2, 3, 4], 2.5) == pytest.approx(0.5)
    assert nfl.probability_over([], 2) is None


# ----------------------------------------------------------- calibration

def test_calibration_moves_the_model_toward_the_book(game):
    market = json.loads((FIXTURES / "market_phi_at_chi.json").read_text())
    tuned, history = nfl.calibrate(game, market, rounds=4, runs=1500, seed=3)
    assert history[-1]["mean_error"] < history[0]["mean_error"] * 0.7
    assert tuned.calibrated and not game.calibrated      # the original is untouched


def test_calibration_does_not_mutate_the_setup_it_was_given(game):
    before = json.dumps(game.as_json(), sort_keys=True)
    nfl.calibrate(game, [["DeVonta Smith", "Rec Yards", 72.5]], rounds=2, runs=400)
    assert json.dumps(game.as_json(), sort_keys=True) == before


def test_a_market_that_names_nobody_is_loud():
    with pytest.raises(ValueError, match="no market line matched"):
        nfl.calibrate(small_game(), [["Nobody At All", "Rec Yards", 50.5]],
                      rounds=1, runs=200)


# ------------------------------------------------- board -> projections

def board_rows():
    return json.loads((FIXTURES / "board_phi_at_chi.json").read_text())


def test_projections_carry_both_models_as_scenarios(game, simulated):
    market = nfl.simulate(game, runs=3000, seed=99)
    made, sims = nfl.projections_from(board_rows(), simulated, market)
    assert made
    for projection in made:
        assert set(projection.scenarios) == {"usage", "market"}
        assert projection.confidence != "low"
        assert sims.covers("usage", [projection])
        assert sims.covers("market", [projection])


def test_a_prop_with_no_market_line_says_so_and_drops_to_low(simulated):
    made, _ = nfl.projections_from(board_rows(), simulated, None)
    assert made and all(p.confidence == "low" for p in made)
    assert all(any("NO MARKET LINE" in r for r in p.reasons) for p in made)


def test_opposite_sided_models_are_called_a_coin_flip(game):
    """A pick whose two models disagree about the SIDE is not a 55% pick."""
    usage = {"P": {nfl.REC_YARDS: [10.0] * 100 + [90.0] * 100}}      # 50/50 at 50
    market = {"P": {nfl.REC_YARDS: [10.0] * 190 + [90.0] * 10}}      # strongly under
    board = [{"player": "P", "stat": "Receiving Yards", "line": 49.5}]
    made, _ = nfl.projections_from(board, {"P": {nfl.REC_YARDS:
                                                 [90.0] * 160 + [10.0] * 40}}, market)
    assert made[0].reasons
    assert any("OPPOSITE sides" in r for r in made[0].reasons)


def test_the_simulations_handed_over_are_the_stat_not_a_win_flag(simulated, game):
    """The builder grades a simulation by comparing it with the line itself."""
    made, sims = nfl.projections_from(board_rows(), simulated, None)
    values = sims.by_scenario["usage"][made[0].prop_id]
    assert max(values) > 1.0, "these look like win flags, not simulated stats"


def test_a_whole_number_line_carries_push_mass(simulated):
    board = [{"player": "Kalif Raymond", "stat": "Receptions", "line": 4}]
    made, _ = nfl.projections_from(board, simulated, None)
    assert made[0].p_push > 0
    assert made[0].p_win + made[0].p_push <= 1 + 1e-9


def test_a_board_row_the_model_does_not_produce_is_skipped(simulated):
    board = [{"player": "Nobody", "stat": "Receptions", "line": 2.5},
             {"player": "Kalif Raymond", "stat": "Longest Reception", "line": 20.5}]
    made, _ = nfl.projections_from(board, simulated, None)
    assert made == []


# --------------------------------------------------------- round-tripping

def test_a_game_setup_round_trips(game):
    assert GameSetup.from_json(game.as_json()).as_json() == game.as_json()


def test_the_fixture_lands_near_the_books_lines(simulated):
    """A faithful port should already be in the right postcode before any
    calibration: these are the lines reference/calibrate.py was aimed at."""
    checks = [("Jalen Hurts", nfl.PASS_YARDS, 216.5, 60),
              ("Saquon Barkley", nfl.RUSH_YARDS, 71.5, 35),
              ("DeVonta Smith", nfl.REC_YARDS, 72.5, 35)]
    for player, stat, line, tolerance in checks:
        median = statistics.median(simulated[player][stat])
        assert abs(median - line) < tolerance, (
            f"{player} {stat}: model median {median:.1f} against a book line of "
            f"{line} — that is a porting error, not a disagreement")

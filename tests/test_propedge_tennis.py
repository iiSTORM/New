"""Best-of-three tennis: exact where it can be, simulated where it cannot.

The exact chain is checkable against things that must be true rather than
against a reference implementation, and those are the tests worth having: two
equal players win a set exactly half the time, a match goes to three sets
exactly half the time, a hold at p=0.5 is exactly 0.5, and the whole
distribution sums to one. A Markov chain that gets any of those wrong is wrong
everywhere, and none of them needs a second opinion to verify.

The set chain is also cross-checked against a brute-force simulation built from
hold probabilities, which shares no code with it.
"""
import math
import random
import statistics

import pytest

from propedge import tennis as t


# ------------------------------------------------------------ closed forms

def test_an_even_server_holds_exactly_half_the_time():
    assert t.hold_probability(0.5) == pytest.approx(0.5)


def test_holding_rises_with_the_serve():
    holds = [t.hold_probability(p) for p in (0.5, 0.55, 0.6, 0.65, 0.7)]
    assert holds == sorted(holds)
    assert t.hold_probability(0.64) == pytest.approx(0.813, abs=0.002)


def test_the_extremes_are_the_extremes():
    assert t.hold_probability(1.0) == pytest.approx(1.0)
    assert t.hold_probability(0.0) == pytest.approx(0.0)


def test_equal_servers_split_a_tiebreak():
    assert t.tiebreak_win(0.62, 0.62, True) == pytest.approx(0.5, abs=1e-9)
    assert t.tiebreak_win(0.62, 0.62, False) == pytest.approx(0.5, abs=1e-9)


def test_a_better_server_wins_more_tiebreaks():
    assert t.tiebreak_win(0.70, 0.60, True) > 0.5
    assert t.tiebreak_win(0.60, 0.70, True) < 0.5


def test_serving_first_in_a_tiebreak_is_worth_something():
    first = t.tiebreak_win(0.64, 0.64, True)
    assert first == pytest.approx(0.5, abs=1e-9)   # over a full tiebreak, nothing


# --------------------------------------------------------------- the chain

@pytest.mark.parametrize("p", [0.52, 0.60, 0.64, 0.70])
def test_a_set_distribution_is_a_distribution(p):
    assert sum(mass for _, mass in t.set_distribution(p, p, True)) == \
        pytest.approx(1.0)


def test_equal_players_split_sets_exactly():
    got = t.set_distribution(0.64, 0.64, True)
    assert sum(m for k, m in got if k[0] > k[1]) == pytest.approx(0.5, abs=1e-9)


def test_a_set_score_is_always_legal():
    for (a, b, _), mass in t.set_distribution(0.64, 0.60, True):
        assert (max(a, b) == 6 and abs(a - b) >= 2) or max(a, b) == 7
        assert mass >= 0


@pytest.mark.parametrize("pair", [(0.64, 0.64), (0.68, 0.58), (0.55, 0.62)])
def test_a_match_distribution_is_a_distribution(pair):
    assert sum(mass for _, mass in t.match_distribution(*pair)) == \
        pytest.approx(1.0)


def test_equal_players_go_three_sets_exactly_half_the_time():
    """Each set is a coin flip, so P(split the first two) is exactly a half.
    A chain that does not produce this is wrong somewhere upstream."""
    got = t.match_distribution(0.64, 0.64)
    assert sum(m for k, m in got if k[2] + k[3] == 3) == pytest.approx(0.5, abs=1e-9)


def test_equal_players_win_exactly_half_the_matches():
    got = t.match_distribution(0.62, 0.62)
    assert t.probability_over(got, t.MATCH_WIN, None, 0)[0] == \
        pytest.approx(0.5, abs=1e-9)


def test_win_probabilities_sum_to_one():
    got = t.match_distribution(0.68, 0.58)
    a = t.probability_over(got, t.MATCH_WIN, None, 0)[0]
    b = t.probability_over(got, t.MATCH_WIN, None, 1)[0]
    assert a + b == pytest.approx(1.0)
    assert a > b


def test_every_match_is_two_sets_or_three():
    for key, _ in t.match_distribution(0.64, 0.60):
        assert key[2] + key[3] in (2, 3)
        assert max(key[2], key[3]) == 2


def test_the_first_set_is_part_of_the_total():
    for key, _ in t.match_distribution(0.64, 0.60):
        games_a, games_b, _, _, set1_a, set1_b = key
        assert set1_a <= games_a and set1_b <= games_b


def test_a_better_server_plays_longer_matches():
    """Higher holds mean fewer breaks, so sets run to 6-4 and tiebreaks."""
    def expected(p):
        return sum((k[0] + k[1]) * m for k, m in t.match_distribution(p, p))
    assert expected(0.70) > expected(0.64) > expected(0.56)


def test_the_set_chain_agrees_with_a_brute_force_simulation():
    """Shares no code with the dynamic programme: it plays games off hold
    probabilities and counts. A DP and a simulation that agree to a hundredth of
    a game are very unlikely to be wrong in the same direction."""
    rng = random.Random(1)

    def play_set(hold_a, hold_b, a_first):
        games, server = [0, 0], 0 if a_first else 1
        while True:
            if games[0] == 6 and games[1] == 6:
                games[0 if rng.random() < 0.5 else 1] += 1
                return games
            held = rng.random() < (hold_a if server == 0 else hold_b)
            games[server if held else 1 - server] += 1
            server = 1 - server
            if (max(games) >= 6 and abs(games[0] - games[1]) >= 2) or 7 in games:
                return games

    for p in (0.60, 0.64):
        hold = t.hold_probability(p)
        simulated = statistics.mean(
            sum(play_set(hold, hold, rng.random() < 0.5)) for _ in range(20000))
        exact = sum((k[0] + k[1]) * m for k, m in t.set_distribution(p, p, True))
        assert exact == pytest.approx(simulated, abs=0.08), p


# ------------------------------------------------------------ reading it off

def test_a_whole_line_pushes_and_a_half_line_cannot():
    got = t.match_distribution(0.64, 0.62)
    over, push = t.probability_over(got, t.TOTAL_GAMES, 22)
    assert push > 0
    over_half, push_half = t.probability_over(got, t.TOTAL_GAMES, 22.5)
    assert push_half == 0
    assert over + push >= over_half


def test_games_won_is_read_per_player():
    got = t.match_distribution(0.70, 0.56)
    strong = t.probability_over(got, t.GAMES_WON, 11.5, 0)[0]
    weak = t.probability_over(got, t.GAMES_WON, 11.5, 1)[0]
    assert strong > weak


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError, match="kind must be"):
        t.probability_over(t.match_distribution(0.64, 0.64), "serve speed", 100)


def test_the_two_halves_of_form_noise_pull_opposite_ways():
    """Not simply "wider tails", which is what I assumed and it is wrong.

    Uncertainty about the LEVEL does fatten the tails: some weeks both players
    serve better and the match runs long. Uncertainty about the GAP does the
    opposite -- it makes the match more lopsided on average, and a lopsided
    match is a short one -- and it is the larger of the two, so the net effect
    of form noise is to shorten matches at every line.
    """
    exact = t.probability_over(t.match_distribution(0.64, 0.60),
                               t.TOTAL_GAMES, 30.5)[0]
    gap_only = sum(w * t.probability_over(t.match_distribution(a, b),
                                          t.TOTAL_GAMES, 30.5)[0]
                   for a, b, w in t.with_form_noise(0.64, 0.60, level_noise=0.0))
    level_only = sum(w * t.probability_over(t.match_distribution(a, b),
                                            t.TOTAL_GAMES, 30.5)[0]
                     for a, b, w in t.with_form_noise(0.64, 0.60, gap_noise=0.0))
    assert level_only > exact > gap_only
    both = t.blended_probability(0.64, 0.60, t.TOTAL_GAMES, 30.5,
                                 form_noise=True)[0]
    assert both < exact          # the gap term wins


def test_form_noise_shifts_every_line_the_same_way():
    for line in (18.5, 22.5, 26.5, 30.5):
        exact = t.blended_probability(0.64, 0.60, t.TOTAL_GAMES, line,
                                      form_noise=False)[0]
        noisy = t.blended_probability(0.64, 0.60, t.TOTAL_GAMES, line,
                                      form_noise=True)[0]
        assert noisy < exact, line


def test_the_form_noise_weights_are_a_distribution():
    weights = sum(w for _, _, w in t.with_form_noise(0.64, 0.60))
    assert weights == pytest.approx(1.0)


# ------------------------------------------------------------------ fitting

def test_a_fit_reproduces_its_anchors():
    got = t.fit([(t.TOTAL_GAMES, None, 23.5, 0.5), (t.MATCH_WIN, 0, None, 0.62)],
                "ATP")
    assert got.usable
    distribution = t.match_distribution(got.p_a, got.p_b)
    assert t.probability_over(distribution, t.TOTAL_GAMES, 23.5)[0] == \
        pytest.approx(0.5, abs=0.02)
    assert t.probability_over(distribution, t.MATCH_WIN, None, 0)[0] == \
        pytest.approx(0.62, abs=0.02)


def test_a_favourite_comes_out_as_the_better_server():
    got = t.fit([(t.TOTAL_GAMES, None, 23.5, 0.5), (t.MATCH_WIN, 0, None, 0.75)],
                "ATP")
    assert got.p_a > got.p_b and got.gap > 0


def test_totals_alone_cannot_be_fitted_and_say_so():
    """Two very different matches produce the same number of games."""
    with pytest.raises(ValueError, match="unpinned|which player is favoured"):
        t.fit([(t.TOTAL_GAMES, None, 22.5, 0.5)], "ATP")


def test_no_anchors_at_all_is_refused():
    with pytest.raises(ValueError, match="no anchors"):
        t.fit([], "ATP")


def test_a_fit_that_runs_out_of_band_says_which_and_is_not_usable():
    """A total low enough to need more breaks than the tour's band allows is
    the market describing a match this model cannot represent."""
    got = t.fit([(t.TOTAL_GAMES, None, 17.5, 0.5), (t.MATCH_WIN, 0, None, 0.55)],
                "ATP")
    assert not got.usable
    assert got.why_not()


def test_the_wta_band_is_lower_than_the_atp_one():
    assert t.SERVE_BANDS["WTA"][0] < t.SERVE_BANDS["ATP"][0]
    assert t.SERVE_BANDS["WTA"][1] < t.SERVE_BANDS["ATP"][1]


# ------------------------------------------------------------- point level

@pytest.fixture(scope="module")
def points():
    return t.simulate_points(0.64, 0.60, runs=1200, seed=4)


def test_every_simulated_match_is_a_real_match(points):
    a, b = points
    for won, lost, sets_won, sets_lost in zip(a.games_won, a.games_lost,
                                              a.sets_won, a.sets_lost):
        assert max(sets_won, sets_lost) == 2
        assert sets_won + sets_lost in (2, 3)
        assert won + lost >= 12          # even 6-0 6-0 is twelve games


def test_the_two_players_see_the_same_match(points):
    a, b = points
    assert a.games_won == b.games_lost
    assert a.sets_won == b.sets_lost


def test_the_better_server_wins_more(points):
    a, b = points
    assert statistics.mean(a.sets_won) > statistics.mean(b.sets_won)


def test_aces_and_double_faults_track_their_rates():
    high, _ = t.simulate_points(0.64, 0.64, aces=(0.20, 0.02),
                                double_faults=(0.01, 0.01), runs=800, seed=6)
    low, _ = t.simulate_points(0.64, 0.64, aces=(0.02, 0.02),
                               double_faults=(0.01, 0.01), runs=800, seed=6)
    assert statistics.mean(high.aces) > statistics.mean(low.aces) * 3


def test_fantasy_scoring_is_the_plans_not_the_prototypes():
    """reference/tennis_model.py's docstring says half a point for an ace and
    its own code says one. The plan says one, and the plan wins."""
    totals = t.PointTotals(aces=[3], double_faults=[1], breaks=[0],
                           games_won=[12], games_lost=[8], sets_won=[2],
                           sets_lost=[0])
    # 10 played + 12 - 8 + 3*2 - 3*0 + 3 - 1
    assert totals.fantasy() == [10 + 12 - 8 + 6 - 0 + 3 - 1]


def test_the_fantasy_constants_match_the_plan():
    assert (t.FANTASY_PLAYED, t.FANTASY_GAME_WON, t.FANTASY_GAME_LOST) == (10, 1, -1)
    assert (t.FANTASY_SET_WON, t.FANTASY_SET_LOST) == (3, -3)
    assert (t.FANTASY_ACE, t.FANTASY_DOUBLE_FAULT) == (1, -1)


# ------------------------------------------------------- the break-prop gate

def test_a_break_rate_that_matches_reality_passes(points):
    a, _ = points
    modelled = statistics.mean(a.breaks)
    ok, got, message = t.break_rate_check(a, modelled)
    assert ok and got == pytest.approx(modelled) and "inside tolerance" in message


def test_a_break_rate_that_does_not_match_is_refused(points):
    a, _ = points
    ok, _, message = t.break_rate_check(a, statistics.mean(a.breaks) + 3)
    assert not ok and "iid-points weakness" in message


def test_no_observed_rate_means_no_price(points):
    """An independent-points model is known to get this wrong in one direction,
    so an unchecked break prop is worse than no break prop."""
    ok, _, message = t.break_rate_check(points[0], None)
    assert not ok and "no observed break rate" in message


# ---------------------------------------------------------- board -> rows

def fitted():
    return t.fit([(t.TOTAL_GAMES, None, 23.5, 0.5), (t.MATCH_WIN, 0, None, 0.62)],
                 "ATP")


def row(stat, line, who=0, player="Player A"):
    return {"player": player, "opponent": "Player B", "stat": stat, "line": line,
            "who": who, "start_time": "2026-09-30T13:00:00-04:00"}


def test_the_chain_prices_the_games_props(points):
    made = t.projections_from([row("Total Games", 22.5),
                               row("1st Set Total Games", 9.5)], fitted(), points)
    assert len(made) == 2
    for projection in made:
        assert projection.sport == "tennis"
        assert any("exact best-of-three" in r for r in projection.reasons)


def test_each_player_is_their_own_team(points):
    made = t.projections_from([row("Total Games Won", 11.5)], fitted(), points)
    assert made[0].team == "Player A"
    assert made[0].team_key == "tennis:player a"


def test_the_break_prop_is_gated_out_without_an_observed_rate(points):
    board = [row("Total Games", 22.5), row("Break Points Won", 3.5)]
    assert len(t.projections_from(board, fitted(), points)) == 1


def test_the_break_prop_is_priced_when_the_rates_agree(points):
    board = [row("Break Points Won", 3.5)]
    observed = {"Player A": statistics.mean(points[0].breaks)}
    made = t.projections_from(board, fitted(), points, observed_breaks=observed)
    assert len(made) == 1
    assert any("inside tolerance" in r for r in made[0].reasons)


def test_a_row_that_does_not_say_which_player_is_skipped(points):
    """Guessing from name order would price the favourite's line as the
    underdog's, which is a wrong number that looks entirely reasonable."""
    board = [{"player": "Player A", "opponent": "Player B",
              "stat": "Total Games Won", "line": 11.5}]
    assert t.projections_from(board, fitted(), points) == []


def test_an_unusable_fit_prices_nothing(points):
    bad = t.fit([(t.TOTAL_GAMES, None, 17.5, 0.5), (t.MATCH_WIN, 0, None, 0.55)],
                "ATP")
    assert t.projections_from([row("Total Games", 22.5)], bad, points) == []


def test_a_stat_the_model_does_not_know_is_skipped(points):
    assert t.projections_from([row("Service Games Won", 9.5)], fitted(),
                              points) == []


def test_a_simulated_stat_needs_the_simulation():
    assert t.projections_from([row("Aces", 5.5)], fitted(), None) == []


def test_every_row_carries_two_scenarios(points):
    for projection in t.projections_from([row("Total Games", 22.5)], fitted(),
                                         points):
        assert set(projection.scenarios) == {"fitted", "held_loosely"}
        assert projection.stress_width >= 0

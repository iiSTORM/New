"""The row every model emits, and the two ways a set of them gets priced.

The cross-check that matters is at the bottom: priced with the correlation set
to zero, the joint path has to agree exactly with the independent enumeration
from phase 1. Two implementations of the same arithmetic that disagree at rho=0
disagree everywhere, and the difference would be invisible -- both produce
plausible probabilities.
"""
import pytest

from propedge.model import (BLEND, Projection, Simulations, price,
                            table_from_correlation, table_from_simulations)
from propedge.sizing import outcome_table


def proj(prop_id="a", player="P", team="Alpha", opponent="Beta", side="over",
         line=20.5, **kw):
    kw.setdefault("p_win", 0.6)
    return Projection(prop_id=prop_id, sport="cs2", player=player, team=team,
                      opponent=opponent, stat="kills", line=line, side=side,
                      maps=2, **kw)


def total(table):
    return sum(o.probability for o in table)


# ------------------------------------------------------------- the row itself

def test_a_single_probability_becomes_one_scenario():
    assert proj().scenarios == {BLEND: 0.6}


def test_scenarios_without_a_blend_average_to_one():
    made = proj(p_win=None, scenarios={"usage": 0.55, "market": 0.65})
    assert made.p_win == pytest.approx(0.60)
    assert (made.stress_low, made.stress_high) == (0.55, 0.65)
    assert made.stress_width == pytest.approx(0.10)


def test_an_explicit_blend_is_not_overwritten_by_the_average():
    """60/40 market weighting is not the mean, and the model gets to say so."""
    made = proj(p_win=0.62, scenarios={"usage": 0.55, "market": 0.65})
    assert made.p_win == 0.62


def test_the_conditional_convention_converts():
    """reference/evaluate_v2.py reports P(win | no push); an outcome table needs
    the unconditional number or the mass does not add up."""
    made = Projection.from_conditional(0.60, p_push=0.10, prop_id="a", sport="cs2",
                                      player="P", team="A", opponent="B",
                                      stat="kills", line=20, side="over")
    assert made.p_win == pytest.approx(0.54)
    assert made.p_win + made.p_push + 0.36 == pytest.approx(1.0)


@pytest.mark.parametrize("kw,match", [
    (dict(side="sideways"), "side must be"),
    (dict(confidence="vibes"), "confidence must be"),
    (dict(prop_id="  "), "needs a prop_id"),
    (dict(p_win=1.4), "not a probability"),
    (dict(p_win=-0.1), "not a probability"),
    (dict(p_win=0.8, p_push=0.3, line=20), "exceeds 1"),
    (dict(p_push=0.05), "cannot push"),          # 20.5 is a half point
])
def test_a_malformed_row_is_refused(kw, match):
    with pytest.raises(ValueError, match=match):
        proj(**kw)


def test_a_row_with_no_probability_at_all_is_refused():
    with pytest.raises(ValueError, match="no p_win and no scenarios"):
        proj(p_win=None)


def test_a_fixture_is_the_same_named_either_way_round():
    assert proj(team="Alpha", opponent="Beta").match_key == \
        proj(team="Beta", opponent="Alpha").match_key


def test_in_tennis_the_player_is_the_team():
    singles = Projection(prop_id="t", sport="tennis", player="Alcaraz", stat="games",
                         line=20.5, side="over", p_win=0.6)
    assert singles.team_key == "tennis:alcaraz"


@pytest.mark.parametrize("side,line,value,state", [
    ("over", 20.5, 21, "won"), ("over", 20.5, 20, "lost"),
    ("under", 20.5, 20, "won"), ("under", 20.5, 21, "lost"),
    ("over", 20, 20, "push"), ("under", 20, 20, "push"),
])
def test_a_simulated_value_grades_the_same_way_a_real_one_does(side, line, value, state):
    assert proj(side=side, line=line).hit(value) == state


def test_the_leg_it_becomes_carries_the_probability_and_the_lock():
    made = proj(p_win=0.61, start_time="2026-09-29T21:00:00-04:00").to_leg()
    assert made.model_prob == 0.61
    assert made.start_time == "2026-09-29T21:00:00-04:00"
    assert made.line_at_placement == 20.5


def test_json_round_trips():
    made = proj(scenarios={"usage": 0.55, "market": 0.65}, p_win=0.62,
                reasons=("form", "matchup"))
    assert Projection.from_json(made.as_json()).as_json() == made.as_json()


# -------------------------------------------------------------- simulations

def test_simulations_of_different_lengths_are_refused():
    """If two props have different run counts, index i is not the same game."""
    sims = Simulations().add("usage", "a", [1, 2, 3])
    with pytest.raises(ValueError, match="the same run"):
        sims.add("usage", "b", [1, 2])


def test_coverage_is_all_or_nothing():
    sims = Simulations().add("usage", "a", [1, 2])
    assert not sims.covers("usage", [proj("a"), proj("b", player="Q")])
    sims.add("usage", "b", [1, 2])
    assert sims.covers("usage", [proj("a"), proj("b", player="Q")])
    assert not sims.covers("market", [proj("a")])


def test_a_table_from_simulations_counts_what_happened():
    """Two legs that always hit together: the joint is the marginal, not its
    square. This is why simulations beat a correlation constant."""
    sims = Simulations()
    sims.add("usage", "a", [30, 30, 30, 10, 10])      # over 20.5: 3 of 5
    sims.add("usage", "b", [30, 30, 30, 10, 10])      # identical, so perfectly tied
    table = {o.label: o for o in table_from_simulations(
        [proj("a"), proj("b", player="Q", team="Beta")], sims, "usage",
        printed_multiplier="3")}
    assert table["2/2 at 3x"].probability == pytest.approx(0.6)
    assert total(table.values()) == pytest.approx(1.0)


def test_simulations_carry_pushes_through():
    sims = Simulations()
    sims.add("usage", "a", [20, 30, 10, 20])          # line 20: pushes twice
    sims.add("usage", "b", [30, 30, 30, 30])
    table = {o.label: o for o in table_from_simulations(
        [proj("a", line=20), proj("b", player="Q", team="Beta")], sims, "usage",
        printed_multiplier="3")}
    # two pushes leave one leg: refunded
    assert table["refund"].probability == pytest.approx(0.5)
    assert total(table.values()) == pytest.approx(1.0)


def test_a_missing_prop_in_a_scenario_is_loud():
    sims = Simulations().add("usage", "a", [1, 2])
    with pytest.raises(KeyError, match="no simulations"):
        table_from_simulations([proj("a"), proj("b", player="Q")], sims, "usage")


def test_price_prefers_simulations_and_says_which():
    sims = Simulations().add("usage", "a", [30] * 10).add("usage", "b", [30] * 10)
    legs = [proj("a"), proj("b", player="Q", team="Beta")]
    _, how = price(legs, "usage", sims, printed_multiplier="3")
    assert "simulations" in how
    _, how = price(legs, "market", sims, printed_multiplier="3")
    assert "correlation" in how


# -------------------------------------------------------------- correlation

def test_the_correlated_table_is_a_distribution():
    for legs in ([proj("a"), proj("b", player="Q", team="Beta")],
                 [proj("a"), proj("b", player="Q", team="Beta"),
                  proj("c", player="R", team="Gamma", opponent="Delta")]):
        assert total(table_from_correlation(legs, printed_multiplier="3")) \
            == pytest.approx(1.0)


def test_same_match_legs_beat_the_product():
    """The whole point of the correlation: two legs on one match land together
    more often than independence says, so the parlay is worth more."""
    same = table_from_correlation(
        [proj("a", team="Alpha", opponent="Beta"),
         proj("b", player="Q", team="Alpha", opponent="Beta")],
        printed_multiplier="3")
    joint = next(o.probability for o in same if o.label == "2/2 at 3x")
    assert joint > 0.6 * 0.6


def test_legs_on_different_matches_multiply():
    apart = table_from_correlation(
        [proj("a", team="Alpha", opponent="Beta"),
         proj("b", player="Q", team="Gamma", opponent="Delta")],
        printed_multiplier="3")
    joint = next(o.probability for o in apart if o.label == "2/2 at 3x")
    assert joint == pytest.approx(0.36, abs=1e-9)


def test_teammates_are_tied_tighter_than_opponents():
    def joint(team_b, opponent_b):
        table = table_from_correlation(
            [proj("a", team="Alpha", opponent="Beta"),
             proj("b", player="Q", team=team_b, opponent=opponent_b)],
            printed_multiplier="3")
        return next(o.probability for o in table if o.label == "2/2 at 3x")
    assert joint("Alpha", "Beta") > joint("Beta", "Alpha") > 0.36


def test_a_scenario_picks_that_scenarios_probability():
    legs = [proj("a", p_win=None, scenarios={"low": 0.5, "high": 0.7}),
            proj("b", player="Q", team="Gamma", opponent="Delta", p_win=None,
                 scenarios={"low": 0.5, "high": 0.7})]
    low = next(o.probability for o in
               table_from_correlation(legs, "low", printed_multiplier="3")
               if o.label == "2/2 at 3x")
    high = next(o.probability for o in
                table_from_correlation(legs, "high", printed_multiplier="3")
                if o.label == "2/2 at 3x")
    assert low == pytest.approx(0.25) and high == pytest.approx(0.49)


def test_a_flex_slip_is_refused_rather_than_guessed():
    """One joint probability answers "did everything land", which is the whole
    question for a power slip and only part of it for flex."""
    with pytest.raises(ValueError, match="price a flex slip from simulations"):
        table_from_correlation([proj("a"), proj("b", player="Q")], mode="flex")


def test_pushes_shrink_a_correlated_slip_too():
    table = {o.label: o for o in table_from_correlation(
        [proj("a", line=20, p_push=0.1),
         proj("b", player="Q", team="Beta"),
         proj("c", player="R", team="Gamma", opponent="Delta")],
        printed_multiplier="6")}
    assert "2/2 at 3x" in table          # the shrunk slip, at the 2-pick price
    assert total(table.values()) == pytest.approx(1.0)


def test_at_zero_correlation_both_pricing_paths_agree_exactly():
    """The cross-check. Phase 1 enumerates win/push/loss assuming independence;
    this path integrates a copula. At rho=0 the copula IS independence, so any
    disagreement is a bug in one of them -- and both would look plausible."""
    legs = [proj("a", p_win=0.58, line=20, p_push=0.04),
            proj("b", player="Q", team="Beta", p_win=0.61),
            proj("c", player="R", team="Gamma", opponent="Delta", p_win=0.55,
                 line=18, p_push=0.03)]
    correlated = {o.label: o.probability for o in table_from_correlation(
        legs, printed_multiplier="6", rho_team=0, rho_fixture=0)}
    independent = {o.label: o.probability for o in outcome_table(
        [(p.p_win, p.p_push) for p in legs], "power", "6")}
    assert set(correlated) == set(independent)
    for label, probability in independent.items():
        assert correlated[label] == pytest.approx(probability, abs=1e-9), label

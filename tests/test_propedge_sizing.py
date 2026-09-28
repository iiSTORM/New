"""Stakes: the outcome table, Kelly on it, and the caps that bind it.

The point of enumerating the table rather than using "probability of winning"
is the push. A push shrinks the slip and pays at a different multiplier, and a
shrink to one leg returns the stake -- a refund cuts the downside without
touching the upside, so it makes a slip WORTH MORE, and a two-outcome
approximation cannot see that. The test for it is direct: the same legs with a
push probability get a larger Kelly fraction than without.
"""
import pytest

from propedge.payouts import PayoutTable
from propedge.sizing import (MIN_STAKE_CENTS, Outcome, kelly_fraction,
                             outcome_table, recommend)


def probability_total(table):
    return sum(o.probability for o in table)


# ------------------------------------------------------------ outcome tables

def test_the_table_is_a_probability_distribution():
    for legs in ([(0.6, 0.0), (0.6, 0.0)],
                 [(0.55, 0.05), (0.6, 0.02), (0.5, 0.1)],
                 [(0.5, 0.0)] * 6):
        assert probability_total(outcome_table(legs, "power", "3")) == pytest.approx(1)


def test_a_two_leg_power_slip_is_win_or_lose():
    table = {o.label: o for o in outcome_table([(0.6, 0.0), (0.5, 0.0)], "power", "3")}
    assert set(table) == {"2/2 at 3x", "loss"}
    assert table["2/2 at 3x"].probability == pytest.approx(0.30)
    assert table["2/2 at 3x"].net_return == pytest.approx(2.0)
    assert table["loss"].net_return == pytest.approx(-1.0)


def test_a_push_on_a_two_leg_slip_becomes_a_refund():
    table = {o.label: o for o in outcome_table([(0.6, 0.1), (0.5, 0.0)], "power", "3")}
    assert "refund" in table
    # either leg pushing leaves one leg, which is refunded: 0.1 of the time
    assert table["refund"].probability == pytest.approx(0.1)
    assert table["refund"].net_return == 0.0


def test_a_push_on_a_three_leg_slip_reprices_at_the_two_leg_multiplier():
    table = {o.label: o for o in
             outcome_table([(0.6, 0.1), (0.6, 0.0), (0.6, 0.0)], "power", "6")}
    assert "3/3 at 6x" in table
    assert "2/2 at 3x" in table          # the shrunk slip, at the table's 2-pick
    assert table["2/2 at 3x"].probability == pytest.approx(0.1 * 0.6 * 0.6)


def test_a_flex_table_prices_every_tier():
    labels = {o.label for o in outcome_table([(0.6, 0.0)] * 3, "flex")}
    assert labels == {"3/3 at 2.25x", "2/3 at 1.25x", "loss"}


def test_an_impossible_leg_is_refused_not_normalised():
    with pytest.raises(ValueError, match="not a probability"):
        outcome_table([(0.7, 0.4), (0.5, 0.0)], "power", "3")


def test_a_custom_table_flows_into_the_pricing():
    five = PayoutTable(power={3: "5"})
    labels = {o.label for o in outcome_table([(0.6, 0.0)] * 3, "power", table=five)}
    assert "3/3 at 5x" in labels


# ------------------------------------------------------------------- Kelly

def test_kelly_matches_the_closed_form_on_an_even_money_bet():
    """f* = 2p - 1 when the bet pays +1 / -1."""
    table = [Outcome(0.6, 1.0, "win"), Outcome(0.4, -1.0, "loss")]
    assert kelly_fraction(table) == pytest.approx(0.2, abs=1e-6)


def test_kelly_matches_the_closed_form_on_a_3x_payout():
    """f* = (p(b+1) - 1) / b with b = 2: p = 0.4 -> 0.1."""
    table = [Outcome(0.4, 2.0, "win"), Outcome(0.6, -1.0, "loss")]
    assert kelly_fraction(table) == pytest.approx(0.1, abs=1e-6)


def test_no_edge_means_no_stake():
    assert kelly_fraction([Outcome(1 / 3, 2.0, "win"),
                           Outcome(2 / 3, -1.0, "loss")]) == 0.0
    assert kelly_fraction([Outcome(0.3, 2.0, "win"),
                           Outcome(0.7, -1.0, "loss")]) == 0.0


def test_an_empty_table_is_no_stake():
    assert kelly_fraction([]) == 0.0


def test_a_chance_of_a_refund_makes_the_same_slip_worth_more():
    """The whole reason the outcome table is enumerated."""
    without = kelly_fraction(outcome_table([(0.58, 0.0), (0.58, 0.0)], "power", "3"))
    with_push = kelly_fraction(outcome_table([(0.58, 0.05), (0.58, 0.0)], "power", "3"))
    assert with_push > without > 0


def test_kelly_never_stakes_the_whole_bankroll():
    certain = outcome_table([(0.99, 0.0), (0.99, 0.0)], "power", "3")
    assert 0 < kelly_fraction(certain) < 1


# ------------------------------------------------------------------- caps

def good_slip():
    return outcome_table([(0.75, 0.0), (0.75, 0.0)], "power", "3")


def test_half_kelly_by_default_and_quarter_when_the_stress_range_is_wide():
    narrow = recommend(good_slip(), 100_000, stress_width=0.02)
    wide = recommend(good_slip(), 100_000, stress_width=0.25)
    assert float(narrow.applied_fraction) == 0.5
    assert float(wide.applied_fraction) == 0.25
    assert wide.stake_cents < narrow.stake_cents
    assert any("quarter Kelly" in r for r in wide.reasons)


def test_the_per_slip_cap_binds():
    got = recommend(good_slip(), 100_000)
    assert got.stake_cents == 10_000          # 10% of bankroll
    assert got.binding == "per-slip cap"


def test_the_nightly_cap_binds_after_enough_slips():
    got = recommend(good_slip(), 100_000, staked_tonight_cents=24_000)
    assert got.stake_cents == 1_000           # 25% of 100k, less 24k already out
    assert got.binding == "per-night cap"


def test_nothing_is_left_of_the_nightly_budget():
    got = recommend(good_slip(), 100_000, staked_tonight_cents=25_000)
    assert got.bet is False
    assert any("minimum entry is more than the caps allow" in r for r in got.reasons)


def test_no_edge_means_no_bet():
    flat = outcome_table([(0.5, 0.0), (0.5, 0.0)], "power", "3")
    got = recommend(flat, 100_000)
    assert (got.bet, got.stake_cents, got.binding) == (False, 0, "no edge")


def test_a_thin_edge_on_a_small_bankroll_takes_the_minimum_and_says_so():
    """The $1 floor is PrizePicks', not Kelly's, so taking it is overbetting."""
    thin = outcome_table([(0.58, 0.02), (0.57, 0.0)], "power", "3")
    got = recommend(thin, 1319)
    assert (got.stake_cents, got.bet, got.binding) == (100, True, "minimum entry")
    assert any("slightly more than Kelly asks" in r for r in got.reasons)


def test_the_minimum_is_refused_when_it_breaks_the_per_slip_cap():
    """On a $5 bankroll, $1 is 20% of it — over the 10% cap, so no bet."""
    got = recommend(good_slip(), 500)
    assert (got.bet, got.stake_cents) == (False, 0)
    assert got.binding == "below minimum"


def test_the_stake_shrinks_with_the_bankroll():
    """Recomputed from the current balance, so a loss lowers the next stake."""
    before = recommend(good_slip(), 100_000).stake_cents
    after = recommend(good_slip(), 50_000).stake_cents
    assert after == before // 2


def test_the_independence_caveat_is_carried_on_the_result():
    assert recommend(good_slip(), 100_000).independent is True


def test_the_minimum_is_the_prizepicks_minimum():
    assert MIN_STAKE_CENTS == 100

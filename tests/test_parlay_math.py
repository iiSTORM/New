"""The parlay combining rule's own correctness, separate from parity.

tests/parlay_parity.test.mjs checks that the JS and this agree. It cannot
check that either is RIGHT, because both use the same rational approximations
on purpose -- if the Python used math.erf, every comparison would measure the
approximation's error rather than the two ports agreeing.

So the approximations are checked against an exact normal here, and so are the
properties the combining rule has to have: it reduces to a product when legs
are independent, it makes correlated legs more likely to land together than
independence does, and it is bounded.
"""
import math
import statistics
from statistics import NormalDist

import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "dev"))

import parlay_math as pm

EXACT = NormalDist()


class TestTheApproximationsAreGoodEnough:
    """Both are used because JS has no erf. Their error has to be far below
    anything a probability displays."""

    def test_the_cdf_is_within_a_ten_millionth_everywhere_that_matters(self):
        worst = max(abs(pm.standard_normal_cdf(z / 100) - EXACT.cdf(z / 100))
                    for z in range(-500, 501))
        assert worst < 1e-7, f"worst cdf error {worst:.2e}"

    def test_the_quantile_is_within_a_hundred_millionth(self):
        worst = max(abs(pm.standard_normal_quantile(p / 1000) - EXACT.inv_cdf(p / 1000))
                    for p in range(1, 1000))
        assert worst < 1e-8, f"worst quantile error {worst:.2e}"

    def test_the_two_are_inverses_of_each_other(self):
        for p in (0.01, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99):
            assert abs(pm.standard_normal_cdf(pm.standard_normal_quantile(p)) - p) < 1e-7

    @pytest.mark.parametrize("p", [0.0, 1.0, -0.5, 2.0])
    def test_the_quantile_refuses_what_is_not_a_probability(self, p):
        assert math.isinf(pm.standard_normal_quantile(p))


class TestIndependentLegsMultiply:
    def test_zero_correlation_is_exactly_the_product(self):
        got = pm.group_hit_probability([0.6, 0.5, 0.4], 0)
        assert abs(got - 0.6 * 0.5 * 0.4) < 1e-15

    def test_legs_in_different_matches_multiply_whatever_the_correlation(self):
        legs = [{"p": 0.6, "matchKey": "a"}, {"p": 0.5, "matchKey": "b"}]
        assert abs(pm.joint_hit_probability(legs, 0.5) - 0.30) < 1e-12

    def test_a_single_leg_is_its_own_probability(self):
        for rho in (0, 0.104, 0.9):
            assert abs(pm.group_hit_probability([0.63], rho) - 0.63) < 1e-9


class TestCorrelatedLegsAreNotIndependent:
    def test_same_match_legs_are_likelier_to_all_land(self):
        """The whole reason this is not a product. A map that runs long carries
        every over on it."""
        same = pm.group_hit_probability([0.6] * 3, pm.SAME_MATCH_CORRELATION)
        independent = 0.6 ** 3
        assert same > independent, f"{same} is not above {independent}"

    def test_the_gap_widens_with_the_number_of_legs(self):
        gaps = []
        for n in (2, 3, 4, 5, 6):
            same = pm.group_hit_probability([0.6] * n, pm.SAME_MATCH_CORRELATION)
            gaps.append(same / 0.6 ** n)
        assert gaps == sorted(gaps), f"relative gap does not grow: {gaps}"
        assert gaps[-1] > 1.3, f"six same-match legs differ by only {gaps[-1]:.2f}x"

    def test_the_gap_widens_with_the_correlation(self):
        at = [pm.group_hit_probability([0.6] * 4, rho) for rho in (0, 0.1, 0.3, 0.6)]
        assert at == sorted(at), f"not monotone in rho: {at}"

    def test_perfect_correlation_would_be_the_smallest_leg(self):
        """The limit the model has to respect: if every leg turns on one coin,
        they all land exactly when the least likely one does."""
        got = pm.group_hit_probability([0.6, 0.7, 0.8], 0.999)
        assert abs(got - 0.6) < 0.02, got

    def test_the_shipped_correlation_is_what_was_measured(self):
        """55.2% same-match agreement against 50.0% across matches, at a base
        rate near even, is rho = 1 - (1 - 0.552) / (2 * 0.25)."""
        implied = 1 - (1 - 0.552) / (2 * 0.25 * 1.0)
        assert abs(pm.SAME_MATCH_CORRELATION - implied) < 0.01, (
            f"ships {pm.SAME_MATCH_CORRELATION}, the measurement implies {implied:.3f}")


class TestBounds:
    @pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 6])
    def test_a_joint_probability_is_a_probability(self, n):
        for rho in (0, 0.104, 0.5):
            got = pm.group_hit_probability([0.55] * n, rho)
            assert 0 <= got <= 1

    def test_it_never_exceeds_its_smallest_leg(self):
        for rho in (0, 0.104, 0.5, 0.9):
            got = pm.group_hit_probability([0.4, 0.8, 0.9], rho)
            assert got <= 0.4 + 1e-9, f"rho={rho} gives {got}, above the smallest leg"

    def test_it_is_never_below_the_independent_product(self):
        """Positive correlation only ever helps a parlay of same-direction legs."""
        for n in (2, 3, 4, 5):
            product = 0.55 ** n
            assert pm.group_hit_probability([0.55] * n, pm.SAME_MATCH_CORRELATION) >= product - 1e-12


class TestBreakEven:
    @pytest.mark.parametrize("multiplier,legs,want", [
        (3, 2, 0.5773502691896257),
        (5, 3, 0.5848035476425733),
        (10, 4, 0.5623413251903491),
    ])
    def test_it_is_the_nth_root_of_one_over_the_payout(self, multiplier, legs, want):
        assert abs(pm.break_even_per_leg(multiplier, legs) - want) < 1e-12

    def test_a_payout_that_returns_the_stake_cannot_be_broken_even(self):
        assert pm.break_even_per_leg(1, 2) is None

    def test_break_even_makes_expected_value_exactly_zero(self):
        for multiplier, legs in ((3, 2), (5, 3), (20, 5)):
            per_leg = pm.break_even_per_leg(multiplier, legs)
            joint = per_leg ** legs
            assert abs(pm.parlay_expected_value(joint, multiplier)) < 1e-12

    def test_the_measured_hit_rate_makes_every_size_negative(self):
        """The number that matters commercially right now. At the 50.2% per-leg
        accuracy measured over 1105 graded CS2 props, no entry size on the
        published table is worth making -- and the machinery should say so
        rather than needing a person to work it out."""
        measured = 0.502
        for legs, multiplier in ((2, 3), (3, 5), (4, 10), (5, 20), (6, 37.5)):
            joint = measured ** legs
            ev = pm.parlay_expected_value(joint, multiplier)
            assert ev < 0, f"{legs}-leg at {multiplier}x would be +EV at {measured:.1%} a leg"

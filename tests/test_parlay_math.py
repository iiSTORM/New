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


class TestTheTwoLevels:
    """One correlation was wrong by more than a factor of three.

    Legs on one roster agree 57.1% of the time (7,436 pairs) and legs on
    opposing sides of the same fixture 52.2% (5,910), so a fixture produces two
    quite different shapes and the concentrated one is where all of the
    concentration value sits. The stat does not matter: same stat 55.1%,
    different stat 54.7%.
    """

    def test_the_team_level_is_above_the_fixture_level(self):
        """Required for the middle loading, sqrt(rt - rf), to be real at all."""
        assert pm.SAME_TEAM_CORRELATION > pm.OPPOSING_TEAMS_CORRELATION

    def test_both_come_from_the_agreement_rates_measured(self):
        for agreement, shipped in ((0.571, pm.SAME_TEAM_CORRELATION),
                                   (0.522, pm.OPPOSING_TEAMS_CORRELATION)):
            implied = 1 - (1 - agreement) / (2 * 0.25)
            assert abs(shipped - implied) < 0.005, (
                f"ships {shipped}, {agreement:.1%} agreement implies {implied:.3f}")

    def test_the_interval_brackets_the_point_estimate(self):
        assert (pm.SAME_MATCH_CORRELATION_LOW < pm.SAME_MATCH_CORRELATION
                < pm.SAME_MATCH_CORRELATION_HIGH)

    def test_one_roster_beats_one_fixture_beats_separate_fixtures(self):
        """The ordering the whole concentrated-rung idea turns on."""
        p = 0.497
        roster = pm.joint_hit_probability([{"p": p, "matchKey": "m", "teamKey": "A"}] * 5)
        fixture = pm.joint_hit_probability(
            [{"p": p, "matchKey": "m", "teamKey": "A" if i < 3 else "B"} for i in range(5)])
        spread = pm.joint_hit_probability(
            [{"p": p, "matchKey": f"m{i}", "teamKey": f"t{i}"} for i in range(5)])
        assert roster > fixture > spread, (roster, fixture, spread)
        assert abs(spread - p ** 5) < 1e-9, "separate fixtures are not the plain product"

    @pytest.mark.parametrize("rho", [0.043, 0.101, 0.143, 0.3, 0.6])
    def test_a_two_leg_group_reproduces_exactly_the_correlation_asked_for(self, rho):
        """The construction itself, against the closed form.

        For two standard normals with correlation rho, both above their medians,
        P(both) = 1/4 + arcsin(rho) / 2pi exactly -- so the correlation the
        integral actually produces can be read back out and compared to the one
        requested. (A linear 4*P - 1 was tried first and failed at rho 0.3 by
        0.11, which is the approximation being wrong rather than the code: at
        rho 0.3 the exact P is 0.29849 and 4 * 0.29849 - 1 = 0.194.)
        """
        joint = pm.fixture_hit_probability([[0.5, 0.5]], rho, rho * 0.3)
        implied = math.sin(2 * math.pi * (joint - 0.25))
        assert abs(implied - rho) < 1e-4, (
            f"asked for {rho}, the integral produces {implied:.6f}")

    def test_two_legs_on_opposing_sides_get_the_fixture_level(self):
        """The other half of the same check: across the fixture it must be the
        LOWER correlation, not the team one."""
        joint = pm.fixture_hit_probability([[0.5], [0.5]], pm.SAME_TEAM_CORRELATION,
                                           pm.OPPOSING_TEAMS_CORRELATION)
        implied = math.sin(2 * math.pi * (joint - 0.25))
        assert abs(implied - pm.OPPOSING_TEAMS_CORRELATION) < 1e-4, (
            f"across the fixture the integral produces {implied:.6f}, not "
            f"{pm.OPPOSING_TEAMS_CORRELATION}")

    def test_a_side_with_no_key_is_its_own_side(self):
        """Conservative: it correlates at the fixture level only, never at the
        higher team level, so nothing is assumed into existence."""
        unkeyed = pm.joint_hit_probability([{"p": 0.6, "matchKey": "m"},
                                           {"p": 0.6, "matchKey": "m"}])
        keyed = pm.joint_hit_probability([{"p": 0.6, "matchKey": "m", "teamKey": "A"},
                                          {"p": 0.6, "matchKey": "m", "teamKey": "A"}])
        assert unkeyed < keyed

    @pytest.mark.parametrize("rho_team,rho_fixture", [(0.05, 0.05), (0.05, 0.2), (0.0, 0.3)])
    def test_an_imaginary_team_loading_falls_back_rather_than_going_nan(
            self, rho_team, rho_fixture):
        got = pm.fixture_hit_probability([[0.6], [0.6]], rho_team, rho_fixture)
        assert got is not None and 0 < got < 1 and not math.isnan(got)

    def test_both_levels_off_is_the_product(self):
        assert abs(pm.fixture_hit_probability([[0.6], [0.5]], 0, 0) - 0.30) < 1e-15


class TestTheConcentratedRungsSignIsNotDetermined:
    """The reason the view shows an interval and not a number.

    At the 49.7% per leg measured over 75 matches, a same-roster 5- or 6-leg is
    positive at the correlation point estimate and negative at the low end of
    its interval. That is not a recommendation either way, and the app says so.
    """

    MEASURED = 0.497
    MULTIPLIERS = {2: 3, 3: 5, 4: 10, 5: 20, 6: 37.5}

    def ev(self, legs, rho_team, rho_fixture, multiplier):
        joint = pm.joint_hit_probability(legs, rho_team, rho_fixture)
        return pm.parlay_expected_value(joint, multiplier)

    def roster(self, n):
        return [{"p": self.MEASURED, "matchKey": "m", "teamKey": "A"}] * n

    def scaled(self, target):
        factor = target / pm.SAME_MATCH_CORRELATION
        return (min(0.95, pm.SAME_TEAM_CORRELATION * factor),
                min(0.94, pm.OPPOSING_TEAMS_CORRELATION * factor))

    def test_spread_rungs_are_negative_at_every_size(self):
        for n, multiplier in self.MULTIPLIERS.items():
            legs = [{"p": self.MEASURED, "matchKey": f"m{i}", "teamKey": f"t{i}"}
                    for i in range(n)]
            ev = self.ev(legs, pm.SAME_TEAM_CORRELATION, pm.OPPOSING_TEAMS_CORRELATION,
                         multiplier)
            assert ev < 0, f"{n}-leg spread is {ev:+.0%} at {self.MEASURED:.1%} a leg"

    def test_small_concentrated_rungs_are_negative_across_the_whole_interval(self):
        """Two and three legs need correlation far above anything measured."""
        for n in (2, 3):
            for target in (pm.SAME_MATCH_CORRELATION_LOW, pm.SAME_MATCH_CORRELATION,
                           pm.SAME_MATCH_CORRELATION_HIGH):
                rt, rf = self.scaled(target)
                ev = self.ev(self.roster(n), rt, rf, self.MULTIPLIERS[n])
                assert ev < 0, f"{n}-leg roster is {ev:+.0%} at rho {target}"

    def test_the_big_concentrated_rungs_change_sign_inside_the_interval(self):
        """Which is the finding, and the reason nothing here is presented as a
        recommendation. If this test starts failing because the interval has
        narrowed, the view's caveat needs revisiting rather than the test."""
        flipped = []
        for n in (5, 6):
            lo_rt, lo_rf = self.scaled(pm.SAME_MATCH_CORRELATION_LOW)
            hi_rt, hi_rf = self.scaled(pm.SAME_MATCH_CORRELATION_HIGH)
            low = self.ev(self.roster(n), lo_rt, lo_rf, self.MULTIPLIERS[n])
            high = self.ev(self.roster(n), hi_rt, hi_rf, self.MULTIPLIERS[n])
            if low < 0 < high:
                flipped.append(n)
        assert flipped, ("no concentrated rung changes sign across the correlation "
                         "interval any more — the view still says one does")

    def test_at_the_point_estimate_the_six_leg_roster_is_positive(self):
        """Stated plainly rather than left implied: this is what was flagged,
        and it is why the shape exists in the ladder at all."""
        ev = self.ev(self.roster(6), pm.SAME_TEAM_CORRELATION,
                     pm.OPPOSING_TEAMS_CORRELATION, 37.5)
        assert ev > 0, f"the six-leg roster measures {ev:+.0%} at the point estimate"


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

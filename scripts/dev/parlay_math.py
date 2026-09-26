#!/usr/bin/env python3
"""Python half of the parlay combining rule. See tests/parlay_parity.test.mjs.

The rule itself, and why it is not a product of probabilities, is documented
where it runs -- src/app.jsx, under "Combining legs into a parlay". The short
version: the graded record says two legs in one match land the same way 55.2%
of the time against 50.0% for legs in different matches, so within a match
they share a factor and multiplying is wrong.

This exists so the JS can be checked rather than trusted. Nothing imports it
at runtime; a parlay is computed in the browser, and this is the second
implementation that says the browser's answer is right.
"""
import math

SAME_MATCH_CORRELATION = 0.101
# Two levels, measured separately: 57.1% agreement between legs on one roster
# (7,436 pairs) and 52.2% between legs on opposing sides of the same fixture
# (5,910 pairs). See the long note in src/app.jsx.
SAME_TEAM_CORRELATION = 0.143
OPPOSING_TEAMS_CORRELATION = 0.043
# Bootstrapped over the 79 matches the pairs come from, not over the pairs.
SAME_MATCH_CORRELATION_LOW = 0.047
SAME_MATCH_CORRELATION_HIGH = 0.160
FACTOR_INTEGRATION_LIMIT = 8
FACTOR_INTEGRATION_STEPS = 400
NESTED_INTEGRATION_STEPS = 200


def standard_normal_pdf(z):
    return math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)


def standard_normal_cdf(z):
    """Deliberately the SAME rational approximation as the JS, not erf.

    math.erf is exact to machine precision and the JS has no erf at all, so
    using it here would make every comparison a measurement of the
    approximation's error instead of of the two ports agreeing. The
    approximation's own accuracy is checked separately against erf.
    """
    t = 1 / (1 + 0.2316419 * abs(z))
    poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937
           + t * (-1.821255978 + t * 1.330274429))))
    upper = standard_normal_pdf(z) * poly
    return 1 - upper if z >= 0 else upper


ACKLAM_A = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
            1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
ACKLAM_B = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
            6.680131188771972e+01, -1.328068155288572e+01]
ACKLAM_C = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
            -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
ACKLAM_D = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
            3.754408661907416e+00]


def standard_normal_quantile(p):
    if not 0 < p < 1:
        return float("-inf") if p <= 0 else float("inf")
    low = 0.02425
    if p < low:
        q = math.sqrt(-2 * math.log(p))
        return ((((((ACKLAM_C[0] * q + ACKLAM_C[1]) * q + ACKLAM_C[2]) * q + ACKLAM_C[3]) * q
                 + ACKLAM_C[4]) * q + ACKLAM_C[5])
                / ((((ACKLAM_D[0] * q + ACKLAM_D[1]) * q + ACKLAM_D[2]) * q + ACKLAM_D[3]) * q + 1))
    if p > 1 - low:
        return -standard_normal_quantile(1 - p)
    q, r = p - 0.5, (p - 0.5) ** 2
    return (((((ACKLAM_A[0] * r + ACKLAM_A[1]) * r + ACKLAM_A[2]) * r + ACKLAM_A[3]) * r
             + ACKLAM_A[4]) * r + ACKLAM_A[5]) * q \
        / (((((ACKLAM_B[0] * r + ACKLAM_B[1]) * r + ACKLAM_B[2]) * r + ACKLAM_B[3]) * r
            + ACKLAM_B[4]) * r + 1)


def group_hit_probability(probabilities, rho):
    ps = [p for p in probabilities if isinstance(p, (int, float)) and 0 < p < 1]
    if len(ps) != len(probabilities):
        return None
    if not ps:
        return 1.0
    if not rho:
        result = 1.0
        for p in ps:
            result *= p
        return result
    if len(ps) == 1:
        return ps[0]

    thresholds = [standard_normal_quantile(1 - p) for p in ps]
    root, rest = math.sqrt(rho), math.sqrt(1 - rho)
    lo, hi = -FACTOR_INTEGRATION_LIMIT, FACTOR_INTEGRATION_LIMIT
    h = (hi - lo) / FACTOR_INTEGRATION_STEPS

    def at(z):
        product = 1.0
        for t in thresholds:
            product *= 1 - standard_normal_cdf((t - root * z) / rest)
        return standard_normal_pdf(z) * product

    total = at(lo) + at(hi)
    for i in range(1, FACTOR_INTEGRATION_STEPS):
        total += at(lo + i * h) * (4 if i % 2 else 2)
    return min(1.0, max(0.0, (h / 3) * total))


def integrate_over_factor(f, steps):
    lo, hi = -FACTOR_INTEGRATION_LIMIT, FACTOR_INTEGRATION_LIMIT
    h = (hi - lo) / steps
    total = f(lo) + f(hi)
    for i in range(1, steps):
        total += f(lo + i * h) * (4 if i % 2 else 2)
    return (h / 3) * total


def fixture_hit_probability(sides, rho_team, rho_fixture):
    """P(every leg on one fixture lands), legs split by side.

    sides: [[p, ...], [p, ...]] -- one list per team.
    """
    flat = [p for side in sides for p in side]
    if any(not (isinstance(p, (int, float)) and 0 < p < 1) for p in flat):
        return None
    if not flat:
        return 1.0
    if len(flat) == 1:
        return flat[0]
    if not rho_team and not rho_fixture:
        result = 1.0
        for p in flat:
            result *= p
        return result
    if not rho_team > rho_fixture:
        # An imaginary team loading. Fall back to the flat model at the larger.
        return group_hit_probability(flat, max(rho_team, rho_fixture))

    a_fixture = math.sqrt(rho_fixture)
    b_team = math.sqrt(rho_team - rho_fixture)
    rest = math.sqrt(1 - rho_team)
    thresholds_by_side = [[standard_normal_quantile(1 - p) for p in side] for side in sides]

    def outer(z_fixture):
        product = 1.0
        for thresholds in thresholds_by_side:
            def inner_f(z_team, thresholds=thresholds):
                inner = 1.0
                for t in thresholds:
                    inner *= 1 - standard_normal_cdf(
                        (t - a_fixture * z_fixture - b_team * z_team) / rest)
                return standard_normal_pdf(z_team) * inner
            product *= integrate_over_factor(inner_f, NESTED_INTEGRATION_STEPS)
        return standard_normal_pdf(z_fixture) * product

    return min(1.0, max(0.0, integrate_over_factor(outer, NESTED_INTEGRATION_STEPS)))


def joint_hit_probability(legs, rho_team=SAME_TEAM_CORRELATION,
                          rho_fixture=OPPOSING_TEAMS_CORRELATION):
    """legs: [{"p": float, "matchKey": str|None, "teamKey": str|None}]."""
    if not legs:
        return None
    by_fixture = {}
    order = []
    for leg in legs:
        key = leg.get("matchKey") or f"__fixture{len(by_fixture)}"
        if key not in by_fixture:
            by_fixture[key] = ({}, [])
            order.append(key)
        sides, side_order = by_fixture[key]
        side = leg.get("teamKey") or f"__side{len(sides)}"
        if side not in sides:
            sides[side] = []
            side_order.append(side)
        sides[side].append(leg.get("p"))
    joint = 1.0
    for key in order:
        sides, side_order = by_fixture[key]
        group = fixture_hit_probability([sides[s] for s in side_order],
                                        rho_team, rho_fixture)
        if group is None:
            return None
        joint *= group
    return joint


def break_even_per_leg(multiplier, legs):
    if not multiplier or multiplier <= 1 or not legs or legs < 1:
        return None
    return (1 / multiplier) ** (1 / legs)


def parlay_expected_value(joint, multiplier):
    if joint is None or not multiplier:
        return None
    return joint * multiplier - 1

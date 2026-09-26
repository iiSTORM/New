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

SAME_MATCH_CORRELATION = 0.104
FACTOR_INTEGRATION_LIMIT = 8
FACTOR_INTEGRATION_STEPS = 400


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


def joint_hit_probability(legs, rho=SAME_MATCH_CORRELATION):
    """legs: [{"p": float, "matchKey": str|None}]."""
    if not legs:
        return None
    by_match = {}
    order = []
    for leg in legs:
        key = leg.get("matchKey") or f"__{len(by_match)}"
        if key not in by_match:
            by_match[key] = []
            order.append(key)
        by_match[key].append(leg.get("p"))
    joint = 1.0
    for key in order:
        group = group_hit_probability(by_match[key], rho)
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

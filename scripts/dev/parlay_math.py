"""Where the parlay maths used to live. It is now propedge/joint.py.

It moved because the phase-3 slip builder needs the same arithmetic at runtime
and a second copy of a measured correlation is a second thing to get out of
step. This shim stays so tests/parlay_parity.test.mjs and
tests/test_parlay_math.py keep importing `parlay_math` and keep pinning
propedge/joint.py against src/app.jsx.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from propedge.joint import *            # noqa: F401,F403
from propedge.joint import (            # noqa: F401  explicit, for readers
    FACTOR_INTEGRATION_LIMIT, FACTOR_INTEGRATION_STEPS, NESTED_INTEGRATION_STEPS,
    OPPOSING_TEAMS_CORRELATION, SAME_MATCH_CORRELATION,
    SAME_MATCH_CORRELATION_HIGH, SAME_MATCH_CORRELATION_LOW,
    SAME_TEAM_CORRELATION, break_even_per_leg, fixture_hit_probability,
    group_hit_probability, integrate_over_factor, joint_hit_probability,
    parlay_expected_value, standard_normal_cdf, standard_normal_pdf,
    standard_normal_quantile)

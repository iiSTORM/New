"""The weekly check on CLASS_GAP_KILLS and EVENT_PACE."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "dev"))
sys.path.insert(0, str(ROOT / "scripts"))

import check_event_factors as cef  # noqa: E402
import optimize_weights as ow  # noqa: E402


def test_the_implied_factor_scales_the_shipped_one_by_what_was_left_over():
    got = {"pace": {"Worlds": (0.9, 40)}, "gap": (1.21, 1.0, 12)}
    (pace, gap) = cef.verdicts(got)
    assert pace[2] == ow.EVENT_PACE["Worlds"] * 0.9
    assert abs(gap[2] - ow.CLASS_GAP_KILLS * 1.1) < 1e-9


def test_drift_needs_both_a_real_move_and_enough_maps():
    moved = {"pace": {"Worlds": (0.8, 40)}, "gap": (2.0, 1.0, 12)}
    assert [v[4] for v in cef.verdicts(moved)] == [True, True]
    thin = {"pace": {"Worlds": (0.8, 5)}, "gap": (2.0, 1.0, 3)}
    assert [v[4] for v in cef.verdicts(thin)] == [False, False]
    held = {"pace": {"Worlds": (1.02, 40)}, "gap": (1.1, 1.0, 12)}
    assert [v[4] for v in cef.verdicts(held)] == [False, False]

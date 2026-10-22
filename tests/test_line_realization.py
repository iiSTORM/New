"""LINE_REALIZATION in the app is the measured one, and the measure is sound.

The app's factor is the share of a shown edge against the line that showed
up on the graded record. A number like that is only worth carrying while it
is the measured one, so the app is pinned to the file the measuring script
writes, and the script's own pieces are pinned to their definitions.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "dev"))
sys.path.insert(0, str(ROOT / "scripts"))

import line_realization as lr  # noqa: E402

APP = (ROOT / "src" / "app.jsx").read_text()


def app_line_realization():
    body = re.search(r"const LINE_REALIZATION = (\{.*?\n\});", APP, re.S).group(1)
    as_json = re.sub(r"(\w+):", r'"\1":', body.replace("\n", " "))
    as_json = re.sub(r",\s*\}", "}", as_json)
    return {g: {s: float(v) for s, v in stats.items()} for g, stats in json.loads(as_json).items()}


def test_the_app_carries_the_measured_factors():
    shipped = json.loads((ROOT / "scripts" / "dev" / "line_realization.json").read_text())["shipped"]
    assert app_line_realization() == {g: {s: float(v) for s, v in st.items()} for g, st in shipped.items()}


def test_the_script_uses_the_apps_evidence_discount():
    rows = re.findall(r"\{ upTo: (\w+), factor: ([\d.]+) \}",
                      re.search(r"const EDGE_REALIZATION = \[(.*?)\];", APP, re.S).group(1))
    app = [(float("inf") if u == "Infinity" else float(u), float(f)) for u, f in rows]
    assert app == [(float(u), f) for u, f in lr.EDGE_REALIZATION]


def test_slope_is_the_least_squares_weight():
    pts = [("m", 2.0, 1.0), ("m", -1.0, -0.5), ("n", 4.0, 2.0)]
    assert lr.slope(pts) == 0.5
    assert lr.slope([("m", 0.0, 3.0)]) is None


def test_only_a_factor_whose_interval_excludes_one_ships_and_it_never_inflates():
    cells = [{"game": "cs2", "stat": "kills", "slope": 0.351, "high": 0.56},
             {"game": "lol", "stat": "kills", "slope": 0.72, "high": 1.33},     # could be 1: left alone
             {"game": "valorant", "stat": "kills", "slope": -0.06, "high": 0.47},
             {"game": "cs2", "stat": "headshots", "n": 40}]                       # too few props
    assert lr.shipped_factors(cells) == {"cs2": {"kills": 0.35}, "valorant": {"kills": 0.0}}


def test_the_adjusted_projection_matches_the_apps_adjust_edge():
    # adjustEdge: (mean + multiplier * (projection - mean)) - line
    assert lr.adjusted(20.0, 10, 16.0) == 16.0 + 0.93 * 4.0
    assert lr.adjusted(20.0, 2, None) == 20.0


def test_the_slate_tool_carries_its_own_measured_factors():
    from propedge import esports
    shipped = json.loads((ROOT / "scripts" / "dev" / "line_realization_slate.json").read_text())["shipped"]
    assert esports.LINE_REALIZATION == shipped
    assert esports.line_realization("lol", "kills") == 1.0


def test_the_slate_tool_applies_it_to_the_shrunk_mean():
    from propedge import esports
    scales = {"cs2": {"kills": {"2": 6.0}}}
    on_ = esports.EsportsModel(scales=scales).probabilities("cs2", "kills", 2, 40.0, 30.5, 40)
    off_ = esports.EsportsModel(scales=scales, realize=False).probabilities("cs2", "kills", 2, 40.0, 30.5, 40)
    assert on_[0] < off_[0]                    # a smaller over edge is a less confident over
    assert on_[3].shrunk - 30.5 == pytest.approx((off_[3].shrunk - 30.5) * 0.46)

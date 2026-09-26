"""The decile test has to be reading the app's real gate.

decile_test.py deliberately keeps no copy of PARLAY_TIER_THRESHOLD, the row
floor or the residual scales -- it pulls them out of src/app.jsx, so the verdict
it reports on a schedule is the verdict the Parlays tab would reach. That only
holds while the extraction works, and the extraction is a regex over JavaScript
source: it fails silently if someone renames a constant or reformats a literal.
It already has, once. RESIDUAL_SCALE keys its windows by map count --

    kills: { 1: 4.66, 2: 7.70 }

-- and the first version of the key-quoting pass only handled identifier-like
keys, so `1:` reached json.loads unquoted and the whole script died. It died
loudly, which was luck; a rename of PARLAY_TIER_THRESHOLD would instead have
been a SystemExit and a reformat could have produced a *wrong* table. So the
extraction is tested: that it is loud when a name goes missing, that every
number in the literal survives the round trip, and that the gate rule itself
(separated iff the confident half's interval clears the other half's mean)
matches what parlayEvidence does in the app.
"""
import json
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("requests")  # the optimizer chain imports it

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "dev"))

import decile_test as dt

APP_SOURCE = (REPO_ROOT / "src" / "app.jsx").read_text(encoding="utf-8")


# ---------------------------------------------------------------- extraction

def test_missing_constant_is_loud_not_silent():
    """A rename must stop the script, not give it a default."""
    with pytest.raises(SystemExit):
        dt.js_number("PARLAY_TIER_THRESHOLD_RENAMED_BY_SOMEONE", APP_SOURCE)


def test_numeric_keys_survive():
    """The bug this file exists for: bare integer keys are legal JS, not JSON."""
    got = dt.js_object("SYNTHETIC", "const SYNTHETIC = {\n"
                       "  cs2: { kills: { 1: 4.66, 2: 7.70 } },  // a comment\n"
                       "};\n")
    assert got == {"cs2": {"kills": {"1": 4.66, "2": 7.7}}}


def test_trailing_commas_and_comments_survive():
    got = dt.js_object("SYNTHETIC",
                       "const SYNTHETIC = {\n"
                       "  a: { b: 1, },  // trailing comma, inline comment\n"
                       "  c: { d: 2 },\n"
                       "};\n")
    assert got == {"a": {"b": 1}, "c": {"d": 2}}


def test_nesting_stops_at_the_matching_brace():
    """The brace walk must not run on into whatever follows the literal."""
    got = dt.js_object("SYNTHETIC",
                       "const SYNTHETIC = { a: { b: 1 } };\n"
                       "const OTHER = { c: { d: 2 } };\n")
    assert got == {"a": {"b": 1}}


def test_every_number_in_the_literal_round_trips():
    """An independent scan of the raw RESIDUAL_SCALE block, compared with the
    parsed table. Independent on purpose: a second regex that only looks for
    `<key>: <number>` pairs cannot share a bug with the JSON rewrite."""
    start = APP_SOURCE.index("const RESIDUAL_SCALE = {")
    end = APP_SOURCE.index("\n};", start)
    block = APP_SOURCE[start:end]
    raw = sorted(float(v) for v in re.findall(r"\b\d+:\s*(\d+(?:\.\d+)?)", block))
    gate = dt.Gate()
    parsed = sorted(float(v) for stats in gate.scales.values()
                    for windows in stats.values() for v in windows.values())
    assert raw, "no `<window>: <number>` pairs found — has RESIDUAL_SCALE moved?"
    assert parsed == raw


def test_gate_numbers_are_plausible():
    gate = dt.Gate()
    assert 0 < gate.threshold < 5              # standardised edge, not a probability
    assert isinstance(gate.min_rows, int) and gate.min_rows >= 2
    assert 0 < gate.exponent < 1               # between independent maps and linear
    assert set(gate.scales) >= {"cs2", "lol", "valorant"}


def test_gate_matches_the_shipped_constants():
    """Read once here the crude way, to catch the extraction drifting from the
    file rather than pinning values that are allowed to be re-measured."""
    for name, attr, cast in (("PARLAY_TIER_THRESHOLD", "threshold", float),
                             ("PARLAY_MIN_ROWS_PER_BAND", "min_rows", int),
                             ("RESIDUAL_SCALE_EXPONENT", "exponent", float)):
        literal = re.search(rf"^const {name} = (-?[\d.]+);", APP_SOURCE, re.M)
        assert literal, f"{name} is no longer a plain `const NAME = number;`"
        assert getattr(dt.Gate(), attr) == cast(literal.group(1))


# ------------------------------------------------------------ residual scale

def test_measured_window_is_returned_verbatim():
    gate = dt.Gate()
    assert gate.residual_scale("cs2", "kills", 2) == gate.scales["cs2"]["kills"]["2"]
    assert gate.residual_scale("lol", "assists", 3) == gate.scales["lol"]["assists"]["3"]


def test_unmeasured_window_stretches_from_the_nearest():
    """Valorant is only ever measured at two maps; three has to come from it."""
    gate = dt.Gate()
    anchor = gate.scales["valorant"]["kills"]["2"]
    assert gate.residual_scale("valorant", "kills", 3) == pytest.approx(
        anchor * (3 / 2) ** gate.exponent)
    # and it must GROW with the window, never shrink
    assert gate.residual_scale("valorant", "kills", 3) > anchor


def test_nearest_window_not_the_first_one():
    gate = dt.Gate()
    lol = gate.scales["lol"]["kills"]
    assert gate.residual_scale("lol", "kills", 4) == pytest.approx(
        lol["3"] * (4 / 3) ** gate.exponent)


@pytest.mark.parametrize("game,stat,maps", [
    ("cs2", "first_bloods", 2),   # a stat with no measured scale
    ("dota", "kills", 2),         # a game we do not carry
    ("cs2", "kills", 0),          # a window that cannot exist
    ("cs2", "kills", -1),
    ("cs2", "kills", None),
    ("cs2", "kills", 2.5),        # not an integer window
])
def test_no_scale_rather_than_a_guess(game, stat, maps):
    assert dt.Gate().residual_scale(game, stat, maps) is None


# ------------------------------------------------------------ the gate rule

def rows(n, z, wins, cluster_size=1):
    """n synthetic rows at confidence z, `wins` of them won, spread over
    matches of cluster_size rows each."""
    out = []
    for i in range(n):
        out.append({"game": "cs2", "stat": "kills", "maps": 2, "z": z,
                    "won": i < wins, "cluster": ("cs2", "d", i // cluster_size)})
    return out


def test_clustering_counts_matches_not_props():
    """Ten props off one map are one observation, not ten."""
    assert dt.clustered(rows(10, 1.0, 10, cluster_size=10)) is None
    got = dt.clustered(rows(10, 1.0, 5, cluster_size=5))
    assert got["clusters"] == 2 and got["rows"] == 10


def test_row_floor_is_enforced_before_any_rate_is_quoted():
    gate = dt.Gate()
    thin = rows(gate.min_rows - 1, 1.0, gate.min_rows - 1) + rows(200, 0.1, 100)
    got = dt.verdict(thin, gate)
    assert got["separated"] is False
    assert "the floor" in got["why"]
    assert got["big"] is None and got["small"] is None


def test_separated_only_when_the_interval_clears():
    gate = dt.Gate()
    confident = rows(200, gate.threshold + 0.4, 180)     # 90%
    rest = rows(200, gate.threshold - 0.4, 100)          # 50%
    for r in rest:
        r["cluster"] = ("cs2", "e", r["cluster"][2])     # distinct matches
    got = dt.verdict(confident + rest, gate)
    assert got["separated"] is True
    assert got["big"]["lo"] > got["small"]["mean"]


def test_ordered_but_not_separated_says_so():
    gate = dt.Gate()
    confident = rows(60, gate.threshold + 0.4, 31)       # 51.7%
    rest = rows(60, gate.threshold - 0.4, 30)            # 50%
    for r in rest:
        r["cluster"] = ("cs2", "e", r["cluster"][2])
    got = dt.verdict(confident + rest, gate)
    assert got["separated"] is False
    assert "ordered, but not separated" in got["why"]


def test_below_is_reported_as_below():
    """The current real answer, and it must not read as merely unproven."""
    gate = dt.Gate()
    confident = rows(60, gate.threshold + 0.4, 24)       # 40%
    rest = rows(60, gate.threshold - 0.4, 30)            # 50%
    for r in rest:
        r["cluster"] = ("cs2", "e", r["cluster"][2])
    got = dt.verdict(confident + rest, gate)
    assert got["separated"] is False
    assert "BELOW" in got["why"]


# -------------------------------------------------------------- the reporting

def test_bands_partition_the_rows():
    every = rows(137, 1.0, 70)
    for i, r in enumerate(every):
        r["z"] = i / 137
    bands = dt.deciles(every, 10)
    assert sum(len(b) for _, b in bands) == len(every)
    assert len(bands) == 10
    # most confident first
    heads = [max(r["z"] for r in b) for _, b in bands]
    assert heads == sorted(heads, reverse=True)


def test_bands_survive_fewer_rows_than_bands():
    assert sum(len(b) for _, b in dt.deciles(rows(3, 1.0, 2), 10)) == 3


def test_two_proportion_z_signs_the_right_way():
    better, worse = rows(100, 1.0, 70), rows(100, 0.1, 40)
    assert dt.two_proportion_z(better, worse) > 1.96
    assert dt.two_proportion_z(worse, better) < -1.96
    assert dt.two_proportion_z(better, better) == pytest.approx(0)
    assert dt.two_proportion_z([], worse) is None


def test_matches_needed_is_none_once_the_bar_is_already_met():
    big = {"mean": 0.6, "lo": 0.55, "hi": 0.65, "clusters": 80, "rows": 900}
    assert dt.matches_needed(big, {"mean": 0.56, "lo": 0.5, "hi": 0.62,
                                   "clusters": 80, "rows": 900}) is None
    need = dt.matches_needed(big, {"mean": 0.50, "lo": 0.47, "hi": 0.53,
                                   "clusters": 80, "rows": 900})
    assert isinstance(need, int) and need >= 0

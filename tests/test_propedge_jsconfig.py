"""Reading measured constants out of src/app.jsx.

A regex over JavaScript is the right tool here -- the numbers were measured once
and live where they are used -- but it fails in two ways that need covering. A
renamed constant must raise rather than return a default, and a reformatted
literal must raise rather than return a WRONG table. The second already
happened: RESIDUAL_SCALE keys its windows by map count, and quoting only
identifier-like keys left `1:` unquoted.
"""
import pytest

from propedge import jsconfig


def test_a_number_is_read():
    assert jsconfig.number("RESIDUAL_SCALE_EXPONENT") == 0.63
    assert 0 < jsconfig.number("PARLAY_TIER_THRESHOLD") < 5


def test_a_renamed_number_raises_rather_than_defaulting():
    with pytest.raises(LookupError, match="renamed"):
        jsconfig.number("SOMETHING_NOBODY_DEFINED")


def test_a_renamed_object_raises():
    with pytest.raises(LookupError, match="object literal"):
        jsconfig.obj("NO_SUCH_TABLE")


def test_numeric_keys_survive():
    got = jsconfig.obj("T", "const T = {\n  cs2: { kills: { 1: 4.66, 2: 7.70 } },\n};\n")
    assert got == {"cs2": {"kills": {"1": 4.66, "2": 7.7}}}


def test_line_and_block_comments_and_trailing_commas_survive():
    got = jsconfig.obj("T", """const T = {
      /* a block comment
         over two lines */
      a: { b: 1, },   // and a line comment
      c: { d: 2 },
    };
    """)
    assert got == {"a": {"b": 1}, "c": {"d": 2}}


def test_it_stops_at_the_matching_brace():
    got = jsconfig.obj("T", "const T = { a: { b: 1 } };\nconst U = { c: 2 };\n")
    assert got == {"a": {"b": 1}}


def test_something_that_is_not_a_literal_of_numbers_raises():
    with pytest.raises(LookupError, match="no longer a plain literal"):
        jsconfig.obj("T", "const T = { a: computeIt(), b: 2 };\n")


def test_negative_numbers_read():
    assert jsconfig.number("T", "const T = -0.25;") == -0.25


def test_the_real_tables_are_shaped_as_expected():
    scales = jsconfig.obj("RESIDUAL_SCALE")
    assert set(scales) >= {"cs2", "lol", "valorant"}
    for game, stats in scales.items():
        for stat, windows in stats.items():
            assert windows, f"{game}.{stat} has no windows"
            for window, value in windows.items():
                assert int(window) > 0 and float(value) > 0
    table = jsconfig.obj("PAYOUT_MULTIPLIERS")
    assert table["2"] == 3 and table["3"] == 6


def test_the_research_scripts_import_and_answer():
    """beat_the_line and can_we_beat_the_line are the two numbers this project
    should be judged by, so they have to keep running as the data grows.

    Only that they import and expose their entry points -- running them takes
    minutes and needs the full data files, which is a job for a person and not
    for every test run.
    """
    import importlib
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "dev"))
    for name in ("beat_the_line", "can_we_beat_the_line", "search_weights_by_outcome"):
        module = importlib.import_module(name)
        assert callable(module.main), name
    ols = importlib.import_module("can_we_beat_the_line").ols
    # y = 3 + 2x exactly: the coefficients must come back, or every t in that
    # script is meaningless.
    beta, errors = ols([5.0, 7.0, 9.0, 11.0], [[1.0, 2.0, 3.0, 4.0]])
    assert beta is not None
    assert abs(beta[0] - 3.0) < 1e-6 and abs(beta[1] - 2.0) < 1e-6


def test_the_regression_refuses_a_singular_design():
    import importlib
    ols = importlib.import_module("can_we_beat_the_line").ols
    beta, _ = ols([1.0, 2.0, 3.0], [[1.0, 1.0, 1.0]])   # constant column
    assert beta is None

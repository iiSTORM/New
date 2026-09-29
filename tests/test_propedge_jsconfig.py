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

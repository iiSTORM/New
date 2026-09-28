"""Money is integer cents, and the conversion has to be exact at the edges.

19.19 is the case that matters: Decimal(19.19) is 19.1899999999999995..., so a
naive conversion rounds a starting bankroll down by a cent on the first day and
every derived balance is wrong from then on.
"""
from decimal import Decimal

import pytest

from propedge.money import dollars, fmt, multiply, to_cents


@pytest.mark.parametrize("given,cents", [
    (19.19, 1919), ("19.19", 1919), ("$19.19", 1919), (Decimal("19.19"), 1919),
    (1, 100), ("1", 100), (0, 0), ("0.01", 1), (2.5, 250), ("$1,019.19", 101919),
    ("  $8.13 ", 813), (37.5, 3750), (0.005, 1), (0.004, 0),
])
def test_to_cents(given, cents):
    assert to_cents(given) == cents


def test_a_bool_is_not_an_amount():
    with pytest.raises(TypeError):
        to_cents(True)


@pytest.mark.parametrize("cents,text", [
    (1919, "$19.19"), (1319, "$13.19"), (-150, "-$1.50"), (0, "$0.00"),
    (5625, "$56.25"), (813, "$8.13"),
])
def test_fmt(cents, text):
    assert fmt(cents) == text


@pytest.mark.parametrize("stake,multiplier,payout", [
    (150, "37.5", 5625), (100, "6", 600), (100, "3", 300),
    (250, "3.252", 813), (100, "2.25", 225), (100, "1.25", 125),
    (100, "0.4", 40), (333, "1.5", 500),   # 499.5 rounds half-up
])
def test_multiply_is_exact(stake, multiplier, payout):
    assert multiply(stake, multiplier) == payout


def test_dollars_round_trips():
    for cents in (0, 1, 813, 1319, 101919):
        assert to_cents(dollars(cents)) == cents


def test_a_hundred_stakes_do_not_drift():
    """The failure floats would give: sum a cent-priced stake many times."""
    assert sum(to_cents("0.07") for _ in range(100)) == to_cents("7.00")

"""Money, in integer cents, never in floats.

A bankroll is a running total of many small numbers. Floats lose to that:
0.1 + 0.2 is not 0.3, and a ledger that derives a balance by summing a few
hundred float entries will drift away from the number in the account. The
drift is small, silent, and lands on the one quantity the whole app exists to
get right. So every amount here is an int of cents, and the only place a
fraction appears is a payout multiplier, which is a Decimal.

Rounding is half-up to the cent, which is what a payout of $1.50 x 37.5
should be read as. Where the real settled amount is known it is recorded
rather than computed -- see slips.settle -- so this rounding decides an
ESTIMATE, not the ledger.
"""
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")


def to_cents(amount):
    """Dollars (str, int, float or Decimal) -> integer cents.

    Floats are accepted because they come out of JSON, and converted through
    str() so 19.19 becomes 1919 rather than 1918: Decimal(19.19) is
    19.1899999999999995026200849679298698902130126953125.
    """
    if isinstance(amount, bool):
        raise TypeError("a bool is not an amount")
    if isinstance(amount, int):
        return amount * 100
    if isinstance(amount, float):
        amount = str(amount)
    value = (Decimal(str(amount).strip().lstrip("$").replace(",", ""))
             .quantize(CENT, rounding=ROUND_HALF_UP))
    return int(value * 100)


def dollars(cents):
    """Integer cents -> Decimal dollars, for display and for JSON."""
    return (Decimal(int(cents)) / 100).quantize(CENT)


def fmt(cents):
    """Integer cents -> "$13.19", negatives as "-$1.50"."""
    value = dollars(cents)
    return f"-${-value:.2f}" if value < 0 else f"${value:.2f}"


def multiply(cents, multiplier):
    """Stake x multiplier -> integer cents, half-up.

    The multiplier is a Decimal because 37.5 is exact in decimal and not in
    binary, and a payout is money.
    """
    product = Decimal(int(cents)) * Decimal(str(multiplier))
    return int(product.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

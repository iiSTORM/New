"""The balance is a sum of entries, and the two ways it silently goes wrong.

Both failures inflate the bankroll and neither throws on its own: staking a
slip twice debits twice, and settling a slip twice credits twice. A double
credit looks exactly like a good night, which is why the ledger refuses both
rather than leaving it to the caller.
"""
import pytest

from propedge.ledger import (DEPOSIT, PAYOUT, REFUND, STAKE, WITHDRAWAL,
                             Ledger, LedgerError)


def test_the_balance_is_the_sum_of_the_entries():
    book = Ledger()
    book.add(DEPOSIT, 1919, note="starting bankroll")
    book.add(STAKE, 150, "slip_1")
    book.add(STAKE, 250, "slip_2")
    book.add(STAKE, 100, "slip_3")
    book.add(STAKE, 100, "slip_4")
    assert book.balance() == 1319


def test_a_payout_and_a_refund_both_credit():
    book = Ledger()
    book.add(DEPOSIT, 1000)
    book.add(STAKE, 100, "slip_1")
    book.add(PAYOUT, 300, "slip_1")
    book.add(STAKE, 150, "slip_2")
    book.add(REFUND, 150, "slip_2")
    assert book.balance() == 1200


def test_a_withdrawal_debits():
    book = Ledger()
    book.add(DEPOSIT, 2000)
    book.add(WITHDRAWAL, 500)
    assert book.balance() == 1500


def test_a_slip_cannot_be_staked_twice():
    book = Ledger()
    book.add(STAKE, 100, "slip_1")
    with pytest.raises(LedgerError, match="already staked"):
        book.add(STAKE, 100, "slip_1")
    assert book.balance() == -100


def test_a_slip_cannot_be_paid_twice():
    book = Ledger()
    book.add(STAKE, 100, "slip_1")
    book.add(PAYOUT, 300, "slip_1")
    with pytest.raises(LedgerError, match="already settled"):
        book.add(PAYOUT, 300, "slip_1")
    with pytest.raises(LedgerError, match="already settled"):
        book.add(REFUND, 100, "slip_1")


def test_direction_comes_from_the_kind_not_the_sign():
    """A stake of -150 is a typo that would CREDIT the bankroll."""
    book = Ledger()
    with pytest.raises(LedgerError, match="magnitude"):
        book.add(STAKE, -150)
    assert book.balance() == 0


def test_a_zero_entry_is_refused():
    with pytest.raises(LedgerError, match="not an event"):
        Ledger().add(PAYOUT, 0, "slip_1")


def test_an_adjustment_may_be_negative():
    book = Ledger()
    book.add(DEPOSIT, 1000)
    book.add("adjustment", -19, note="the app says a cent less; app wins")
    assert book.balance() == 981


def test_an_unknown_kind_is_refused():
    with pytest.raises(LedgerError, match="kind must be"):
        Ledger().add("bonus", 500)


def test_history_is_the_running_balance_in_order():
    book = Ledger()
    book.add(DEPOSIT, 1000, at="2026-09-28T10:00:00+00:00")
    book.add(STAKE, 100, "s1", at="2026-09-28T22:00:00+00:00")
    book.add(PAYOUT, 600, "s1", at="2026-09-29T02:00:00+00:00")
    assert [balance for _, balance in book.history()] == [1000, 900, 1500]


def test_balance_at_ignores_later_entries():
    book = Ledger()
    book.add(DEPOSIT, 1000, at="2026-09-28T10:00:00+00:00")
    book.add(STAKE, 100, "s1", at="2026-09-29T22:00:00+00:00")
    assert book.balance_at("2026-09-28T23:59:59+00:00") == 1000


def test_staked_on_is_exposure_not_net():
    """A win earlier in the evening does not buy room for another bet."""
    book = Ledger()
    book.add(DEPOSIT, 2000, at="2026-09-28T10:00:00+00:00")
    book.add(STAKE, 100, "s1", at="2026-09-28T22:00:00+00:00")
    book.add(PAYOUT, 600, "s1", at="2026-09-28T23:00:00+00:00")
    book.add(STAKE, 150, "s2", at="2026-09-28T23:30:00+00:00")
    assert book.staked_on("2026-09-28") == 250


def test_staked_on_takes_a_date_object():
    from datetime import date
    book = Ledger()
    book.add(STAKE, 100, "s1", at="2026-09-28T22:00:00+00:00")
    assert book.staked_on(date(2026, 9, 28)) == 100
    assert book.staked_on(date(2026, 9, 27)) == 0


def test_a_betting_night_is_local_not_utc():
    """9pm Eastern is already tomorrow in UTC. If the ledger dated entries by
    UTC day, half an evening's slips would land in the next night's cap and the
    late slips would get a fresh 25% budget."""
    book = Ledger()
    book.add(STAKE, 150, "s1", at="2026-09-28T19:00:00-04:00")   # 23:00 UTC
    book.add(STAKE, 250, "s2", at="2026-09-28T21:30:00-04:00")   # 01:30 UTC, 9/29
    assert book.staked_on("2026-09-28") == 400
    assert book.staked_on("2026-09-29") == 0


def test_entries_are_ordered_by_instant_not_by_wall_clock():
    """Two zones, or either side of a DST change: the string order is wrong."""
    book = Ledger()
    book.add(DEPOSIT, 1000, at="2026-09-28T23:00:00-04:00")   # 03:00 UTC 9/29
    book.add(STAKE, 100, "s1", at="2026-09-29T01:00:00+00:00")  # two hours earlier
    assert [balance for _, balance in book.history()] == [-100, 900]

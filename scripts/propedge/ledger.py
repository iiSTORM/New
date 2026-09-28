"""The bankroll, derived from a ledger and never typed in.

A balance that is stored is a balance that can be wrong. Every amount here is
an entry -- deposit, withdrawal, stake, payout, refund -- and the balance is
their sum. If the number disagrees with the app, the entries say where.

Two invariants the ledger enforces itself, because both failures are silent
and both inflate the bankroll:

  * a slip is staked ONCE. Entering the same slip twice is a double debit.
  * a slip is paid ONCE. Settling twice is a double credit, and a double
    credit looks exactly like a good night.
"""
import uuid
from datetime import date, datetime, timezone

DEPOSIT, WITHDRAWAL, STAKE, PAYOUT, REFUND, ADJUSTMENT = (
    "deposit", "withdrawal", "stake", "payout", "refund", "adjustment")

#: Which way each kind moves the balance. An adjustment carries its own sign,
#: for the one case that is neither: correcting an entry that was wrong.
SIGN = {DEPOSIT: 1, WITHDRAWAL: -1, STAKE: -1, PAYOUT: 1, REFUND: 1,
        ADJUSTMENT: 1}
KINDS = tuple(SIGN)
#: Kinds that settle a slip, at most one of which may exist per slip.
SETTLEMENT_KINDS = (PAYOUT, REFUND)


def _now():
    """Local time WITH its offset, e.g. 2026-09-28T21:14:03-04:00.

    Not UTC. A betting night is a local thing: a slip placed at 9pm Eastern is
    already tomorrow in UTC, so a UTC day would split one night's exposure
    across two nightly caps and let the second half of the evening bet again
    against a fresh budget. Storing local time with the offset keeps the date
    at the front of the string equal to the night it belongs to, and keeps the
    instant unambiguous.
    """
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _instant(entry):
    """An entry's timestamp as a comparable datetime.

    Sorting the ISO strings would be close but not right: two entries written
    either side of a DST change, or from two machines in different zones, sort
    by their local wall clock rather than by when they happened.
    """
    try:
        parsed = datetime.fromisoformat(entry["at"])
    except (TypeError, ValueError):
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class LedgerError(Exception):
    """A write that would make the balance wrong."""


class Ledger:
    def __init__(self, entries=None):
        self.entries = list(entries or [])

    # ---------------------------------------------------------------- writes

    def add(self, kind, amount_cents, slip_id=None, note="", at=None):
        if kind not in KINDS:
            raise LedgerError(f"kind must be one of {KINDS}, not {kind!r}")
        amount = int(amount_cents)
        if kind != ADJUSTMENT and amount < 0:
            raise LedgerError(f"a {kind} is a magnitude; its direction comes from "
                              f"its kind, so pass {abs(amount)} not {amount}")
        if kind != ADJUSTMENT and amount == 0:
            raise LedgerError(f"a {kind} of zero is not an event; skip it")
        if kind == STAKE and slip_id and self.staked(slip_id):
            raise LedgerError(f"{slip_id} is already staked — entering it twice "
                              "would debit the bankroll twice")
        if kind in SETTLEMENT_KINDS and slip_id and self.settled(slip_id):
            raise LedgerError(f"{slip_id} is already settled — paying it twice "
                              "would credit the bankroll twice")
        entry = {"id": f"led_{uuid.uuid4().hex[:12]}", "at": at or _now(),
                 "kind": kind, "amount_cents": amount, "slip_id": slip_id,
                 "note": note}
        self.entries.append(entry)
        return entry

    def signed(self, entry):
        return SIGN[entry["kind"]] * int(entry["amount_cents"])

    # ----------------------------------------------------------------- reads

    def balance(self):
        return sum(self.signed(e) for e in self.entries)

    def balance_at(self, when):
        """The balance as of `when` (an ISO timestamp), for the bankroll chart."""
        cutoff = _instant({"at": when})
        return sum(self.signed(e) for e in self.entries if _instant(e) <= cutoff)

    def history(self):
        """[(at, balance_after)] in order, for plotting."""
        out, running = [], 0
        for entry in sorted(self.entries, key=_instant):
            running += self.signed(entry)
            out.append((entry["at"], running))
        return out

    def for_slip(self, slip_id):
        return [e for e in self.entries if e["slip_id"] == slip_id]

    def staked(self, slip_id):
        return any(e["kind"] == STAKE for e in self.for_slip(slip_id))

    def settled(self, slip_id):
        return any(e["kind"] in SETTLEMENT_KINDS for e in self.for_slip(slip_id))

    def staked_on(self, day):
        """Total staked on a calendar day (UTC), for the per-night cap.

        `day` is a date or "YYYY-MM-DD", matched against the LOCAL date each
        entry was written with -- see _now(). Deliberately the stake total and
        not the net: the cap is on exposure, and a win earlier in the evening
        does not buy room for more.
        """
        prefix = day.isoformat() if isinstance(day, date) else str(day)
        return sum(int(e["amount_cents"]) for e in self.entries
                   if e["kind"] == STAKE and e["at"].startswith(prefix))

    def as_json(self):
        return list(self.entries)

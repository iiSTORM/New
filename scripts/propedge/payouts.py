"""Payout multipliers: the defaults, and what a slip becomes when it shrinks.

The multiplier that matters is the one printed on the slip, and this module
never overrides it -- a slip carries its own `multiplier` and that is what
settles it. Goblins, demons and promos all move it (a goblin lowers the line
and the multiplier; the Guarantee Pick promo drops a line to 0.5 and pays
less), so a table cannot be trusted to reproduce it.

The table exists for the two cases where there is no printed number:

  1. Planning a slip that has not been placed yet.
  2. A slip that SHRANK. A 3-pick where one leg pushes pays as a 2-pick, and
     the 2-pick multiplier was never printed anywhere. Anything derived this
     way is marked estimated=True and reconciled against the real payout when
     it lands.

DEFAULTS ARE A STARTING POINT, NOT A FACT. PrizePicks has run 3-pick power at
both 5x and 6x; 6x is what a real slip showed on 2026-09-28, so that is the
default here. src/app.jsx's PAYOUT_MULTIPLIERS still says 5x for the same
size -- see README. Flex tables move more often still. Check yours and
override.
"""
from decimal import Decimal

# Power play: every leg has to land.
POWER = {2: Decimal("3"), 3: Decimal("6"), 4: Decimal("10"),
         5: Decimal("20"), 6: Decimal("37.5")}

# Flex play: {slip size: {legs correct: multiplier}}. Sizes absent from a
# row pay nothing.
FLEX = {
    3: {3: Decimal("2.25"), 2: Decimal("1.25")},
    4: {4: Decimal("5"), 3: Decimal("1.5")},
    5: {5: Decimal("10"), 4: Decimal("2"), 3: Decimal("0.4")},
    6: {6: Decimal("25"), 5: Decimal("2"), 4: Decimal("0.4")},
}

MODES = ("power", "flex")


class PayoutTable:
    """A table a slip can be priced against, with your own overrides."""

    def __init__(self, power=None, flex=None):
        self.power = dict(POWER)
        self.power.update({int(k): Decimal(str(v)) for k, v in (power or {}).items()})
        self.flex = {size: dict(row) for size, row in FLEX.items()}
        for size, row in (flex or {}).items():
            self.flex.setdefault(int(size), {}).update(
                {int(k): Decimal(str(v)) for k, v in row.items()})

    def multiplier(self, mode, size, correct=None):
        """The multiplier for a slip of `size` legs with `correct` of them won.

        Returns None where the table has no entry -- an unplayable size, or a
        flex slip that missed too many. None means "pays nothing", and the
        caller distinguishes that from "pays zero" only in the message it
        shows, because they are the same money.
        """
        if mode == "power":
            return self.power.get(size) if correct in (None, size) else None
        if mode == "flex":
            return (self.flex.get(size) or {}).get(
                size if correct is None else correct)
        raise ValueError(f"mode must be one of {MODES}, not {mode!r}")

    def as_json(self):
        return {"power": {str(k): str(v) for k, v in sorted(self.power.items())},
                "flex": {str(size): {str(k): str(v) for k, v in sorted(row.items())}
                         for size, row in sorted(self.flex.items())}}

    @classmethod
    def from_json(cls, blob):
        blob = blob or {}
        return cls(power=blob.get("power"), flex=blob.get("flex"))


DEFAULT_TABLE = PayoutTable()

#: What a leg can be when a slip is priced or settled.
WON, PUSH, LOST = "won", "push", "lost"


def net_return(states, mode, size, printed_multiplier=None, table=None):
    """(net return per unit staked, label) for one pattern of leg results.

    One function, because this is the same question three times over: what a
    slip pays once its legs are decided (slips.settle), what each win/push/loss
    combination is worth when sizing it (sizing.outcome_table), and what each
    simulation pays when a model hands over per-simulation outcomes. Three
    copies of "a push shrinks the slip and a shrink to one leg refunds" is three
    chances for the sizing to disagree with the settlement about the same money.

    `states` is one of WON / PUSH / LOST per leg. `size` is the slip as PLACED,
    so a shrink can be detected: the printed multiplier describes the size that
    was printed and nothing else.

    Returns net, so -1.0 is a lost stake, 0.0 a refund and +2.0 a 3x payout.
    """
    table = table or DEFAULT_TABLE
    active = [state for state in states if state != PUSH]
    if len(active) <= 1:
        return 0.0, "refund"
    correct = sum(1 for state in active if state == WON)
    shrunk = len(active) != size
    if mode == "power" and not shrunk and printed_multiplier is not None:
        multiplier = (Decimal(str(printed_multiplier)) if correct == len(active)
                      else None)
    else:
        multiplier = table.multiplier(mode, len(active), correct)
    if multiplier is None:
        return -1.0, "loss"
    return float(multiplier) - 1.0, f"{correct}/{len(active)} at {multiplier}x"

"""Slips, legs, the PrizePicks rules, and what a slip pays once it settles.

The rules encoded here are not preferences, they are mechanics that have
already cost money when left to memory:

  * A slip needs players from at least two different teams, and one prop per
    player. In tennis each player IS their own team.
  * A whole-number line that lands exactly is a PUSH: the leg is removed and
    the slip shrinks -- a 3-pick becomes a 2-pick and pays as one. A slip
    shrunk to a single leg is refunded, not lost.
  * A player who does not play is removed the same way (DNP), never graded as
    a loss.
  * A line like 15.5 cannot push. Only whole numbers can.

Settlement returns an ESTIMATE of the payout and says so. The real number is
whatever lands in the account, and `settle` takes it when you have it, because
a shrunk slip's multiplier was never printed anywhere and a promo leg moves it
in ways no table knows.
"""
import uuid
from dataclasses import dataclass, field, asdict
from decimal import Decimal

from .money import multiply, to_cents
from .payouts import DEFAULT_TABLE, MODES

PENDING, WON, LOST, PUSH, DNP = "pending", "won", "lost", "push", "dnp"
#: A leg that was played but whose number was never written down. It exists for
#: backfilling history: a slip from last week is known to have lost without
#: every leg's stat line being recoverable. A slip holding one can only be
#: settled with the real payout, never estimated, and calibration ignores it.
UNKNOWN = "unknown"
LEG_RESULTS = (PENDING, WON, LOST, PUSH, DNP, UNKNOWN)
#: A slip's own status. "partial" is a flex slip that paid something less than
#: a full hit; it is still a loss whenever the payout is under the stake, and
#: the ledger is what says so.
SLIP_STATUSES = (PENDING, WON, LOST, "refunded", "partial")
SIDES = ("over", "under")
#: Removed, not graded. Both shrink the slip.
REMOVED = (PUSH, DNP)


def _new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class Leg:
    sport: str
    player: str
    stat: str
    line: float
    side: str                      # "over" | "under"
    team: str = ""
    opponent: str = ""
    odds_type: str = "standard"    # standard | goblin | demon | promo
    maps: int = None               # esports window, e.g. maps 1-2
    model_prob: float = None
    line_at_placement: float = None
    closing_line: float = None
    actual: float = None
    result: str = PENDING
    #: When the game locks, as the board posted it. Needed for two things: the
    #: builder's early/late split, and closing-line value -- "did the line move
    #: before lock" has no answer without knowing when lock was.
    start_time: str = ""
    note: str = ""

    def __post_init__(self):
        if self.side not in SIDES:
            raise ValueError(f"side must be one of {SIDES}, not {self.side!r}")
        if self.result not in LEG_RESULTS:
            raise ValueError(f"result must be one of {LEG_RESULTS}, not {self.result!r}")
        if self.line_at_placement is None:
            self.line_at_placement = self.line

    @property
    def team_key(self):
        """What counts as "the same team" for the two-team rule.

        In tennis the opponents are two individuals, so the player is the
        team -- otherwise a blank team field would make a legal two-player
        slip look like one team and be rejected.
        """
        if self.sport == "tennis":
            return f"tennis:{self.player.strip().lower()}"
        return (self.team or "").strip().lower()

    @property
    def player_key(self):
        return f"{self.sport}:{self.player.strip().lower()}"

    @property
    def can_push(self):
        """Only a whole-number line can land exactly on the line."""
        return float(self.line).is_integer()

    def grade(self, actual=None, dnp=False):
        """Set this leg's result from the number the player actually put up."""
        if dnp:
            self.result = DNP
            return self.result
        if actual is not None:
            self.actual = float(actual)
        if self.actual is None:
            self.result = PENDING
        elif self.can_push and float(self.actual) == float(self.line):
            self.result = PUSH
        elif self.side == "over":
            self.result = WON if self.actual > self.line else LOST
        else:
            self.result = WON if self.actual < self.line else LOST
        return self.result

    def as_json(self):
        return asdict(self)

    @classmethod
    def from_json(cls, blob):
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in blob.items() if k in known})


@dataclass
class Slip:
    mode: str                      # "power" | "flex"
    stake_cents: int
    placed_at: str = ""
    legs: list = field(default_factory=list)
    multiplier: Decimal = None     # AS PRINTED ON THE SLIP, at its original size
    id: str = field(default_factory=lambda: _new_id("slip"))
    status: str = PENDING
    payout_cents: int = None       # what actually landed, once known
    early_payout_taken: bool = False
    notes: str = ""

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, not {self.mode!r}")
        self.stake_cents = int(self.stake_cents)
        if self.multiplier is not None:
            self.multiplier = Decimal(str(self.multiplier))

    @property
    def n_legs(self):
        return len(self.legs)

    @property
    def is_settled(self):
        return self.status != PENDING

    def problems(self):
        """Every reason this slip is not placeable, as plain sentences.

        Returns a list so all of them are reported at once -- being told about
        one broken rule at a time on a phone at 7pm is how the second one gets
        missed.
        """
        out = []
        if self.n_legs < 2:
            out.append(f"a slip needs at least 2 legs, this has {self.n_legs}")
        if self.stake_cents < 100:
            out.append("the minimum entry is $1.00")
        players = [leg.player_key for leg in self.legs]
        repeated = sorted({p for p in players if players.count(p) > 1})
        if repeated:
            out.append("one prop per player per slip; repeated: "
                       + ", ".join(sorted({leg.player for leg in self.legs
                                           if leg.player_key in repeated})))
        teams = {leg.team_key for leg in self.legs}
        if "" in teams:
            out.append("every leg needs a team (tennis aside, where the player is the team)")
        elif len(teams) < 2:
            out.append("a slip needs players from at least 2 different teams; "
                       f"all {self.n_legs} legs are {self.legs[0].team}")
        return out

    def is_valid(self):
        return not self.problems()

    def as_json(self):
        blob = asdict(self)
        blob["legs"] = [leg.as_json() for leg in self.legs]
        blob["multiplier"] = None if self.multiplier is None else str(self.multiplier)
        return blob

    @classmethod
    def from_json(cls, blob):
        blob = dict(blob)
        legs = [Leg.from_json(l) for l in blob.pop("legs", [])]
        known = {f for f in cls.__dataclass_fields__} - {"legs"}
        return cls(legs=legs, **{k: v for k, v in blob.items() if k in known})


@dataclass
class Settlement:
    status: str
    payout_cents: int
    multiplier: Decimal = None
    estimated: bool = False
    removed: tuple = ()
    reasons: tuple = ()

    @property
    def net_cents(self):
        """Signed result against the stake, for reading at a glance."""
        return self.payout_cents


def settle(slip, table=None, actual_payout=None):
    """What the slip pays. Raises if any leg is still pending.

    `actual_payout` (dollars) is the number from the account. Pass it whenever
    you have it: it becomes the payout and the estimate is kept only to be
    compared against it.
    """
    table = table or DEFAULT_TABLE
    pending = [leg for leg in slip.legs if leg.result == PENDING]
    if pending:
        raise ValueError("still pending: "
                         + ", ".join(f"{leg.player} {leg.stat}" for leg in pending))
    unknown = [leg for leg in slip.legs if leg.result == UNKNOWN]
    if unknown and actual_payout is None:
        raise ValueError(
            "cannot estimate a payout while these legs' numbers are unknown: "
            + ", ".join(f"{leg.player} {leg.stat}" for leg in unknown)
            + " — pass the payout that actually landed")
    if unknown:
        # No estimate is possible and none is attempted: with a leg's number
        # missing, "partial" cannot be distinguished from "won", so the payout
        # is recorded and the status says only whether anything came back.
        paid = to_cents(actual_payout)
        return Settlement(
            LOST if paid == 0 else WON, paid, None, False, (),
            tuple(f"{leg.player} {leg.stat} was never written down" for leg in unknown)
            + ("payout taken as recorded, not estimated",))

    removed = tuple(leg for leg in slip.legs if leg.result in REMOVED)
    active = [leg for leg in slip.legs if leg.result in (WON, LOST)]
    reasons = []
    for leg in removed:
        reasons.append(f"{leg.player} {leg.side} {leg.line} {leg.stat} "
                       + ("pushed" if leg.result == PUSH else "did not play")
                       + " — leg removed, slip shrinks")

    if len(active) <= 1:
        reasons.append(f"only {len(active)} leg(s) left after removals, so the "
                       "entry is refunded")
        got = Settlement("refunded", slip.stake_cents, None, False, removed,
                         tuple(reasons))
    else:
        correct = sum(1 for leg in active if leg.result == WON)
        size = len(active)
        shrunk = size != slip.n_legs
        # The printed multiplier only describes the size that was printed. A
        # shrunk slip has to come off the table, and that is an estimate.
        if slip.mode == "power" and not shrunk and slip.multiplier is not None:
            multiplier = slip.multiplier if correct == size else None
            estimated = False
        else:
            multiplier = table.multiplier(slip.mode, size, correct)
            estimated = shrunk or slip.multiplier is None
        if multiplier is None:
            payout = 0
            reasons.append(f"{size - correct} of {size} legs missed" if size != correct
                           else "no multiplier on file for this size")
            status = LOST
        else:
            payout = multiply(slip.stake_cents, multiplier)
            # "partial" means not every leg landed, not that the slip lost
            # money: a 2-of-3 flex pays 1.25x, which is a profit AND a partial
            # hit. Whether a night made money is the ledger's business.
            status = WON if correct == size else "partial"
            reasons.append(f"{correct} of {size} legs landed at {multiplier}x")
        if estimated and multiplier is not None:
            reasons.append("multiplier taken from the payout table, not the slip — "
                           "confirm it against what actually paid")
        got = Settlement(status, payout, multiplier, estimated, removed, tuple(reasons))

    if actual_payout is not None:
        real = to_cents(actual_payout)
        if real != got.payout_cents:
            got.reasons += (f"estimated {got.payout_cents} cents, actually paid {real}; "
                            "the recorded payout is the real one",)
        got.payout_cents = real
        got.estimated = False
        if got.status != "refunded":
            landed = all(leg.result == WON for leg in slip.legs
                         if leg.result in (WON, LOST))
            got.status = LOST if real == 0 else (WON if landed else "partial")
    return got

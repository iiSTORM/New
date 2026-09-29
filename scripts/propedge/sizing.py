"""How much to stake: fractional Kelly on the slip's whole outcome table.

Not on "probability of winning". A PrizePicks slip has more than two outcomes
and the extra ones matter: a push shrinks the slip and pays at a DIFFERENT
multiplier, and a shrink to one leg refunds the stake. Refunds are worth real
money to a Kelly calculation -- they cut the downside without touching the
upside -- and a two-outcome approximation throws them away.

So the outcome table is enumerated exactly. Each leg is won / pushed / lost, a
slip has at most six legs, and 3^6 is 729 combinations: there is no reason to
approximate something that small. Every combination is priced through the same
payout table that settles the slip, so the sizing and the settlement cannot
disagree about what a shrunk slip pays.

Legs are treated as INDEPENDENT here, which they are not -- two legs on one
match share the game's pace, and that raises the variance of the joint
outcome. That is the parlay builder's problem (phase 3), it makes this
function's estimate optimistic, and `independent=True` on the result is there
so nothing downstream can forget it.

Against reference/kelly.py, which solves the same problem: that one takes GROSS
return multiples (3.0 means $1 comes back as $3) and this one takes NET
(+2.0 for the same slip), so a table moved between them needs r -> r - 1. Its
`kelly_joint` optimises several slips at once, which is strictly better than
this module's per-slip solve minus the night's remaining budget; that belongs
with the builder in phase 3, and the caps here are the conservative version of
the same constraint.
"""
import itertools
import math
from dataclasses import dataclass, field
from decimal import Decimal

from .payouts import DEFAULT_TABLE, net_return

HALF_KELLY = Decimal("0.5")
QUARTER_KELLY = Decimal("0.25")
#: A stress range wider than this on p_win means the estimate is sensitive to
#: assumptions, so the stake drops to quarter Kelly.
WIDE_STRESS = 0.10
PER_SLIP_CAP = Decimal("0.10")     # of bankroll
PER_NIGHT_CAP = Decimal("0.25")    # of bankroll, exposure not net
MIN_STAKE_CENTS = 100              # PrizePicks minimum entry
#: What replaces Kelly when the probabilities cannot be trusted. Kelly is a
#: function of those probabilities: wrong by five points and the stake is wrong
#: by roughly a factor of two, in the same direction, compounding. A flat
#: fraction of the bankroll does not care whether 60% means 60%.
FLAT_UNIT = Decimal("0.02")
POLICIES = ("kelly", "flat")


@dataclass
class Outcome:
    probability: float
    net_return: float      # per unit staked: +2.0 means $1 -> $3 back
    label: str = ""


@dataclass
class Stake:
    stake_cents: int
    bet: bool
    kelly_fraction: float = 0.0
    applied_fraction: Decimal = HALF_KELLY
    expected_value: float = 0.0
    binding: str = ""
    independent: bool = True
    reasons: tuple = ()
    outcomes: list = field(default_factory=list)


def outcome_table(legs, mode="power", printed_multiplier=None, table=None):
    """Every win/push/loss combination of the legs, with its net return.

    `legs` is a list of (p_win, p_push). Probabilities that do not leave room
    for a loss are rejected rather than silently normalised -- a leg priced at
    p_win 0.7 and p_push 0.4 is a mistake somewhere upstream, and quietly
    rescaling it would hide the mistake inside a stake.
    """
    table = table or DEFAULT_TABLE
    size = len(legs)
    for i, (p_win, p_push) in enumerate(legs):
        if p_win < 0 or p_push < 0 or p_win + p_push > 1 + 1e-9:
            raise ValueError(f"leg {i}: p_win {p_win} + p_push {p_push} is not a "
                             "probability")
    per_leg = [(("won", p_win), ("push", p_push), ("lost", 1 - p_win - p_push))
               for p_win, p_push in legs]

    merged = {}
    for combo in itertools.product(*per_leg):
        probability = math.prod(p for _, p in combo)
        if probability <= 0:
            continue
        net, label = net_return([state for state, _ in combo], mode, size,
                                printed_multiplier, table)
        merged[label] = merged.get(label, [0.0, net])
        merged[label][0] += probability
        merged[label][1] = net
    return [Outcome(p, net, label) for label, (p, net) in
            sorted(merged.items(), key=lambda kv: -kv[1][1])]


def kelly_fraction(outcomes):
    """The f that maximises expected log growth, by bisection.

    E[log(1 + f r)] is concave in f, so its derivative
    g(f) = sum p_i r_i / (1 + f r_i) is decreasing: one sign change, and
    bisection cannot miss it. Newton can, on a table with a near-certain
    total loss.
    """
    if not outcomes:
        return 0.0
    expected = sum(o.probability * o.net_return for o in outcomes)
    if expected <= 0:
        return 0.0
    worst = min(o.net_return for o in outcomes)
    # f must keep 1 + f*r > 0 for every outcome, so it is bounded by the
    # biggest loss. A total loss (r = -1) bounds f below 1.
    hi = 0.999999 if worst <= -1 else (0.999999 / -worst if worst < 0 else 1e6)
    g = lambda f: sum(o.probability * o.net_return / (1 + f * o.net_return)
                      for o in outcomes)
    if g(hi) > 0:
        return hi
    lo = 0.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if g(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def recommend(outcomes, bankroll_cents, staked_tonight_cents=0, stress_width=0.0,
              per_slip_cap=PER_SLIP_CAP, per_night_cap=PER_NIGHT_CAP,
              min_stake_cents=MIN_STAKE_CENTS, policy="kelly", policy_reason=""):
    """A stake, or a refusal with the reason.

    The caps are not advice, they bind: 10% of the bankroll on one slip, 25%
    across the night, recomputed from the CURRENT balance every time so a loss
    shrinks the next stake automatically.

    `policy` comes from analytics.sizing_policy, which measures whether the
    probabilities feeding Kelly have been honest. Under "flat" the Kelly maths
    still runs and is still reported -- the edge test below is what decides
    whether to play at all -- but the SIZE becomes a fixed fraction of the
    bankroll, because a mis-calibrated Kelly is not a smaller Kelly, it is a
    bigger one.
    """
    if policy not in POLICIES:
        raise ValueError(f"policy must be one of {POLICIES}, not {policy!r}")
    reasons = []
    expected = sum(o.probability * o.net_return for o in outcomes)
    fraction = kelly_fraction(outcomes)
    applied = QUARTER_KELLY if stress_width > WIDE_STRESS else HALF_KELLY
    if applied == QUARTER_KELLY:
        reasons.append(f"stress range {stress_width:.0%} wide, so quarter Kelly "
                       "rather than half")
    if fraction <= 0:
        return Stake(0, False, 0.0, applied, expected, "no edge",
                     reasons=tuple(reasons + [
                         f"expected return {expected:+.1%} per unit — no stake "
                         "has a positive edge here"]), outcomes=outcomes)

    if policy == "flat":
        reasons.append(policy_reason or "flat units in force rather than Kelly")
        wanted = int(bankroll_cents * float(FLAT_UNIT))
    else:
        wanted = int(bankroll_cents * float(applied) * fraction)
    slip_cap = int(bankroll_cents * float(per_slip_cap))
    night_room = int(bankroll_cents * float(per_night_cap)) - int(staked_tonight_cents)

    stake, binding = wanted, ("flat units" if policy == "flat" else "kelly")
    if stake > slip_cap:
        stake, binding = slip_cap, "per-slip cap"
        reasons.append(f"Kelly wanted {wanted} cents; the {per_slip_cap:.0%} "
                       "per-slip cap binds")
    if stake > night_room:
        stake, binding = max(night_room, 0), "per-night cap"
        reasons.append(f"only {max(night_room, 0)} cents of the {per_night_cap:.0%} "
                       "nightly budget is left")

    if stake < min_stake_cents:
        # The $1 minimum is PrizePicks' floor, not Kelly's, so taking it
        # overbets. How far is allowed to matter: staking more than FULL Kelly
        # turns expected log growth down, and more than twice full Kelly turns
        # it negative. reference/kelly.py draws the line at full Kelly (it
        # skips when the half-Kelly stake is under half the minimum, which is
        # the same test), and that is the line kept here.
        # Under flat units Kelly is not the yardstick -- the whole point is that
        # it cannot be trusted -- so the floor is judged against the caps alone.
        full_kelly_cents = (bankroll_cents if policy == "flat"
                            else int(bankroll_cents * fraction))
        if (min_stake_cents <= full_kelly_cents and min_stake_cents <= slip_cap
                and min_stake_cents <= night_room):
            reasons.append(f"Kelly wanted {stake} cents, under the $1 minimum "
                           f"entry, so $1 it is — more than half Kelly asks but "
                           f"still inside full Kelly ({full_kelly_cents} cents)")
            return Stake(min_stake_cents, True, fraction, applied, expected,
                         "minimum entry", reasons=tuple(reasons), outcomes=outcomes)
        why = ("more than full Kelly" if min_stake_cents > full_kelly_cents
               else "more than the caps allow")
        reasons.append(f"the $1 minimum entry is {why} on a "
                       f"{bankroll_cents}-cent bankroll (full Kelly is "
                       f"{full_kelly_cents} cents) — skip this one rather than "
                       "overbet it")
        return Stake(0, False, fraction, applied, expected, "below minimum",
                     reasons=tuple(reasons), outcomes=outcomes)

    return Stake(stake, True, fraction, applied, expected, binding,
                 reasons=tuple(reasons), outcomes=outcomes)

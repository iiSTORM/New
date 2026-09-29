"""Turn projections into a ranked slate of slips, with stakes and a reason.

Four rules from the build plan decide what is even allowed, and they are
enforced before anything is priced, because a slip PrizePicks will not accept
is not a candidate however good its expected value looks:

  * players from at least two different teams, one prop per player;
  * each leg appears in at most ONE recommended slip. On 2026-09-28 a single
    goalkeeper leg sat on two slips and one number busted both, which is not two
    bets, it is one bet at twice the stake with the paperwork of two;
  * ranked by WORST-CASE expected value across scenarios, never the blend;
  * nothing is offered at all unless it clears the bar.

On the worst case: a slip is priced once per scenario, whole, and the worst of
those prices ranks it. Taking each leg's own low and multiplying them would be a
corner no model produces -- it assumes every model is simultaneously at its
worst and independently so, which is both incoherent and, being a product of
lows, far too pessimistic to compare slips by.

Silence is a real output here. If nothing clears the bar the answer is no slips,
printed as no slips, because the alternative -- always showing the best three --
turns a slate into a habit.
"""
import itertools
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from .analytics import parse_ts, sizing_policy
from .model import BLEND, price
from .money import fmt
from .payouts import DEFAULT_TABLE
from .sizing import recommend
from .slips import Slip

#: Worst-case expected return per unit staked, below which nothing is offered.
MIN_WORST_EV = 0.10
#: Slip sizes searched by default. 4-6 are allowed but not searched: the
#: multiplier grows slower than the joint probability falls once the legs are
#: correlated, so they rarely clear the bar and they crowd the search.
DEFAULT_SIZES = (2, 3)
#: A slip belongs to the early set if it locks before this hour, local time.
LATE_CUTOFF_HOUR = 23


@dataclass
class Candidate:
    projections: tuple
    by_scenario: dict                # scenario -> expected return per unit
    worst_scenario: str
    outcomes: list                   # under the worst scenario
    how: str                         # how it was priced
    multiplier: object = None
    stake: object = None             # a sizing.Stake, once the slate is built
    breakdown: tuple = field(default_factory=tuple)

    @property
    def worst_ev(self):
        return self.by_scenario[self.worst_scenario]

    @property
    def blend_ev(self):
        return sum(self.by_scenario.values()) / len(self.by_scenario)

    @property
    def size(self):
        return len(self.projections)

    @property
    def legs(self):
        return {p.prop_id for p in self.projections}

    @property
    def stress_width(self):
        """The widest single leg, which is what drops the stake to quarter Kelly."""
        return max((p.stress_width for p in self.projections), default=0.0)

    @property
    def locks_at(self):
        """When the slip locks: the FIRST leg to start, not the last.

        A slip is live from the moment any leg's game begins, so that is the
        deadline for placing it.
        """
        stamps = [parse_ts(p.start_time) for p in self.projections]
        stamps = [s for s in stamps if s]
        return min(stamps) if stamps else None


def scenarios_of(projections):
    """The named worlds this slip is stress-tested in.

    A leg missing one falls back to its own blend rather than dropping the
    world, so one model carrying fewer scenarios cannot silently narrow the
    stress test for the whole slip.

    "blend" is the name a single-probability projection's number is filed under,
    and it is dropped as soon as any real scenario exists: mixing it in would
    add a world in which the scenario-carrying legs use the AVERAGE of their own
    worlds, which is not a world any model produced. With nothing but
    single-number legs it is the only world there is, and stays.
    """
    names = set()
    for projection in projections:
        names.update(projection.scenarios)
    named = names - {BLEND}
    return sorted(named) if named else sorted(names)


def is_legal(projections, mode="power"):
    """The PrizePicks rules, via the same Slip the tracker will record."""
    trial = Slip(mode=mode, stake_cents=100,
                 legs=[p.to_leg() for p in projections])
    return trial.problems()


def evaluate(projections, sims=None, mode="power", multiplier=None, table=None):
    """Price one combination under every scenario. None if it cannot be priced."""
    by_scenario, outcomes_by_scenario, how = {}, {}, ""
    for scenario in scenarios_of(projections):
        try:
            outcomes, how = price(projections, scenario, sims, mode, multiplier, table)
        except (ValueError, KeyError):
            return None
        by_scenario[scenario] = sum(o.probability * o.net_return for o in outcomes)
        outcomes_by_scenario[scenario] = outcomes
    if not by_scenario:
        return None
    worst = min(by_scenario, key=lambda name: by_scenario[name])
    return Candidate(tuple(projections), by_scenario, worst,
                     outcomes_by_scenario[worst], how, multiplier)


def search(projections, sims=None, sizes=DEFAULT_SIZES, mode="power",
           table=None, min_worst_ev=MIN_WORST_EV):
    """Every legal combination that clears the bar, best worst-case first."""
    table = table or DEFAULT_TABLE
    found = []
    for size in sizes:
        multiplier = table.multiplier(mode, size)
        if multiplier is None:
            continue
        for combination in itertools.combinations(projections, size):
            if is_legal(combination, mode):
                continue
            candidate = evaluate(combination, sims, mode, multiplier, table)
            if candidate and candidate.worst_ev >= min_worst_ev:
                found.append(candidate)
    found.sort(key=lambda c: (-c.worst_ev, -c.blend_ev, c.size))
    return found


def take_disjoint(candidates):
    """Greedy, best first, skipping anything reusing a leg already spoken for.

    Greedy rather than an optimal packing on purpose: the ranking is the point,
    and an optimiser that drops the best slip to fit two mediocre ones is
    answering a question nobody asked.
    """
    taken, used = [], set()
    for candidate in candidates:
        if candidate.legs & used:
            continue
        taken.append(candidate)
        used |= candidate.legs
    return taken


def split_by_lock(candidates, now=None, cutoff_hour=LATE_CUTOFF_HOUR):
    """(early, late): early locks before tonight's cutoff, local time.

    A slip with no start time on any leg is treated as late rather than early:
    the early set exists so it can be placed before going out, and something
    that might already have locked does not belong in it.
    """
    now = now or datetime.now().astimezone()
    cutoff = datetime.combine(now.date(), time(hour=cutoff_hour),
                              tzinfo=now.tzinfo)
    if now > cutoff:                       # already past it; the cutoff is tomorrow's
        cutoff += timedelta(days=1)
    early, late = [], []
    for candidate in candidates:
        locks = candidate.locks_at
        (early if locks and locks < cutoff else late).append(candidate)
    return early, late


def explain(candidate, bankroll_cents):
    """Why each leg, what sinks it, what pushes, what to check. Plain sentences."""
    lines = []
    for projection in candidate.projections:
        odds = "" if projection.odds_type == "standard" else f" [{projection.odds_type}]"
        lines.append(f"{projection.player} {projection.side} {projection.line:g} "
                     f"{projection.stat}{odds} — {projection.p_win:.0%} "
                     f"({projection.stress_low:.0%}–{projection.stress_high:.0%}, "
                     f"{projection.confidence} confidence)")
        for reason in projection.reasons:
            lines.append(f"    {reason}")

    weakest = min(candidate.projections, key=lambda p: p.stress_low)
    lines.append(f"what sinks it: {weakest.player} — the worst case any scenario "
                 f"gives it is {weakest.stress_low:.0%}, and one leg takes the "
                 "whole slip down")
    widest = max(candidate.projections, key=lambda p: p.stress_width)
    if widest.stress_width > 0.10:
        lines.append(f"least settled: {widest.player}, {widest.stress_width:.0%} "
                     "between the scenarios — that is what drops this to quarter "
                     "Kelly")

    pushable = [p for p in candidate.projections if float(p.line).is_integer()]
    if pushable:
        names = ", ".join(f"{p.player} {p.line:g}" for p in pushable)
        lines.append(f"can push: {names} — a whole number landing exactly removes "
                     "the leg and the slip shrinks"
                     + (f", and at {candidate.size} legs a shrink leaves one pick, "
                        "which is refunded" if candidate.size == 2 else
                        f", paying as a {candidate.size - 1}-pick"))
    else:
        lines.append("no leg can push: every line is a half point")

    lines.append(f"priced by: {candidate.how}")
    checks = ["read the multiplier off the slip before submitting — this is "
              f"priced at {candidate.multiplier}x"]
    odd_ones = [p for p in candidate.projections if p.odds_type != "standard"]
    if odd_ones:
        checks.append("a demon, goblin or promo leg changes the multiplier: "
                      + ", ".join(f"{p.player} is {p.odds_type}" for p in odd_ones))
    checks.append("confirm every player is starting; a scratch removes the leg "
                  "and shrinks the slip")
    lines.extend(f"check: {check}" for check in checks)
    return tuple(lines)


def build_slate(projections, tracker=None, sims=None, sizes=DEFAULT_SIZES,
                mode="power", min_worst_ev=MIN_WORST_EV, now=None, table=None,
                bankroll_cents=None):
    """The night's slate: an early set and a late set, sized and explained."""
    table = table or (tracker.table if tracker else DEFAULT_TABLE)
    bankroll = (bankroll_cents if bankroll_cents is not None
                else (tracker.balance() if tracker else 0))
    now = now or datetime.now().astimezone()

    policy, policy_reason = (sizing_policy(tracker.slips) if tracker
                             else ("kelly", "no record to judge calibration by"))
    ranked = take_disjoint(search(projections, sims, sizes, mode, table,
                                  min_worst_ev))
    early, late = split_by_lock(ranked, now, LATE_CUTOFF_HOUR)

    staked = tracker.ledger.staked_on(now.date()) if tracker else 0
    for candidate in early + late:
        candidate.stake = recommend(
            candidate.outcomes, bankroll, staked_tonight_cents=staked,
            stress_width=candidate.stress_width, policy=policy,
            policy_reason=policy_reason)
        if candidate.stake.bet:
            staked += candidate.stake.stake_cents
        candidate.breakdown = explain(candidate, bankroll)
    return {"early": early, "late": late, "policy": policy,
            "policy_reason": policy_reason, "bankroll_cents": bankroll,
            "already_staked_cents": (tracker.ledger.staked_on(now.date())
                                     if tracker else 0),
            "considered": len(projections), "cutoff_hour": LATE_CUTOFF_HOUR}


def render(slate):
    """The slate as lines of text."""
    out = [f"bankroll {fmt(slate['bankroll_cents'])}, "
           f"{fmt(slate['already_staked_cents'])} already staked today; "
           f"{slate['considered']} projection(s) considered",
           f"sizing: {slate['policy']} — {slate['policy_reason']}"]
    for name, label in (("early", f"EARLY — locks before "
                                  f"{slate['cutoff_hour']}:00 local"),
                        ("late", "LATE")):
        out.append("")
        out.append(label)
        if not slate[name]:
            out.append(f"  nothing clears +{MIN_WORST_EV:.0%} worst-case EV. "
                       "No slip is the answer, not a shortage of one.")
            continue
        for candidate in slate[name]:
            locks = candidate.locks_at
            out.append(f"  {candidate.size}-pick at {candidate.multiplier}x   "
                       f"worst case {candidate.worst_ev:+.1%} "
                       f"({candidate.worst_scenario}), blend "
                       f"{candidate.blend_ev:+.1%}"
                       + (f", locks {locks:%H:%M}" if locks else ""))
            out.append(f"    stake {fmt(candidate.stake.stake_cents)}"
                       if candidate.stake.bet else "    NO BET")
            for reason in candidate.stake.reasons:
                out.append(f"      {reason}")
            for line in candidate.breakdown:
                out.append(f"    {line}")
            out.append("")
    return out

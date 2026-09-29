"""The row every model emits, and how a set of those rows gets priced.

One shape, so a CS2 projection, an NFL simulation and a tennis Markov chain all
reach the builder as the same thing and can sit on the same slip. The fields are
the build plan's: who, what line, which side, how likely, how confident, when it
locks, and why.

TWO CONVENTIONS THAT ARE EASY TO GET WRONG, both fixed here:

`p_win` and `p_push` are UNCONDITIONAL and sum with the loss to 1. The
prototype in reference/evaluate_v2.py reports the conditional figure instead --
`(over or under) / (1 - push)`, the chance of winning GIVEN no push -- which is
the right number to compare against a break-even and the wrong one to put in an
outcome table. `Projection.from_conditional` converts.

A STRESS RANGE IS NOT A PER-LEG INTERVAL. reference/parlays_v2.py has this
right and it matters: it runs the whole slate under a usage model and a
market-calibrated model and takes `min(p_usage, p_market)` for the slip, not
for each leg. Taking each leg's own low and multiplying them is a corner no
scenario produces -- it assumes every model is simultaneously at its worst and
independently so. So a projection carries `scenarios`, a named probability per
world, and a slip is priced once per world with the worst one ranking it.
"""
import itertools
from dataclasses import dataclass, field, asdict

from .joint import (OPPOSING_TEAMS_CORRELATION, SAME_TEAM_CORRELATION,
                    joint_hit_probability)
from .payouts import DEFAULT_TABLE, LOST, PUSH, WON, net_return
from .sizing import Outcome
from .slips import Leg, SIDES

CONFIDENCE = ("high", "medium", "low")
#: The name a single-scenario projection's probability is filed under.
BLEND = "blend"


@dataclass
class Projection:
    prop_id: str
    sport: str
    player: str
    stat: str
    line: float
    side: str                       # over | under
    team: str = ""
    opponent: str = ""
    maps: int = None
    p_win: float = None             # unconditional, the number to bet on
    p_push: float = 0.0             # unconditional; 0 for any half-point line
    scenarios: dict = field(default_factory=dict)   # name -> unconditional p_win
    confidence: str = "low"
    start_time: str = ""
    odds_type: str = "standard"
    reasons: tuple = ()
    #: Legs sharing this can be priced from per-simulation outcomes rather than
    #: from the correlation model. Usually one game.
    sim_group: str = ""
    model: str = ""

    def __post_init__(self):
        if self.side not in SIDES:
            raise ValueError(f"side must be one of {SIDES}, not {self.side!r}")
        if self.confidence not in CONFIDENCE:
            raise ValueError(f"confidence must be one of {CONFIDENCE}, "
                             f"not {self.confidence!r}")
        if not str(self.prop_id).strip():
            raise ValueError("a projection needs a prop_id")
        self.scenarios = dict(self.scenarios or {})
        if self.p_win is None and self.scenarios:
            # The blend is the mean of the worlds unless one was given.
            self.p_win = sum(self.scenarios.values()) / len(self.scenarios)
        if self.p_win is None:
            raise ValueError(f"{self.prop_id}: no p_win and no scenarios")
        if not self.scenarios:
            self.scenarios = {BLEND: self.p_win}
        self.reasons = tuple(self.reasons or ())
        for name, p in list(self.scenarios.items()) + [("p_win", self.p_win)]:
            if not 0 <= p <= 1:
                raise ValueError(f"{self.prop_id}: {name} is {p}, not a probability")
            if p + self.p_push > 1 + 1e-9:
                raise ValueError(f"{self.prop_id}: {name} {p} + p_push "
                                 f"{self.p_push} exceeds 1")
        if float(self.line).is_integer() is False and self.p_push:
            raise ValueError(f"{self.prop_id}: line {self.line} cannot push, so "
                             f"p_push must be 0, not {self.p_push}")

    @classmethod
    def from_conditional(cls, p_win_given_no_push, p_push=0.0, **kw):
        """Build from the prototype's convention: P(win | no push)."""
        return cls(p_win=p_win_given_no_push * (1 - p_push), p_push=p_push, **kw)

    @property
    def stress_low(self):
        return min(self.scenarios.values())

    @property
    def stress_high(self):
        return max(self.scenarios.values())

    @property
    def stress_width(self):
        return self.stress_high - self.stress_low

    @property
    def match_key(self):
        """The fixture, unordered, so both sides of it group together."""
        pair = tuple(sorted(((self.team or "?").strip().lower(),
                             (self.opponent or "?").strip().lower())))
        return f"{self.sport}:{pair[0]}:{pair[1]}"

    @property
    def team_key(self):
        if self.sport == "tennis":
            return f"tennis:{self.player.strip().lower()}"
        return f"{self.sport}:{(self.team or '').strip().lower()}"

    @property
    def player_key(self):
        return f"{self.sport}:{self.player.strip().lower()}"

    def to_leg(self):
        """The tracker's Leg, for when the slip is actually placed."""
        return Leg(sport=self.sport, player=self.player, team=self.team,
                   opponent=self.opponent, stat=self.stat, line=self.line,
                   side=self.side, odds_type=self.odds_type, maps=self.maps,
                   model_prob=self.p_win, start_time=self.start_time)

    def hit(self, value):
        """What a simulated or actual stat line does to this leg."""
        if float(self.line).is_integer() and float(value) == float(self.line):
            return PUSH
        if self.side == "over":
            return WON if value > self.line else LOST
        return WON if value < self.line else LOST

    def as_json(self):
        blob = asdict(self)
        blob["reasons"] = list(self.reasons)
        return blob

    @classmethod
    def from_json(cls, blob):
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in blob.items() if k in known})


class Simulations:
    """Per-simulation stat lines, when a model has them.

    A game simulator knows more than a probability per leg: it knows which
    simulations produced which combination, which IS the joint distribution. Two
    legs on one drive, or one map, move together in a way no correlation
    constant describes, so where these exist they are used and the correlation
    model is not.

    Stored as {scenario: {prop_id: (value, value, ...)}}, every prop in a
    scenario sharing a simulation index -- sims[s][a][i] and sims[s][b][i] are
    the same simulated game.
    """

    def __init__(self):
        self.by_scenario = {}

    def add(self, scenario, prop_id, values):
        values = tuple(float(v) for v in values)
        existing = self.by_scenario.setdefault(scenario, {})
        if existing and len(next(iter(existing.values()))) != len(values):
            raise ValueError(
                f"{scenario}/{prop_id}: {len(values)} simulations against "
                f"{len(next(iter(existing.values())))} already stored — they have "
                "to be the same run, or index i is not the same game")
        existing[prop_id] = values
        return self

    def covers(self, scenario, projections):
        have = self.by_scenario.get(scenario) or {}
        return bool(have) and all(p.prop_id in have for p in projections)

    def scenarios(self):
        return sorted(self.by_scenario)

    def count(self, scenario):
        have = self.by_scenario.get(scenario) or {}
        return len(next(iter(have.values()))) if have else 0


# ============================================================
# Pricing a set of projections
# ============================================================

def _merge(counts):
    return [Outcome(p, net, label) for label, (p, net) in
            sorted(counts.items(), key=lambda kv: -kv[1][1])]


def table_from_simulations(projections, sims, scenario, mode="power",
                           printed_multiplier=None, table=None):
    """The outcome table counted straight off the simulations.

    Exact, in the sense that whatever correlation the simulator has is carried
    through without being modelled twice: the legs are evaluated together, one
    simulated game at a time.
    """
    values = sims.by_scenario.get(scenario) or {}
    missing = [p.prop_id for p in projections if p.prop_id not in values]
    if missing:
        raise KeyError(f"{scenario} has no simulations for {missing}")
    runs = len(values[projections[0].prop_id])
    counts = {}
    for i in range(runs):
        states = [p.hit(values[p.prop_id][i]) for p in projections]
        net, label = net_return(states, mode, len(projections),
                                printed_multiplier, table)
        entry = counts.setdefault(label, [0.0, net])
        entry[0] += 1 / runs
    return _merge(counts)


def table_from_correlation(projections, scenario=None, mode="power",
                           printed_multiplier=None, table=None,
                           rho_team=SAME_TEAM_CORRELATION,
                           rho_fixture=OPPOSING_TEAMS_CORRELATION):
    """The outcome table with the measured same-match correlation applied.

    For a power slip, which is the only shape where "everything that survived
    won" is the whole question -- so a single joint probability answers it, and
    that is what propedge/joint.py computes.

    Pushes are enumerated independently. They are rare (only a whole-number line
    can push) and small, and the correlation between two legs BOTH landing
    exactly on their number is not something the record can measure. That is an
    approximation and it is the only one here.
    """
    if mode != "power":
        raise ValueError("only a power slip reduces to one joint probability; "
                         "price a flex slip from simulations")
    size = len(projections)
    p_win = [(p.scenarios.get(scenario, p.p_win) if scenario else p.p_win)
             for p in projections]
    counts = {}
    for pattern in itertools.product([False, True], repeat=size):
        probability = 1.0
        for pushes, projection in zip(pattern, projections):
            probability *= projection.p_push if pushes else 1 - projection.p_push
        if probability <= 0:
            continue
        survivors = [(projection, win) for pushes, projection, win
                     in zip(pattern, projections, p_win) if not pushes]
        states = [PUSH if pushes else WON for pushes in pattern]
        if len(survivors) <= 1:
            net, label = net_return(states, mode, size, printed_multiplier, table)
            entry = counts.setdefault(label, [0.0, net])
            entry[0] += probability
            continue
        # Conditional on not pushing, which is what the joint model prices.
        legs = [{"p": min(max(win / (1 - projection.p_push), 1e-9), 1 - 1e-9),
                 "matchKey": projection.match_key, "teamKey": projection.team_key}
                for projection, win in survivors]
        all_win = joint_hit_probability(legs, rho_team, rho_fixture)
        if all_win is None:
            raise ValueError("a leg's probability is outside (0, 1), so the "
                             "joint model refused it")
        won_net, won_label = net_return(states, mode, size, printed_multiplier, table)
        lost_states = [PUSH if pushes else WON for pushes in pattern]
        lost_states[next(i for i, pushes in enumerate(pattern) if not pushes)] = LOST
        lost_net, lost_label = net_return(lost_states, mode, size,
                                          printed_multiplier, table)
        for label, net, mass in ((won_label, won_net, all_win),
                                 (lost_label, lost_net, 1 - all_win)):
            entry = counts.setdefault(label, [0.0, net])
            entry[0] += probability * mass
    return _merge(counts)


def price(projections, scenario, sims=None, mode="power", printed_multiplier=None,
          table=None):
    """(outcomes, how) -- simulations where they exist, correlation otherwise."""
    if sims is not None and sims.covers(scenario, projections):
        return (table_from_simulations(projections, sims, scenario, mode,
                                       printed_multiplier, table),
                f"{sims.count(scenario)} simulations")
    return (table_from_correlation(projections, scenario, mode,
                                   printed_multiplier, table),
            "measured same-match correlation")

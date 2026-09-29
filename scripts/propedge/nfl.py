"""An NFL game simulator that emits per-simulation outcomes, not just numbers.

Ported from reference/mnf_model_v2/model_v2.py, which was written for one game
-- Eagles at Bears on 2026-09-28 -- with the teams, the quarterback who might be
pulled, and the sign of the spread all baked into the code. This is the same
model with those pulled out into a GameSetup, so it runs for any fixture.

WHY IT MATTERS THAT IT SIMULATES. Everything else in this project prices a
parlay through a measured correlation constant, because a kills projection is a
number and a number cannot tell you how two legs move together. A game
simulation can: leg A and leg B are evaluated in the same simulated game, so a
slip on two receivers sharing one quarterback's attempts is priced from the
games where both got fed, not from an average. Phase 3's builder already prefers
`Simulations` over the correlation model wherever they exist -- this is the
first thing that produces them.

PURE STDLIB, DELIBERATELY. The prototype is numpy and reads beautifully as
vectorised code. This package has no dependencies and adding one for a model
that has not yet proven itself is the wrong trade, so the vectorised draws
became loops. The cost is sim count: 20,000 runs rather than 100,000. That is
not a meaningful loss of precision -- the standard error on a probability near
one half is sqrt(0.25/20000) = 0.35 of a point, against 0.16 at 100,000 -- and
both are far below the error in the usage assumptions feeding it.

NOTHING HERE IS CALIBRATED OUT OF THE BOX. The prototype's BASE numbers are
hand-set for one game from two weeks of 2026 usage, and `calibrate` exists
because they have to be tuned to the market before they mean anything. A
GameSetup built from guesses produces confident nonsense, which is why
`Projection`s made from it are marked low confidence until calibration has run.
"""
import math
import random
from dataclasses import asdict, dataclass, field, replace

from .model import Projection, Simulations

#: The stats the simulator produces, in its own names. PrizePicks spells several
#: of them differently; see STAT_ALIASES.
PASS_ATTEMPTS, PASS_YARDS, PASS_COMP = "Pass Attempts", "Pass Yards", "Pass Comp"
RUSH_ATTEMPTS, RUSH_YARDS = "Rush Atts", "Rush Yards"
TARGETS, RECEPTIONS, REC_YARDS = "Rec Targets", "Recs", "Rec Yards"
COMBINED_YARDS = "Rush+Rec Yds"

#: What a board is likely to call each of them. Matched case-insensitively.
STAT_ALIASES = {
    "pass attempts": PASS_ATTEMPTS, "passing attempts": PASS_ATTEMPTS,
    "pass yards": PASS_YARDS, "passing yards": PASS_YARDS,
    "pass completions": PASS_COMP, "completions": PASS_COMP,
    "rush attempts": RUSH_ATTEMPTS, "rush atts": RUSH_ATTEMPTS,
    "carries": RUSH_ATTEMPTS,
    "rush yards": RUSH_YARDS, "rushing yards": RUSH_YARDS,
    "targets": TARGETS, "rec targets": TARGETS,
    "receptions": RECEPTIONS, "recs": RECEPTIONS,
    "receiving yards": REC_YARDS, "rec yards": REC_YARDS,
    "rush+rec yds": COMBINED_YARDS, "rush + rec yards": COMBINED_YARDS,
    "rushing + receiving yards": COMBINED_YARDS,
}
#: The share of the ball nobody on the board gets. Kept in the draw so the
#: shares are a real simplex, dropped before any player's line is priced.
OTHER = "other"
DEFAULT_RUNS = 20_000


def stat_key(name):
    return STAT_ALIASES.get(str(name).strip().lower(), str(name).strip())


@dataclass
class TeamSetup:
    """One team's usage, before the game script moves any of it."""
    code: str
    quarterback: str
    plays: float = 62.0
    plays_sd: float = 6.0
    pass_rate: float = 0.5
    #: How much the pass rate moves per point of margin. Trailing teams throw.
    script_k: float = 0.006
    completion_rate: float = 0.64
    sack_rate: float = 0.075
    qb_rush_attempts: float = 3.0            # Poisson mean
    qb_rush_ypc: float = 4.0
    rushers: dict = field(default_factory=dict)   # name -> share of carries
    targets: dict = field(default_factory=dict)   # name -> share of targets
    #: Probability this team's starting quarterback is pulled, and how much of
    #: the workload survives if he is. The prototype hardcoded one team's.
    qb_pull_probability: float = 0.0

    def players(self):
        return (set(self.rushers) | set(self.targets) | {self.quarterback}) - {OTHER}


@dataclass
class GameSetup:
    home: TeamSetup
    away: TeamSetup
    #: The HOME team's spread in points, negative when home is favoured, which
    #: is how a book prints it. The prototype carried one team's spread under
    #: that team's name, which is exactly the sort of thing that flips a sign
    #: when the model meets its second fixture.
    home_spread: float = 0.0
    margin_sd: float = 13.0
    #: Dirichlet concentration: higher means shares vary less game to game.
    concentration: float = 35.0
    exit_probability: dict = field(default_factory=dict)   # name -> P(early exit)
    ypc: dict = field(default_factory=dict)                # name -> yards per carry
    catch_rate: dict = field(default_factory=dict)         # name -> catch rate
    ypr: dict = field(default_factory=dict)                # name -> yards per reception
    kneel_probability: float = 0.85
    calibrated: bool = False

    def teams(self):
        return (self.home, self.away)

    def as_json(self):
        return {"home": asdict(self.home), "away": asdict(self.away),
                "home_spread": self.home_spread, "margin_sd": self.margin_sd,
                "concentration": self.concentration,
                "exit_probability": dict(self.exit_probability),
                "ypc": dict(self.ypc), "catch_rate": dict(self.catch_rate),
                "ypr": dict(self.ypr), "kneel_probability": self.kneel_probability,
                "calibrated": self.calibrated}

    @classmethod
    def from_json(cls, blob):
        known = {f for f in TeamSetup.__dataclass_fields__}
        def team(side):
            raw = blob[side]
            return TeamSetup(**{k: v for k, v in raw.items() if k in known})
        mine = {f for f in cls.__dataclass_fields__} - {"home", "away"}
        return cls(home=team("home"), away=team("away"),
                   **{k: v for k, v in blob.items() if k in mine})

    def team_of(self, player):
        for team in self.teams():
            if player == team.quarterback or player in team.rushers \
                    or player in team.targets:
                return team
        return None


# ============================================================
# Draws the standard library does not have
# ============================================================

def _binomial(rng, n, p):
    """Inverse transform, which is fast exactly where this model needs it.

    n is a play count (under 90) and p is usually one player's share, so n*p is
    a handful and the loop runs a handful of times. It falls back to a normal
    approximation when n*p is large enough for that to matter, which here is
    only the dropback and attempt counts.
    """
    n = int(n)
    if n <= 0 or p <= 0:
        return 0
    if p >= 1:
        return n
    if n * p > 30 and n * (1 - p) > 30:
        mean, sd = n * p, math.sqrt(n * p * (1 - p))
        return max(0, min(n, int(round(rng.gauss(mean, sd)))))
    target = rng.random()
    pmf = (1 - p) ** n
    total = pmf
    for k in range(n):
        if total >= target:
            return k
        pmf *= (n - k) / (k + 1) * (p / (1 - p))
        total += pmf
    return n


def _poisson(rng, mean):
    """Knuth, which is fine for the small means this model uses."""
    if mean <= 0:
        return 0
    if mean > 30:
        return max(0, int(round(rng.gauss(mean, math.sqrt(mean)))))
    target, total, k = math.exp(-mean), rng.random(), 0
    cumulative = target
    while cumulative < total:
        k += 1
        target *= mean / k
        cumulative += target
        if k > 1000:
            break
    return k


def _dirichlet(rng, weights):
    draws = [rng.gammavariate(max(w, 1e-9), 1.0) for w in weights]
    total = sum(draws) or 1.0
    return [d / total for d in draws]


def _multinomial(rng, count, shares):
    """Split `count` between shares, by sequential conditional binomials.

    Sequential rather than independent binomials per share: drawing each
    player's count independently would not add up to the attempts the team
    actually had, and the whole point of sharing one quarterback's attempts is
    that they are a fixed pot.
    """
    out, left, remaining = [], int(count), 1.0
    for share in shares:
        if left <= 0 or remaining <= 0:
            out.append(0)
            continue
        drawn = _binomial(rng, left, min(1.0, share / remaining))
        out.append(drawn)
        left -= drawn
        remaining -= share
    return out


def _carry_yards(rng, count, ypc):
    """Carries: a normal body with a fat right tail for the breakaway run."""
    if count <= 0:
        return 0.0
    base = ypc - 1.44          # the prototype's offset; the tail carries the rest
    total = 0.0
    for _ in range(int(count)):
        value = rng.gauss(base, 3.2)
        if rng.random() < 0.12:
            value += rng.expovariate(1 / 12)
        else:
            value = max(value, -4)
        total += value
    return total


def _reception_yards(rng, count, mean, cv=0.85):
    """Receptions: lognormal, because a catch cannot go far backwards and can go
    a very long way forwards."""
    if count <= 0:
        return 0.0
    spread = math.log(1 + cv * cv)
    mu = math.log(max(mean, 0.1)) - spread / 2
    sigma = math.sqrt(spread)
    return sum(math.exp(rng.gauss(mu, sigma)) for _ in range(int(count)))


# ============================================================
# The simulation
# ============================================================

def simulate(game, runs=DEFAULT_RUNS, seed=1):
    """{player: {stat: [value per run]}}, plus "_margin" for the home team.

    Every player's every stat is indexed by the same run, which is the whole
    point: index i is one simulated game.
    """
    rng = random.Random(seed)
    out = {}

    def add(player, stat, index, value):
        out.setdefault(player, {}).setdefault(stat, [0.0] * runs)
        out[player][stat][index] += value

    margins = [rng.gauss(game.home_spread, game.margin_sd) for _ in range(runs)]
    for team in game.teams():
        is_home = team is game.home
        for i in range(runs):
            # `lead` is how far THIS team is ahead, which is what the game
            # script responds to. home_spread follows the book -- negative when
            # the home team is favoured -- so a negative margin is the home team
            # winning and its lead is the margin negated. The prototype carried
            # the opposite convention (positive meant that team winning) and
            # porting one without the other had winning teams throwing MORE,
            # which is backwards and is what test_trailing_teams_throw_more
            # exists to catch.
            lead = -margins[i] if is_home else margins[i]
            plays = rng.gauss(team.plays, team.plays_sd) + 0.08 * lead
            plays = int(max(40, min(85, round(plays))))
            pass_rate = team.pass_rate - team.script_k * lead + rng.gauss(0, 0.05)
            pass_rate = max(0.3, min(0.8, pass_rate))

            dropbacks = _binomial(rng, plays, pass_rate)
            runs_called = plays - dropbacks
            attempts = _binomial(rng, dropbacks, 1 - team.sack_rate)
            qb_runs = min(_poisson(rng, team.qb_rush_attempts)
                          + (dropbacks - attempts) // 2, runs_called)
            back_runs = runs_called - qb_runs
            completion = max(0.35, min(0.9, rng.gauss(team.completion_rate, 0.06)))

            survives = 1.0
            if team.qb_pull_probability and rng.random() < team.qb_pull_probability:
                survives = rng.uniform(0.3, 0.8)

            add(team.quarterback, PASS_ATTEMPTS, i, round(attempts * survives))
            add(team.quarterback, RUSH_ATTEMPTS, i, round(qb_runs * survives))
            add(team.quarterback, RUSH_YARDS, i,
                _carry_yards(rng, round(qb_runs * survives), team.qb_rush_ypc))

            # An early exit scales one player's share down; the rest absorb it
            # because the shares are renormalised afterwards.
            available = {}
            for player in team.players():
                chance = game.exit_probability.get(player, 0.0)
                available[player] = (rng.uniform(0.1, 0.9)
                                     if chance and rng.random() < chance else 1.0)

            for kind, weights in (("run", team.rushers), ("target", team.targets)):
                if not weights:
                    continue
                names = list(weights)
                total_weight = sum(weights.values()) or 1.0
                shares = _dirichlet(rng, [weights[n] / total_weight * game.concentration
                                          for n in names])
                shares = [s * available.get(n, 1.0) for n, s in zip(names, shares)]
                scale = sum(shares) or 1.0
                shares = [s / scale for s in shares]
                pot = back_runs if kind == "run" else attempts
                counts = _multinomial(rng, pot, shares)
                if kind == "run":
                    for name, count in zip(names, counts):
                        if name == OTHER:
                            continue
                        add(name, RUSH_ATTEMPTS, i, count)
                        add(name, RUSH_YARDS, i,
                            _carry_yards(rng, count, game.ypc.get(name, 4.0)))
                else:
                    pass_yards = completions = 0.0
                    for name, count in zip(names, counts):
                        rate = game.catch_rate.get(name, 0.65) + (completion
                                                                  - team.completion_rate)
                        caught = _binomial(rng, count, max(0.2, min(0.97, rate)))
                        yards = _reception_yards(rng, caught, game.ypr.get(name, 10.0))
                        pass_yards += yards
                        completions += caught
                        if name == OTHER:
                            continue
                        add(name, TARGETS, i, count)
                        add(name, RECEPTIONS, i, caught)
                        add(name, REC_YARDS, i, yards)
                    add(team.quarterback, PASS_YARDS, i, round(pass_yards * survives))
                    add(team.quarterback, PASS_COMP, i, round(completions * survives))

    # Victory-formation kneels are QB rush attempts at about a yard lost each,
    # and they are why a winning quarterback's rushing line is not his running.
    for team in game.teams():
        winning = [i for i in range(runs)
                   if (margins[i] < 0) == (team is game.home)]
        for i in winning:
            if rng.random() < game.kneel_probability:
                kneels = rng.randint(1, 3)
                add(team.quarterback, RUSH_ATTEMPTS, i, kneels)
                add(team.quarterback, RUSH_YARDS, i, -kneels)

    for player, stats in out.items():
        if RUSH_YARDS in stats or REC_YARDS in stats:
            rush = stats.get(RUSH_YARDS) or [0.0] * runs
            rec = stats.get(REC_YARDS) or [0.0] * runs
            stats[COMBINED_YARDS] = [a + b for a, b in zip(rush, rec)]
    out["_margin"] = {"home_margin": margins}
    return out


def probability_over(values, line):
    """P(over) with the push split, which is how a book reads a whole number."""
    if not values:
        return None
    over = sum(1 for v in values if v > line)
    push = sum(1 for v in values if v == line)
    return (over + 0.5 * push) / len(values)


# ============================================================
# Calibration to the market
# ============================================================

def calibrate(game, market, rounds=14, runs=20_000, seed=7, report=None):
    """Nudge usage and efficiency until the model's 50/50 point sits on the line.

    `market` is [(player, stat, line)] from a sportsbook, vig removed. A book's
    line IS its 50/50 point, so a model that disagrees with it is claiming to
    know more than the market about a number the market prices for a living --
    which is a claim worth making only where usage explains it, and never by
    accident.

    The update is the prototype's: multiplicative, per stat, with the step
    halved late. Deliberately not a gradient method -- the objective is a Monte
    Carlo estimate and its noise would swamp any derivative.
    """
    tuned = replace(game, ypc=dict(game.ypc), catch_rate=dict(game.catch_rate),
                    ypr=dict(game.ypr),
                    home=replace(game.home, rushers=dict(game.home.rushers),
                                 targets=dict(game.home.targets)),
                    away=replace(game.away, rushers=dict(game.away.rushers),
                                 targets=dict(game.away.targets)))
    history = []
    for round_number in range(rounds):
        simulated = simulate(tuned, runs, seed=seed + round_number)
        errors = []
        step = 0.9 if round_number < rounds * 0.6 else 0.5
        for player, stat, line in market:
            stat = stat_key(stat)
            values = (simulated.get(player) or {}).get(stat)
            if not values:
                continue
            drift = 0.5 - probability_over(values, line)
            errors.append(abs(drift))
            team = tuned.team_of(player)
            if team is None:
                continue
            if stat == RECEPTIONS and player in team.targets:
                team.targets[player] *= math.exp(step * drift * 1.6)
            elif stat == REC_YARDS:
                tuned.ypr[player] = tuned.ypr.get(player, 10.0) * math.exp(
                    step * drift * 1.2)
            elif stat == RUSH_YARDS and player == team.quarterback:
                team.qb_rush_ypc *= math.exp(step * drift * 0.6)
            elif stat == RUSH_YARDS:
                tuned.ypc[player] = tuned.ypc.get(player, 4.0) * math.exp(
                    step * drift * 0.5)
                if player in team.rushers:
                    team.rushers[player] *= math.exp(step * drift * 0.8)
            elif stat == PASS_YARDS:
                team.completion_rate = max(0.5, min(0.75, team.completion_rate
                                                    + step * drift * 0.08))
        if not errors:
            raise ValueError("no market line matched a player and stat the model "
                             "produces — check the names against GameSetup")
        row = {"round": round_number, "mean_error": sum(errors) / len(errors),
               "max_error": max(errors), "lines": len(errors)}
        history.append(row)
        if report:
            report(row)
    tuned.calibrated = True
    return tuned, history


# ============================================================
# Board -> projections the builder can use
# ============================================================

def projections_from(board, usage, market=None, market_weight=0.6,
                     scenario_names=("usage", "market"), game_id="nfl"):
    """(projections, simulations) for a posted board.

    `usage` and `market` are both simulate() outputs -- the model on its own
    assumptions, and the model bent to the sportsbook's lines. Both are carried
    as scenarios, so a prop the two disagree about is ranked by the pessimistic
    one, and the blend is what `p_win` reports.

    A prop only the usage model has an opinion on -- no market line was supplied
    for that player and stat, so calibration never touched it -- is flagged in
    its reasons and dropped to low confidence rather than quietly presented as
    if the market agreed.
    """
    projections, sims = [], Simulations()
    usage_name, market_name = scenario_names
    for row in board:
        player = row.get("player")
        stat = stat_key(row.get("stat"))
        line = row.get("line")
        if line is None or not player:
            continue
        usage_values = (usage.get(player) or {}).get(stat)
        if not usage_values:
            continue
        market_values = ((market or {}).get(player) or {}).get(stat)
        p_over_usage = probability_over(usage_values, line)
        p_over = p_over_usage
        scenarios = {}
        if market_values:
            p_over_market = probability_over(market_values, line)
            p_over = market_weight * p_over_market + (1 - market_weight) * p_over_usage
        push = (sum(1 for v in usage_values if v == line) / len(usage_values)
                if float(line).is_integer() else 0.0)
        side = "over" if p_over >= 0.5 else "under"
        decided = max(1e-9, 1 - push)

        def as_win(p_over_value):
            raw = p_over_value if side == "over" else 1 - p_over_value
            # p_over already splits the push; rescale to an unconditional win.
            return max(0.0, min(decided, raw * decided / max(decided, 1e-9)))

        scenarios[usage_name] = as_win(p_over_usage)
        if market_values:
            scenarios[market_name] = as_win(p_over_market)
        def leans(p_over_value):
            """Which side a scenario prefers, and how strongly -- said against
            the side actually picked, so a scenario that disagrees reads as a
            disagreement rather than as support at a strange number."""
            prefers = "over" if p_over_value >= 0.5 else "under"
            strength = max(p_over_value, 1 - p_over_value)
            return (f"{strength:.0%} {prefers}"
                    + ("" if prefers == side else f", against this {side} pick"))

        reasons = [f"{len(usage_values)} simulated games; usage model says "
                   + leans(p_over_usage)]
        confidence = "medium"
        if market_values:
            reasons.append(f"market-calibrated model says {leans(p_over_market)}; "
                           f"blended {market_weight:.0%} market / "
                           f"{1 - market_weight:.0%} usage")
            if (p_over_market - 0.5) * (p_over_usage - 0.5) < 0:
                reasons.append("the two models take OPPOSITE sides, so this is a "
                               "coin flip dressed as a pick — the worst case is "
                               "what ranks it")
            elif abs(p_over_market - p_over_usage) > 0.10:
                reasons.append("the two disagree by more than ten points, so the "
                               "worst case is what ranks this")
        else:
            confidence = "low"
            reasons.append("NO MARKET LINE for this prop, so only the usage model "
                           "has an opinion and calibration never touched it — "
                           "treat it as a guess with a number attached")
        made = Projection(
            prop_id=row.get("prop_id") or
            f"nfl:{str(player).lower()}:{stat}:{line}:{row.get('start_time','')}",
            sport="nfl", player=player, team=row.get("team") or "",
            opponent=row.get("opponent") or "", stat=stat, line=line, side=side,
            p_win=as_win(p_over), p_push=push, scenarios=scenarios,
            confidence=confidence, start_time=row.get("start_time") or "",
            odds_type=row.get("odds_type") or "standard",
            model="propedge.nfl", reasons=tuple(reasons), sim_group=game_id)
        projections.append(made)
        # The builder grades a simulation by comparing it against the line, so
        # what it needs is the SIMULATED STAT, not a win flag.
        sims.add(usage_name, made.prop_id, usage_values)
        if market_values:
            sims.add(market_name, made.prop_id, market_values)
    return projections, sims

"""Best-of-three tennis, exactly where it can be and simulated where it cannot.

Two prototypes came in for this. reference/tennis_exact.py is an exact Markov
chain over a match -- the full joint distribution of games, sets and the first
set -- and reference/tennis_model.py is a point-level Monte Carlo that also
produces aces, double faults and break points. Both are numpy; this package has
no dependencies, and the exact one turns out not to need any: a hold probability
is closed form and the rest is small dynamic programmes over dictionaries.

So the split here is by what each is actually good for:

  * games, sets, first-set and match-win props come from the EXACT chain. No
    Monte Carlo error at all, which matters because these are the props anchored
    to the market and a fit that chases sampling noise fits noise.
  * aces, double faults, break points and the fantasy score come from a point
    simulation, because they are properties of individual points that the game
    chain has integrated away.

THE WARNING THE PROTOTYPE WROTE DOWN, AND WHY IT IS ENFORCED HERE. An
independent-points model overstates how often a big server holds: real service
games are correlated, a server who loses the first two points is in a different
game from the average one. That means the model UNDERSTATES breaks, and break
props are the ones it will therefore price wrong in a predictable direction.
`break_rate_check` compares the model's breaks per match against a player's
real rate and refuses to price the prop when they disagree past a tolerance,
rather than shipping a number with a warning attached to it.

FANTASY SCORING is the build plan's, which is not the prototype's: match played
10, game won +1, game lost -1, set won +3, set lost -3, ace +1, double fault -1.
reference/tennis_model.py's docstring says aces and double faults are worth half
a point while its own code scores them at one, so it disagrees with itself; the
plan is the authority and the code follows the plan.

EACH PLAYER IS THEIR OWN TEAM, which the slip rules already know -- a two-leg
slip on both players in one match is legal on PrizePicks and is also two legs on
one match, so it gets the same-fixture correlation treatment automatically.
"""
import math
import random
from dataclasses import dataclass, field
from functools import lru_cache

from .model import Projection

#: Serve-level bands by tour: outside these a fit has wandered somewhere no
#: professional match lives, and the answer is "no fit" rather than a number.
SERVE_BANDS = {"ATP": (0.50, 0.80), "WTA": (0.42, 0.72)}
#: Typical rates, used as a starting point and where a player has no measurement.
TOUR_DEFAULTS = {"ATP": {"serve": 0.64, "ace": 0.075, "df": 0.030},
                 "WTA": {"serve": 0.56, "ace": 0.035, "df": 0.045}}

#: The build plan's fantasy scoring. reference/tennis_model.py's docstring says
#: half a point for an ace and its code says one; the plan says one.
FANTASY_PLAYED = 10
FANTASY_GAME_WON, FANTASY_GAME_LOST = 1, -1
FANTASY_SET_WON, FANTASY_SET_LOST = 3, -3
FANTASY_ACE, FANTASY_DOUBLE_FAULT = 1, -1

#: Match-to-match form noise on the serve gap and level, from
#: reference/run_tennis2.py. A player is not the same player every week and a
#: distribution that pretends otherwise is too confident at the tails.
FORM_NOISE_GAP = 0.045
FORM_NOISE_LEVEL = 0.02
#: Five-point Gauss-Hermite (probabilists'), normalised to weights summing to 1.
#: Hardcoded because it is a fixed constant and importing numpy for five numbers
#: would be the whole dependency argument lost over nothing.
HERMITE_NODES = (-2.856970013872805, -1.355626179974265, 0.0,
                 1.355626179974265, 2.856970013872805)
HERMITE_WEIGHTS = (0.011257411327721, 0.222075922005613, 0.533333333333333,
                   0.222075922005613, 0.011257411327721)

#: What a distribution can be asked about.
TOTAL_GAMES = "total_games"
GAMES_WON = "games_won"
FIRST_SET_TOTAL = "first_set_total"
FIRST_SET_GAMES_WON = "first_set_games_won"
MATCH_WIN = "match_win"
KINDS = (TOTAL_GAMES, GAMES_WON, FIRST_SET_TOTAL, FIRST_SET_GAMES_WON, MATCH_WIN)


# ============================================================
# The exact chain
# ============================================================

def hold_probability(p):
    """P(server wins a game) from P(server wins a point), closed form.

    The deuce term is the geometric sum: from 40-40 the server needs two in a
    row before the returner does, which is p^2 / (p^2 + q^2).
    """
    q = 1 - p
    return p ** 4 * (1 + 4 * q + 10 * q * q) + 20 * p ** 3 * q ** 3 * (
        p * p / (p * p + q * q))


@lru_cache(maxsize=4096)
def tiebreak_win(p_a, p_b, a_serves_first):
    """P(A wins a tiebreak), by dynamic programme over points.

    The serving pattern is the fiddly part: A, B, B, A, A, B, B -- one point then
    alternating pairs -- so who serves point k is ((k + 1) // 2) % 2.
    """
    states = {(0, 0): 1.0}
    won = 0.0
    while states:
        nxt = {}
        for (a, b), mass in states.items():
            k = a + b
            serving_a = ((k + 1) // 2) % 2 == 0
            if not a_serves_first:
                serving_a = not serving_a
            point = p_a if serving_a else 1 - p_b
            for scored_a, chance in ((True, point), (False, 1 - point)):
                na, nb = (a + 1, b) if scored_a else (a, b + 1)
                if max(na, nb) >= 7 and abs(na - nb) >= 2:
                    if na > nb:
                        won += mass * chance
                elif na > 40 or nb > 40:            # cannot happen; a guard
                    continue
                else:
                    nxt[(na, nb)] = nxt.get((na, nb), 0.0) + mass * chance
        states = nxt
    return won


@lru_cache(maxsize=4096)
def set_distribution(p_a, p_b, a_serves_first):
    """{(games_a, games_b, a_serves_first_next_set): probability} for one set.

    Who serves first in the NEXT set is carried because it is not free
    information: it is whoever did not serve the last game, and over a
    three-set match that alternation is worth about a game.
    """
    hold_a, hold_b = hold_probability(p_a), hold_probability(p_b)
    breaker = tiebreak_win(p_a, p_b, a_serves_first)
    finished, states = {}, {(0, 0): 1.0}
    for _ in range(14):
        nxt = {}
        for (a, b), mass in states.items():
            k = a + b
            serving_a = (k % 2 == 0) == a_serves_first
            if a == 6 and b == 6:
                serves_next = ((k + 1) % 2 == 0)
                for score, chance in (((7, 6), breaker), ((6, 7), 1 - breaker)):
                    key = (score[0], score[1], serves_next)
                    finished[key] = finished.get(key, 0.0) + mass * chance
                continue
            point = hold_a if serving_a else 1 - hold_b
            for won_a, chance in ((True, point), (False, 1 - point)):
                na, nb = (a + 1, b) if won_a else (a, b + 1)
                over = ((max(na, nb) >= 6 and abs(na - nb) >= 2)
                        or na == 7 or nb == 7)
                if over:
                    serves_next = (((na + nb) % 2 == 0) == a_serves_first)
                    key = (na, nb, serves_next)
                    finished[key] = finished.get(key, 0.0) + mass * chance
                else:
                    nxt[(na, nb)] = nxt.get((na, nb), 0.0) + mass * chance
        states = nxt
        if not states:
            break
    return tuple(sorted(finished.items()))


@lru_cache(maxsize=512)
def match_distribution(p_a, p_b):
    """{(games_a, games_b, sets_a, sets_b, set1_a, set1_b): probability}.

    Averaged over who serves first, because nobody prices that and it is a coin
    toss. A third set is only played when the first two split, which is where
    the total-games distribution gets its two humps.
    """
    out = {}
    for a_first, weight in ((True, 0.5), (False, 0.5)):
        for (g1a, g1b, a_first_2), p1 in set_distribution(p_a, p_b, a_first):
            set1_a = int(g1a > g1b)
            for (g2a, g2b, a_first_3), p2 in set_distribution(p_a, p_b, a_first_2):
                set2_a = int(g2a > g2b)
                if set1_a == set2_a:
                    key = (g1a + g2a, g1b + g2b, 2 * set1_a, 2 * (1 - set1_a),
                           g1a, g1b)
                    out[key] = out.get(key, 0.0) + weight * p1 * p2
                    continue
                for (g3a, g3b, _), p3 in set_distribution(p_a, p_b, a_first_3):
                    set3_a = int(g3a > g3b)
                    key = (g1a + g2a + g3a, g1b + g2b + g3b, 1 + set3_a,
                           1 + (1 - set3_a), g1a, g1b)
                    out[key] = out.get(key, 0.0) + weight * p1 * p2 * p3
    total = sum(out.values()) or 1.0
    return tuple((key, mass / total) for key, mass in sorted(out.items()))


def _value(key, kind, who):
    games_a, games_b, sets_a, sets_b, set1_a, set1_b = key
    if kind == TOTAL_GAMES:
        return games_a + games_b
    if kind == GAMES_WON:
        return games_a if who == 0 else games_b
    if kind == FIRST_SET_TOTAL:
        return set1_a + set1_b
    if kind == FIRST_SET_GAMES_WON:
        return set1_a if who == 0 else set1_b
    raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")


def probability_over(distribution, kind, line, who=0):
    """(p_over, p_push). A whole line pushes; a half line cannot."""
    if kind == MATCH_WIN:
        wanted = 2 if who == 0 else 2
        index = 2 if who == 0 else 3
        return (sum(mass for key, mass in distribution if key[index] == wanted), 0.0)
    over = push = 0.0
    for key, mass in distribution:
        value = _value(key, kind, who)
        if value > line:
            over += mass
        elif value == line:
            push += mass
    return (over, push)


def with_form_noise(p_a, p_b, gap_noise=FORM_NOISE_GAP, level_noise=FORM_NOISE_LEVEL,
                    band=None):
    """[(p_a, p_b, weight)] over match-to-match form.

    A player is not the same player every week. Without this the tails are too
    thin: every extreme total-games outcome needs both players to have been
    exactly as good as their average, and they never are.
    """
    level, gap = (p_a + p_b) / 2, p_a - p_b
    low, high = band or (0.30, 0.90)
    out = []
    for node_gap, weight_gap in zip(HERMITE_NODES, HERMITE_WEIGHTS):
        for node_level, weight_level in zip(HERMITE_NODES, HERMITE_WEIGHTS):
            shifted_gap = gap + gap_noise * node_gap
            shifted_level = level + level_noise * node_level
            a = min(high, max(low, shifted_level + shifted_gap / 2))
            b = min(high, max(low, shifted_level - shifted_gap / 2))
            out.append((round(a, 3), round(b, 3), weight_gap * weight_level))
    return out


def blended_probability(p_a, p_b, kind, line, who=0, form_noise=True, band=None):
    """(p_over, p_push), integrated over form noise unless told not to."""
    if not form_noise:
        return probability_over(match_distribution(round(p_a, 3), round(p_b, 3)),
                                kind, line, who)
    over = push = 0.0
    for a, b, weight in with_form_noise(p_a, p_b, band=band):
        got_over, got_push = probability_over(match_distribution(a, b), kind,
                                             line, who)
        over += weight * got_over
        push += weight * got_push
    return (over, push)


# ============================================================
# Fitting to the market
# ============================================================

@dataclass
class Fit:
    p_a: float
    p_b: float
    error: float
    anchors: int
    tour: str
    #: True when a serve level ended up on the edge of its tour's band, which
    #: means the market's numbers want a match this model cannot represent --
    #: usually a total low enough to need more breaks than the band allows.
    #: Worth saying out loud: the fit did not converge, it ran out of room.
    at_band_edge: bool = False

    @property
    def level(self):
        return (self.p_a + self.p_b) / 2

    @property
    def gap(self):
        return self.p_a - self.p_b

    @property
    def usable(self):
        """A fit that reproduces its anchors and is not jammed against a wall."""
        return self.error < 0.01 and not self.at_band_edge

    def why_not(self):
        if self.at_band_edge:
            return (f"a serve level sits on the edge of the {self.tour} band "
                    f"{SERVE_BANDS[self.tour]}: the anchors want a match this "
                    "model cannot represent, so nothing is priced from it")
        if self.error >= 0.01:
            return (f"the fit misses its {self.anchors} anchor(s) by "
                    f"{math.sqrt(self.error / self.anchors):.1%} on average, "
                    "which is too far to price from")
        return ""


def fit(anchors, tour="ATP", form_noise=False, refinements=4, steps=9):
    """Serve levels that reproduce the market's own prices.

    `anchors` is [(kind, who, line, target_probability)] -- a total-games line at
    0.5, a moneyline at its vig-free win probability, a games-won line at 0.5.
    A match with only a total and no sense of who is favoured is not fittable
    and says so: two very different matches produce the same number of games.

    Coarse grid then successive refinement rather than a simplex. The objective
    is exact here (no Monte Carlo noise), smooth, and two-dimensional, so a
    search that cannot get stuck is worth more than one that converges fast --
    and reference/tennis_exact.py needed scipy for its Nelder-Mead.
    """
    band = SERVE_BANDS.get(tour) or SERVE_BANDS["ATP"]
    if not anchors:
        raise ValueError("no anchors: nothing to fit to")
    if not any(kind in (GAMES_WON, FIRST_SET_GAMES_WON, MATCH_WIN)
               for kind, *_ in anchors):
        raise ValueError(
            "every anchor is a total, so which player is favoured is unpinned — "
            "two very different matches produce the same number of games. Add a "
            "moneyline or a games-won line.")

    def loss(level, gap):
        p_a, p_b = level + gap / 2, level - gap / 2
        if not (band[0] <= p_a <= band[1] and band[0] <= p_b <= band[1]):
            return 10.0
        total = 0.0
        for kind, who, line, target in anchors:
            over, _ = blended_probability(p_a, p_b, kind, line, who or 0,
                                          form_noise=form_noise, band=band)
            total += (over - target) ** 2
        return total

    level_lo, level_hi = band
    gap_lo, gap_hi = -0.35, 0.35
    best = (None, None, float("inf"))
    for _ in range(refinements):
        for i in range(steps):
            level = level_lo + (level_hi - level_lo) * i / (steps - 1)
            for j in range(steps):
                gap = gap_lo + (gap_hi - gap_lo) * j / (steps - 1)
                value = loss(round(level, 4), round(gap, 4))
                if value < best[2]:
                    best = (level, gap, value)
        level_span = (level_hi - level_lo) / (steps - 1)
        gap_span = (gap_hi - gap_lo) / (steps - 1)
        level_lo = max(band[0], best[0] - level_span)
        level_hi = min(band[1], best[0] + level_span)
        gap_lo, gap_hi = best[1] - gap_span, best[1] + gap_span
    level, gap, error = best
    p_a, p_b = round(level + gap / 2, 4), round(level - gap / 2, 4)
    edge = 1e-3
    at_edge = any(abs(value - bound) < edge
                  for value in (p_a, p_b) for bound in band)
    return Fit(p_a=p_a, p_b=p_b, error=error, anchors=len(anchors), tour=tour,
               at_band_edge=at_edge)


# ============================================================
# Points, for what the game chain has integrated away
# ============================================================

@dataclass
class PointTotals:
    """Per-simulation counts for the things a game-level chain cannot see."""
    aces: list = field(default_factory=list)
    double_faults: list = field(default_factory=list)
    breaks: list = field(default_factory=list)
    games_won: list = field(default_factory=list)
    games_lost: list = field(default_factory=list)
    sets_won: list = field(default_factory=list)
    sets_lost: list = field(default_factory=list)

    def fantasy(self):
        """The build plan's scoring, not the prototype's docstring's."""
        return [FANTASY_PLAYED
                + FANTASY_GAME_WON * won + FANTASY_GAME_LOST * lost
                + FANTASY_SET_WON * sets_won + FANTASY_SET_LOST * sets_lost
                + FANTASY_ACE * aces + FANTASY_DOUBLE_FAULT * double_faults
                for won, lost, sets_won, sets_lost, aces, double_faults
                in zip(self.games_won, self.games_lost, self.sets_won,
                       self.sets_lost, self.aces, self.double_faults)]


def _play_game(rng, serve, ace_rate, df_rate, counters, server):
    """One service game, point by point. Returns True if the server held."""
    points = [0, 0]
    while True:
        roll = rng.random()
        if roll < serve:
            points[0] += 1
            if rng.random() < ace_rate / max(serve, 1e-9):
                counters["aces"][server] += 1
        else:
            points[1] += 1
            if rng.random() < df_rate / max(1 - serve, 1e-9):
                counters["double_faults"][server] += 1
        if points[0] >= 4 and points[0] - points[1] >= 2:
            return True
        if points[1] >= 4 and points[1] - points[0] >= 2:
            return False


def _play_tiebreak(rng, serves, first):
    points, k = [0, 0], 0
    while True:
        server = first if ((k + 1) // 2) % 2 == 0 else 1 - first
        if rng.random() < serves[server]:
            points[server] += 1
        else:
            points[1 - server] += 1
        k += 1
        if max(points) >= 7 and abs(points[0] - points[1]) >= 2:
            return 0 if points[0] > points[1] else 1


def simulate_points(p_a, p_b, aces=(0.075, 0.075), double_faults=(0.03, 0.03),
                    runs=4000, seed=1):
    """(PointTotals for A, PointTotals for B) over `runs` simulated matches.

    Independent points, which is the model's known weakness rather than an
    oversight: real service games are correlated, so this OVERSTATES holds and
    therefore understates breaks. That is why break_rate_check exists and why
    a break prop is refused rather than shrugged at.
    """
    rng = random.Random(seed)
    serves = (p_a, p_b)
    totals = (PointTotals(), PointTotals())
    for _ in range(runs):
        counters = {"aces": [0, 0], "double_faults": [0, 0]}
        breaks = [0, 0]
        games = [0, 0]
        sets = [0, 0]
        server = rng.randint(0, 1)
        first_set = None
        while max(sets) < 2:
            set_games = [0, 0]
            while True:
                if set_games[0] == 6 and set_games[1] == 6:
                    winner = _play_tiebreak(rng, serves, server)
                    set_games[winner] += 1
                    games[winner] += 1
                    server = 1 - server
                else:
                    held = _play_game(rng, serves[server],
                                      aces[server], double_faults[server],
                                      counters, server)
                    winner = server if held else 1 - server
                    if not held:
                        breaks[winner] += 1
                    set_games[winner] += 1
                    games[winner] += 1
                    server = 1 - server
                if ((max(set_games) >= 6 and abs(set_games[0] - set_games[1]) >= 2)
                        or 7 in set_games):
                    break
            sets[0 if set_games[0] > set_games[1] else 1] += 1
            if first_set is None:
                first_set = list(set_games)
        for who in (0, 1):
            other = 1 - who
            totals[who].aces.append(counters["aces"][who])
            totals[who].double_faults.append(counters["double_faults"][who])
            totals[who].breaks.append(breaks[who])
            totals[who].games_won.append(games[who])
            totals[who].games_lost.append(games[other])
            totals[who].sets_won.append(sets[who])
            totals[who].sets_lost.append(sets[other])
    return totals


def break_rate_check(totals, observed_breaks_per_match, tolerance=0.75):
    """(ok, modelled, message) -- does the model's break count match reality?

    The build plan asks for exactly this, and the reason is specific: an
    independent-points model overstates holds, so its break count comes in LOW
    and every break-points prop is priced toward the under by a mechanism that
    has nothing to do with the players. A tolerance of three quarters of a break
    a match is about a fifth of a typical total.
    """
    if not totals.breaks:
        return (False, None, "no simulated matches to count breaks in")
    modelled = sum(totals.breaks) / len(totals.breaks)
    if observed_breaks_per_match is None:
        return (False, modelled, "no observed break rate on file to check against, "
                                 "and an independent-points model is known to "
                                 "understate breaks — not priced")
    drift = modelled - observed_breaks_per_match
    if abs(drift) > tolerance:
        return (False, modelled,
                f"model breaks {modelled:.2f} a match against a real "
                f"{observed_breaks_per_match:.2f} ({drift:+.2f}) — past the "
                f"{tolerance} tolerance, so this is the iid-points weakness "
                "showing and the prop is not priced")
    return (True, modelled,
            f"model breaks {modelled:.2f} a match against a real "
            f"{observed_breaks_per_match:.2f}, inside tolerance")


# ============================================================
# Board -> projections
# ============================================================

#: What a board calls each thing, against what this module can answer. The
#: first four come from the exact chain; the rest need points simulated.
BOARD_STATS = {
    "total games": (TOTAL_GAMES, False),
    "total games won": (GAMES_WON, False),
    "games won": (GAMES_WON, False),
    "1st set total games": (FIRST_SET_TOTAL, False),
    "1st set total games won": (FIRST_SET_GAMES_WON, False),
    "aces": ("aces", True),
    "double faults": ("double_faults", True),
    "fantasy score": ("fantasy", True),
    "break points won": ("breaks", True),
}
#: Break props are the ones the iid-points assumption gets wrong in a known
#: direction, so they are priced only when break_rate_check passes.
GATED_STATS = ("breaks",)


def _empirical(values, line):
    """(p_over, p_push) from simulated values."""
    if not values:
        return None
    over = sum(1 for v in values if v > line) / len(values)
    push = (sum(1 for v in values if v == line) / len(values)
            if float(line).is_integer() else 0.0)
    return (over, push)


def projections_from(board, fitted, points=None, observed_breaks=None,
                     form_noise=True):
    """Projection rows for one match's board.

    `fitted` is a Fit; `points` a (PointTotals, PointTotals) from
    simulate_points, needed only for the props the game chain cannot see.
    `observed_breaks` maps a player to their real breaks per match, which is
    what gates the break props.

    Two scenarios per row: the fit as it stands, and the same fit with the form
    noise doubled. The second is not a different opinion, it is the same opinion
    held less tightly, and taking the worst of the two is the conservative
    reading of a fit that came entirely from the market.
    """
    if not fitted.usable:
        return []
    out = []
    band = SERVE_BANDS.get(fitted.tour)
    for row in board:
        player, opponent = row.get("player"), row.get("opponent") or ""
        raw = str(row.get("stat", "")).strip().lower()
        mapped = BOARD_STATS.get(raw)
        line = row.get("line")
        if not player or mapped is None or line is None:
            continue
        kind, needs_points = mapped
        # `who` says which side of the FIT this row is about: 0 is the player
        # whose serve is p_a. A board row does not carry that -- it carries two
        # names -- so the caller resolves it once per match and puts it on the
        # row, because guessing from name order here would silently price the
        # favourite's line as the underdog's.
        try:
            who = int(row["who"])
        except (KeyError, TypeError, ValueError):
            continue
        if who not in (0, 1):
            continue
        reasons = []

        if needs_points:
            if points is None:
                continue
            totals = points[who]
            if kind in GATED_STATS:
                ok, modelled, message = break_rate_check(
                    totals, (observed_breaks or {}).get(player))
                if not ok:
                    continue
                reasons.append(message)
            values = {"aces": totals.aces, "double_faults": totals.double_faults,
                      "breaks": totals.breaks,
                      "fantasy": totals.fantasy()}[kind]
            split = _empirical(values, line)
            if split is None:
                continue
            p_over, p_push = split
            # A simulated stat has no second world to be held loosely in: the
            # form noise lives in the fitted serve level, which these do not use.
            p_over_wide = p_over
            reasons.append(f"{len(values)} simulated matches, point by point")
            if kind in ("aces", "double_faults"):
                reasons.append("aces and double faults come from the serve rates "
                               "given, not from the fitted serve level — the fit "
                               "is a model parameter, not a serve statistic")
        else:
            p_over, p_push = blended_probability(
                fitted.p_a, fitted.p_b, kind, line, who,
                form_noise=form_noise, band=band)
            p_over_wide = p_over
            if form_noise:
                # Held less tightly: the same fit with twice the form noise.
                wide = 0.0
                for a, b, weight in with_form_noise(
                        fitted.p_a, fitted.p_b, gap_noise=FORM_NOISE_GAP * 2,
                        level_noise=FORM_NOISE_LEVEL * 2, band=band):
                    got, _ = probability_over(match_distribution(a, b), kind,
                                              line, who)
                    wide += weight * got
                p_over_wide = wide
            reasons.append("exact best-of-three Markov chain, no simulation error")
            reasons.append(f"fitted to {fitted.anchors} market anchor(s): serve "
                           f"{fitted.p_a:.3f} against {fitted.p_b:.3f}")

        side = "over" if p_over >= 0.5 else "under"
        decided = 1 - p_push

        def as_win(value):
            return max(0.0, min(decided, (value if side == "over" else 1 - value)
                                - (0 if side == "over" else p_push)))

        scenarios = {"fitted": as_win(p_over), "held_loosely": as_win(p_over_wide)}
        out.append(Projection(
            prop_id=(row.get("prop_id")
                     or f"tennis:{str(player).lower()}:{raw}:{line}:"
                        f"{row.get('start_time', '')}"),
            sport="tennis", player=player,
            # Each player is their own team, which the slip rules already know:
            # both players of one match on one slip is legal AND is two legs on
            # one fixture, so it gets the same-match correlation treatment.
            team=player, opponent=opponent, stat=raw, line=line, side=side,
            p_win=as_win(p_over), p_push=p_push, scenarios=scenarios,
            confidence="medium" if fitted.error < 1e-4 else "low",
            start_time=row.get("start_time") or "",
            odds_type=row.get("odds_type") or "standard",
            model="propedge.tennis", reasons=tuple(reasons)))
    return out

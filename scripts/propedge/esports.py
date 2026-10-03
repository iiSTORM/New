"""CS2, Valorant and LoL projections, turned into probabilities a slip can use.

This does not invent a projection. The model that produces a player's expected
kills already exists, it is the one the app runs, and its Python form
(scripts/dev/optimize_weights.py) is pinned against src/app.jsx by
tests/model_parity.test.mjs. A second projection model would be a second thing
to keep calibrated. So this module does the three things the existing one does
not:

  1. turns an expected TOTAL into P(over), P(push) and P(under) for a posted
     line, using the residual scale measured per game, stat and window;
  2. shrinks the disagreement with the line by how much evidence there is,
     because a projection off four maps is not a disagreement, it is noise;
  3. blends in what the graded record says about the market's own bias.

WHAT THE BACKTEST ACTUALLY FOUND, because it decides how much of this to
believe. On 1,791 graded props replayed point in time, parameters chosen on the
first half and reported on the second:

  * calibration is off by 3.9% on the held-out half, inside the five points that
    would otherwise take Kelly away;
  * the most confident fifth of picks realised 57.7% against the least
    confident's 39.8%, clustered on the match. That separates, which nothing
    else in this project has yet managed;
  * and 57.7% is EXACTLY the break-even for a 2-pick power play at 3x. The best
    fifth of these picks is a coin flip against the payout, before correlation.

Two things that follow, and neither is comfortable. At prior_weight 0 the
ranking INVERTS -- the most confident fifth lands 44.8% against 56.6%. So the
separation is the market-bias prior, not the player projection. And splitting
within each (game, stat, maps) cell, where the prior is constant, the projection
shows nothing: -1.3% over 316 CS2 maps-2 kills and -6.5% over 306 headshots, the
two largest cells, against strong positives in cells of 75 and 83. Large samples
flat or negative with small samples strongly positive is the shape of noise.

So this ships as what it is: a measured market bias with a player model that has
not yet earned its place, kept honest by emitting BOTH as scenarios so the
builder's worst case is priced on the projection's opinion, which is the
pessimistic one. Read scripts/dev/backtest_esports.py's output before trusting a
number that comes out of here.

THE TRAP THIS DELIBERATELY DOES NOT FALL INTO. cs2_data.json's `actual` totals
for a Bo3 cover MAPS 1-2 only, and reference/esports_projections.py divides by 2
to get a per-map rate. The shipped model ALREADY does that --
recency_weighted_rate divides by the maps each entry covers and
project_point_in_time multiplies back up by the window asked for -- so dividing
again here would halve every projection. Verified before writing a line of this.

WHY PUSHES FALL OUT RATHER THAN BEING ASSUMED AWAY. Kills and headshots are
integers, so a whole-number line can land exactly, and the outcome table needs
that mass. A continuous normal puts zero probability on it. So the normal is
integrated between half-integer boundaries: P(push) on a line of 15 is the mass
between 14.5 and 15.5, and on 15.5 it is zero by construction.
"""
import collections
import os
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime

from . import jsconfig
from .model import Projection

sys.path.insert(0, os.path.join(jsconfig.REPO_ROOT, "scripts", "dev"))
#: The shipped model, in the form the parity test pins against src/app.jsx.
#: Imported rather than reimplemented: one model, one set of weights.
import optimize_weights as ow  # noqa: E402

GAMES = ("cs2", "valorant", "lol")
SOURCES = {"cs2": "cs2_data.json", "lol": "data.json",
           "valorant": "valorant_data.json"}
#: Regions that are an EVENT rather than a league, whose matches belong to the
#: form of players who mostly play elsewhere. A Valorant team is listed in its
#: league and again at Champions, so a player's recent form is split across two
#: region blobs and neither is complete on its own.
#: reference/esports_projections.py already did this -- it reads
#: `for reg in (league_region, 'VCT Champions')` -- and leaving it out cost 78
#: of 79 Valorant props on the 2026-09-29 board: every one resolved to two
#: candidate rosters with the same team name and was dropped as ambiguous.
INTERNATIONAL_REGIONS = {"valorant": ("VCT Champions",)}

#: Maps of evidence at which the disagreement with the line is taken at half
#: strength. Chosen on the FIRST half of the graded record and reported on the
#: second -- scripts/dev/backtest_esports.py, which prints the whole grid so the
#: choice can be seen to be a choice among near-ties rather than a peak.
SHRINK_MAPS = 4.0
#: How much of the final probability comes from the market-bias prior rather
#: than from the projection. ZERO: this is a projection tool, and a number that
#: is half "lines run high" is not a projection of anything.
#:
#: The backtest that chose 0.50 was answering "what scores best", and the honest
#: reading of its answer was that the market bias was doing all the work. Baking
#: it in made the tool better at betting and worse at its job -- every slate came
#: out unders, and none of it was about the players. The bias has not gone away
#: and is not hidden: it is measured, displayed against every pick as agreement
#: or disagreement, and left for you to weigh. What it no longer does is quietly
#: become the answer.
#:
#: Raise it if you want the old behaviour back; nothing else changes.
PRIOR_WEIGHT = 0.0
#: Graded props at which a measured under-rate is taken at half strength,
#: shrinking it toward 50% when the sample is thin.
PRIOR_SHRINK_N = 150.0
#: Below this many maps of history, no projection is offered at all.
MIN_MAPS = 3

EXACT = statistics.NormalDist()


def _today():
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def residual_scale(game, stat, maps, scales=None, exponent=None):
    """The model's own error for this game, stat and window, from src/app.jsx.

    Read rather than copied, and stretched to an unobserved window by the
    measured exponent -- CS2 and Valorant have never had a maps-1-3 line settle.
    """
    scales = scales if scales is not None else jsconfig.obj("RESIDUAL_SCALE")
    exponent = (exponent if exponent is not None
                else jsconfig.number("RESIDUAL_SCALE_EXPONENT"))
    by_window = (scales.get(game) or {}).get(stat)
    if not by_window or not isinstance(maps, int) or maps <= 0:
        return None
    exact = by_window.get(str(maps))
    if isinstance(exact, (int, float)):
        return float(exact)
    windows = [int(w) for w in by_window if int(w) > 0]
    if not windows:
        return None
    near = min(windows, key=lambda w: abs(w - maps))
    return by_window[str(near)] * (maps / near) ** exponent


def outcome_probabilities(mu, sigma, line):
    """(p_over, p_push, p_under) for an integer-valued stat against `line`.

    Integrated between half-integer boundaries, so a whole-number line carries
    real push mass and a half-point line carries none. Uses the exact normal CDF
    rather than the rational approximation in propedge/joint.py: that one is
    deliberately the JS's, so the parity test measures agreement instead of
    approximation error, and nothing here needs to agree with any JS.
    """
    if not sigma or sigma <= 0:
        return None
    if float(line).is_integer():
        upper = EXACT.cdf((line + 0.5 - mu) / sigma)
        lower = EXACT.cdf((line - 0.5 - mu) / sigma)
        return (1 - upper, max(0.0, upper - lower), lower)
    below = EXACT.cdf((line - mu) / sigma)
    return (1 - below, 0.0, below)


def shrink_toward_line(mu, line, maps_of_evidence, shrink_maps=SHRINK_MAPS):
    """Pull the projection toward the posted line by how much evidence there is.

    A projection off four maps that disagrees with the line by six kills is not
    a six-kill edge, it is four maps of noise. This is NOT the shrink inside the
    model (that one pulls toward the league mean, to stop a small sample looking
    like a strong player); this pulls toward the MARKET, and says how much of
    our disagreement with it we are willing to bet on.

    It is also the most direct answer available to what the decile test keeps
    reporting: that the confident half of our picks has not been landing more
    often than the rest. Less disagreement means fewer slips clear the bar, and
    that is the intended effect rather than a side effect.
    """
    if not maps_of_evidence or maps_of_evidence <= 0:
        return line
    trust = maps_of_evidence / (maps_of_evidence + shrink_maps)
    return line + (mu - line) * trust


def under_rates(graded):
    """{(game, stat, maps, odds_type): (under_rate, n)} from the graded record.

    Measured rather than hardcoded. The build plan quotes the rates as of
    2026-09-28 and a snapshot in the source would be stale within the week, so
    it is computed from the file and the plan's numbers are a test.

    KEYED ON ODDS TYPE, which the plan's pooled figures are not, and it matters:
    a goblin's line is moved in your favour and a demon's against you, so their
    under-rates are not measurements of the standard board's bias. On the graded
    record CS2 goblins went under 35.3% of the time against 54.3% for standard
    lines, and LoL 51.9% against 82.8%. Pooling the three gave the plan's LoL
    figure of 73.5% over 83 props, which understates the standard board by nine
    points and describes no product that can actually be bet.

    Thin cells are not a problem to filter for -- prior_under shrinks them
    toward 50% by their own sample size, so CS2's 17 graded goblins arrive
    saying almost nothing, which is correct.

    Pushes are excluded: an under rate is a rate among decided props.
    """
    counts = collections.defaultdict(lambda: [0, 0])
    for row in graded or []:
        if row.get("result") not in ("over", "under"):
            continue
        key = (row.get("game"), row.get("stat"), row.get("maps"),
               row.get("odds_type") or "standard")
        counts[key][0] += 1 if row["result"] == "under" else 0
        counts[key][1] += 1
    return {key: (unders / n, n) for key, (unders, n) in counts.items() if n}


def prior_under(rates, game, stat, maps, odds_type="standard",
                shrink_n=PRIOR_SHRINK_N):
    """The under-rate prior, shrunk toward 50% by its own sample size.

    Returns (p_under, n) or None where the record has nothing to say. 300 graded
    props saying 57% is worth more than 20 saying 70%, and neither is worth
    taking at face value.
    """
    found = rates.get((game, stat, maps, odds_type or "standard"))
    if not found:
        return None
    rate, n = found
    return (0.5 + (rate - 0.5) * n / (n + shrink_n), n)


@dataclass
class Detail:
    """Everything that went into one probability, for the slip's breakdown."""
    projection: float
    shrunk: float
    sigma: float
    maps_of_evidence: float
    p_model: float
    p_prior: float = None
    prior_n: int = 0
    prior_weight: float = 0.0


class EsportsModel:
    """Projections for a posted board, as Projection rows the builder can use."""

    def __init__(self, data_by_game=None, graded=None, shrink_maps=SHRINK_MAPS,
                 prior_weight=PRIOR_WEIGHT, prior_shrink_n=PRIOR_SHRINK_N,
                 min_maps=MIN_MAPS, weights=None, scales=None, exponent=None):
        self.data = data_by_game or {}
        self.rates = under_rates(graded)
        self.shrink_maps = shrink_maps
        self.prior_weight = prior_weight
        self.prior_shrink_n = prior_shrink_n
        self.min_maps = min_maps
        self.weights = weights or ow.SHIPPED_WEIGHTS
        self.scales = scales if scales is not None else jsconfig.obj("RESIDUAL_SCALE")
        self.exponent = (exponent if exponent is not None
                         else jsconfig.number("RESIDUAL_SCALE_EXPONENT"))
        self._index = {}
        #: (game, player, stat, maps, cutoff, team, opponent) -> (mu, evidence).
        #: A board carries the standard, demon and goblin variants of one prop as
        #: separate rows with the same player, stat and window, and every
        #: projection rescans that region's whole match history -- so without
        #: this the same scan runs three times for one answer.
        self._means = {}

    @classmethod
    def from_files(cls, root=".", graded=None, **kw):
        data = {}
        for game, filename in SOURCES.items():
            loaded = ow.load_region_data(os.path.join(root, filename))
            if loaded:
                data[game] = loaded
        return cls(data, graded, **kw)

    def locate(self, game, player, team=None):
        """(region, team, player record) for a handle, keyed on the PLAYER.

        Never on a pair of team names: the board and the match data spell teams
        differently often enough that pairing on them loses players, and a
        handle is unique within a game. Where a handle is on two rosters the
        stated team breaks the tie, and if it cannot, nothing is returned rather
        than a guess.
        """
        if game not in self._index:
            index = collections.defaultdict(list)
            for region, blob in (self.data.get(game) or {}).items():
                for team_name, info in (blob.get("teams") or {}).items():
                    for record in info.get("players") or []:
                        index[str(record.get("name", "")).lower()].append(
                            (region, team_name, record))
            self._index[game] = index
        found = self._index[game].get(str(player).lower()) or []
        if not found:
            return None
        if len(found) == 1:
            return found[0]
        stated = str(team or "").strip().lower()
        exact = [row for row in found if row[1].strip().lower() == stated]
        candidates = exact or found
        if len(candidates) == 1:
            return candidates[0]
        # Several rosters, same team name: one player listed in their league and
        # again at an international event. Not ambiguity -- the same person --
        # so take the region carrying the most matches for that team, which is
        # where the form actually is. Genuinely different teams stay ambiguous.
        teams = {row[1].strip().lower() for row in candidates}
        if len(teams) != 1:
            return None
        return max(candidates, key=lambda row: self._team_matches(game, row[0], row[1]))

    def _team_matches(self, game, region, team):
        blob = (self.data.get(game) or {}).get(region) or {}
        return sum(1 for match in (blob.get("past_matches") or [])
                   if team in (match.get("actual") or {}))

    def history_for(self, game, region):
        """(past_matches, teams) for a region, with international events folded in.

        A Valorant player's recent form is split between their league's blob and
        Champions', and a projection off one of them is a projection off half the
        evidence. Matches are deduplicated on (date, teamA, teamB) so a fixture
        listed in both regions is not counted twice.
        """
        blob = (self.data.get(game) or {}).get(region) or {}
        extra = [name for name in INTERNATIONAL_REGIONS.get(game, ())
                 if name != region and name in (self.data.get(game) or {})]
        if not extra:
            return (blob.get("past_matches") or [], blob.get("teams") or {})
        matches, seen = [], set()
        teams = {}
        for name in [region] + extra:
            other = (self.data.get(game) or {}).get(name) or {}
            for match in other.get("past_matches") or []:
                key = (match.get("date"), match.get("teamA"), match.get("teamB"))
                if key in seen:
                    continue
                seen.add(key)
                matches.append(match)
            for team_name, info in (other.get("teams") or {}).items():
                teams.setdefault(team_name, info)
        matches.sort(key=lambda match: str(match.get("date") or ""))
        return (matches, teams)

    def project_mean(self, game, player, stat, maps, team=None, opponent=None,
                     as_of=None, patch=None):
        """(expected total over `maps`, maps of evidence) or None."""
        located = self.locate(game, player, team)
        if located is None:
            return None
        region, team_name, record = located
        weights = (self.weights.get(game) or {}).get(stat)
        if not weights:
            return None
        key = (game, str(player).lower(), stat, maps, as_of, team_name,
               opponent or "", patch)
        if key not in self._means:
            past_matches, teams = self.history_for(game, region)
            mu, evidence = ow.project_point_in_time(
                past_matches, teams, record,
                team_name, opponent or "", maps, weights, as_of, stat, patch)
            self._means[key] = None if mu is None else (mu, evidence)
        return self._means[key]

    def probabilities(self, game, stat, maps, mu, line, evidence,
                      odds_type="standard"):
        """(p_over, p_push, p_under, Detail) with the shrink and prior applied."""
        sigma = residual_scale(game, stat, maps, self.scales, self.exponent)
        if not sigma:
            return None
        shrunk = shrink_toward_line(mu, line, evidence, self.shrink_maps)
        split = outcome_probabilities(shrunk, sigma, line)
        if split is None:
            return None
        p_over, p_push, p_under = split
        detail = Detail(projection=mu, shrunk=shrunk, sigma=sigma,
                        maps_of_evidence=evidence, p_model=p_under)
        prior = prior_under(self.rates, game, stat, maps, odds_type,
                            self.prior_shrink_n)
        if prior and self.prior_weight:
            p_prior, n = prior
            detail.p_prior, detail.prior_n = p_prior, n
            detail.prior_weight = self.prior_weight
            decided = p_over + p_under
            if decided > 0:
                # Blended as CONDITIONAL rates and rescaled by the decided mass,
                # so the push probability is untouched: the prior is a statement
                # about which way decided props land, and says nothing about how
                # often one lands exactly on the number.
                blended = ((1 - self.prior_weight) * (p_under / decided)
                           + self.prior_weight * p_prior)
                p_under = blended * decided
                p_over = decided - p_under
        return (p_over, p_push, p_under, detail)

    def project(self, prop, as_of=None, patch=None, prop_id=None):
        """One board row -> a Projection on whichever side the model prefers.

        `as_of` is the cutoff the projection is built from and it is never
        optional in effect: the underlying model compares it against every
        historical match date, so None would compare a string to None and throw.
        A board row's own start date is the right answer -- "project this as of
        the game it is for" -- with today as the fallback for a row that has no
        start time, which is the same thing for a board posted today.
        """
        game = prop.get("game")
        stat, maps, line = prop.get("stat"), prop.get("maps"), prop.get("line")
        if game not in GAMES or not isinstance(maps, int) or line is None:
            return None
        as_of = as_of or str(prop.get("start_time") or "")[:10] or _today()
        mean = self.project_mean(game, prop.get("player"), stat, maps,
                                 prop.get("team"), prop.get("opponent"),
                                 as_of, patch)
        if mean is None:
            return None
        mu, evidence = mean
        if evidence < self.min_maps:
            return None
        priced = self.probabilities(game, stat, maps, mu, line, evidence,
                                    prop.get("odds_type") or "standard")
        if priced is None:
            return None
        p_over, p_push, p_under, detail = priced
        side = "over" if p_over >= p_under else "under"
        p_win = p_over if side == "over" else p_under
        # Both worlds are the MODEL's, stressed by its own error rather than by
        # the market's opinion: the projection at the measured residual scale,
        # and the same projection at a scale half again as wide. A model that
        # only clears the bar while it is certain of its own accuracy should be
        # ranked by the version that is not.
        scenarios = {"projection": p_win,
                     "wider_error": self._at_wider_scale(game, stat, maps, detail,
                                                         line, side)}
        if scenarios["wider_error"] is None:
            scenarios = {"projection": p_win}
        return Projection(
            prop_id=prop_id or self.prop_id(prop), sport=game,
            player=prop.get("player"), team=prop.get("team") or "",
            opponent=prop.get("opponent") or "", stat=stat, line=line,
            side=side, maps=maps, p_win=p_win, p_push=p_push,
            scenarios=scenarios, confidence=self.confidence(evidence),
            start_time=prop.get("start_time") or "",
            odds_type=prop.get("odds_type") or "standard",
            model="propedge.esports", reasons=self.reasons(detail, side, line),
            sim_group="")

    @staticmethod
    def prop_id(prop):
        """Identity includes the START TIME, because a player can have the same
        line on several matches the same day.

        R4DYX had kills 16.5 posted on three Sangal matches at 12:30, 13:30 and
        14:30 on 2026-09-29. Without the start time all three are one id, and
        the builder's one-leg-one-slip bookkeeping is keyed on that id -- so two
        of the three would silently vanish, or worse, two slips would each think
        they held a different leg. The same mistake keyed the closing-line
        history without a start time and would have closed a Tuesday bet with a
        Friday number.
        """
        # A single-map line on map 2 or 3 carries "map"; map 1 does not, so
        # every id minted before the field existed is unchanged.
        first = prop.get("map") or 1
        return (f"{prop.get('game')}:{str(prop.get('player')).lower()}:"
                f"{prop.get('stat')}:{prop.get('maps')}"
                f"{'@' + str(first) if first != 1 else ''}:{prop.get('line')}:"
                f"{prop.get('odds_type') or 'standard'}:"
                f"{prop.get('start_time') or ''}")

    WIDER_SCALE = 1.5

    def _at_wider_scale(self, game, stat, maps, detail, line, side):
        """The same projection, priced as if the model's error were half again
        as wide. Not a different opinion -- the same one, held less tightly."""
        split = outcome_probabilities(detail.shrunk, detail.sigma * self.WIDER_SCALE,
                                      line)
        if split is None:
            return None
        p_over, _, p_under = split
        return p_over if side == "over" else p_under

    @staticmethod
    def confidence(maps_of_evidence):
        if maps_of_evidence >= 20:
            return "high"
        return "medium" if maps_of_evidence >= 8 else "low"

    def reasons(self, detail, side, line):
        out = [f"projects {detail.projection:.1f} against a line of {line:g}, "
               f"off {detail.maps_of_evidence:g} maps",
               f"disagreement shrunk to {detail.shrunk:.1f} — "
               f"{detail.maps_of_evidence:g} maps buys "
               f"{detail.maps_of_evidence / (detail.maps_of_evidence + self.shrink_maps):.0%} "
               f"of it",
               f"the model's own error at this window is {detail.sigma:.1f}"]
        if detail.p_prior is not None:
            # Shown AGAINST the pick rather than folded into it: the market's
            # bias is a fact about the board worth knowing, and it is not a
            # projection of this player.
            leans = "under" if detail.p_prior > 0.5 else "over"
            agrees = (leans == side)
            out.append(
                f"the graded record leans {leans} in this cell "
                f"({max(detail.p_prior, 1 - detail.p_prior):.0%} over "
                f"{detail.prior_n} props)"
                + (" — the same way as this pick" if agrees else
                   f" — the OPPOSITE way to this {side} pick, which is the whole "
                   "of the disagreement you are betting on")
                + (f", weighted {detail.prior_weight:.0%} into the number"
                   if detail.prior_weight else ", and is not in the number"))
        return tuple(out)

    def project_board(self, props, as_of=None):
        """Every board row that can be projected, best-supported first."""
        out = []
        for game, players in (props.get("props") or props).items():
            for rows in players.values():
                for row in rows:
                    made = self.project({**row, "game": game}, as_of)
                    if made is not None:
                        out.append(made)
        out.sort(key=lambda p: -p.p_win)
        return out

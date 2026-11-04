#!/usr/bin/env python3
"""A map-by-map series simulator for esports fixtures.

The projections answer one question per prop: how many kills, on average. A
simulation answers all of them at once, and in a way that keeps them
consistent with each other: in each run the series is actually played, map by
map, so a team that gets stomped in run 4,123 has all five of its players
have a bad day in that run, a Bo3 that ends 2-0 voids every maps-1-3 line in
that run, and one team's kills are always the other's deaths.

ONE MAP, AS SIMULATED

  1. Who wins it. A map-level win probability, from the fixture's bookmaker
     price when there is one (de-vigged and turned from a series price into a
     map price), otherwise from a point-in-time Elo over every series this
     game's data holds. LoL's Elo is fitted on Oracle's Elixir games when they
     are on file, which carry MSI, First Stand, EWC and last year's Worlds and
     so put LCK and LCS teams on one scale.

  2. What each team did. A REAL past map of each team is drawn -- one with
     the same result as the simulated one, weighted to recent maps -- so the
     shape of a team's games (how often it stomps, how often it scrapes by)
     comes from its own record rather than from a bell curve.

  3. How many kills. The map's two kill totals come from one of the two
     drawn maps, picked at random (A's map, or B's seen from the other
     side), so a map keeps its real length and spread -- a long CS2 map gives
     both teams more kills -- and each team's style counts half the time.
     Then the event pace and class-gap factors the projections use. One
     side's kills are the other side's deaths, always.

  4. Who got them. Each player's kills, deaths, assists (and CS2 headshots)
     are that player's line from the drawn map, scaled to the team totals in
     step 3, so the within-team split is a real one. A current player who did
     not play the drawn map takes the line of the player who did and is no
     longer on the roster; failing that, a line from their own history.

  5. LoL only: game length, dragons, barons, towers and the firsts, from an
     Oracle's Elixir team row of each side with the same result.

The series ends when a team reaches the wins it needs. Thousands of runs are
then summarised (summarise()) into the distributions the app shows, and
scripts/dev/simulation_backtest.py scores the engine against the projections
on the graded record, point in time.

    python scripts/propedge/series_sim.py --game lol --teams "Gen.G" "LYON"
"""
import argparse
import bisect
import collections
import csv
import gzip
import json
import math
import os
import random
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "dev"))

import series_score                                 # noqa: E402

GAME_STATS = {"cs2": ("k", "d", "a", "hs"), "lol": ("k", "d", "a"),
              "valorant": ("k", "d", "a"), "dota2": ("k", "d", "a")}
STAT_NAMES = {"kills": "k", "deaths": "d", "assists": "a", "headshots": "hs"}
SOURCES = {"cs2": "cs2_data.json", "lol": "data.json",
           "valorant": "valorant_data.json", "dota2": "dota_data.json"}
DEFAULT_RUNS = 10_000
ELO_K = 24
ELO_START = 1500.0
HALF_LIFE_DAYS = 120          # recency weight on a team's past maps
MAX_MAPS_PER_TEAM = 80        # most recent maps a team's draws come from
MIN_MAPS = 4                  # fewer and the fixture is not simulated
MIN_BUCKET = 5                # fewer same-result maps and either result is used
#: What counts as a stomp and a close map, by the winner's kill total against
#: the loser's: a ratio for the stomp, a margin for the close one.
STOMP_RATIO = 2.0
CLOSE_MARGIN = {"lol": 3, "dota2": 5, "cs2": 6, "valorant": 6}
#: Smoothed bootstrap: each drawn map's kill totals are multiplied by
#: exp(N(0, JITTER)), the same draw for both teams (a map runs long or short
#: for both). With 15-30 maps per team the raw draws can only take those few
#: values, and the next map will not repeat one of them exactly. Chosen by
#: scripts/dev/simulation_backtest.py --jitter.
JITTER = 0.10


def norm(name):
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())


def _day_number(date):
    try:
        y, m, d = (int(x) for x in str(date)[:10].split("-"))
    except ValueError:
        return 0
    # days since 1970-01-01, close enough for recency weights
    import datetime
    return (datetime.date(y, m, d) - datetime.date(1970, 1, 1)).days


@dataclass
class TeamMap:
    date: str
    opponent: str
    won: bool
    players: dict             # player key -> {stat: n}
    names: dict               # player key -> display name
    kills: int
    deaths: int


@dataclass
class Extras:
    date: str
    won: bool
    values: dict


# ----------------------------------------------------------------- the library

class Library:
    """Every map each team in one game has played, point-in-time lookups over
    them, and the Elo that says who wins."""

    def __init__(self, game, regions, oe_rows=None):
        self.game = game
        self.stats = GAME_STATS[game]
        self.regions = regions
        self.maps = collections.defaultdict(list)          # team -> [TeamMap] by date
        self.extras = collections.defaultdict(list)        # team -> [Extras] by date (LoL)
        self.display = {}                                  # team key -> display name
        series = []
        seen_maps, seen_series = set(), set()
        #: (date, frozenset of the two team keys) -> {"games": n, "wins": {team: n},
        #: "lineups": {team: [(key, name)]}}: every series either source holds,
        #: so a backtest can find a past fixture's format and lineups.
        self.series_index = {}
        self._oe_covered = set()
        if oe_rows:
            self._add_oe_maps(oe_rows, seen_maps, seen_series)
        for region, blob in regions.items():
            for m in (blob.get("past_matches") or []) + (blob.get("history_matches") or []):
                self._add_match(m, seen_maps, seen_series, series)
        for rows in self.maps.values():
            rows.sort(key=lambda r: r.date)
        self._dates = {t: [r.date for r in rows] for t, rows in self.maps.items()}
        elo_games = series
        if oe_rows:
            self._add_oe(oe_rows)
            oe_series = self._oe_series(oe_rows)
            if oe_series:
                last = max(s[0] for s in oe_series)
                elo_games = oe_series + [s for s in series if s[0] > last]
        self.elo_history, self.elo_final = _elo_history(sorted(elo_games))

    def _add_match(self, m, seen_maps, seen_series, series):
        date = str(m.get("date") or "")[:10]
        games = [g for g in (m.get("per_game") or []) if len(g or {}) == 2]
        if not date or not games:
            return
        a, b = m.get("teamA"), m.get("teamB")
        key = (date, tuple(sorted((norm(a), norm(b)))))
        if key in self._oe_covered:
            return
        idx = self.series_index.setdefault((date, frozenset((norm(a), norm(b)))), {
            "best_of": best_of_for(m), "names": {norm(a): a, norm(b): b},
            "lineups": {norm(t): [(norm(p), p) for p in (games[0].get(t) or {})] for t in games[0]}})
        if key not in seen_series:
            seen_series.add(key)
            score = series_score.orient(m.get("score"), a, b, m.get("winner"))
            try:
                wa, wb = (int(x) for x in str(score).split("-")[:2])
                if wa + wb:
                    series.append((date, norm(a), norm(b), wa, wb))
            except ValueError:
                pass
        for i, one in enumerate(games):
            if (key, i) in seen_maps:
                continue
            seen_maps.add((key, i))
            teams = list(one)
            totals = {t: sum((s.get("k") or 0) for s in (one[t] or {}).values()) for t in teams}
            for t, o in ((teams[0], teams[1]), (teams[1], teams[0])):
                won = totals[t] > totals[o] or (totals[t] == totals[o] and m.get("winner") == t)
                self.display.setdefault(norm(t), t)
                self.maps[norm(t)].append(TeamMap(
                    date=date, opponent=norm(o), won=won,
                    players={norm(p): {s: (v.get(s) or 0) for s in self.stats}
                             for p, v in (one[t] or {}).items()},
                    names={norm(p): p for p in (one[t] or {})},
                    kills=totals[t], deaths=totals[o]))

    def _add_oe_maps(self, rows, seen_maps, seen_series):
        """LoL maps from Oracle's Elixir: every game, with the real winner.

        Preferred over data.json for LoL: it keeps events whose region data.json
        drops once they end (Demacia Cup, the Worlds play-ins), and its result
        column says who won the map rather than leaving it to the kill count.
        """
        self._oe_covered = set()
        games = collections.defaultdict(lambda: {"teams": {}, "players": collections.defaultdict(dict)})
        for row in rows:
            g = games[row.get("gameid")]
            team = row.get("teamname")
            if row.get("position") == "team":
                g["teams"][team] = row
            else:
                g["players"][team][row.get("playername")] = row
        by_series = collections.defaultdict(list)
        for gid, g in games.items():
            if len(g["teams"]) != 2:
                continue
            (ta, ra), (tb, rb) = g["teams"].items()
            date = str(ra.get("date"))[:10]
            by_series[(date, frozenset((norm(ta), norm(tb))))].append((ra.get("date"), gid, g))
        for (date, pair), played in by_series.items():
            played.sort()
            self._oe_covered.add((date, tuple(sorted(pair))))
            wins = collections.Counter()
            first = played[0][2]
            for _, gid, g in played:
                teams = list(g["teams"])
                for t, o in ((teams[0], teams[1]), (teams[1], teams[0])):
                    row = g["teams"][t]
                    won = row.get("result") == "1"
                    wins[norm(t)] += won
                    players = g["players"].get(t) or {}
                    def n(x):
                        try:
                            return int(float(x))
                        except (TypeError, ValueError):
                            return 0
                    kills = n(row.get("kills"))
                    deaths = n(row.get("deaths"))
                    self.display.setdefault(norm(t), t)
                    self.maps[norm(t)].append(TeamMap(
                        date=date, opponent=norm(o), won=won,
                        players={norm(p): {"k": n(r.get("kills")), "d": n(r.get("deaths")),
                                           "a": n(r.get("assists"))} for p, r in players.items()},
                        names={norm(p): p for p in players}, kills=kills, deaths=deaths))
            need = max(wins.values()) if wins else 1
            self.series_index[(date, pair)] = {
                "best_of": max(1, 2 * need - 1) if len(played) > 1 or need > 1 else 1,
                "names": {norm(t): t for t in first["teams"]},
                "lineups": {norm(t): [(norm(p), p) for p in first["players"].get(t, {})]
                            for t in first["teams"]},
                "wins": dict(wins), "games": len(played)}

    def series_on(self, team_a, team_b, date):
        """A past series' format and lineups, from whichever source holds it."""
        return self.series_index.get((str(date)[:10], frozenset((norm(team_a), norm(team_b)))))

    def _add_oe(self, rows):
        for row in rows:
            if row.get("position") != "team":
                continue
            vals = {}
            for col in ("gamelength", "dragons", "barons", "towers", "heralds", "void_grubs",
                        "firstblood", "firstdragon", "firstbaron", "firsttower", "firstherald"):
                try:
                    vals[col] = float(row[col]) if row.get(col) not in (None, "") else None
                except ValueError:
                    vals[col] = None
            self.extras[norm(row.get("teamname"))].append(
                Extras(date=str(row.get("date"))[:10], won=row.get("result") == "1", values=vals))
        for rows in self.extras.values():
            rows.sort(key=lambda r: r.date)

    @staticmethod
    def _oe_series(rows):
        """Each Oracle's Elixir game as a one-map series for the Elo."""
        games = collections.defaultdict(list)
        for row in rows:
            if row.get("position") == "team":
                games[row.get("gameid")].append(row)
        out = []
        for gid, pair in games.items():
            if len(pair) != 2:
                continue
            a, b = pair
            date = str(a.get("date"))[:10]
            wa = 1 if a.get("result") == "1" else 0
            out.append((date, norm(a.get("teamname")), norm(b.get("teamname")), wa, 1 - wa))
        return out

    # ---- lookups

    def maps_before(self, team, date):
        rows = self.maps.get(team) or []
        i = bisect.bisect_left(self._dates.get(team) or [], str(date)[:10])
        return rows[max(0, i - MAX_MAPS_PER_TEAM):i]

    def extras_before(self, team, date):
        rows = self.extras.get(team) or []
        i = bisect.bisect_left([r.date for r in rows], str(date)[:10])
        return rows[max(0, i - MAX_MAPS_PER_TEAM):i]

    def rating(self, team, date):
        rows = self.elo_history.get(team)
        if not rows:
            return ELO_START
        for d, r in rows:
            if d >= str(date)[:10]:
                return r
        return self.elo_final.get(team, ELO_START)

    def roster(self, team, date, size=5):
        """The players on a team's most recent map before `date`."""
        rows = self.maps_before(team, date)
        if not rows:
            return []
        last = rows[-1]
        return [(key, last.names.get(key, key)) for key in last.players][:size]


def _elo_history(series):
    rating = collections.defaultdict(lambda: ELO_START)
    history = collections.defaultdict(list)
    for date, a, b, wa, wb in series:
        for team in (a, b):
            if not history[team] or history[team][-1][0] != date:
                history[team].append((date, rating[team]))
        for won_a in [1] * wa + [0] * wb:
            exp_a = 1 / (1 + 10 ** ((rating[b] - rating[a]) / 400))
            rating[a] += ELO_K * (won_a - exp_a)
            rating[b] -= ELO_K * (won_a - exp_a)
    return dict(history), dict(rating)


def load_library(game, root=".", oe_dir="oracles_elixir"):
    path = os.path.join(root, SOURCES[game])
    if not os.path.exists(path):
        return None
    regions = json.load(open(path)).get("regions") or {}
    oe_rows = None
    oe_path = os.path.join(root, oe_dir)
    if game == "lol" and os.path.isdir(oe_path):
        oe_rows = []
        for name in sorted(os.listdir(oe_path)):
            if name.endswith(".csv.gz"):
                with gzip.open(os.path.join(oe_path, name), "rt", encoding="utf-8") as f:
                    oe_rows += list(csv.DictReader(f))
    return Library(game, regions, oe_rows)


# ----------------------------------------------------------------- factors

def kill_factors(regions, team_a, team_b):
    """(A's kill multiplier, B's): the event pace and the class gap, exactly as
    the projections apply them (scripts/dev/optimize_weights.py), read from
    the region whose roster file holds both teams."""
    import optimize_weights as ow
    for blob in (regions or {}).values():
        teams = blob.get("teams") or {}
        if team_a in teams and team_b in teams:
            cfg = {"key": "k"}
            fa = (ow.event_pace_multiplier(teams, team_a, team_b, cfg)
                  * ow.class_gap_multiplier(teams, team_a, team_b, cfg))
            fb = (ow.event_pace_multiplier(teams, team_b, team_a, cfg)
                  * ow.class_gap_multiplier(teams, team_b, team_a, cfg))
            return fa, fb
    return 1.0, 1.0


def best_of_for(match):
    """A past series' format: stated, or read off its score."""
    for key in ("best_of",):
        if isinstance(match.get(key), int):
            return match[key]
    fmt = str(match.get("series_format") or "")
    if fmt[:2].lower() == "bo" and fmt[2:].isdigit():
        return int(fmt[2:])
    try:
        need = max(int(x) for x in str(match.get("score")).split("-")[:2])
        return 2 * need - 1 if need > 0 else 3
    except ValueError:
        return 3


# ----------------------------------------------------------------- win chances

def elo_map_probability(ra, rb):
    return 1 / (1 + 10 ** ((rb - ra) / 400))


def series_probability(p, best_of):
    """P(win the series) for a map probability p."""
    need = best_of // 2 + 1
    total = 0.0
    for lost in range(need):
        total += math.comb(need - 1 + lost, lost) * p ** need * (1 - p) ** lost
    return total


def map_probability_from_series(q, best_of):
    """Invert series_probability by bisection."""
    if best_of <= 1:
        return q
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if series_probability(mid, best_of) < q:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def fixture_map_probability(lib, team_a, team_b, date, best_of, odds=None):
    """(p that A wins a map, where it came from)."""
    implied = (odds or {}).get("implied") or {}
    qa = None
    for name, q in implied.items():
        if norm(name) == team_a:
            qa = q
        elif norm(name) == team_b and qa is None:
            qa = 1 - q
    if isinstance(qa, (int, float)) and 0 < qa < 1:
        return map_probability_from_series(qa, best_of), "odds"
    return elo_map_probability(lib.rating(team_a, date), lib.rating(team_b, date)), "elo"


# ----------------------------------------------------------------- one run

def _weights(rows, date):
    today = _day_number(date)
    w = [0.5 ** (max(0, today - _day_number(r.date)) / HALF_LIFE_DAYS) for r in rows]
    return w if sum(w) > 0 else [1.0] * len(rows)


class Pool:
    """A team's drawable maps, split by result, with their weights."""

    def __init__(self, rows, date):
        self.all = rows
        self.by = {True: [r for r in rows if r.won], False: [r for r in rows if not r.won]}
        self.w = {k: _weights(v, date) for k, v in self.by.items()}
        self.w_all = _weights(rows, date)

    def draw(self, rng, won):
        rows = self.by[won]
        if len(rows) >= MIN_BUCKET:
            return rng.choices(rows, self.w[won])[0]
        return rng.choices(self.all, self.w_all)[0]


def _stochastic_round(x, rng):
    base = math.floor(x)
    return int(base + (1 if rng.random() < x - base else 0))


def _player_lines(drawn, roster, rng, stats):
    """{player key: {stat: n}} for the roster from one drawn map."""
    present = [p for p, _ in roster if p in drawn.players]
    spare = [p for p in drawn.players if p not in {q for q, _ in roster}]
    out = {}
    for p, _ in roster:
        if p in drawn.players:
            out[p] = dict(drawn.players[p])
        elif spare:
            out[p] = dict(drawn.players[spare.pop(0)])
        else:
            # nobody to stand in for: an average of the present players' lines
            src = [drawn.players[q] for q in present] or list(drawn.players.values())
            out[p] = {s: sum(v.get(s, 0) for v in src) / max(1, len(src)) for s in stats}
    return out


def _scale(lines, stat, target, rng):
    """Share `target` among the players in proportion to their drawn lines,
    in whole numbers that add up to exactly `target`: each takes the floor
    of their share and the leftover units go one at a time, drawn in
    proportion to what each was owed. Rounding each player on their own does
    not add up, and then one team's kills stop being the other's deaths."""
    keys = list(lines)
    raw = [max(0.0, float(lines[k].get(stat, 0))) for k in keys]
    total = sum(raw)
    shares = ([r * target / total for r in raw] if total > 0
              else [target / max(1, len(keys))] * len(keys))
    whole = [int(math.floor(x)) for x in shares]
    rest = [x - w for x, w in zip(shares, whole)]
    for _ in range(int(target) - sum(whole)):
        if sum(rest) <= 0:
            i = rng.randrange(len(keys))
        else:
            i = rng.choices(range(len(keys)), rest)[0]
            rest[i] = 0.0
        whole[i] += 1
    for k, w in zip(keys, whole):
        lines[k][stat] = w


@dataclass
class Fixture:
    game: str
    team_a: str
    team_b: str
    date: str
    best_of: int
    roster_a: list
    roster_b: list
    p_map: float
    p_source: str
    kill_factor_a: float = 1.0
    kill_factor_b: float = 1.0
    names: dict = field(default_factory=dict)


def play_map(lib, fx, pools, extras, rng):
    """One simulated map: (A won, {team: {player: stats}}, team kills, extras)."""
    a_won = rng.random() < fx.p_map
    ma = pools[0].draw(rng, a_won)
    mb = pools[1].draw(rng, not a_won)
    # The map's kills come from ONE of the two drawn maps, picked at random:
    # A's (its kills and deaths) or B's (its deaths and kills). Averaging the
    # two, as the first version did, halves the spread of a team's total and
    # removes what both sides of a map share -- its length: a long CS2 map or
    # a bloody LoL game gives BOTH teams more kills. Over many runs each
    # team's own style still counts half the time.
    if rng.random() < 0.5:
        ka, kb = ma.kills, ma.deaths
    else:
        ka, kb = mb.deaths, mb.kills
    if JITTER:
        shared = math.exp(rng.gauss(0, JITTER))
        ka *= shared
        kb *= shared
    ka *= fx.kill_factor_a
    kb *= fx.kill_factor_b
    ka_i, kb_i = _stochastic_round(ka, rng), _stochastic_round(kb, rng)
    lines_a = _player_lines(ma, fx.roster_a, rng, lib.stats)
    lines_b = _player_lines(mb, fx.roster_b, rng, lib.stats)
    for lines, kills, deaths, drawn in ((lines_a, ka_i, kb_i, ma), (lines_b, kb_i, ka_i, mb)):
        ratio = kills / drawn.kills if drawn.kills else 1.0
        _scale(lines, "k", kills, rng)
        _scale(lines, "d", deaths, rng)
        for v in lines.values():
            v["a"] = _stochastic_round(v.get("a", 0) * ratio, rng)
            if "hs" in lib.stats:
                v["hs"] = min(v["k"], _stochastic_round(v.get("hs", 0) * ratio, rng))
    extra = None
    if extras[0] and extras[1]:
        ea = rng.choice([e for e in extras[0] if e.won == a_won] or extras[0]).values
        eb = rng.choice([e for e in extras[1] if e.won != a_won] or extras[1]).values
        extra = {}
        la, lb = ea.get("gamelength"), eb.get("gamelength")
        if la and lb:
            extra["length_min"] = (la + lb) / 2 / 60
        for col in ("dragons", "barons", "towers"):
            if ea.get(col) is not None and eb.get(col) is not None:
                extra[col] = (ea[col], eb[col])
        for col in ("firstblood", "firstdragon", "firstbaron", "firsttower"):
            x, y = ea.get(col), eb.get(col)
            if x is None or y is None:
                continue
            extra[col] = "A" if (x == 1 and y != 1) else "B" if (y == 1 and x != 1) else (
                "A" if rng.random() < 0.5 else "B")
    return a_won, (lines_a, lines_b), (ka_i, kb_i), extra


def play_series(lib, fx, pools, extras, rng):
    need = fx.best_of // 2 + 1
    wins = [0, 0]
    maps = []
    while max(wins) < need:
        got = play_map(lib, fx, pools, extras, rng)
        wins[0 if got[0] else 1] += 1
        maps.append(got)
    return maps


def prepare(lib, team_a, team_b, date, best_of, roster_a=None, roster_b=None, odds=None,
            kill_factor_a=1.0, kill_factor_b=1.0):
    """A Fixture and its draw pools, or None when either side lacks history."""
    ta, tb = norm(team_a), norm(team_b)
    rows_a, rows_b = lib.maps_before(ta, date), lib.maps_before(tb, date)
    if len(rows_a) < MIN_MAPS or len(rows_b) < MIN_MAPS:
        return None
    ra = roster_a or lib.roster(ta, date)
    rb = roster_b or lib.roster(tb, date)
    p, source = fixture_map_probability(lib, ta, tb, date, best_of, odds)
    fx = Fixture(game=lib.game, team_a=ta, team_b=tb, date=str(date)[:10], best_of=best_of,
                 roster_a=ra, roster_b=rb, p_map=p, p_source=source,
                 kill_factor_a=kill_factor_a, kill_factor_b=kill_factor_b,
                 names={ta: lib.display.get(ta, team_a), tb: lib.display.get(tb, team_b)})
    pools = (Pool(rows_a, date), Pool(rows_b, date))
    extras = (lib.extras_before(ta, date), lib.extras_before(tb, date))
    return fx, pools, extras


def run(lib, prepared, runs=DEFAULT_RUNS, seed=1):
    fx, pools, extras = prepared
    rng = random.Random(seed)
    for _ in range(runs):
        yield play_series(lib, fx, pools, extras, rng)


# ----------------------------------------------------------------- summaries

def window_total(series, side, player, stat, window):
    """A player's total over maps 1..window, or None when fewer were played."""
    if len(series) < window:
        return None
    return sum(m[1][side][player].get(stat, 0) for m in series[:window])


def _hist(values):
    if not values:
        return None
    c = collections.Counter(values)
    lo, hi = min(c), max(c)
    return {"min": lo, "counts": [c.get(v, 0) for v in range(lo, hi + 1)]}


def _quantiles(values, qs=(0.1, 0.5, 0.9)):
    s = sorted(values)
    return [s[min(len(s) - 1, int(q * len(s)))] for q in qs]


def summarise(lib, prepared, runs=DEFAULT_RUNS, seed=1, windows=None):
    """Everything the app shows about one fixture, from `runs` simulated series."""
    fx = prepared[0]
    best_of = fx.best_of
    windows = windows or sorted({1, 2, 3} | ({best_of} if best_of > 3 else set()))
    windows = [w for w in windows if w <= best_of]
    stats = lib.stats
    sides = ((0, fx.team_a, fx.roster_a), (1, fx.team_b, fx.roster_b))
    scores = collections.Counter()
    maps_played = collections.Counter()
    per_map = [collections.defaultdict(list) for _ in range(best_of)]
    totals = {(side, p, s, w): [] for side, _, roster in sides for p, _ in roster
              for s in stats for w in windows}
    mvp = collections.Counter()
    top_map = [collections.Counter() for _ in range(best_of)]
    story = collections.Counter()
    a_series = 0
    close = CLOSE_MARGIN.get(lib.game, 5)
    for series in run(lib, prepared, runs, seed):
        wa = sum(1 for m in series if m[0])
        wb = len(series) - wa
        a_series += wa > wb
        scores[f"{wa}-{wb}"] += 1
        maps_played[len(series)] += 1
        series_kills = collections.Counter()
        for i, (a_won, lines, kills, extra) in enumerate(series):
            pm = per_map[i]
            pm["a_won"].append(1 if a_won else 0)
            pm["total"].append(kills[0] + kills[1])
            pm["kills_a"].append(kills[0])
            pm["kills_b"].append(kills[1])
            win_k, lose_k = (kills[0], kills[1]) if a_won else (kills[1], kills[0])
            pm["stomp"].append(1 if win_k >= STOMP_RATIO * max(1, lose_k) else 0)
            pm["close"].append(1 if win_k - lose_k <= close else 0)
            best = max(((side, p, lines[side][p]["k"]) for side, _, roster in sides
                        for p, _ in roster), key=lambda x: x[2])
            top_map[i][(best[0], best[1])] += 1
            for side, _, roster in sides:
                for p, _ in roster:
                    series_kills[(side, p)] += lines[side][p]["k"]
            if extra:
                for k, v in extra.items():
                    pm["x_" + k].append(v)
        for side, _, roster in sides:
            for p, _ in roster:
                for s in stats:
                    for w in windows:
                        t = window_total(series, side, p, s, w)
                        if t is not None:
                            totals[(side, p, s, w)].append(t)
        star = max(series_kills, key=series_kills.get)
        mvp[star] += 1
        story[(f"{wa}-{wb}", star)] += 1

    def name(side, p):
        roster = fx.roster_a if side == 0 else fx.roster_b
        return dict(roster).get(p, p)

    team_names = [fx.names.get(fx.team_a, fx.team_a), fx.names.get(fx.team_b, fx.team_b)]
    out_maps = []
    for i, pm in enumerate(per_map):
        n = len(pm["total"])
        if not n:
            continue
        got = {"map": i + 1, "played": n / runs, "a_win": sum(pm["a_won"]) / n,
               "total_kills": {"mean": sum(pm["total"]) / n, "q": _quantiles(pm["total"]),
                               "hist": _hist(pm["total"])},
               "kills_a": sum(pm["kills_a"]) / n, "kills_b": sum(pm["kills_b"]) / n,
               "stomp": sum(pm["stomp"]) / n, "close": sum(pm["close"]) / n,
               "top_fragger": [{"player": name(s, p), "team": team_names[s], "share": c / n}
                               for (s, p), c in top_map[i].most_common(4)]}
        if pm.get("x_length_min"):
            lengths = pm["x_length_min"]
            got["length_min"] = {"mean": sum(lengths) / len(lengths), "q": _quantiles(lengths),
                                 "over_35": sum(1 for x in lengths if x > 35) / len(lengths),
                                 "under_28": sum(1 for x in lengths if x < 28) / len(lengths)}
        for col in ("dragons", "barons", "towers"):
            vals = pm.get("x_" + col)
            if vals:
                got[col] = [sum(v[0] for v in vals) / len(vals), sum(v[1] for v in vals) / len(vals)]
        for col in ("firstblood", "firstdragon", "firstbaron", "firsttower"):
            vals = pm.get("x_" + col)
            if vals:
                got[col] = sum(1 for v in vals if v == "A") / len(vals)
        out_maps.append(got)
    players = []
    for side, team, roster in sides:
        for p, display in roster:
            entry = {"player": display, "team": team_names[side], "side": side, "stats": {}}
            for s in stats:
                per = {}
                for w in windows:
                    vals = totals[(side, p, s, w)]
                    if vals:
                        per[str(w)] = {"played": len(vals) / runs, "mean": sum(vals) / len(vals),
                                       "q": _quantiles(vals), "hist": _hist(vals)}
                entry["stats"][s] = per
            entry["mvp"] = mvp[(side, p)] / runs
            players.append(entry)
    return {
        "game": lib.game, "teams": team_names, "date": fx.date, "best_of": best_of,
        "runs": runs, "p_map": fx.p_map, "p_map_source": fx.p_source,
        "elo": [lib.rating(fx.team_a, fx.date), lib.rating(fx.team_b, fx.date)],
        "kill_factors": [fx.kill_factor_a, fx.kill_factor_b],
        "series": {"a_win": a_series / runs,
                   "scores": {k: v / runs for k, v in sorted(scores.items())},
                   "maps_played": {str(k): v / runs for k, v in sorted(maps_played.items())}},
        "maps": out_maps, "players": players,
        "storylines": [{"score": sc, "mvp": name(s, p), "mvp_team": team_names[s],
                        "share": c / runs} for (sc, (s, p)), c in story.most_common(6)],
    }


def p_over(hist, line):
    """(P over, P push, P under) from a summarised histogram."""
    if not hist:
        return None
    n = sum(hist["counts"])
    over = push = 0
    for i, c in enumerate(hist["counts"]):
        v = hist["min"] + i
        if v > line:
            over += c
        elif v == line:
            push += c
    return over / n, push / n, (n - over - push) / n


# ----------------------------------------------------------------- command line

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", required=True, choices=sorted(SOURCES))
    ap.add_argument("--teams", nargs=2, required=True)
    ap.add_argument("--date", default=None, help="simulate as of this date (default: today)")
    ap.add_argument("--best-of", type=int, default=3, dest="best_of")
    ap.add_argument("--runs", type=int, default=DEFAULT_RUNS)
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)
    import datetime
    date = args.date or datetime.date.today().isoformat()
    lib = load_library(args.game, args.root)
    prepared = prepare(lib, args.teams[0], args.teams[1], date, args.best_of)
    if prepared is None:
        print("not enough history for one of the teams")
        return 1
    got = summarise(lib, prepared, args.runs)
    a, b = got["teams"]
    print(f"{a} v {b}, Bo{got['best_of']}, {got['runs']} runs; map win {got['p_map']:.1%} "
          f"({got['p_map_source']}); {a} wins the series {got['series']['a_win']:.1%}")
    print("  scores: " + ", ".join(f"{k} {v:.0%}" for k, v in got["series"]["scores"].items()))
    for m in got["maps"]:
        line = (f"  map {m['map']} (played {m['played']:.0%}): {a} wins {m['a_win']:.0%}, "
                f"{m['total_kills']['mean']:.1f} kills (10-90%: {m['total_kills']['q'][0]}-"
                f"{m['total_kills']['q'][2]}), stomp {m['stomp']:.0%}, close {m['close']:.0%}")
        if "length_min" in m:
            line += f", {m['length_min']['mean']:.1f} min"
        print(line)
    for p in got["players"]:
        k = p["stats"]["k"]
        print(f"  {p['player']:<14} {p['team']:<18} kills map1 {k['1']['mean']:.1f}"
              + (f"  maps1-2 {k['2']['mean']:.1f}" if "2" in k else "")
              + f"  series MVP {p['mvp']:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

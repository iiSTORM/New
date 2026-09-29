"""Usage inputs for the NFL model, from nflverse's weekly player stats.

The prototype's shares are hand-set -- "2026 wks1-2 targets blended with 2025
roles" in a comment -- which is fine for one game on one night and is the single
biggest reason that model cannot be pointed at a second fixture. This builds the
same numbers by measurement: target share, carry share, catch rate, yards per
reception and yards per carry, straight from what the players actually did.

Stdlib only, including the download: urllib and csv, so `propedge` still needs
nothing installed. The file is about 1.5MB a season.

WHAT THIS DOES NOT DO. It measures usage, not opponent strength, and it has no
opinion about whether a share will hold. Three weeks of a season is a small
sample and a player's share over three games is mostly noise -- which is why
`min_games` exists, why the shares are shrunk toward the positional average, and
why nothing here is a substitute for calibrating to the market. Usage tells you
who gets the ball. The book tells you how far it goes.
"""
import csv
import io
import os
import urllib.request
from collections import defaultdict

from .nfl import OTHER, TeamSetup

WEEKLY_URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
              "stats_player/stats_player_week_{season}.csv")
#: Games below which a player's share is mostly noise and gets shrunk hard.
SHRINK_GAMES = 3.0
#: A league-ish prior for the rates, used where a player has almost no sample.
PRIOR_CATCH_RATE = 0.65
PRIOR_YPR = 10.5
PRIOR_YPC = 4.2
REGULAR = "REG"


def fetch_weekly(season, path=None, cache=None, timeout=60):
    """Rows of one season's weekly player stats.

    `path` reads a file instead of the network, which is how the tests run and
    how a repeat run avoids downloading 1.5MB again. `cache` writes what it
    downloads there.
    """
    if path:
        with open(path, newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    if cache and os.path.exists(cache):
        return fetch_weekly(season, path=cache)
    url = WEEKLY_URL.format(season=season)
    with urllib.request.urlopen(url, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    if cache:
        with open(cache, "w", encoding="utf-8") as handle:
            handle.write(raw)
    return list(csv.DictReader(io.StringIO(raw)))


def _number(row, key):
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def _shrink(share, games, prior, shrink_games=SHRINK_GAMES):
    """Pull a rate toward a prior by how many games are behind it."""
    if games <= 0:
        return prior
    trust = games / (games + shrink_games)
    return prior + (share - prior) * trust


def team_rows(rows, team, weeks=None, season_type=REGULAR):
    out = []
    for row in rows:
        if row.get("team") != team:
            continue
        if season_type and row.get("season_type") not in (season_type, None, ""):
            continue
        if weeks and str(row.get("week")) not in {str(w) for w in weeks}:
            continue
        out.append(row)
    return out


def usage(rows, team, weeks=None, min_games=1, quarterback=None):
    """Measured shares and rates for one team.

    Returns a dict the caller can turn into a TeamSetup, with the totals kept so
    the caller can see how thin the sample is rather than being handed shares
    that look equally solid at three games and thirty.
    """
    mine = team_rows(rows, team, weeks)
    if not mine:
        return None
    games = len({row.get("week") for row in mine})
    per_player = defaultdict(lambda: defaultdict(float))
    appearances = defaultdict(set)
    for row in mine:
        name = row.get("player_display_name")
        if not name:
            continue
        appearances[name].add(row.get("week"))
        for key in ("targets", "receptions", "receiving_yards", "carries",
                    "rushing_yards", "attempts", "completions", "passing_yards"):
            per_player[name][key] += _number(row, key)

    total_targets = sum(p["targets"] for p in per_player.values())
    total_carries = sum(p["carries"] for p in per_player.values())
    # EVERY dropback, not the starter's. A team that used two quarterbacks has
    # its pass rate understated by however many the backup threw -- Chicago
    # came out at 34% that way, which is not a football number.
    total_attempts = sum(p["attempts"] for p in per_player.values())
    # `quarterback` is an override because measured usage answers "who threw the
    # most over these weeks", and the question that matters is "who is starting
    # on Sunday". Over weeks 1-3 of 2026 those are different people in Chicago.
    if quarterback is None:
        quarterback = max(per_player, key=lambda n: per_player[n]["attempts"],
                          default=None)
    qb = per_player.get(quarterback) or {}

    targets, rushers, catch_rate, ypr, ypc = {}, {}, {}, {}, {}
    for name, stats in per_player.items():
        played = len(appearances[name])
        if played < min_games:
            continue
        if stats["targets"] and total_targets:
            targets[name] = stats["targets"] / total_targets
            catch_rate[name] = _shrink(
                stats["receptions"] / stats["targets"], stats["targets"] / 5,
                PRIOR_CATCH_RATE)
            if stats["receptions"]:
                ypr[name] = _shrink(stats["receiving_yards"] / stats["receptions"],
                                    stats["receptions"] / 4, PRIOR_YPR)
        if stats["carries"] and total_carries and name != quarterback:
            rushers[name] = stats["carries"] / total_carries
            ypc[name] = _shrink(stats["rushing_yards"] / stats["carries"],
                                stats["carries"] / 8, PRIOR_YPC)

    return {
        "team": team, "games": games, "quarterback": quarterback,
        "targets": targets, "rushers": rushers, "catch_rate": catch_rate,
        "ypr": ypr, "ypc": ypc,
        "pass_attempts_per_game": total_attempts / max(games, 1),
        "starter_attempts_per_game": qb.get("attempts", 0) / max(games, 1),
        "carries_per_game": total_carries / max(games, 1),
        "completion_rate": (qb.get("completions", 0) / qb["attempts"]
                            if qb.get("attempts") else 0.62),
        "qb_carries_per_game": (per_player.get(quarterback, {}).get("carries", 0)
                                / max(games, 1)) if quarterback else 0.0,
        "qb_rush_yards": (per_player.get(quarterback, {}).get("rushing_yards", 0)
                          if quarterback else 0.0),
    }


def team_setup(measured, keep=6, plays_sd=6.0, script_k=0.006, sack_rate=0.075,
               quarterback=None):
    """A TeamSetup from `usage`, with the long tail folded into "other".

    Only the players who actually see the ball are named. Everyone else becomes
    the `other` share, which keeps the Dirichlet draw on a real simplex without
    pretending to project a fourth tight end.
    """
    if not measured:
        return None, {}

    def top(shares):
        ranked = sorted(shares.items(), key=lambda kv: -kv[1])[:keep]
        named = dict(ranked)
        rest = max(0.0, 1.0 - sum(named.values()))
        if rest > 0.001:
            named[OTHER] = rest
        return named

    plays = measured["pass_attempts_per_game"] + measured["carries_per_game"]
    pass_rate = (measured["pass_attempts_per_game"] / plays) if plays else 0.5
    qb_carries = measured["qb_carries_per_game"]
    setup = TeamSetup(
        code=measured["team"],
        quarterback=quarterback or measured["quarterback"] or "QB",
        plays=max(40.0, min(85.0, plays)), plays_sd=plays_sd,
        pass_rate=max(0.3, min(0.8, pass_rate)), script_k=script_k,
        completion_rate=max(0.5, min(0.78, measured["completion_rate"])),
        sack_rate=sack_rate, qb_rush_attempts=qb_carries,
        qb_rush_ypc=(measured["qb_rush_yards"] /
                     max(qb_carries * measured["games"], 1)) or 4.0,
        rushers=top(measured["rushers"]), targets=top(measured["targets"]))
    rates = {"catch_rate": measured["catch_rate"], "ypr": measured["ypr"],
             "ypc": measured["ypc"]}
    return setup, rates

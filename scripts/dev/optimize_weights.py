#!/usr/bin/env python3
"""
Backtests the kill-projector model against real historical results and
searches for the weight combination that minimizes prediction error.

This is a direct, function-for-function Python port of the model in
kill-projector.jsx — same recency decay, same point-in-time logic, same
patch-awareness, same fallbacks. The point is to measure "how accurate is
what's actually deployed" and find better weights for it, not to build a
separate idealized model that the app doesn't actually run.

Usage (run from the repo root, where data.json / valorant_data.json live):
    python scripts/optimize_weights.py
    python scripts/optimize_weights.py --stat deaths
    python scripts/optimize_weights.py --game valorant

Outputs:
  1. Baseline MAE (mean absolute error, in kills/deaths/assists per player
     per 2-game window) using the app's current DEFAULT_WEIGHTS.
  2. A coordinate-descent search over the weight space, reporting the best
     weights found and the resulting MAE.
  3. A per-region breakdown so you can see if any one region is dragging
     the average down or behaving very differently from the rest.
"""
import argparse
import json
import re
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path

# ============================================================
# STAT TYPES — mirrors STAT_TYPES in kill-projector.jsx exactly.
# ============================================================
STAT_TYPES = {
    "kills": {"key": "k", "oppBasis": "d", "useKP": True, "laneSpecific": True},
    "deaths": {"key": "d", "oppBasis": "k", "useKP": False, "laneSpecific": True},
    "assists": {"key": "a", "oppBasis": "d", "useKP": True, "laneSpecific": False},
    # CS2 only -- LoL and Valorant record no such thing. Declared here the
    # same way src/app.jsx declares it, so both ports agree about which
    # stats a game actually has rather than one of them finding out by
    # getting zero rows back.
    "headshots": {"key": "hs", "oppBasis": "d", "useKP": False, "laneSpecific": False,
                  "games": ["cs2"]},
}

# The weights the app actually ships, mirrored from
# DEFAULT_WEIGHTS_BY_GAME_AND_STAT in src/app.jsx. DEFAULT_WEIGHTS below is
# only this script's search STARTING POINT and is not what anyone runs, so
# measuring it tells you nothing about the live model.
SHIPPED_WEIGHTS = {
    "lol": {
        "kills":    {"history": 0.8, "opponent": 0.4, "kp": 0.0, "recencyHalfLife": 8, "patchDiscount": 0.0, "career": 0.6, "share": 0.0, "shrink": 0.0},
        "deaths":   {"history": 0.8, "opponent": 0.4, "kp": 0.0, "recencyHalfLife": 8, "patchDiscount": 0.0, "career": 0.6, "share": 0.0, "shrink": 0.0},
        "assists":  {"history": 0.8, "opponent": 0.4, "kp": 0.0, "recencyHalfLife": 8, "patchDiscount": 0.0, "career": 0.6, "share": 0.0, "shrink": 0.0},
        "headshots": {"history": 0.0, "opponent": 0.0, "kp": 0.0, "recencyHalfLife": 20, "patchDiscount": 0.0, "career": 0.0, "share": 0.0, "shrink": 0.0},
    },
    "valorant": {
        "kills":    {"history": 0.7, "opponent": 0, "kp": 0.0, "recencyHalfLife": 8, "patchDiscount": 0, "career": 0, "share": 0.4, "shrink": 4.0},
        "deaths":   {"history": 0.7, "opponent": 0, "kp": 0.0, "recencyHalfLife": 6, "patchDiscount": 0.3, "career": 0, "share": 0.7, "shrink": 0.0},
        "assists":  {"history": 0.4, "opponent": 0, "kp": 0.0, "recencyHalfLife": 6, "patchDiscount": 0.8, "career": 0, "share": 0.4, "shrink": 1.0},
        "headshots": {"history": 0.0, "opponent": 0.0, "kp": 0.0, "recencyHalfLife": 20, "patchDiscount": 0.0, "career": 0.0, "share": 0.0, "shrink": 0.0},
    },
    "cs2": {
        "kills":    {"history": 0.0, "opponent": 0.0, "kp": 0.0, "recencyHalfLife": 6, "patchDiscount": 0.0, "career": 1.0, "share": 0.0, "shrink": 8.0},
        "deaths":   {"history": 0.0, "opponent": 0.0, "kp": 0.0, "recencyHalfLife": 20, "patchDiscount": 0.0, "career": 1.0, "share": 0.6, "shrink": 4.0},
        "assists":  {"history": 0.0, "opponent": 0.0, "kp": 0.0, "recencyHalfLife": 20, "patchDiscount": 0.0, "career": 1.0, "share": 0.0, "shrink": 8.0},
        "headshots": {"history": 0.0, "opponent": 0.0, "kp": 0.0, "recencyHalfLife": 20, "patchDiscount": 0.0, "career": 0.0, "share": 0.0, "shrink": 3.0},
    },
}

DEFAULT_WEIGHTS = {
    "history": 0.3, "opponent": 1.0, "kp": 0.3,
    "recencyHalfLife": 6, "patchDiscount": 0.4, "career": 0.0, "share": 0.0, "shrink": 0.0,
    "careerRamp": 0,  # 0 = off, reproducing the previous flat career weight exactly; see project_point_in_time for what this measures and why
}


# ============================================================
# Direct ports of the JS model functions
# ============================================================

def stat_applies_to(stat_type, game):
    """Whether a game records this stat at all.

    A stat with no `games` list is universal. Asking a game for one it
    does not record is not an error, it just has nothing to say -- but it
    is worth saying so explicitly rather than reporting an empty sample.
    """
    games = STAT_TYPES[stat_type].get("games")
    return games is None or game in games

def get_actual_stat(match, team, player_name, stat_key):
    raw = (match.get("actual") or {}).get(team, {}).get(player_name)
    if raw is None:
        return None  # undefined equivalent: player didn't appear in this match
    if isinstance(raw, (int, float)):
        return raw if stat_key == "k" else "unavailable"  # legacy kills-only format
    return raw.get(stat_key)


@lru_cache(maxsize=None)
def parse_patch(patch_str):
    # Pure function of a short string, called once per match per
    # (player, cutoff) cache fill — memoized because the regex showed up
    # as real cost at search scale, and the set of distinct patch strings
    # across a whole dataset is tiny (a few dozen).
    if not patch_str:
        return None
    m = re.match(r"^(\d+)\.(\d+)", patch_str)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def compare_patch(a, b):
    if not a or not b:
        return 0
    return a[0] - b[0] if a[0] != b[0] else a[1] - b[1]


def latest_patch(past_matches, cutoff_date):
    best = None
    for m in past_matches:
        if cutoff_date is not None and (not m.get("date") or m["date"] >= cutoff_date):
            continue
        p = parse_patch(m.get("patch"))
        if p and (not best or compare_patch(p, best) > 0):
            best = p
    return best


_recency_entries_cache = {}  # keyed by (id(past_matches), team, player_name, stat_key, cutoff_date)

# CPython reuses memory addresses. Any cache keyed on id(obj) is therefore
# only sound while that object is still ALIVE -- if it is freed, a later
# object can be allocated at the same address and silently collide with
# the dead one's cache entries.
#
# This is not hypothetical: it was caught by a real experiment that built
# temporary filtered copies of past_matches in a loop. Each copy was
# freed before the next was built, so all of them landed on the SAME
# address, and pools of 100, 80 and 75 matches all read each other's
# cached values — producing identical MAEs for genuinely different
# inputs.
#
# Pinning a strong reference to every keyed object closes that hole
# completely: while an entry is cached, its object cannot be collected,
# so its id cannot be reused by anything else. Callers that build pools
# dynamically should still call clear_point_in_time_caches() between
# pools to bound memory, but correctness no longer depends on them
# remembering to.
_cache_keepalive = {}


def _pin(obj):
    """Returns id(obj), keeping a strong reference so the id stays valid
    and unique for as long as anything is cached against it."""
    key = id(obj)
    if key not in _cache_keepalive:
        _cache_keepalive[key] = obj
    return key


def clear_point_in_time_caches():
    """Drops every point-in-time cache and releases the pinned objects.
    Call this when switching to a different past_matches pool (e.g. a
    filtering experiment) to bound memory; correctness does not depend
    on it thanks to _pin above."""
    _recency_entries_cache.clear()
    _player_names_stat_cache.clear()
    _league_avg_for_role_cache.clear()
    _point_in_time_team_stat_cache.clear()
    _point_in_time_league_avg_stat_cache.clear()
    _league_avg_kp_cache.clear()
    _team_total_cache.clear()
    _share_rate_cache.clear()
    _league_pace_cache.clear()
    _cache_keepalive.clear()


def _recency_entries(past_matches, team, player_name, stat_key, cutoff_date):
    """Builds the (date, val, patch) list for one player as of one
    cutoff -- the expensive half of recency_weighted_rate, split out and
    cached because NONE of it depends on the weights being searched.

    This was a real, measured bottleneck. recency_weighted_rate runs
    once per prediction, and each call rescanned every match in the
    region and ran the parse_patch regex on each one. A full LoL search
    is ~7,400 predictions x ~210 weight evaluations per stat, so the
    identical filter+sort+regex work was being redone hundreds of times
    for every single (player, cutoff) pair -- hundreds of millions of
    regex calls per stat. After the tournament-discovery work quadrupled
    the match pool (241 -> 967 series), that tipped a full run into tens
    of minutes.

    Only the weighting loop left in recency_weighted_rate actually
    varies with half_life / patch_discount / reference_patch, so that
    stays uncached and cheap (it iterates just this player's games).

    Cached on id(past_matches) following the same rationale already
    documented for _point_in_time_team_stat_cache: past_matches is a
    stable object reference for the whole duration of one run, loaded
    once via load_region_data and never rebuilt mid-search. Entries are
    plain tuples rather than dicts to keep the cache lean, since it
    holds roughly one list per (player, match) pair."""
    key = (_pin(past_matches), team, player_name, stat_key, cutoff_date)
    cached = _recency_entries_cache.get(key)
    if cached is not None:
        return cached
    entries = []
    for m in past_matches:
        if cutoff_date is not None and (not m.get("date") or m["date"] >= cutoff_date):
            continue
        if not m.get("actual") or team not in (m.get("teamA"), m.get("teamB")) or team not in m["actual"]:
            continue
        val = get_actual_stat(m, team, player_name, stat_key)
        if val is None or val == "unavailable":
            continue
        # maps_counted rides along so the weighted per-game rate below
        # can divide by the maps a series ACTUALLY covered. Without it a
        # Bo5's 3-map total is divided by 2, inflating that player's rate
        # — which is exactly what pushed the optimizer to shift weight
        # off recent form and onto the prior-split `hist` figure (which
        # comes from gol.gg's own per-game stats and stayed correct).
        entries.append((m.get("date") or "", val, parse_patch(m.get("patch")),
                        m.get("maps_counted", 2)))
    entries.sort(key=lambda e: e[0])
    _recency_entries_cache[key] = entries
    return entries


def recency_weighted_rate(past_matches, team, player_name, stat_key, cutoff_date, half_life,
                           reference_patch, patch_discount):
    entries = _recency_entries(past_matches, team, player_name, stat_key, cutoff_date)
    if not entries:
        return None, 0
    n = len(entries)
    flat = half_life >= 20
    weighted_sum, weighted_games = 0.0, 0.0
    total_maps = 0
    for idx, (_date, val, patch, maps) in enumerate(entries):
        matches_ago = (n - 1) - idx
        weight = 1.0 if flat else 0.5 ** (matches_ago / half_life)
        if reference_patch and patch and compare_patch(patch, reference_patch) != 0:
            weight *= (1 - patch_discount)
        weighted_sum += val * weight
        weighted_games += maps * weight
        total_maps += maps
    rate = weighted_sum / weighted_games if weighted_games > 0 else None
    # Second return value is the prior-GAMES count used for cold-start
    # detection, so it must also reflect real maps rather than n * 2.
    return rate, total_maps


def likely_starters(players):
    """Same heuristic as kill-projector.jsx's likelyStarters() — the 5
    players with the most recorded games are the most likely current
    starters, used whenever a team's players list exceeds 5 (CS2 in
    particular has no roster endpoint at all, so its player list is the
    union of everyone who's appeared for that team name across the whole
    discovery window, which can genuinely exceed 5 once a roster change
    happens mid-window)."""
    if len(players) <= 5:
        return players
    return sorted(players, key=lambda p: p.get("cur", {}).get("g", 0), reverse=True)[:5]


def team_stat_per_game(teams, team_name, stat_key):
    # A team can appear in past_matches without having an entry in `teams`
    # — real case: CS2's "Haunted House" showed up as a historical
    # opponent but was never in the discovered/backfilled team set, which
    # crashed the whole assists run with a KeyError. Returning None lets
    # callers fall back to a league-average-style estimate instead of
    # dying on one incomplete team.
    entry = teams.get(team_name)
    if not entry or not entry.get("players"):
        return None
    all_players = entry["players"]
    # Distinct-role count handles LoL roster swaps correctly (e.g. two
    # players sharing "JNG" after a mid-split change still count as one
    # active slot) — computed against the FULL roster history, not just
    # likely starters, since a departed player's role still legitimately
    # counts toward "how many distinct roles has this team fielded."
    distinct_roles = len(set(p.get("role") for p in all_players))
    if distinct_roles > 1:
        return sum(p["cur"][stat_key] for p in all_players) / distinct_roles
    # No role data (CS2, or Valorant players without one) — both the sum
    # AND the divisor now consistently scope to the same likely-starters
    # subset. A real, confirmed bug here previously summed EVERY player
    # unconditionally while only capping the divisor at min(5,
    # len(players)) — silently inflating any team with more than 5
    # tracked players, which fed directly into resolve_opponent_
    # multiplier's team-wide path (the one CS2 kills/deaths/assists all
    # actually use).
    players = likely_starters(all_players)
    return sum(p["cur"][stat_key] for p in players) / (len(players) or 1)


def league_avg_stat(teams, stat_key):
    values = [v for v in (team_stat_per_game(teams, t, stat_key) for t in teams) if v is not None]
    return sum(values) / len(values) if values else None


_point_in_time_team_stat_cache = {}  # keyed by (id(past_matches), team, stat_key, cutoff_date) -- past_matches is a stable object reference for the whole duration of one evaluation run, same reasoning as the game_has_role_data cache above


def point_in_time_team_stat(past_matches, team, stat_key, cutoff_date):
    key = (_pin(past_matches), team, stat_key, cutoff_date)
    if key in _point_in_time_team_stat_cache:
        return _point_in_time_team_stat_cache[key]
    total, games = 0.0, 0
    for m in past_matches:
        if not m.get("date") or m["date"] >= cutoff_date:
            continue
        opp = m["teamB"] if m["teamA"] == team else (m["teamA"] if m["teamB"] == team else None)
        if not opp or not m.get("actual"):
            continue
        source_team = opp if stat_key == "d" else team
        source_data = m["actual"].get(source_team)
        if not source_data:
            continue
        for player_name in source_data:
            val = get_actual_stat(m, source_team, player_name, "k")
            if isinstance(val, (int, float)):
                total += val
        # Real map count, not a fixed 2 — "actual" sums maps 1-2 for a
        # Bo3 but 1-3 for a Bo5, so dividing every match by 2 inflates
        # the per-game rate for anyone with Bo5 history.
        games += m.get("maps_counted", 2)
    result = total / games if games > 0 else None
    _point_in_time_team_stat_cache[key] = result
    return result


_point_in_time_league_avg_stat_cache = {}  # keyed by (id(past_matches), id(teams), stat_key, cutoff_date)


def point_in_time_league_avg_stat(past_matches, teams, stat_key, cutoff_date):
    # This is the real, much bigger cost that was found causing a
    # measured 20+ minute CS2 run (well outside the normal range): it
    # loops over EVERY team and calls point_in_time_team_stat for each,
    # which itself does a full scan over past_matches with a nested
    # per-player loop — O(teams x matches x players), previously
    # recomputed from scratch on every single call with zero caching.
    # This path used to only run for assists; the earlier fix routing
    # CS2 kills/deaths through team-wide opponent comparison (since CS2
    # has no role data for the lane-specific path) meant it started
    # getting hit far more often without this cost being addressed at
    # the same time.
    key = (_pin(past_matches), _pin(teams), stat_key, cutoff_date)
    if key in _point_in_time_league_avg_stat_cache:
        return _point_in_time_league_avg_stat_cache[key]
    rates = [r for r in (point_in_time_team_stat(past_matches, t, stat_key, cutoff_date) for t in teams) if r is not None]
    result = sum(rates) / len(rates) if rates else None
    _point_in_time_league_avg_stat_cache[key] = result
    return result


# ============================================================
# Lane-specific opponent adjustment — mirrors the same feature in
# kill-projector.jsx. A player's kills/deaths are compared against the
# specific opponent in their own role, not a team-wide average. Assists
# stay team-wide (STAT_TYPES["assists"]["laneSpecific"] = False) since the
# diagnostic found assists had the strongest team-wide signal of the three.
# ============================================================

_player_names_stat_cache = {}  # keyed by (id(past_matches), team, player_names, stat_key, cutoff_date)


def point_in_time_player_names_stat(past_matches, team, player_names, stat_key, cutoff_date):
    # Cached: weight-independent, and profiling a real LoL search showed
    # this single function accounting for ~95% of total runtime
    # (92,400 calls per weight evaluation, x ~630 evaluations in a full
    # 3-stat search). See point_in_time_league_avg_for_role below for
    # the bigger structural reason it was called so often.
    key = (_pin(past_matches), team, tuple(player_names), stat_key, cutoff_date)
    cached = _player_names_stat_cache.get(key)
    if cached is not None:
        return cached
    total, games = 0.0, 0
    for m in past_matches:
        if not m.get("date") or m["date"] >= cutoff_date:
            continue
        if not m.get("actual") or team not in m["actual"]:
            continue
        maps = m.get("maps_counted", 2)  # see note in point_in_time_team_stat
        for name in player_names:
            val = get_actual_stat(m, team, name, stat_key)
            if isinstance(val, (int, float)):
                total += val
                games += maps
    rate = total / games if games > 0 else None
    result = (rate, games)
    _player_names_stat_cache[key] = result
    return result


_league_avg_for_role_cache = {}  # keyed by (id(past_matches), id(teams), role, stat_key, cutoff_date)


def point_in_time_league_avg_for_role(past_matches, teams, role, stat_key, cutoff_date):
    """Cached — this was the single dominant cost in a full weight
    search, and the reason is structural rather than subtle.

    For a given (role, stat_key, cutoff_date) this returns the SAME
    league-wide average for every player in that role, and it does not
    depend on any weight being searched. But it sits inside
    resolve_opponent_multiplier, which runs once per prediction, so it
    was being recomputed ~8,400 times per weight evaluation and ~630
    times over a full 3-stat search — scanning every match in the region
    for every team on each of those calls. Across a real dataset that is
    on the order of 700 genuinely distinct values being computed tens of
    millions of times.

    Same id()-keyed caching rationale already documented for the other
    point-in-time caches: past_matches and teams are stable object
    references for the whole duration of one run."""
    key = (_pin(past_matches), _pin(teams), role, stat_key, cutoff_date)
    cached = _league_avg_for_role_cache.get(key)
    if cached is not None:
        return cached
    rates = []
    for team_name, team_data in teams.items():
        role_names = [p["name"] for p in team_data["players"] if p.get("role") == role]
        if not role_names:
            continue
        r, _ = point_in_time_player_names_stat(past_matches, team_name, role_names, stat_key, cutoff_date)
        if r is not None:
            rates.append(r)
    result = sum(rates) / len(rates) if rates else None
    _league_avg_for_role_cache[key] = result
    return result


def lane_opponent_multiplier(past_matches, teams, player, opponent_team, opp_strength, opp_basis_key, cutoff_date):
    role = player.get("role")
    if not role or opponent_team not in teams:
        return None
    opponent_role_names = [p["name"] for p in teams[opponent_team]["players"] if p.get("role") == role]
    if not opponent_role_names:
        return None
    opp_stat, _ = point_in_time_player_names_stat(past_matches, opponent_team, opponent_role_names, opp_basis_key, cutoff_date)
    league_avg = point_in_time_league_avg_for_role(past_matches, teams, role, opp_basis_key, cutoff_date)
    if opp_stat is None or not league_avg:
        return None
    return 1 + opp_strength * (opp_stat / league_avg - 1)


_game_has_role_data_cache = {}  # keyed by id(teams) -- teams is a stable object reference for the whole duration of one evaluation run (loaded once via load_region_data, never reconstructed mid-search), so this is safe and avoids a real, meaningful cost: this function is called from resolve_opponent_multiplier, which runs for EVERY prediction, potentially hundreds of thousands to millions of times across a full coordinate-descent search — a real, measured slowdown (a full CS2 run taking 20+ minutes, well outside the normal range) traced back to this doing a fresh full-player scan on every single one of those calls with no caching at all.


def game_has_role_data(teams):
    """True if ANY player in this dataset has role info at all — used to
    distinguish "this specific player is missing a role" (LoL/Valorant,
    where a real diagnostic found team-wide as a FALLBACK for that case
    specifically to be weak-to-negative — see resolve_opponent_multiplier)
    from "this game doesn't track roles at all" (CS2 — role is always
    None for every player, confirmed via direct inspection, not an edge
    case). The original neutral-over-team-wide-fallback finding was
    measured before CS2 existed in this app at all, so it was never
    actually validated for a game with zero role data; applying it there
    anyway made `opponent` completely inert for CS2 kills/deaths without
    that ever being a deliberate, tested decision — this restores a
    real, functioning opponent adjustment for CS2 specifically without
    touching LoL/Valorant's already-validated behavior at all."""
    key = id(teams)
    if key not in _game_has_role_data_cache:
        _game_has_role_data_cache[key] = any(
            p.get("role") for team in teams.values() for p in team.get("players", [])
        )
    return _game_has_role_data_cache[key]


def resolve_opponent_multiplier(teams, past_matches, player, opponent_team, opp_strength, cfg, cutoff_date):
    if cfg["laneSpecific"] and game_has_role_data(teams):
        # Only reached for games that actually track roles (LoL,
        # Valorant). For lane-specific stats (kills, deaths) there, a
        # real diagnostic (--diagnose-opponent, split by used_lane) found
        # the team-wide fallback signal weak-to-negative on its own --
        # using it as a fallback for the occasional player missing a
        # role match was silently cancelling out the real lane-specific
        # signal, since a single opponent weight applies uniformly
        # across both populations. Neutral (no adjustment) beats a
        # fallback we've specifically measured to be unreliable, for
        # THIS case specifically.
        lane_mult = lane_opponent_multiplier(past_matches, teams, player, opponent_team, opp_strength, cfg["oppBasis"], cutoff_date)
        return lane_mult if lane_mult is not None else 1.0
    # Team-wide path — used for assists always (laneSpecific=False), AND
    # now for kills/deaths in any game with NO role data at all (CS2).
    # That second case used to silently fall through to the branch above
    # and always return neutral (1.0), since lane_opponent_multiplier
    # immediately bails when player.get("role") is falsy — which is
    # EVERY CS2 player, always, confirmed via direct inspection, not an
    # edge case. That meant `opponent` was completely inert for CS2
    # kills/deaths, undocumented and unintended, not a deliberate
    # decision the way the neutral-fallback above actually was for
    # LoL/Valorant.
    opp_stat_pt = point_in_time_team_stat(past_matches, opponent_team, cfg["oppBasis"], cutoff_date)
    league_avg_pt = point_in_time_league_avg_stat(past_matches, teams, cfg["oppBasis"], cutoff_date)
    opp_stat = opp_stat_pt if opp_stat_pt is not None else team_stat_per_game(teams, opponent_team, cfg["oppBasis"])
    league_avg = league_avg_pt if league_avg_pt is not None else league_avg_stat(teams, cfg["oppBasis"])
    if opp_stat is None or not league_avg:
        return 1.0  # no usable opponent estimate — stay neutral rather than guess
    return 1 + opp_strength * (opp_stat / league_avg - 1)


# The kill-participation baseline, derived from the roster instead of
# hardcoded. See the long note on leagueAvgKP in src/app.jsx: the literal
# 66.0 that lived here is a League of Legends figure, and against it no
# CS2 or Valorant player (medians 26.4 / 27.7, maxima 39.7 / 35.1) could
# ever score above 1.0, turning a two-sided adjustment into a flat ~6%
# haircut on every projection in both games. MAE -- the only thing every
# weight in this file was ever tuned against -- cannot see a constant
# offset, so it survived every search run here.
LEAGUE_AVG_KP_FALLBACK = 66.0  # only reached when no player carries a kp at all
_league_avg_kp_cache = {}  # keyed by id(teams) -- same stable-reference reasoning as the caches above


def league_avg_kp(teams):
    if not teams:
        return LEAGUE_AVG_KP_FALLBACK
    key = _pin(teams)
    if key in _league_avg_kp_cache:
        return _league_avg_kp_cache[key]
    values = [p["cur"]["kp"] for entry in teams.values()
              for p in (entry or {}).get("players") or []
              if isinstance((p.get("cur") or {}).get("kp"), (int, float)) and p["cur"]["kp"] > 0]
    avg = sum(values) / len(values) if values else LEAGUE_AVG_KP_FALLBACK
    _league_avg_kp_cache[key] = avg
    return avg


KP_SHRINK = 4  # prior matches at which a player's own KP is worth as much
               # as the league's. Their per-match sample is thin (median 2
               # in CS2), and unshrunk it errs by 3.86pp against 3.32pp
               # shrunk.


def match_kp(match, team, player_name):
    """One match's kill participation, as a percentage.

    kp_numerator is exactly k + a on all 1414 CS2 rows that carry it, so
    the same figure is derivable for LoL and Valorant, which do not.
    kp_denominator is preferred where present because it is the team's
    kills WHILE THAT PLAYER PLAYED -- it differs between players on a side
    that used a substitute -- and falls back to the side's total.
    """
    raw = (match.get("actual") or {}).get(team, {}).get(player_name)
    if not isinstance(raw, dict):
        return None
    den = raw.get("kp_denominator") or team_total(match, team, "k")
    if not den:
        return None
    num = raw.get("kp_numerator")
    if num is None:
        if raw.get("k") is None or raw.get("a") is None:
            return None
        num = raw["k"] + raw["a"]
    return 100.0 * num / den


def point_in_time_kp(past_matches, teams, team, player_name, cutoff_date):
    """Recency-weighted KP from matches before the cutoff, shrunk to league.

    Replaces reading player["cur"]["kp"], which is a WHOLE-SEASON figure
    with no cutoff awareness -- so a backtest of a match in May was fed a
    kill participation partly built from games played in August. That leak
    was worth 0.66pp of apparent accuracy, measured by comparing the
    season figure against its own leave-one-out version.
    """
    values = [v for v in (match_kp(m, team, player_name)
                          for m in _prior(past_matches, team, cutoff_date))
              if v is not None]
    if not values:
        return None
    rate = _weighted_mean(_decayed(values))
    league = league_avg_kp(teams)
    if league:
        w = len(values) / (len(values) + KP_SHRINK)
        rate = w * rate + (1 - w) * league
    return rate


def kp_multiplier(player, history_weight, kp_strength, teams,
                  past_matches=None, team=None, cutoff_date=None):
    if not kp_strength:
        return 1.0
    league = league_avg_kp(teams)
    kp = None
    if past_matches is not None and team is not None:
        kp = point_in_time_kp(past_matches, teams, team, player["name"], cutoff_date)
    if kp is None:
        # No prior appearances to build one from. The season figure is the
        # only thing left, and in the live path (cutoff_date None) it is
        # legitimately everything-so-far rather than a leak.
        cur_kp = player["cur"]["kp"]
        if not cur_kp:
            return 1.0
        hist_kp = player["hist"]["kp"] if player.get("hist") else cur_kp
        kp = history_weight * hist_kp + (1 - history_weight) * cur_kp
    return 1 + kp_strength * (kp / league - 1)


CS2_CAREER_DAY_HALF_LIFE = 180  # matches scrape_cs2_career.py's own constant -- measured via scripts/sweep_cs2_day_half_life.py; see that constant's comment for why the honest read is "this parameter barely matters" rather than "180 is the answer"


def point_in_time_cs2_career_rate(player, stat_key, cutoff_date):
    """Computes the decayed career baseline FRESH for this specific
    cutoff_date, from the player's raw per-game history
    (player["career_games"], each {"k","d","a","date"}) — replacing a
    real, confirmed leakage bug where a single static "career" number
    was computed once at scrape time ("most recent N games as of right
    now") with no cutoff awareness, meaning a historical backtest
    prediction could be fed a career number partly built from games that
    hadn't happened yet as of that prediction's own date. Filters to
    games strictly before cutoff_date first (string comparison, same
    convention the rest of this file already uses for match dates), then
    applies the same day-based exponential decay scrape_cs2_career.py's
    own decayed_baseline() uses, just computed point-in-time here instead
    of once globally."""
    games = player.get("career_games") or []
    eligible = [g for g in games if g.get("date") and g["date"] < cutoff_date]
    if not eligible:
        return None
    try:
        cutoff_dt = datetime.fromisoformat(cutoff_date.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None
    total_weight = 0.0
    weighted = 0.0
    for g in eligible:
        try:
            game_dt = datetime.fromisoformat(g["date"].replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, AttributeError):
            continue
        days_ago = max(0, (cutoff_dt - game_dt).days)
        weight = 0.5 ** (days_ago / CS2_CAREER_DAY_HALF_LIFE)
        total_weight += weight
        weighted += g.get(stat_key, 0) * weight
    return weighted / total_weight if total_weight > 0 else None


# ============================================================
# TEAM-SHARE TIER -- a second opinion built from a different quantity
# ============================================================
# The rest of the model predicts a player's kills per map directly. This
# tier predicts their SHARE of their own team's kills, and multiplies it
# by how many kills a map in this league tends to produce. The two are
# blended by the `share` weight, exactly as `career` is blended.
#
# It exists because those are measurably two different quantities. In
# CS2, the coefficient of variation of a player's raw series kills
# averages 0.235; of their share of the team total, 0.160 -- 32% steadier,
# and steadier for 111 of the 131 players with enough series to measure.
#
# The pace half is where the surprise is, and it decides the design.
# Correlation between a team's own recency-weighted history and its next
# match's per-map total (scripts/dev/experiment_kill_share.py):
#
#     CS2      r = -0.037      Valorant  r = +0.016      LoL  r = +0.208
#
# CS2 and Valorant team pace is NOISE. A team's own history predicts its
# next pace no better than a coin. The likely mechanism in CS2 is that
# being better shortens the map rather than raising the kill count -- a
# 13-4 has fewer rounds, so fewer kills to share -- and the two effects
# cancel. That is why the pace term here is the LEAGUE average and not
# the team's own: using the team's realised history imports that noise
# into the multiplier, and measured as a replacement it was 16-26% WORSE
# than the shipped model while the league version was better.
#
# An oracle variant, fed the real team total it is predicting, scores
# -19% on CS2 kills and -51% on CS2 deaths. That headroom is NOT
# reachable -- it is the value of knowing the noise -- but it does say
# where the remaining error lives, and that deaths are the most
# pace-driven stat of the three. The adopted weights match: deaths take
# much more of this tier than kills, and LoL takes none of it at all
# because its opponent term (r = +0.208, a real signal there) already
# does this job.
SHARE_HALF_LIFE = 6  # matches. Swept 2..999: moves OOS MAE by under 0.4pp
                     # on every adopted combination and never changes a
                     # fold count, so it is pinned rather than tuned.
_team_total_cache = {}
_share_rate_cache = {}
_league_pace_cache = {}


def team_total(match, team, stat_key):
    """Every recorded player's stat on one side of one match, summed.

    Reproduces CS2's own kp_denominator field exactly on all 282
    team-sides that carry one. Returns None below five players so a
    partially-recorded side cannot masquerade as a low-scoring team;
    every side in all three datasets currently records five.
    """
    key = (_pin(match), team, stat_key)
    if key in _team_total_cache:
        return _team_total_cache[key]
    side = (match.get("actual") or {}).get(team)
    total, seen = 0, 0
    for raw in (side or {}).values():
        if isinstance(raw, dict):
            if raw.get(stat_key) is not None:
                total += raw[stat_key]
                seen += 1
        elif isinstance(raw, (int, float)) and stat_key == "k":
            total += raw
            seen += 1
    result = total if seen >= 5 else None
    _team_total_cache[key] = result
    return result


def _decayed(values):
    n = len(values)
    return [(v, 0.5 ** ((n - 1 - i) / SHARE_HALF_LIFE)) for i, v in enumerate(values)]


def _weighted_mean(pairs):
    total_weight = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / total_weight if total_weight > 0 else None


def _prior(past_matches, team, cutoff_date):
    out = [m for m in past_matches
           if m.get("actual") and (m.get("teamA") == team or m.get("teamB") == team)
           and (cutoff_date is None or (m.get("date") and m["date"] < cutoff_date))]
    out.sort(key=lambda m: m.get("date") or "")
    return out


def share_rate(past_matches, team, player_name, stat_key, cutoff_date):
    """The player's recency-weighted share of their team's own total."""
    key = (_pin(past_matches), team, player_name, stat_key, cutoff_date)
    if key in _share_rate_cache:
        return _share_rate_cache[key]
    values = []
    for m in _prior(past_matches, team, cutoff_date):
        got = get_actual_stat(m, team, player_name, stat_key)
        if got is None or got == "unavailable":
            continue
        total = team_total(m, team, stat_key)
        if not total:
            continue
        values.append(got / total)
    result = _weighted_mean(_decayed(values)) if values else None
    _share_rate_cache[key] = result
    return result


def league_pace_per_map(past_matches, teams, stat_key, cutoff_date):
    """What one map in this league tends to produce for one team.

    Averaged over teams rather than over match-sides so a team that plays
    more often does not drag the league figure toward its own pace.
    """
    key = (_pin(past_matches), _pin(teams), stat_key, cutoff_date)
    if key in _league_pace_cache:
        return _league_pace_cache[key]
    per_team = []
    for team in teams:
        values = []
        for m in _prior(past_matches, team, cutoff_date):
            total = team_total(m, team, stat_key)
            if not total:
                continue
            values.append(total / (m.get("maps_counted") or 2))
        rate = _weighted_mean(_decayed(values)) if values else None
        if rate is not None:
            per_team.append(rate)
    result = sum(per_team) / len(per_team) if per_team else None
    _league_pace_cache[key] = result
    return result


def share_tier_rate(past_matches, teams, team, player_name, stat_key, cutoff_date):
    """share x league pace, per map -- or None when either half is missing."""
    share = share_rate(past_matches, team, player_name, stat_key, cutoff_date)
    if share is None:
        return None
    pace = league_pace_per_map(past_matches, teams, stat_key, cutoff_date)
    if not pace:
        return None
    return share * pace


def blend_share_tier(per_game, weight, past_matches, teams, team, player_name,
                     stat_key, cutoff_date):
    """Blend at the FINAL per-map figure, not into `base`.

    This is where it was measured: the tier is a second opinion on the
    whole prediction, opponent and kp multipliers included, rather than
    another input to one of them. Falls back to the unblended figure when
    the tier cannot be computed -- a player with no prior appearances, or
    a league with no completed matches -- so a thin region degrades to
    today's behaviour instead of losing its projection.
    """
    if not weight:
        return per_game
    tier = share_tier_rate(past_matches, teams, team, player_name, stat_key, cutoff_date)
    if tier is None:
        return per_game
    return (1 - weight) * per_game + weight * tier


# ============================================================
# THIN-SAMPLE SHRINKAGE
# ============================================================
# A player with three maps on record gets a rate computed from three
# maps, and the model treated that with exactly the confidence it gave a
# rate built from sixty. The error decomposition says what that costs:
# bucketing every prediction by how many prior maps the player had,
#
#     Valorant kills   0-5 maps: MAE 6.77    5-15: 5.89    15-30: 5.95
#     CS2 kills        0-5 maps: MAE 6.67    5-15: 5.89    15-30: 5.34
#
# and those thin buckets are not a fringe: 34% of Valorant rows and 50%
# of CS2's. LoL does not show it, because LoL players arrive with a
# career baseline and a previous split behind them; Valorant has neither
# (no career scraper exists for it, and hist is null), so a new player's
# rate there is three maps and nothing else.
#
# So the estimate is pulled toward the league's average player by
# n / (n + k), the standard empirical-Bayes weight: no pull at all once a
# player has a real sample, most of the way to the prior when they have
# none. k is per game and stat because it is the sample size at which the
# player's own rate becomes worth as much as the league's, and that is
# not the same number in a game with career data as in one without.
#
# Measured out-of-sample over 6 walk-forward folds; adopted only where a
# majority of folds agreed. It is HARMFUL in LoL at every k tried
# (+0.45% to +11.22% on kills), which is the same finding from the other
# side and why LoL ships k=0.
ROSTER_SIZE = 5  # players per side, in all three games. tests/model_tiers.test.mjs
                 # asserts every recorded side in every dataset has exactly
                 # this many, so it is checked against the data rather than
                 # assumed to stay true.


def league_player_rate(past_matches, teams, stat_key, cutoff_date):
    """What one player on an average team produces in a map."""
    pace = league_pace_per_map(past_matches, teams, stat_key, cutoff_date)
    return pace / ROSTER_SIZE if pace else None


def shrink_to_prior(per_map, k, prior_games, past_matches, teams, stat_key, cutoff_date):
    """Pull a per-map rate toward the league's average player.

    Applied to the FINAL per-map figure, after the opponent, kp and share
    layers, because that is the number actually being trusted and it is
    where this was measured. Falls through unchanged when there is no
    league to compare against.
    """
    if not k:
        return per_map
    prior = league_player_rate(past_matches, teams, stat_key, cutoff_date)
    if not prior:
        return per_map
    weight = prior_games / (prior_games + k)
    return weight * per_map + (1 - weight) * prior


def project_point_in_time(past_matches, teams, player, team, opponent_team, games, weights,
                           cutoff_date, stat_type, match_patch):
    cfg = STAT_TYPES[stat_type]
    ref_patch = parse_patch(match_patch)
    pt_rate, pt_games = recency_weighted_rate(
        past_matches, team, player["name"], cfg["key"], cutoff_date,
        weights["recencyHalfLife"], ref_patch, weights["patchDiscount"]
    )
    hist_rate = player["hist"][cfg["key"]] if player.get("hist") else None

    if pt_rate is not None:
        base = weights["history"] * hist_rate + (1 - weights["history"]) * pt_rate if hist_rate is not None else pt_rate
    else:
        # A player can have no rate for this stat at all -- headshots are
        # recorded only for CS2, and only from the run that started
        # capturing them. Returning None beats both alternatives: the
        # KeyError this used to raise, and the silent NaN the JS side
        # produced from the same expression, which renders as an empty
        # projection rather than an absent one.
        cur_rate = (player.get("cur") or {}).get(cfg["key"])
        base = hist_rate if hist_rate is not None else cur_rate
        if base is None:
            return None, pt_games

    # Career tier — a prior blended on TOP of the existing recent-form/
    # split-history base, using its own independent weight rather than
    # forcing a 3-way sum-to-1 average. Consistent with how patchDiscount/
    # kp are already separate multiplicative layers rather than folded
    # into one blend. Falls back cleanly (base unchanged) when a player
    # has no career data.
    #
    # LoL players use a static player["career"] field (their own real
    # leakage risk is smaller in practice since a whole-season aggregate
    # dilutes any single prediction's overlap, but is NOT architecturally
    # immune to the same issue — worth revisiting with the same fix once
    # CS2's is validated). CS2 players use point_in_time_cs2_career_rate()
    # instead, computed fresh per cutoff_date from raw game history —
    # required after a real, confirmed leakage bug (see that function's
    # docstring) made CS2's static version give a misleadingly perfect-
    # looking career:1.0 in a real backtest.
    if "career_games" in player:
        career_rate = point_in_time_cs2_career_rate(player, cfg["key"], cutoff_date)
    else:
        career = player.get("career")
        career_rate = career.get(cfg["key"]) if career else None
    if career_rate is not None and weights.get("career", 0) > 0:
        # careerRamp: scales the career weight by how much CURRENT-split
        # data the player has accumulated as of this cutoff, instead of
        # applying one flat weight regardless.
        #
        # This exists because of a real measured finding, not a hunch.
        # scripts/diagnose_lol_career_leakage.py bucketed every LoL
        # prediction by season quartile and found career's benefit rising
        # monotonically through the season -- kills -1.1% -> +6.6%,
        # assists -1.5% -> +10.8%, deaths +0.3% -> +4.6%. Career is
        # actively HARMFUL early and strongly helpful late, consistently
        # across all three stats. (That same diagnostic was built to test
        # the opposite hypothesis -- that career was LEAKING future data
        # and therefore flattering early-season matches. The data refuted
        # it outright: the gradient runs the other way.)
        #
        # Plausible mechanism: at SEASON_HALF_LIFE=0.05 the career figure
        # collapses to roughly the player's current-SEASON aggregate,
        # which early in a split is dominated by other splits entirely
        # (different meta, sometimes different roster) and is a blurry
        # comparison; late in a split it is mostly that split's own
        # games, making it a clean independently-sourced measurement of
        # exactly the thing being predicted.
        #
        # ramp = 0 disables this and reproduces the previous flat-weight
        # behaviour EXACTLY, so it is safe as a default and the weight
        # search can rule the whole idea out by choosing 0.
        career_weight = weights["career"]
        ramp = weights.get("careerRamp", 0)
        if ramp:
            career_weight *= min(1.0, pt_games / ramp)
        base = career_weight * career_rate + (1 - career_weight) * base

    opp_mult = resolve_opponent_multiplier(teams, past_matches, player, opponent_team, weights["opponent"], cfg, cutoff_date)

    kp_mult = kp_multiplier(player, weights["history"], weights["kp"], teams,
                              past_matches, team, cutoff_date) if cfg["useKP"] else 1.0

    per_game = base * opp_mult * kp_mult
    per_game = blend_share_tier(per_game, weights.get("share", 0), past_matches, teams,
                                team, player["name"], cfg["key"], cutoff_date)
    per_game = shrink_to_prior(per_game, weights.get("shrink", 0), pt_games,
                               past_matches, teams, cfg["key"], cutoff_date)
    return per_game * games, pt_games


# ============================================================
# Backtest harness
# ============================================================

def load_region_data(path, region_keys=None):
    """Loads a data.json/valorant_data.json file. Returns {region: {teams, past_matches}}."""
    if not Path(path).exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    regions = data.get("regions", {})
    if region_keys:
        regions = {k: v for k, v in regions.items() if k in region_keys}
    return regions


def collect_predictions_with_dates(region_data, stat_type, weights):
    """Same as collect_predictions but each row carries its match date, so
    rows can be split into time windows afterwards.

    One pass produces every row the model predicts; because projections are
    point-in-time, a row's prediction never depends on which window it is
    later scored in. That is what makes walk-forward validation cheap here:
    k folds cost one pass, not k.
    """
    return _collect(region_data, stat_type, weights, with_dates=True)


def collect_predictions(region_data, stat_type, weights):
    """Returns list of (region, predicted, actual) for every player-match
    where a real actual value exists — i.e. every point the model is
    actually trying to predict, evaluated point-in-time."""
    return _collect(region_data, stat_type, weights, with_dates=False)


def _collect(region_data, stat_type, weights, with_dates):
    cfg = STAT_TYPES[stat_type]
    results = []
    for region_key, rd in region_data.items():
        teams = rd.get("teams", {})
        past_matches = rd.get("past_matches", [])
        if not teams or not past_matches:
            continue
        for match in past_matches:
            for side in ("teamA", "teamB"):
                team = match[side]
                opp = match["teamB"] if side == "teamA" else match["teamA"]
                if team not in teams:
                    continue
                for player in teams[team]["players"]:
                    actual = get_actual_stat(match, team, player["name"], cfg["key"])
                    if actual is None or actual == "unavailable":
                        continue
                    # maps_counted, not a hardcoded 2. "actual" now sums
                    # the series' real prop window — maps 1-2 for a Bo3
                    # but maps 1-3 for a Bo5 — so predicting two maps for
                    # every match under-predicts every Bo5 by roughly a
                    # third. That mismatch is invisible per-row and shows
                    # up only as inflated MAE plus weights contorting to
                    # absorb an error the model structurally cannot fix.
                    # Defaults to 2 for records written before the field
                    # existed.
                    predicted, prior_games = project_point_in_time(
                        past_matches, teams, player, team, opp,
                        match.get("maps_counted", 2), weights,
                        match["date"], stat_type, match.get("patch")
                    )
                    if predicted is None:
                        continue  # no rate for this stat -- not a prediction to score
                    if prior_games == 0 and not player.get("hist"):
                        continue  # true cold start with zero grounding — not a fair test of the model
                    if with_dates:
                        results.append((region_key, match["date"], predicted, actual))
                    else:
                        results.append((region_key, predicted, actual))
    return results


def mae(results):
    if not results:
        return None
    return sum(abs(p - a) for _, p, a in results) / len(results)


def evaluate(region_data, stat_type, weights):
    return mae(collect_predictions(region_data, stat_type, weights))


# ============================================================
# WALK-FORWARD VALIDATION
#
# The search below picks weights by minimising MAE over the SAME rows it
# then reports. Projections are point-in-time, so no individual prediction
# peeks at its own future — but the WEIGHTS are still chosen with the whole
# season visible, and that is a different kind of leakage. Measured on this
# repo's own data: re-running the search on the first 70% of the season
# improved training MAE on all three stats and made held-out MAE WORSE on
# all three (kills 2.664 -> 2.635 train, 2.840 -> 2.860 held out). The
# search was fitting noise and reporting it as progress.
#
# These helpers score a weight set on time-ordered folds it was not fitted
# on, which is the only number that says anything about tomorrow's matches.
# A fold costs nothing extra: one pass produces every row, and folds are
# slices of it.
# ============================================================

def fold_boundaries(dates, k, start_frac=0.4):
    """k contiguous validation windows over the last (1 - start_frac) of the
    timeline. The earlier part is never scored — it is the history the model
    needs before it can predict anything at all."""
    ordered = sorted(dates)
    if not ordered:
        return []
    tail = ordered[int(len(ordered) * start_frac):]
    if not tail:
        return []
    edges = [tail[int(len(tail) * i / k)] for i in range(k)] + [None]
    return list(zip(edges[:-1], edges[1:]))


def window_rows(rows, lo, hi):
    return [r for r in rows if r[1] >= lo and (hi is None or r[1] < hi)]


def mae_dated(rows):
    if not rows:
        return None
    return sum(abs(p - a) for _, _, p, a in rows) / len(rows)


def per_fold_mae(region_data, stat_type, weights, folds):
    rows = collect_predictions_with_dates(region_data, stat_type, weights)
    return [mae_dated(window_rows(rows, lo, hi)) for lo, hi in folds]


def compare_out_of_sample(region_data, stat_type, baseline, candidate, folds):
    """Fold-by-fold comparison of two weight sets. Returns (mean change %,
    folds won, n folds). A change that does not win most folds is noise
    however large its average looks."""
    b = per_fold_mae(region_data, stat_type, baseline, folds)
    c = per_fold_mae(region_data, stat_type, candidate, folds)
    pairs = [(x, y) for x, y in zip(b, c) if x and y]
    if not pairs:
        return None, 0, 0
    mean_b = sum(x for x, _ in pairs) / len(pairs)
    mean_c = sum(y for _, y in pairs) / len(pairs)
    wins = sum(1 for x, y in pairs if y < x)
    return 100.0 * (mean_c - mean_b) / mean_b, wins, len(pairs)


def knockout_report(region_data, stat_type, weights, folds):
    """Turn each parameter off in turn and measure out-of-sample.

    A parameter whose removal costs nothing is not free: the search still
    assigns it a value, fitted to noise, and that value ships. On this
    repo's LoL data kp, patchDiscount and recencyHalfLife all move
    out-of-sample MAE by under 0.2% in either direction, while career,
    opponent and history are worth 1.5-3% each."""
    base = per_fold_mae(region_data, stat_type, weights, folds)
    base_mean = sum(x for x in base if x) / len([x for x in base if x])
    print(f"  {'parameter off':26s} {'OOS MAE':>9s} {'change':>9s}  verdict")
    print(f"  {'(none - baseline)':26s} {base_mean:9.4f} {'':>9s}")
    rows = []
    # share and shrink belong here as much as the rest. Left out, the
    # report for a stat that leans on them -- cs2 headshots leans on both
    # and on nothing else -- printed a baseline and no rows at all, which
    # reads as "nothing carries this model" rather than "this report does
    # not look at what does".
    for param, neutral in (("history", 0.0), ("opponent", 0.0), ("kp", 0.0),
                            ("patchDiscount", 0.0), ("career", 0.0),
                            ("share", 0.0), ("shrink", 0.0),
                            ("recencyHalfLife", 20)):
        if weights.get(param) == neutral:
            continue
        trial = dict(weights)
        trial[param] = neutral
        vals = [x for x in per_fold_mae(region_data, stat_type, trial, folds) if x]
        mean = sum(vals) / len(vals)
        change = 100.0 * (mean - base_mean) / base_mean
        # A NEGATIVE change means the model got BETTER without the
        # parameter — it is not inert, it is doing harm, and that is the
        # most valuable thing this table can find. CS2 ships career at its
        # maximum of 1.0 while removing it improves out-of-sample MAE.
        if change > 1.0:
            verdict = "carries the model"
        elif change > 0.25:
            verdict = "contributes"
        elif change < -0.25:
            verdict = "HARMFUL - model is better without it"
        else:
            verdict = "inert - noise risk"
        rows.append((param, mean, change, verdict))
    for param, mean, change, verdict in sorted(rows, key=lambda r: -r[2]):
        print(f"  {param:26s} {mean:9.4f} {change:+8.2f}%  {verdict}")


# Minimum RELATIVE improvement required to accept a parameter change.
#
# Coordinate descent otherwise takes any improvement at all, however
# small, which is not harmless: a real LoL run accepted flipping
# patchDiscount from 0.0 to 1.0 -- a total inversion of that parameter's
# meaning -- to buy 0.0023 MAE (0.08%) on n=7368, and simultaneously
# dropped recencyHalfLife from 20 to 2. Both are far inside the noise
# floor for a sample that size, and both would have shipped as if they
# were findings. The other two stats converged cleanly at 20/0.0, which
# is what made the kills result visibly suspect.
#
# 0.25% is deliberately conservative: large enough to reject fourth-
# decimal noise, small enough that genuine effects (every real
# improvement measured on this project has been >1%) pass easily.
MIN_RELATIVE_IMPROVEMENT = 0.0025


def coordinate_descent(region_data, stat_type, start_weights, passes=3):
    candidates = {
        "history": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8],
        "opponent": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0],
        "kp": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0],
        "recencyHalfLife": [2, 3, 4, 5, 6, 8, 10, 14, 20],
        "patchDiscount": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0],
        "career": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 1.0],
        "share": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
        "shrink": [0.0, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0],
    }
    cfg = STAT_TYPES[stat_type]
    # careerRamp is deliberately NOT searched. It was a real hypothesis,
    # properly tested, and REJECTED by the data: all three LoL stats
    # independently chose careerRamp=0 (off). The idea came from a
    # genuine measured finding -- career's benefit rises through the
    # season and is actively negative early (see
    # diagnose_lol_career_leakage.py) -- but the search answered that
    # problem more simply, roughly halving the flat career weight
    # instead (0.8/0.85/0.85 -> 0.4/0.5/0.3). Right diagnosis, wrong
    # prescription. The code path in project_point_in_time still honours
    # the parameter, so this is one line to re-enable if the data changes
    # shape, but searching nine candidates x three passes x three stats
    # for an option already measured as dead is pure cost.
    params = ["history", "opponent", "recencyHalfLife", "patchDiscount", "career", "share", "shrink"]
    if cfg["useKP"]:
        params.append("kp")

    weights = dict(start_weights)
    best_mae = evaluate(region_data, stat_type, weights)
    print(f"    starting MAE: {best_mae:.4f}" if best_mae is not None else "    no data to evaluate")
    if best_mae is None:
        return weights, best_mae

    for p in range(passes):
        improved = False
        for param in params:
            best_val = weights[param]
            for cand in candidates[param]:
                trial = dict(weights)
                trial[param] = cand
                m = evaluate(region_data, stat_type, trial)
                # Require a MEANINGFUL improvement, not merely any
                # improvement — see MIN_RELATIVE_IMPROVEMENT above.
                if m is not None and m < best_mae * (1 - MIN_RELATIVE_IMPROVEMENT):
                    best_mae = m
                    best_val = cand
                    improved = True
            weights[param] = best_val
        print(f"    pass {p + 1}: MAE {best_mae:.4f}  weights={weights}")
        if not improved:
            break
    return weights, best_mae


def per_region_breakdown(region_data, stat_type, weights):
    preds = collect_predictions(region_data, stat_type, weights)
    by_region = {}
    for region, p, a in preds:
        by_region.setdefault(region, []).append((region, p, a))
    print(f"    per-region MAE (n = sample size):")
    for region, items in sorted(by_region.items()):
        print(f"      {region:20s} MAE={mae(items):.4f}  n={len(items)}")


# ============================================================
# Opponent-signal diagnosis — the search kept zeroing out `opponent`
# across every stat type, which is a strong enough pattern to actually
# investigate rather than just accept. Two live hypotheses:
#   1. The opponent's point-in-time estimate is noisy early in a split
#      (few prior games backing it), adding variance without real signal.
#   2. The opponent side uses a flat average while the player's own side
#      is recency-weighted — that asymmetry alone could be hurting it.
# This measures whether the opponent-strength signal actually correlates
# with real outcomes at all, and whether that correlation improves once
# the opponent's estimate has more games behind it (confirming #1) or
# stays flat regardless of sample size (pointing elsewhere, e.g. #2 or
# a genuinely weak effect in this data).
# ============================================================

def correlation(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    varx = sum((x - mx) ** 2 for x in xs)
    vary = sum((y - my) ** 2 for y in ys)
    if varx == 0 or vary == 0:
        return None
    return cov / (varx * vary) ** 0.5


def collect_opponent_diagnosis_rows(region_data, stat_type, weights):
    """For each player-match, computes the opponent signal exactly the way
    the model actually would (lane-specific first if this stat uses it,
    falling back to team-wide) — not a separate hardcoded team-wide-only
    calculation. Tracks which path was used per row so the report can show
    how often lane-specific data was actually available versus falling
    back, alongside the correlation itself."""
    cfg = STAT_TYPES[stat_type]
    rows = []
    for region_key, rd in region_data.items():
        teams = rd.get("teams", {})
        past_matches = rd.get("past_matches", [])
        if not teams or not past_matches:
            continue
        for match in past_matches:
            for side in ("teamA", "teamB"):
                team = match[side]
                opp = match["teamB"] if side == "teamA" else match["teamA"]
                if team not in teams or opp not in teams:
                    continue

                for player in teams[team]["players"]:
                    actual = get_actual_stat(match, team, player["name"], cfg["key"])
                    if actual is None or actual == "unavailable":
                        continue
                    own_rate, _ = recency_weighted_rate(
                        past_matches, team, player["name"], cfg["key"], match["date"],
                        weights["recencyHalfLife"], None, 0
                    )
                    if own_rate is None:
                        own_rate = player["hist"][cfg["key"]] if player.get("hist") else None
                    if own_rate is None:
                        continue

                    used_lane = False
                    opp_stat, league_avg, opp_games = None, None, 0
                    if cfg["laneSpecific"]:
                        role = player.get("role")
                        if role:
                            opponent_role_names = [p["name"] for p in teams[opp]["players"] if p.get("role") == role]
                            if opponent_role_names:
                                opp_stat, opp_games = point_in_time_player_names_stat(
                                    past_matches, opp, opponent_role_names, cfg["oppBasis"], match["date"]
                                )
                                league_avg = point_in_time_league_avg_for_role(past_matches, teams, role, cfg["oppBasis"], match["date"])
                                if opp_stat is not None and league_avg:
                                    used_lane = True
                    if not used_lane:
                        opp_stat = point_in_time_team_stat(past_matches, opp, cfg["oppBasis"], match["date"])
                        league_avg_pt = point_in_time_league_avg_stat(past_matches, teams, cfg["oppBasis"], match["date"])
                        league_avg = league_avg_pt if league_avg_pt is not None else league_avg_stat(teams, cfg["oppBasis"])
                        opp_games = 8  # team-wide estimates aggregate ~5x the data of a single role — treat as "stable" by default
                        if opp_stat is None:
                            opp_stat = team_stat_per_game(teams, opp, cfg["oppBasis"])
                    if not opp_stat or not league_avg:
                        continue

                    rows.append({
                        "opp_games": opp_games, "opp_ratio": opp_stat / league_avg,
                        "own_base": own_rate * 2, "actual": actual, "used_lane": used_lane,
                    })
    return rows


def diagnose_opponent_signal(region_data, stat_type, weights, threshold=8):
    rows = collect_opponent_diagnosis_rows(region_data, stat_type, weights)
    if not rows:
        print("  no data to diagnose")
        return
    n = len(rows)
    avg_opp_games = sum(r["opp_games"] for r in rows) / n
    lane_count = sum(1 for r in rows if r["used_lane"])
    print(f"  n={n} predictions, avg opponent-estimate sample size = {avg_opp_games:.1f} prior games")
    if STAT_TYPES[stat_type]["laneSpecific"]:
        print(f"  lane-specific comparison used for {lane_count}/{n} rows ({100*lane_count/n:.0f}%) "
              f"— the rest fell back to team-wide (no current opponent on record for that role, or thin data)")
        # Quick check on a likely cause of low coverage: any player past the
        # 5th discovered on a roster gets labeled role="SUB" by the scraper
        # (a real limitation — role is inferred from roster-page position,
        # not an explicit label). A team with several roster changes over
        # the season can end up with real starters mislabeled "SUB", which
        # breaks the "find the opponent in this exact role" lookup.
        total_players, sub_players = 0, 0
        for rd in region_data.values():
            for team_data in rd.get("teams", {}).values():
                for p in team_data["players"]:
                    total_players += 1
                    if p.get("role") == "SUB":
                        sub_players += 1
        if total_players:
            print(f"  {sub_players}/{total_players} players ({100*sub_players/total_players:.0f}%) are role='SUB' "
                  f"(6th+ roster entry discovered) — a likely contributor to low lane-specific coverage")

    def report(subset, label):
        if len(subset) < 10:
            print(f"  {label}: too few samples ({len(subset)}) to report")
            return
        residual = [r["actual"] - r["own_base"] for r in subset]
        opp_dev = [r["opp_ratio"] - 1 for r in subset]
        corr = correlation(opp_dev, residual)
        corr_str = f"{corr:+.3f}" if corr is not None else "undefined"
        print(f"  {label}: n={len(subset)}, corr(opponent deviation, prediction residual) = {corr_str}")

    print(f"  --- {stat_type} ---")
    report(rows, "ALL matches (mixed lane-specific + team-wide fallback)")
    if STAT_TYPES[stat_type]["laneSpecific"]:
        lane_rows = [r for r in rows if r["used_lane"]]
        fallback_rows = [r for r in rows if not r["used_lane"]]
        report(lane_rows, "LANE-SPECIFIC rows only")
        report(fallback_rows, "team-wide FALLBACK rows only")
        report([r for r in lane_rows if r["opp_games"] < threshold], f"  lane-specific, < {threshold} prior games (noisy)")
        report([r for r in lane_rows if r["opp_games"] >= threshold], f"  lane-specific, >= {threshold} prior games (stable)")
    else:
        report([r for r in rows if r["opp_games"] < threshold], f"opponent estimate < {threshold} prior games (noisy)")
        report([r for r in rows if r["opp_games"] >= threshold], f"opponent estimate >= {threshold} prior games (stable)")
    print(f"  (positive correlation = signal is real and pointing the expected direction; "
          f"near zero = no usable signal at that sample size; negative = backwards)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stat", choices=list(STAT_TYPES) + ["all"], default="all")
    # Derived, not listed: a stat added to STAT_TYPES is immediately
    # available here rather than failing on an "invalid choice" from a
    # copy of the list that nobody remembered to update.
    ap.add_argument("--game", choices=["lol", "valorant", "cs2", "all"], default="all")
    ap.add_argument("--data", default="data.json")
    ap.add_argument("--valorant-data", default="valorant_data.json")
    ap.add_argument("--cs2-data", default="cs2_data.json")
    ap.add_argument("--diagnose-opponent", action="store_true",
                     help="Investigate whether the opponent-strength signal correlates with real "
                          "outcomes, and whether it improves once the opponent has more prior games "
                          "on record. Run this instead of the normal weight search.")
    ap.add_argument("--validate", action="store_true",
                     help="Score the currently shipped weights out-of-sample on walk-forward "
                          "folds and report which parameters actually earn their keep. This is "
                          "the honest measurement; the plain search below is in-sample and "
                          "optimistic.")
    ap.add_argument("--candidate", default=None,
                     help='JSON weight overrides to compare against the shipped weights '
                          'out-of-sample, e.g. \'{"history":0.8,"career":0.6}\'. Implies --validate.')
    ap.add_argument("--folds", type=int, default=6,
                     help="Number of walk-forward validation folds (default 6).")
    ap.add_argument("--min-opp-games", type=int, default=8,
                     help="Threshold (in prior games) for splitting 'noisy' vs 'stable' opponent "
                          "estimates in --diagnose-opponent. Default 8 (~4 matches).")
    args = ap.parse_args()

    region_data = {}
    if args.game in ("lol", "all"):
        region_data.update(load_region_data(args.data))
    if args.game in ("valorant", "all"):
        region_data.update(load_region_data(args.valorant_data))
    if args.game in ("cs2", "all"):
        region_data.update(load_region_data(args.cs2_data))

    if not region_data:
        print("No region data loaded — check --data / --valorant-data / --cs2-data paths point at "
              "real files with populated 'teams' and 'past_matches'.", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded regions: {list(region_data.keys())}\n")

    # CS2's data shape makes SOME weights structurally inert — flag this
    # explicitly rather than letting a search "tune" parameters that
    # cannot affect the output. CS2 players have hist=None (no
    # Spring/Summer-style split exists in CS2's continuous tournament
    # calendar), so `history` has no historical rate to blend against
    # (project_point_in_time falls back to the point-in-time rate) and
    # `patchDiscount` has no patch data to discount against.
    #
    # kp is checked SEPARATELY now, not folded into the same condition —
    # it used to be hardcoded to 0 for every CS2 player (a real bug in
    # scrape_cs2.py, now fixed: kp is computed from real per-map
    # players_stats data, the same source k/d/a already come from), so
    # treating "hist=None" and "kp=0" as one combined signal would
    # incorrectly claim kp is still inert for CS2 even after it started
    # carrying real, live data.
    hist_inert_regions = [k for k, v in region_data.items()
                           if any(p.get("hist") is None
                                  for t in (v.get("teams") or {}).values()
                                  for p in (t.get("players") or []))]
    if hist_inert_regions:
        print(f"NOTE: {hist_inert_regions} have players with hist=None — for those regions the "
              f"'history' and 'patchDiscount' weights are structurally inert (they cannot change "
              f"predictions). 'kp' is checked separately below, since it's no longer automatically "
              f"inert just because hist is None.\n")

    kp_inert_regions = [k for k, v in region_data.items()
                         if all(not p.get("cur", {}).get("kp")
                                for t in (v.get("teams") or {}).values()
                                for p in (t.get("players") or []))]
    if kp_inert_regions:
        print(f"NOTE: {kp_inert_regions} have EVERY player with kp=0/falsy — 'kp' is structurally "
              f"inert there too (kp_multiplier short-circuits when cur.kp is falsy). If this "
              f"includes CS2, that's unexpected after the kp computation fix — worth checking "
              f"whether data.json was actually re-scraped with the fix, or is still stale.\n")

    stat_types = list(STAT_TYPES) if args.stat == "all" else [args.stat]

    if args.diagnose_opponent:
        for stat_type in stat_types:
            diagnose_opponent_signal(region_data, stat_type, DEFAULT_WEIGHTS, args.min_opp_games)
            print()
        return

    if args.validate or args.candidate:
        shipped = SHIPPED_WEIGHTS.get(args.game if args.game != "all" else "lol", {})
        override = json.loads(args.candidate) if args.candidate else None
        for stat_type in stat_types:
            base = dict(DEFAULT_WEIGHTS)
            base.update(shipped.get(stat_type, {}))
            rows = collect_predictions_with_dates(region_data, stat_type, base)
            if not rows:
                print(f"=== {stat_type.upper()} === no predictable rows\n")
                continue
            folds = fold_boundaries([r[1] for r in rows], args.folds)
            print(f"=== {stat_type.upper()} ===  {len(rows)} rows, {len(folds)} folds "
                  f"from {folds[0][0]}")
            vals = [x for x in per_fold_mae(region_data, stat_type, base, folds) if x]
            print(f"  shipped weights, out-of-sample MAE: {sum(vals)/len(vals):.4f}")
            oos = sum(vals) / len(vals)
            in_sample = evaluate(region_data, stat_type, base)
            # Only call it optimistic when it actually is. In-sample error is
            # usually the flattering number, but not always: if the fold
            # window happens to cover an easier stretch of the season the
            # comparison inverts, and labelling that "optimistic" would be
            # simply wrong.
            gap = 100.0 * (oos - in_sample) / in_sample
            note = ("optimistic by {:.1f}%".format(gap) if gap > 0.5
                    else "folds are an easier stretch than the season as a whole"
                    if gap < -0.5 else "close to the out-of-sample number")
            print(f"  in-sample MAE (what the search reports): {in_sample:.4f}  <- {note}\n")
            knockout_report(region_data, stat_type, base, folds)
            if override:
                cand = dict(base)
                cand.update(override)
                change, wins, n = compare_out_of_sample(region_data, stat_type, base, cand, folds)
                verdict = ("ADOPT" if change < -0.25 and wins > n / 2
                            else "reject - not consistent" if change < 0
                            else "reject - worse")
                print(f"\n  candidate {override}")
                print(f"    out-of-sample change {change:+.2f}%, wins {wins}/{n} folds -> {verdict}")
            print()
        return

    summary = []
    for stat_type in stat_types:
        print(f"=== {stat_type.upper()} ===")
        baseline_mae = evaluate(region_data, stat_type, DEFAULT_WEIGHTS)
        if baseline_mae is None:
            print("  no predictable player-matches for this stat (data not populated yet)\n")
            continue
        print(f"  baseline (current defaults) MAE: {baseline_mae:.4f}")
        print(f"  searching...")
        best_weights, best_mae = coordinate_descent(region_data, stat_type, DEFAULT_WEIGHTS)
        improvement_pct = 100 * (baseline_mae - best_mae) / baseline_mae if baseline_mae else 0
        print(f"  best weights found: {best_weights}")
        print(f"  best MAE: {best_mae:.4f}  ({improvement_pct:+.1f}% vs baseline)")
        per_region_breakdown(region_data, stat_type, best_weights)
        print()
        summary.append((stat_type, baseline_mae, best_mae, improvement_pct, best_weights))

    print("=== SUMMARY ===")
    for stat_type, base, best, pct, w in summary:
        print(f"  {stat_type:8s}  baseline={base:.4f}  best={best:.4f}  ({pct:+.1f}%)  weights={w}")


if __name__ == "__main__":
    main()
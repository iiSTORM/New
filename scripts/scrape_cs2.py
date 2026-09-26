#!/usr/bin/env python3
"""
CS2 scraper. Two data sources combined:
  - cs2api (PyPI package, wraps bo3.gg): match/team discovery. Proven
    reliable for finished()/get_todays_matches()/get_team_upcoming_matches()
    /search_teams(). get_team_matches() is confirmed BROKEN in this
    package (a real AttributeError bug on their end, not ours) — worked
    around below by scanning finished() instead of relying on it.
  - Direct calls to api.bo3.gg: the actual per-map player stats, since
    cs2api has no method for this at all. Confirmed working via extensive
    live probing:
      GET /matches/{slug}?with=games       -> match metadata + maps list
      GET /games/{id}/players_stats        -> real per-player K/D/A for that map
      GET /games/{id}/game_steam_profiles  -> steam_profile_id -> nickname

CS2 doesn't have discrete franchised "regions" the way LCS/VCT do — it's
an individually-ranked global scene, tournament-based rather than
league-based. Rather than a fixed curated team list (the original
approach — real limitation: any match involving an untracked opponent was
permanently invisible no matter how much data got fetched), this filters
the global match feed by TIER instead — a real quality signal already
present on every match object (confirmed: "tier": "b", "stars": 1 on real
data), used here as a proxy for "notable enough to be worth projecting" —
similar in spirit to what a prop-betting platform would cover. This is an
approximation, not a confirmed match to any specific platform's exact
coverage (no live access to verify against) — ACCEPTED_TIERS below is the
one knob to adjust if real output looks too broad or too narrow.

Games 1+2 only, matching the app's established convention (see
scrape_valorant.py's identical rule) — a 3rd map, when a Bo3 goes the
distance, is intentionally excluded so "actual" data lines up with what
the model always projects for.

No clean "split" boundary exists for CS2's continuous tournament calendar
the way LoL has Spring/Summer — "hist" is left null for every player
(the model already handles this gracefully, falling back to "cur" alone)
rather than forcing a fake historical window.
"""
import asyncio
import collections
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import aiohttp
from cs2api import CS2

# Which map windows a stored record can settle, imported rather than
# restated: the backfill below decides what to re-fetch on exactly the
# question the grader answers, and two copies of that rule would drift.
# score_props imports nothing but the standard library.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from score_props import window_is_covered
from team_aliases import reconcile

BO3_BASE = "https://api.bo3.gg/api/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# Tiers to include, lowercase, when the field is actually populated —
# confirmed via real output that "tier" is frequently None even on
# clearly-important matches (a real match came back tier=None, stars=5 —
# the highest stars value seen in this whole project). stars is the more
# reliably-populated signal, so it's the primary filter below; tier is
# kept as a secondary OR condition for whenever it happens to be present.
ACCEPTED_TIERS = {"s", "a"}

# How far ahead to pull upcoming matches.
#
# Upcoming fixtures used to come from exactly two places: cs2api's
# get_todays_matches(), whose filter is start_date within TODAY, and
# per-team schedules for the teams discovered in this run's finished
# matches. Anything tomorrow involving a team outside that discovered set
# was therefore invisible. On run 116 that left 11 upcoming matches with a
# two-day hole in front of them — the next fixture shown was three days
# out while matches existed the following day — and because the run
# happened at 22:47 UTC, the "today" feed itself returned a single match.
#
# bo3.gg's /matches endpoint takes an arbitrary start_date range (this is
# exactly what get_todays_matches() does, with both bounds set to today),
# so the same confirmed-working query is used here over a window instead.
UPCOMING_WINDOW_DAYS = 7
UPCOMING_PAGE_LIMIT = 100  # the API's own page size in cs2api's queries

# Upper bound on opponents whose rosters get backfilled in one run. Each
# costs a team search plus several match fetches, and the API rate-limits.
MAX_BACKFILL_OPPONENTS = 25

# Teams whose POSTED LINES are waiting on a result, fetched per run so
# the board's own record can grow.
#
# The loop was open at this end. This scraper fetches the most recent
# NOTABLE matches -- tier a/s or 3+ stars -- while the prop provider
# posts lines on a far wider field, so a fixture could be projected,
# played, and never scraped. Of 724 CS2 lines old enough to grade, 646
# were refused for "no completed match on that date", and 645 of those
# were dated AFTER the latest match we hold for that team. We were
# projecting fixtures we could never learn the answer to.
#
# 63 missing results across 51 teams unlock all 646. That is the record
# going from 8 graded matches to about 70, which is the difference
# between a number nobody should act on and one that can be measured
# against the market.
# Raised 30 -> 60 once the backfill proved itself: the first run took
# gradeable CS2 lines from 79 to 394, and the cap was the only reason it
# was not all of them. 51 teams were waiting then, and the queue grows
# daily as new lines are posted, so a cap below the backlog never
# catches up -- it just moves the shortfall to the next run forever.
MAX_RESULT_BACKFILL_TEAMS = 60
# Widened from 3 to 6 — confirmed via live diagnosis that a real match
# can flip from empty player stats to fully populated within minutes
# (bo3.gg's own stats pipeline hasn't finished processing very recent
# matches yet: a game's raw "state" is null with null scores when stats
# aren't ready, vs "done" with real scores when they are). Fetching more
# candidates per team gives a better chance of landing on an already-
# processed match instead of the very latest one, still mid-pipeline.
# Module level because BOTH backfills use it — the roster one and the
# results one.
BACKFILL_MATCHES_PER_TEAM = 6
PROPS_HISTORY_PATH = "props_history.jsonl"
# 1-5 scale inferred from real data (a minor qualifier showed stars=1; the
# match above showed stars=5). 3 is a starting midpoint, not a confirmed
# cutoff — build_region_payload prints the real star distribution across
# the fetched batch, so this is the one number to recalibrate from actual
# output if coverage looks too broad or too narrow.
MIN_STARS = 3

COLOR_PALETTE = [
    "#e0c341", "#4fa8e0", "#e05f9a", "#5fd97a", "#e07a4f", "#9a7ae0",
    "#4fd9c9", "#d94f7a", "#a8d94f", "#4f7ae0",
]


async def bo3_get(session, path, params=None):
    url = f"{BO3_BASE}{path}"
    for attempt in range(3):
        try:
            async with session.get(url, headers=HEADERS, params=params,
                                    timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status == 200:
                    return await resp.json()
                if resp.status == 404:
                    return None
                print(f"  ! {url} -> HTTP {resp.status}", file=sys.stderr)
        except Exception as e:
            print(f"  ! {url} attempt {attempt + 1}/3 failed: {e}", file=sys.stderr)
        await asyncio.sleep(1 + attempt)
    return None


# Matches that started more than this long ago are dropped from the
# upcoming list. Not zero, because a match already under way is still worth
# showing, and bo3.gg's "current" status covers exactly that. But run 116
# published a fixture from the previous day as upcoming, because nothing
# compared start_date against the clock at all.
UPCOMING_MAX_AGE_HOURS = 12


def parse_match_start(m):
    """The match's start time as an aware datetime, or None if unparseable.

    bo3.gg returns ISO-8601 with an offset ("2026-09-21T10:00:00.000+00:00")
    but has also been seen using a space separator, so both are accepted
    rather than assuming one.
    """
    raw = m.get("start_date") or m.get("date")
    if not raw or not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace(" ", "T"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def fetch_upcoming_window(session, days=UPCOMING_WINDOW_DAYS):
    """Every upcoming CS2 match between now and `days` ahead.

    Same endpoint, scope and filters cs2api's get_todays_matches() uses —
    only the start_date bounds differ, so this relies on nothing new about
    the API. Paged, because a week of fixtures exceeds one page.

    Returns [] on failure rather than raising: this is an enrichment over
    the per-team schedules that were already being fetched, and a bad day
    at bo3.gg should degrade coverage, not fail the run.
    """
    start = datetime.now(timezone.utc)
    end = start + timedelta(days=days)
    collected = []
    for offset in range(0, 1000, UPCOMING_PAGE_LIMIT):
        params = {
            "scope": "widget-matches",
            "page[offset]": str(offset),
            "page[limit]": str(UPCOMING_PAGE_LIMIT),
            "sort": "start_date",
            "filter[matches.status][in]": "upcoming,current",
            "filter[matches.start_date][gt]": start.strftime("%Y-%m-%d 00:00"),
            "filter[matches.start_date][lt]": end.strftime("%Y-%m-%d 23:59"),
            "filter[matches.discipline_id][eq]": "1",
            "with": "teams,tournament,ai_predictions,games,streams",
        }
        batch = await bo3_get(session, "/matches", params)
        rows = (batch.get("results", batch) if isinstance(batch, dict) else batch) or []
        collected.extend(rows)
        if len(rows) < UPCOMING_PAGE_LIMIT:
            break
    return collected


async def fetch_team_recent_matches(session, team_id, limit=10):
    """Direct, team-scoped match history — the real endpoint+params found
    in cs2api's own get_team_matches() source (confirmed broken only by a
    self._make_request vs self._api._make_request typo in that package,
    not because the request itself is wrong). Confirmed via live testing
    to correctly return a specific team's own matches, unlike scanning the
    global finished() sample, which only surfaces a team if they happened
    to appear in the last ~100 notable results.

    Response shape here nests team info as team1/team2 objects (via the
    with=teams expansion) rather than flat team1_id/team2_id fields used
    elsewhere in this file — but downstream code (process()) only needs
    each match's "slug", which is present either way, so no further
    normalization is needed."""
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=180)
    params = {
        "scope": "widget-map-pool",
        "page[offset]": "0",
        "page[limit]": str(limit),
        "sort": "-start_date",
        "filter[matches.status][in]": "finished",
        "filter[matches.team_ids][overlap]": str(team_id),
        "filter[matches.start_date][lt]": today.isoformat(),
        "filter[matches.start_date][gt]": start_date.isoformat(),
        "filter[matches.discipline_id][eq]": "1",
        "with": "teams,tournament,ai_predictions,games,match_maps",
    }
    data = await bo3_get(session, "/matches", params=params)
    if not data:
        return []
    return data.get("results", data) if isinstance(data, dict) else data


# Stats beyond k/d/a that this app would model if the source carries
# them. PrizePicks posts lines on more than three stats -- "MAPS 1-2
# Headshots" appears in a real payload alongside kills, deaths and
# assists -- and every one of those lines is currently dropped for want
# of any history to project from.
#
# Spellings are candidates, not knowledge: bo3.gg's players_stats row is
# read here rather than documented anywhere, and it already uses "death"
# singular where you would expect "deaths", so guessing one name and
# shipping it would be the usual way of getting a silent zero. Whatever
# matches first wins, and SOURCE_FIELDS_SEEN records the row's real keys
# so a run reports what was actually on offer instead of leaving the next
# person to guess again.
# Everything worth reading off a bo3.gg players_stats row beyond k/d/a.
#
# A live run reported 34 keys on that row and this file was reading six.
# The names on the left are deliberately short (they are repeated on
# every player of every map) and deliberately match the Valorant
# scraper's where the same thing exists, so one analysis can look at
# both games without a translation table.
#
# Counts and per-round figures are mixed here on purpose and need no
# separation: accumulate_extra_stats sums a series, season_rates divides
# by the map count, and summing a per-round figure across maps then
# dividing by maps is its mean. That only holds while the two agree on
# what a map is -- which is the bug to watch for if either changes.
#
# Economy fields (money_spent, utility_value, weapons_value,
# total_equipment_value, pistols_value, money_save) are deliberately NOT
# taken. They describe buy rounds rather than what a player did, they
# would add weight to a file already carrying 1404 players, and nothing
# has yet shown the performance fields themselves are worth anything.
EXTRA_STAT_FIELDS = {
    "hs": ("headshots", "head_shots", "headshot_kills", "hs", "kills_hs"),
    "adr": ("adr",),                       # damage per round
    "kast": ("kast",),
    "fk": ("first_kills",),                # opening duels won
    "fd": ("first_death",),                # ... and lost. Singular, per the API.
    # bo3.gg's OWN score, not the HLTV rating the name suggests: a real
    # run puts it between 3.5 and 9.3 per map, where HLTV's sits near
    # 1.0-1.3. Monotone and usable, but do not compare it across sites
    # or read 1.0 as average.
    "rating": ("player_rating", "player_rating_value"),
    "clutch": ("clutches",),
    # multikills is offered but is NOT a plain number -- it survived a
    # live run without ever being captured, which is capture_extra_stats
    # refusing a non-numeric value rather than a bug. Probably a
    # breakdown object (2k/3k/4k/5k). Left here so the next person does
    # not rediscover it; unpacking it needs the real shape first.
    "multi": ("multikills",),
    "tk": ("trade_kills",),                # a kill that traded a fallen teammate
    "td": ("trade_death",),
    "dmg": ("damage",),
}
SOURCE_FIELDS_SEEN = set()


def capture_extra_stats(source_row, into):
    """Copy whichever extra stats this source row actually carries.

    Present-but-null is the failure this file has already been bitten by
    twice, so a value is taken only when it is a real number. A missing
    one leaves the key OFF rather than writing a 0, which downstream
    would read as "played and got none".
    """
    for our_key, candidates in EXTRA_STAT_FIELDS.items():
        for candidate in candidates:
            value = source_row.get(candidate)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                into[our_key] = value
                break
    return into


def accumulate_extra_stats(slot, row):
    """Add one map's extra stats into a series total, or void the total.

    A series total summed from only the maps that happened to report the
    stat is worse than no total: it is a real-looking low number, and a
    projection built on it under-predicts with nothing to show anything
    is wrong. So the first map that omits the stat voids it for the whole
    series, and -- the case that is easy to get wrong -- a later map
    carrying it must NOT resurrect it.
    """
    for our_key in EXTRA_STAT_FIELDS:
        flag = our_key + "_incomplete"
        if slot.get(flag):
            continue  # already voided; a later map cannot undo that
        if our_key in row:
            slot[our_key] = slot.get(our_key, 0) + row[our_key]
        else:
            slot.pop(our_key, None)
            slot[flag] = True
    return slot


def season_rates(past_matches, team_name, player_name):
    """One player's per-game rates over every match on record, or None.

    Extracted because this was written out TWICE -- once in the main pass
    and once in the opponent backfill, the second carrying a comment
    saying "same aggregation logic as the main pass". It was not, once
    either changed: adding headshots to the first left 134 of 271 players
    without a headshot rate, silently, because the backfill still built
    its own dict from k/d/a/kp alone. Two copies of a thing that must
    agree is the bug; one function is the fix.

    Rates are total events over total games rather than an average of
    per-match averages, so a Bo3 counts for more than a Bo1 -- the same
    properly-weighted approach kp already used.
    """
    total_k = total_d = total_a = total_games = 0
    kp_numerator = kp_denominator = 0
    extra_totals, extra_games = {}, {}
    for match in past_matches:
        for side in ("teamA", "teamB"):
            if match[side] != team_name:
                continue
            row = (match["actual"].get(team_name) or {}).get(player_name)
            if not row:
                continue
            games = match.get("games", 2)
            total_k += row["k"]
            total_d += row["d"]
            total_a += row["a"]
            total_games += games
            kp_numerator += row.get("kp_numerator", 0)
            kp_denominator += row.get("kp_denominator", 0)
            for our_key in EXTRA_STAT_FIELDS:
                # A series whose total was voided for this stat cannot
                # contribute to the season rate either -- see
                # accumulate_extra_stats for why a partial total is worse
                # than none.
                if our_key in row and not row.get(our_key + "_incomplete"):
                    extra_totals[our_key] = extra_totals.get(our_key, 0) + row[our_key]
                    extra_games[our_key] = extra_games.get(our_key, 0) + games
    if total_games == 0:
        return None
    return {
        "g": total_games,
        "k": total_k / total_games, "d": total_d / total_games, "a": total_a / total_games,
        # Divided by the games that REPORTED the stat, not by every game
        # played -- a player whose earlier matches predate the field would
        # otherwise show half their real rate.
        **{key: extra_totals[key] / extra_games[key]
           for key in extra_totals if extra_games.get(key)},
        # *100 -- a real, confirmed bug found via a live prediction
        # breakdown: kpMultiplier's baseline is on LoL's 0-100 percentage
        # convention, and this produced a 0-1 fraction, silently
        # collapsing the multiplier for literally every CS2 player.
        "kp": (kp_numerator / kp_denominator * 100) if kp_denominator > 0 else 0,
    }

async def fetch_map_player_stats(session, game_id, canonical_name_by_team_id):
    """Returns ({player_name: {"k":.., "d":.., "a":.., "team": name}},
    {team_id: name}) for one specific map — the second dict is a reliable
    team_id<->name mapping straight from players_stats' own nested
    team_clan field.

    Team NAME resolution prefers canonical_name_by_team_id (the short form
    used by upcoming/schedule endpoints, e.g. "Falcons") over players_stats'
    own clan_name (a longer/different form, e.g. "Team Falcons") whenever
    already known — confirmed via real output that these two sources
    disagree on naming convention. Falls back to clan_name when a team_id
    isn't in the lookup yet (typical on a team's first-seen match, before
    reconciliation happens as a post-processing pass in build_region_payload)."""
    stats, profiles = await asyncio.gather(
        bo3_get(session, f"/games/{game_id}/players_stats"),
        bo3_get(session, f"/games/{game_id}/game_steam_profiles"),
    )
    if not stats or not profiles:
        return {}, {}

    name_by_profile_id = {}
    for p in profiles:
        sp = p.get("steam_profile") or {}
        nickname = (sp.get("player") or {}).get("nickname") or sp.get("nickname")
        if nickname:
            name_by_profile_id[p.get("steam_profile_id")] = nickname

    result = {}
    resolved_name_by_team_id = {}
    for s in stats:
        pid = s.get("steam_profile_id")
        name = name_by_profile_id.get(pid)
        team_clan = s.get("team_clan") or {}
        team_id = team_clan.get("team_id")
        team_name = canonical_name_by_team_id.get(team_id) or s.get("clan_name", "")
        if team_id is not None:
            resolved_name_by_team_id[team_id] = team_name
        if not name:
            continue  # can't attribute this row to a real player name — skip rather than guess
        # Explicit None check, not .get(key, 0) -- a real, confirmed bug
        # (found via a live screenshot showing systematic under-
        # prediction across every match on the page) traced back to
        # exactly this pattern in scrape_cs2_career.py: .get(key, 0)'s
        # default only applies when the KEY is missing, not when it's
        # present-but-null, so a map bo3.gg hasn't finished processing
        # yet (confirmed earlier this project: some finished-status
        # matches still have null state/rounds_count/scores) was being
        # silently recorded as a real 0-kill game instead of excluded.
        # Same class of bug here, fixed the same way: skip this player's
        # row entirely for this map rather than treating null as real
        # data feeding cur/pt_rate.
        SOURCE_FIELDS_SEEN.update(s.keys())
        k, d, a = s.get("kills"), s.get("death"), s.get("assists")
        if k is None or d is None or a is None:
            continue
        result[name] = capture_extra_stats(s, {"k": k, "d": d, "a": a, "team": team_name})

    # Real kill participation, computed from data already fetched above —
    # no extra request needed. CS2 has shipped with kp hardcoded to 0 for
    # every player (kpMultiplier() treats 0 as "uncomputed" and stays
    # neutral) since players_stats already has everything needed: each
    # player's own team is right there, so team totals for this map are
    # just a sum over players sharing that team. kp = (kills + assists) /
    # team's total kills for the map, matching how LoL computes it.
    team_total_kills = {}
    for stats_row in result.values():
        team_total_kills[stats_row["team"]] = team_total_kills.get(stats_row["team"], 0) + stats_row["k"]
    for stats_row in result.values():
        total = team_total_kills.get(stats_row["team"], 0)
        stats_row["kp"] = (stats_row["k"] + stats_row["a"]) / total if total > 0 else 0
        # Raw component carried alongside the computed percentage — needed
        # by fetch_match_actuals to correctly combine kp across two maps
        # as sum(kills+assists)/sum(team_total_kills), not by naively
        # averaging two already-computed percentages (which would skew
        # results if the two maps had very different total kill counts).
        stats_row["team_total_k"] = total

    return result, resolved_name_by_team_id


# How many maps `actual` sums over. This is the window the model's
# history is built from and the window CS2's own two-map lines are posted
# over, and it does NOT move: widening it would silently rewrite every
# per-map rate the model has ever fit against.
COLLAPSED_WINDOW_MAPS = 2

# How many maps get a stored per-map breakdown. Covers a Bo5 that goes
# the distance; anything past that is not a format this board sees.
MAX_MAPS_STORED = 5

# The fields kept per map, as opposed to per series. Deliberately only
# the ones a posted line can name: a per-map breakdown of every style
# column would roughly triple a payload the browser downloads on every
# visit, to answer questions nobody is asking of a single map. The
# series total keeps carrying the full set.
PER_MAP_FIELDS = ("k", "d", "a", "hs")


def per_map_entry(row):
    """One player's line on one map, in the shape score_props.py grades.

    Missing fields are left out rather than zero-filled: the grader
    treats an absent stat as "not recorded for this game" and refuses,
    which is the honest answer, while a zero would grade as a real 0.
    """
    return {field: row[field] for field in PER_MAP_FIELDS
            if isinstance(row.get(field), int)}


async def fetch_match_actuals(session, match_slug, canonical_name_by_team_id):
    """Fetches one finished match's games (maps), per-player.

    Returns two views of the same box scores, because two different
    consumers need two different windows:

      totals   — one series total over maps 1-2 exactly, carrying the
                 full stat set. This is what the model's history reads
                 and it is fixed at two maps on purpose (see
                 COLLAPSED_WINDOW_MAPS).
      per_game — a list, one entry per map actually played, carrying
                 k/d/a/hs. This is what makes a map-1 line and a
                 maps-1-3 line gradeable AGAINST THE MAPS THEY NAME
                 rather than being refused for want of a breakdown.
                 Every map is fetched now, including the third of a Bo3
                 that went the distance, which the old code discarded.

    Returns (totals, per_game, maps_played, winner_name, team1_name,
    team2_name, score_str, match_date, team1_id, team2_id) —
    team1_id/team2_id are returned so build_region_payload can later
    reconcile this match's team names against the short form used by
    upcoming-match/schedule endpoints."""
    match = await bo3_get(session, f"/matches/{match_slug}", params={"with": "games"})
    if not match:
        return None
    games = sorted(match.get("games", []), key=lambda g: g.get("number", 0))[:MAX_MAPS_STORED]
    if not games:
        return None

    per_map_results = await asyncio.gather(
        *[fetch_map_player_stats(session, g["id"], canonical_name_by_team_id) for g in games]
    )

    totals = {}
    per_game = []
    name_by_team_id = {}
    for index, (map_stats, resolved_map) in enumerate(per_map_results):
        name_by_team_id.update(resolved_map)
        this_map = {}
        counts_toward_totals = index < COLLAPSED_WINDOW_MAPS
        for player_name, row in map_stats.items():
            team = row["team"]
            this_map.setdefault(team, {})[player_name] = per_map_entry(row)
            if not counts_toward_totals:
                continue
            totals.setdefault(team, {})
            slot = totals[team].setdefault(player_name, {"k": 0, "d": 0, "a": 0, "kp_numerator": 0, "kp_denominator": 0})
            slot["k"] += row["k"]
            slot["d"] += row["d"]
            slot["a"] += row["a"]
            accumulate_extra_stats(slot, row)
            # kp combined as sum(kills+assists)/sum(team_total_kills)
            # across both maps, not by averaging two already-computed
            # percentages — see fetch_map_player_stats for why.
            slot["kp_numerator"] += row["k"] + row["a"]
            slot["kp_denominator"] += row["team_total_k"]
        per_game.append(this_map)

    team_names = list(totals.keys())
    if len(team_names) != 2:
        return None  # incomplete data for this match — better to skip than build a lopsided entry

    id_by_name = {v: k for k, v in name_by_team_id.items()}
    team1_id = id_by_name.get(team_names[0])
    team2_id = id_by_name.get(team_names[1])

    winner_team_id = match.get("winner_team_id")
    winner_name = name_by_team_id.get(winner_team_id)
    if winner_name not in team_names:
        winner_name = None  # couldn't reliably resolve — leave unset rather than guess

    t1s, t2s = match.get("team1_score"), match.get("team2_score")
    score_str = f"{t1s}-{t2s}" if t1s is not None and t2s is not None else ""
    match_date = match.get("start_date")  # confirmed real field — "date" is not

    # maps_played stays the number of maps `totals` covers, not the
    # number played: every consumer of it reads it as "what the series
    # total divides by". The real map count is len(per_game).
    return (totals, per_game, min(len(games), COLLAPSED_WINDOW_MAPS), winner_name,
            team_names[0], team_names[1], score_str, match_date, team1_id, team2_id)


def normalize_team_name(name):
    return (name or "").strip()


def is_notable_match(m):
    """The one predicate deciding what counts as 'worth projecting' for
    matches that have ALREADY happened — stars is the primary signal
    (more reliably populated than tier, per real confirmed output for
    finished matches); tier is an OR'd secondary signal for whenever it
    happens to be present."""
    tier = (m.get("tier") or "").lower()
    stars = m.get("stars") or 0
    return tier in ACCEPTED_TIERS or stars >= MIN_STARS


def is_relevant_upcoming_match(m, discovered_team_ids, notable_tournament_ids):
    """Separate predicate for matches that HAVEN'T happened yet. tier is
    100% unpopulated for upcoming matches (real data: 29/29 today's
    matches had tier=None) and stars is near-zero pre-match (looks like
    accumulated post-match interest, not pre-match importance) — both
    dead signals here. bet_updates presence (real betting odds data) is
    the one confirmed genuinely correct signal: an earlier, more
    conservative version of this function excluded it as "too broad"
    based on team names looking minor (BESTIA Academy, paiN Academy,
    etc.) — but confirmed directly by the person using this app that
    those are real, bettable matches they can see elsewhere. Team-name
    pattern-matching was the wrong basis for that call; "has real posted
    odds" is the actual ground truth for "can be bet on", so it's used
    here as its own sufficient condition, not just a fallback. Tournament
    membership in notable_tournament_ids is kept as an additional OR
    condition since it doesn't hurt and may catch cases bet_updates
    misses."""
    tier = (m.get("tier") or "").lower()
    if tier in ACCEPTED_TIERS:
        return True
    t1id, t2id = m.get("team1_id"), m.get("team2_id")
    if t1id in discovered_team_ids or t2id in discovered_team_ids:
        return True
    if m.get("tournament_id") in notable_tournament_ids:
        return True
    return bool(m.get("bet_updates"))


def print_bet_updates_presence(matches, label):
    """Diagnostic — how many matches in this batch have populated
    bet_updates (real betting odds), to confirm this is a meaningfully
    selective signal before relying on it further."""
    present = sum(1 for m in matches if m.get("bet_updates"))
    print(f"  [debug] {label}: {present}/{len(matches)} have non-null bet_updates")


def print_star_distribution(matches, label):
    """Diagnostic — prints how many matches fall at each star value across
    the given batch, so MIN_STARS can be recalibrated from real numbers
    instead of guessed a second time if the current threshold is off."""
    counts = {}
    for m in matches:
        counts[m.get("stars")] = counts.get(m.get("stars"), 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (kv[0] is None, kv[0]))
    print(f"  [debug] {label} star distribution: " + ", ".join(f"{k}★={v}" for k, v in ordered))


def print_tier_distribution(matches, label):
    """Same idea as print_star_distribution but for tier — helps confirm
    whether tier is reliably populated for upcoming matches specifically,
    since it wasn't for at least one finished match tested earlier."""
    counts = {}
    for m in matches:
        counts[m.get("tier")] = counts.get(m.get("tier"), 0) + 1
    print(f"  [debug] {label} tier distribution: " + ", ".join(f"{k!r}={v}" for k, v in counts.items()))


def extract_match_team_names(m):
    """Pulls team1/team2 display names out of a raw match record from any
    of finished()/get_todays_matches()/get_team_upcoming_matches() — all
    share the same underlying shape (a nested {"name": ...} object, per
    confirmed real output), unlike players_stats' own clan_name field,
    which uses a different naming convention entirely."""
    t1 = m.get("team1")
    t2 = m.get("team2")
    team1 = normalize_team_name(t1.get("name") if isinstance(t1, dict) else m.get("team1_name"))
    team2 = normalize_team_name(t2.get("name") if isinstance(t2, dict) else m.get("team2_name"))
    return team1, team2


async def build_region_payload(cs2, session):
    teams_payload = {}
    past_matches = []
    upcoming_matches = []
    color_state = {"i": 0}

    # ---- Past matches: scan the global finished() feed, filter by TIER
    # instead of a curated team list — see the module docstring for why.
    # Deliberately uses ONLY the confirmed-working zero-argument call
    # (page 1, ~100 most recent global matches); a guessed pagination
    # kwarg (offset=) was tested and found to actively break the call
    # rather than being silently ignored. Proper multi-page pagination is
    # a real, known follow-up — not solved here — but shouldn't block
    # getting real data flowing. ----
    print("Fetching finished() — confirmed-working zero-argument call, page 1 only for now...")
    try:
        batch = await cs2.finished()
    except Exception as e:
        print(f"  ! finished() failed: {e}", file=sys.stderr)
        batch = None

    results = (batch.get("results", batch) if isinstance(batch, dict) else batch) if batch else []
    if results:
        sample = results[0]
        print(f"  [debug] first match: tier={sample.get('tier')!r}, stars={sample.get('stars')!r}")
        print_star_distribution(results, "global finished()")

    tier_filtered = [m for m in results if is_notable_match(m)]
    print(f"  {len(tier_filtered)} notable matches (tier in {sorted(ACCEPTED_TIERS)} OR stars >= {MIN_STARS}) "
          f"(out of {len(results)} most-recent global matches scanned)\n")

    # Tournament-level reputation, built from matches already confirmed
    # notable — used below for upcoming matches, since tier/stars/
    # bet_updates were all tested live and found unreliable or too broad
    # pre-match (tier: 100% None for upcoming matches; stars: near-zero
    # pre-match; bet_updates: present on 23/29 matches, basically every
    # match with any bookmaker coverage at all, not a meaningful filter).
    # A match belonging to the SAME tournament as an already-confirmed
    # notable result is a much more principled signal than anything on
    # the individual upcoming match record itself.
    notable_tournament_ids = {m.get("tournament_id") for m in tier_filtered if m.get("tournament_id") is not None}

    # Was 60 against a feed that reliably returns 100 notable matches, so
    # forty were discovered and thrown away every single run -- already
    # filtered, already known to be worth having, discarded to save the
    # cheap half of the work. Discovery is the expensive part and happens
    # either way; this only adds the per-match stat fetches.
    #
    # Kept at 100 for COVERAGE, not accuracy -- more per-team history
    # does not help CS2, see MATCHES_KEPT_PER_TEAM -- and the first run
    # at 100 says the coverage case is real but SMALL. Measured against
    # the run before it:
    #
    #     matches   481 -> 526   (+45)
    #     players  1404 -> 1426  (+22)
    #     teams     258 -> 262   (+4)
    #     median maps per player    2 -> 2   (the cap bounds depth)
    #     fixture teams rostered  94/98 -> 81/84, i.e. 96% both times
    #
    # So it adds real data every run and did NOT move the rostered share,
    # which was already 96%. Unrostered fixtures went 4 to 3. Worth the
    # extra stat fetches because the additions accumulate and a fixture
    # nobody can project is worth less than one projected imperfectly --
    # but nobody should expect this to show up in an accuracy number.
    MATCH_LIMIT = 100
    matches_to_process = tier_filtered[:MATCH_LIMIT]
    print(f"Fetching per-map player stats for {len(matches_to_process)} matches "
          f"(capped from {len(tier_filtered)} found; this is the slow part)...")
    canonical_name_by_team_id = {}  # starts empty — no curated list to seed it with anymore
    sem = asyncio.Semaphore(6)
    progress = {"done": 0, "total": len(matches_to_process)}

    async def process(m, name_lookup):
        async with sem:
            result = await fetch_match_actuals(session, m["slug"], name_lookup)
            progress["done"] += 1
            if progress["done"] % 10 == 0 or progress["done"] == progress["total"]:
                print(f"  ...{progress['done']}/{progress['total']} matches processed")
            if not result:
                return None
            (totals, per_game, maps_played, winner_name, team1_name, team2_name,
             score_str, match_date, t1id, t2id) = result
            return {
                # The source's own id for this match. Written out because
                # merging runs needs a key, and date+teams is not one: two
                # teams can meet twice in a day, which is exactly the
                # collision that made a parity harness disagree with
                # itself once already. Same reason base_game_id is stored
                # for LoL and game_id for CS2 careers.
                "match_id": m.get("slug"),
                "week": None, "date": match_date[:10] if match_date else None,
                # The full timestamp as well as the day. Two teams do meet
                # twice in a day, and the grader cannot tell which match a
                # posted line belonged to from a calendar date -- it
                # refuses rather than coin-flip, and that refusal was 211
                # of the ungraded lines. The posted line carries a clock;
                # now so does the result.
                "start_time": match_date,
                "patch": None, "teamA": team1_name, "teamB": team2_name,
                "winner": winner_name,
                "score": score_str, "actual": totals, "games": maps_played,
                # One entry per map actually played. `actual` above is
                # the maps 1-2 total and always will be; this is what
                # lets a map-1 line and a maps-1-3 line each be graded
                # over its own window instead of refused.
                "per_game": per_game,
                "_team1_id": t1id, "_team2_id": t2id,  # dropped before writing final output — see reconciliation below
            }

    processed = await asyncio.gather(*[process(m, canonical_name_by_team_id) for m in matches_to_process])
    for entry in processed:
        if entry and entry["actual"]:
            past_matches.append(entry)
    print(f"  {len(past_matches)} matches with real player stats captured\n")

    # ---- Discover which teams are actually notable right now, from the
    # matches just processed — no hardcoded list, this naturally scales to
    # however many teams are playing at an accepted tier currently.
    # Tracks EVERY name variant seen per team_id (not just one) — clan_name
    # is confirmed NOT stable across different matches for the same team
    # (e.g. "MIBR" vs "MIBR!LOS", "Luminosity" vs "Luminosity Gaming" turned
    # out to be the same team_id with different clan_name strings depending
    # on which specific match record you look at), so reconciliation below
    # needs the full list to catch this, not just one name per ID. ----
    names_seen_by_id = {}
    for m in past_matches:
        if m.get("_team1_id") is not None:
            names_seen_by_id.setdefault(m["_team1_id"], []).append(m["teamA"])
        if m.get("_team2_id") is not None:
            names_seen_by_id.setdefault(m["_team2_id"], []).append(m["teamB"])
    discovered_team_ids = set(names_seen_by_id.keys())
    multi_name_ids = {tid: names for tid, names in names_seen_by_id.items() if len(set(names)) > 1}
    if multi_name_ids:
        print(f"  [debug] {len(multi_name_ids)} team(s) have inconsistent naming across matches "
              f"in the source data itself: { {tid: list(set(n)) for tid, n in multi_name_ids.items()} }")
    print(f"Discovered {len(discovered_team_ids)} distinct teams currently playing notable matches\n")

    # ---- Upcoming matches: today's global feed (notable-match filtered,
    # catches any team with no recent finished() result) plus full
    # multi-day schedules for every discovered team. Also builds
    # short_name_by_team_id, used below to reconcile teams_payload's
    # clan_name-based (long-form) names against the short form these
    # schedule endpoints use — confirmed via real output that the two
    # disagree (e.g. "Team Falcons" vs "Falcons"). ----
    short_name_by_team_id = {}
    seen_upcoming_ids = set()
    upcoming_stats = {"raw": 0, "duplicate": 0, "tbd_excluded": 0, "already_played": 0, "added": 0}

    def add_upcoming(m):
        mid = m.get("id")
        upcoming_stats["raw"] += 1
        if mid in seen_upcoming_ids:
            upcoming_stats["duplicate"] += 1
            return
        team1, team2 = extract_match_team_names(m)
        t1id, t2id = m.get("team1_id"), m.get("team2_id")
        if t1id is not None and team1:
            short_name_by_team_id[t1id] = team1
        if t2id is not None and team2:
            short_name_by_team_id[t2id] = team2
        if not team1 or not team2 or "TBD" in (team1, team2):
            upcoming_stats["tbd_excluded"] += 1
            return
        start = parse_match_start(m)
        if start is not None and start < datetime.now(timezone.utc) - timedelta(hours=UPCOMING_MAX_AGE_HOURS):
            upcoming_stats["already_played"] += 1
            return
        seen_upcoming_ids.add(mid)
        upcoming_stats["added"] += 1
        upcoming_matches.append({"date": m.get("start_date") or m.get("date"), "teamA": team1, "teamB": team2, "block": None})

    print("Fetching today's global matches (tier or known-team filtered — see is_relevant_upcoming_match)...")
    try:
        today_batch = await cs2.get_todays_matches()
    except Exception as e:
        print(f"  ! get_todays_matches() failed: {e}", file=sys.stderr)
        today_batch = None
    today_matches = (today_batch.get("results", today_batch) if isinstance(today_batch, dict) else today_batch) if today_batch else []
    if today_matches:
        print_star_distribution(today_matches, "today's global matches")
        print_tier_distribution(today_matches, "today's global matches")
        print_bet_updates_presence(today_matches, "today's global matches")
        today_tournament_overlap = sum(1 for m in today_matches if m.get("tournament_id") in notable_tournament_ids)
        print(f"  [debug] today's global matches: {today_tournament_overlap}/{len(today_matches)} "
              f"belong to a tournament that already produced a notable finished match")
    for m in today_matches:
        if is_relevant_upcoming_match(m, discovered_team_ids, notable_tournament_ids):
            add_upcoming(m)
    print(f"  {len(upcoming_matches)} from today's global feed "
          f"(of {len(today_matches)} raw matches today, before filtering)\n")

    # The global window. This is the source that actually covers tomorrow:
    # the feed above is today-only, and the per-team schedules below reach
    # only the teams this run happened to discover from finished matches.
    print(f"Fetching the next {UPCOMING_WINDOW_DAYS} days of global upcoming matches...")
    try:
        window_matches = await fetch_upcoming_window(session)
    except Exception as e:
        print(f"  ! upcoming-window fetch failed, falling back to team schedules only: {e}",
              file=sys.stderr)
        window_matches = []
    window_relevant = 0
    # Per-day counts before and after filtering. A day that is empty in the
    # published data is either empty at the source or being filtered out,
    # and those need completely different fixes — this says which.
    by_day_raw, by_day_kept = {}, {}
    for m in window_matches:
        start = parse_match_start(m)
        day = start.date().isoformat() if start else "unknown"
        by_day_raw[day] = by_day_raw.get(day, 0) + 1
        if is_relevant_upcoming_match(m, discovered_team_ids, notable_tournament_ids):
            window_relevant += 1
            by_day_kept[day] = by_day_kept.get(day, 0) + 1
            add_upcoming(m)
    print(f"  {len(window_matches)} matches in the window, {window_relevant} relevant "
          f"after filtering")
    for day in sorted(by_day_raw):
        kept = by_day_kept.get(day, 0)
        flag = "   <-- ALL FILTERED OUT" if kept == 0 and by_day_raw[day] else ""
        print(f"    [debug] {day}: {by_day_raw[day]:3d} at source -> {kept:3d} kept{flag}")
    print()

    print(f"Fetching multi-day schedules for {len(discovered_team_ids)} discovered teams...")
    schedule_fetch_failures = 0
    schedule_raw_total = 0
    for team_id in discovered_team_ids:
        try:
            sched = await cs2.get_team_upcoming_matches(team_id)
        except Exception as e:
            schedule_fetch_failures += 1
            print(f"  ! get_team_upcoming_matches({team_id}) failed: {e}", file=sys.stderr)
            continue
        matches = sched.get("results", sched) if isinstance(sched, dict) else sched
        schedule_raw_total += len(matches or [])
        for m in (matches or []):
            add_upcoming(m)
    print(f"  {schedule_raw_total} raw matches across all team schedules "
          f"({schedule_fetch_failures} team(s) failed to fetch)")
    print(f"  [debug] upcoming funnel: {upcoming_stats['raw']} raw seen -> "
          f"{upcoming_stats['duplicate']} duplicate, {upcoming_stats['tbd_excluded']} TBD-excluded, "
          f"{upcoming_stats['already_played']} already played, "
          f"{upcoming_stats['added']} added")
    print(f"  {len(upcoming_matches)} total upcoming matches (today's global feed + discovered teams' schedules)\n")

    # ---- Reconcile names BEFORE aggregating player stats, not after —
    # every name variant seen for a given team_id gets mapped to ONE
    # canonical name (the short form matching upcoming_matches when known,
    # otherwise the most-recently-seen clan_name for that ID) and applied
    # to past_matches directly. This has to happen before the player-stat
    # averaging below, not as a post-hoc merge on teams_payload — merging
    # two already-averaged player lists after the fact would silently drop
    # whichever name variant's matches got processed second, understating
    # a player's real per-game average instead of reflecting their full
    # match history. ----
    canonical_name_by_id = {}
    for tid, names in names_seen_by_id.items():
        canonical_name_by_id[tid] = short_name_by_team_id.get(tid, names[0])

    name_rename_map = {}
    for tid, names in names_seen_by_id.items():
        canonical = canonical_name_by_id[tid]
        for name in set(names):
            if name != canonical:
                name_rename_map[name] = canonical

    if name_rename_map:
        print(f"Reconciling {len(name_rename_map)} team name variant(s) to one canonical name each: "
              f"{name_rename_map}\n")

    for m in past_matches:
        m["teamA"] = name_rename_map.get(m["teamA"], m["teamA"])
        m["teamB"] = name_rename_map.get(m["teamB"], m["teamB"])
        if m["winner"] in name_rename_map:
            m["winner"] = name_rename_map[m["winner"]]
        m["actual"] = {name_rename_map.get(k, k): v for k, v in m["actual"].items()}
        m.pop("_team1_id", None)
        m.pop("_team2_id", None)

    # ---- Fold in what previous runs already scraped ----
    #
    # The global feed is one page, so each run sees only the most recent
    # ~100 matches and any team that has gone quiet since falls out of
    # the file entirely -- rosters, history and all. That is why teams
    # churned between runs and why coverage could never climb: 51 teams
    # rostered against 123 with fixtures, rebuilt from scratch every run.
    #
    # Merging makes coverage cumulative instead. The name reconciliation
    # above is applied to the stored matches too, by name, since they no
    # longer carry the team ids it normally keys on.
    previous = load_previous_matches()
    if previous:
        for m in previous:
            m["teamA"] = name_rename_map.get(m["teamA"], m["teamA"])
            m["teamB"] = name_rename_map.get(m["teamB"], m["teamB"])
            if m.get("winner") in name_rename_map:
                m["winner"] = name_rename_map[m["winner"]]
            m["actual"] = {name_rename_map.get(k, k): v for k, v in (m.get("actual") or {}).items()}
    fresh_count = len(past_matches)
    past_matches = merge_past_matches(previous, past_matches)
    print(f"Merged with previous run: {fresh_count} fresh + {len(previous)} stored "
          f"-> {len(past_matches)} kept (max {MATCHES_KEPT_PER_TEAM} per team)\n")

    # ---- Build teams payload from whoever actually showed up in past_matches ----
    add_team_players(past_matches, past_matches, teams_payload, color_state)

    # ---- Opponent backfill: any team appearing in upcoming_matches that
    # still has no roster data (genuinely outside the tier/star-notable set
    # that drove discovery — e.g. a team whose OWN recent matches didn't
    # individually clear the notability bar, even though they're currently
    # scheduled against a team that did). Without this, these matches would
    # permanently show "roster data not loaded" no matter how discovery is
    # tuned, since they'd never appear via the tier/star filter on their own.
    # Reuses the SAME global finished() results already fetched (via
    # `results`, still in scope) — no extra scanning needed. ----
    covered_names = set(teams_payload.keys())
    # Soonest match first. Backfill costs a team search plus several match
    # fetches per opponent, and the upcoming list now spans a week rather
    # than the handful of fixtures the discovered teams happened to have,
    # so this is no longer a naturally small set. bo3.gg already returns
    # HTTP 429 during a normal run, so it is bounded — and bounded by what
    # matters, which is who plays next.
    #
    # A fixture whose opponent misses the cut is still published; the app
    # renders it without player projections rather than hiding it, which is
    # the right trade. Missing a projection is a worse match card. Missing
    # the match entirely is the bug this whole change exists to fix.
    soonest_by_name = {}
    for m in upcoming_matches:
        for name in (m["teamA"], m["teamB"]):
            if name in covered_names:
                continue
            when = m.get("date") or ""
            if name not in soonest_by_name or when < soonest_by_name[name]:
                soonest_by_name[name] = when
    ranked_opponents = sorted(soonest_by_name, key=lambda n: (soonest_by_name[n], n))
    unresolved_opponents = ranked_opponents[:MAX_BACKFILL_OPPONENTS]
    skipped_backfill = ranked_opponents[MAX_BACKFILL_OPPONENTS:]
    if skipped_backfill:
        print(f"  [debug] {len(skipped_backfill)} opponent(s) beyond the backfill cap of "
              f"{MAX_BACKFILL_OPPONENTS} — their fixtures still appear, without player "
              f"projections: {skipped_backfill}")
    if unresolved_opponents:
        print(f"Backfilling roster data for {len(unresolved_opponents)} opponent(s) with no roster yet: "
              f"{unresolved_opponents}")
        # Widened from 3 to 6 — confirmed via live diagnosis that a real
        # match can flip from empty player stats to fully populated
        # within minutes (bo3.gg's own stats pipeline hasn't finished
        # processing very recent matches yet, not a bug on our end: a
        # game's raw "state" field is null/rounds_count null/scores null
        # when stats aren't ready, vs "done" with real scores when they
        # are). Fetching more candidates per team gives a better chance
        # of landing on an already-processed match instead of the
        # very latest one that might still be mid-pipeline.
        backfill_matches = []
        team_name_by_match_slug = {}  # tracks which target opponent each match came from, for the per-team success report below
        for name in unresolved_opponents:
            try:
                found = await cs2.search_teams(name)
            except Exception as e:
                print(f"  ! search_teams({name!r}) failed: {e}", file=sys.stderr)
                continue
            candidates = found.get("results", found) if isinstance(found, dict) else found
            if not candidates:
                print(f"  ! no team found for opponent {name!r} — will keep showing "
                      f"'roster data not loaded' for this one")
                continue
            exact = next((t for t in candidates if t.get("name") == name), None)
            team = exact or candidates[0]
            opp_id = team["id"]
            short_name_by_team_id[opp_id] = name
            # Direct, team-scoped lookup — no longer dependent on whether
            # this team happened to appear in the ~100-match global sample
            # (confirmed via live testing: DENDELE and Inner Circle both
            # had zero matches in that sample, but this endpoint correctly
            # returns their real match history directly).
            their_matches = await fetch_team_recent_matches(session, opp_id, limit=BACKFILL_MATCHES_PER_TEAM)
            print(f"  {name!r} -> id={opp_id}, {len(their_matches)} recent match(es) found to backfill from")
            for m in their_matches:
                team_name_by_match_slug[m.get("slug")] = name
            backfill_matches.extend(their_matches)

        if backfill_matches:
            backfill_processed = await asyncio.gather(
                *[process(m, short_name_by_team_id) for m in backfill_matches]
            )
            added = 0
            succeeded_by_team = {}
            attempted_by_team = {}
            for m in backfill_matches:
                target = team_name_by_match_slug.get(m.get("slug"))
                if target:
                    attempted_by_team[target] = attempted_by_team.get(target, 0) + 1
            for m, entry in zip(backfill_matches, backfill_processed):
                target = team_name_by_match_slug.get(m.get("slug"))
                if entry and entry["actual"]:
                    entry["teamA"] = short_name_by_team_id.get(entry.pop("_team1_id", None), entry["teamA"])
                    entry["teamB"] = short_name_by_team_id.get(entry.pop("_team2_id", None), entry["teamB"])
                    past_matches.append(entry)
                    added += 1
                    if target:
                        succeeded_by_team[target] = succeeded_by_team.get(target, 0) + 1
            print(f"  added {added} backfilled match(es) with real player stats\n")

            # Per-team success report — distinguishes "found matches but
            # they all failed to process" from "no matches found at all",
            # since both look identical downstream (still no roster) but
            # need different fixes.
            failed_teams = [name for name in unresolved_opponents
                             if succeeded_by_team.get(name, 0) == 0]
            if failed_teams:
                print(f"  [debug] {len(failed_teams)} opponent(s) still have NO usable player data "
                      f"after backfill — attempted vs succeeded per team:")
                for name in failed_teams:
                    print(f"    {name!r}: {attempted_by_team.get(name, 0)} match(es) attempted, "
                          f"{succeeded_by_team.get(name, 0)} succeeded")
                print()

            # Fold the newly-backfilled matches into teams_payload, for
            # BOTH sides of each. The opponent in a backfilled match is
            # not who the backfill went looking for, but its player stats
            # arrive in the same record at no extra request, and refusing
            # to read them is what left most of the fixture list blank.
            add_team_players(past_matches[-added:] if added else [],
                             past_matches, teams_payload, color_state)

    # ---- results for lines we already posted -------------------------
    #
    # Separate from the roster backfill above, which chases teams with no
    # players. This chases teams whose POSTED LINES have no outcome: the
    # fixture was projected, played, and never scraped, because this
    # scraper takes the most recent NOTABLE matches and the prop provider
    # posts far wider. 646 of 724 gradeable CS2 lines were stranded that
    # way, all but one of them dated after the last match we held for
    # that team.
    #
    # Reuses the same per-team fetch, so it is the proven path with a
    # different list of teams. Bounded, and ordered by how many lines are
    # waiting on each team, so a capped run buys the most answers it can.
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    awaiting = teams_awaiting_results(load_props_history(), past_matches,
                                      set(teams_payload), today)
    if awaiting:
        wanted = awaiting[:MAX_RESULT_BACKFILL_TEAMS]
        print(f"Fetching results for {len(wanted)} team(s) with posted lines still ungraded"
              + (f" (of {len(awaiting)}; the rest wait for the next run)"
                 if len(awaiting) > len(wanted) else "") + f": {wanted}")
        result_matches = []
        for name in wanted:
            try:
                found = await cs2.search_teams(name)
            except Exception as e:
                print(f"  ! search_teams({name!r}) failed: {e}", file=sys.stderr)
                continue
            candidates = found.get("results", found) if isinstance(found, dict) else found
            if not candidates:
                continue
            exact = next((t for t in candidates if t.get("name") == name), None)
            team_id = (exact or candidates[0]).get("id")
            if not team_id:
                continue
            short_name_by_team_id[team_id] = name
            result_matches.extend(
                await fetch_team_recent_matches(session, team_id,
                                                limit=BACKFILL_MATCHES_PER_TEAM))
        if result_matches:
            # Deliberately NOT called `processed`. The main block above
            # has a variable by that name, and an edit that dropped this
            # gather silently re-absorbed ITS results instead of these:
            # the run reported "refreshed 72 already on file", changed
            # exactly one record, and fetched none of the 370 matches it
            # had just gone to the trouble of selecting. A name that
            # cannot collide turns that into a NameError.
            result_processed = await asyncio.gather(
                *[process(m, short_name_by_team_id) for m in result_matches])
            for entry in result_processed:
                if entry:
                    entry["teamA"] = short_name_by_team_id.get(entry.pop("_team1_id", None), entry["teamA"])
                    entry["teamB"] = short_name_by_team_id.get(entry.pop("_team2_id", None), entry["teamB"])
            added_results, refreshed_results, touched = absorb_results(past_matches, result_processed)
            print(f"  added {added_results} result(s) for previously ungradeable lines, "
                  f"refreshed {refreshed_results} already on file\n")
            # The entries themselves, not a tail slice: a refreshed match
            # sits wherever it already was, so slicing would scan the
            # wrong records (and, at 0 added, the whole list).
            add_team_players(touched, past_matches, teams_payload, color_state)

    return {"teams": teams_payload, "past_matches": past_matches, "upcoming_matches": upcoming_matches}


def merge_cs2_career_data(payload):
    """Folds each player's RAW per-game career history (from
    scrape_cs2_career.py's output) into their existing record in this
    payload. Simpler than LoL's equivalent (merge.py's
    merge_career_data) since cs2_career_data.json is already keyed by
    player name directly -- no name-based lookup construction needed,
    CS2 has no separate numeric-ID-keyed intermediate step the way
    gol.gg's player IDs required for LoL.

    Stores the raw games list as "career_games", NOT a pre-decayed
    number — a real, confirmed leakage issue found that computing one
    static "career" baseline at scrape time (using "most recent N games
    as of right now") meant a historical backtest prediction could be
    fed a career number partly built from games that hadn't happened yet
    as of that prediction's own date. Consumers now compute the decayed
    baseline POINT-IN-TIME from this raw list — the same standard
    cur/hist/opponent already hold themselves to elsewhere in this
    model — rather than trusting one static snapshot for every
    prediction regardless of when it's dated.

    Gracefully optional: if cs2_career_data.json doesn't exist yet
    (scrape_cs2_career.py hasn't been run, or isn't wired into this
    particular workflow run), every player just gets career_games=[] and
    the rest of the pipeline is unaffected."""
    try:
        with open("cs2_career_data.json") as f:
            career_data = json.load(f)
    except FileNotFoundError:
        print("! cs2_career_data.json not found — skipping career merge (every player gets "
              "career_games=[]; run scrape_cs2_career.py first if this wasn't intentional)",
              file=sys.stderr)
        career_data = {}

    matched = 0
    unmatched = []
    for team in payload.get("teams", {}).values():
        for player in team.get("players", []):
            name = player.get("name")
            record = career_data.get(name)
            games = record.get("games") if record else None
            if games:
                player["career_games"] = games
                matched += 1
            else:
                player["career_games"] = []
                unmatched.append(name)

    total = matched + len(unmatched)
    print(f"Merged CS2 career data: {matched}/{total} player(s) matched")
    if unmatched:
        shown = unmatched[:20]
        more = f" ... (+{len(unmatched) - 20} more)" if len(unmatched) > 20 else ""
        print(f"  ! unmatched (no career data — career_games=[], falls back to cur only): {shown}{more}",
              file=sys.stderr)


async def main():
    async with CS2() as cs2, aiohttp.ClientSession() as session:
        payload = await build_region_payload(cs2, session)

    merge_cs2_career_data(payload)

    # One team, one name. bo3.gg spells a team differently across its own
    # endpoints and across runs -- this script already reported that as a
    # [debug] line above and then wrote both spellings anyway, so the
    # committed file had accumulated 22 teams holding two entries each with
    # their match history divided between them. Every projection reads history
    # by team NAME, so a split team projects off half its games. Run here, on
    # the merged payload, so it heals what is already in the file as well as
    # what this run added. See team_aliases.py for why the rule refuses the
    # rebrands and abbreviations it can also see.
    renames, alias_counts = reconcile(payload)
    if renames:
        folded = collections.defaultdict(list)
        for old_name, new_name in sorted(renames.items()):
            folded[new_name].append(old_name)
        print(f"\nFolded {len(renames)} duplicate team spelling(s) into "
              f"{len(folded)} team(s):")
        for new_name, olds in sorted(folded.items()):
            print(f"  {new_name} <- {', '.join(olds)}")
        print(f"  ({alias_counts['players_moved']} player(s) moved, "
              f"{alias_counts['fields_rewritten']} match field(s) rewritten)")

    # refreshed_at mirrors generated_at here, and that is the true
    # answer rather than a shortcut: this scraper has no partial-outage
    # fallback -- the step fails and commits nothing -- so a region
    # present in the file was re-fetched this run. The field exists on
    # every game so the app can ask one question of all three.
    now_iso = datetime.now(timezone.utc).isoformat()
    payload["refreshed_at"] = now_iso
    output = {
        "generated_at": now_iso,
        "regions": {"CS2": payload},
    }
    with open("cs2_data.json", "w") as f:
        # Written minified: these files are machine-generated and never read
        # by hand, and indent=2 was about two thirds of the bytes
        # (data.json: 7.5MB -> 2.4MB). GitHub serves them gzipped, so the
        # win on the wire is smaller (~535KB -> ~340KB), but the browser
        # still parses the full decompressed text, and every run commits a
        # whole fresh copy.
        json.dump(output, f, separators=(",", ":"))
    print(f"\nWrote cs2_data.json: {len(payload['teams'])} teams, "
          f"{len(payload['past_matches'])} past matches, {len(payload['upcoming_matches'])} upcoming matches")
    report_source_fields(payload)




# How many matches of history to keep per team when merging runs.
#
# The point of merging at all is that a team stays rostered once seen,
# instead of falling out the moment it drops off one page of the global
# feed. Keeping every match ever scraped would do that too and grow
# without bound, so each team keeps its most recent few and a match
# survives while either side still wants it.
#
# Eight because that is what the app draws: the form chart is the last 8,
# and CS2's model leans on the career tier (kills is career 1.0) fed from
# cs2_career_data.json, not on this file's match list.
#
# RAISED TO 16 AND PUT BACK. Thinning the committed history looked like a
# monotonic -3.0% on kills MAE from cap 2 to cap 8, and that was a row-
# composition artefact: changing how much history exists changes WHICH
# ROWS ARE SCOREABLE, because a row with no prior history is dropped
# rather than predicted. Scored on rows that survive at every depth, more
# per-team history is slightly WORSE, on the 1335 rows that survive at
# every depth:
#
#     cap2=6.7220  cap3=6.7606  cap4=6.7415  cap6=6.7843  cap8=6.7846
#
# kills +0.93% from cap 2 to cap 8, deaths +0.61%, assists +0.77%.
#
# Which is consistent with the other CS2 result: pt_games feeds the
# shrink denominator, so deeper history shrinks less, and CS2's MAE
# prefers heavy shrinkage. Eight stands.
MATCHES_KEPT_PER_TEAM = 8


def teams_awaiting_results(props_history, past_matches, tracked_teams, today):
    """Teams to fetch recent results for, busiest first.

    A line is waiting when its fixture has been played, we track the
    team, and we hold nothing that can SETTLE it -- which is not the
    same as holding no match at all. Returns team names ordered by how
    many lines are waiting on each, so a bounded run buys the most
    gradeable rows it can.

    Pure on purpose: the fetch that follows is network-bound and cannot
    be tested offline, but WHICH results are worth asking for is the
    part with the logic in it.
    """
    held = {}
    for m in past_matches or []:
        date = (m.get("date") or "")[:10]
        if not date:
            continue
        for side in ("teamA", "teamB"):
            if m.get(side):
                held.setdefault((m[side], date), []).append(m)

    waiting = {}
    for row in props_history or []:
        if row.get("game") != "cs2":
            continue
        team = row.get("team")
        date = (row.get("start_time") or "")[:10]
        if not team or len(date) != 10:
            continue
        # A fixture that has not happened yet is not a missing result.
        if date > today:
            continue
        if team not in tracked_teams:
            continue
        # A line over a window nothing could ever settle is not a reason
        # to spend a request -- re-fetching buys it nothing.
        maps = row.get("maps")
        if not isinstance(maps, int) or maps <= 0:
            continue
        # Holding A match is not the same as holding one that can settle
        # THIS line. A record with no per-map breakdown answers maps 1-2
        # and nothing else, so a map-1 line sitting behind one is exactly
        # as ungradeable as a line with no match at all -- and counting
        # it as settled is what stranded 202 of them, in matches the
        # backfill then skipped on every subsequent run.
        if any(window_is_covered(m, maps) for m in held.get((team, date), ())):
            continue
        waiting[team] = waiting.get(team, 0) + 1
    return [t for t, _ in sorted(waiting.items(), key=lambda kv: (-kv[1], kv[0]))]


def load_props_history(path=PROPS_HISTORY_PATH):
    """Every posted line on record, or [] when there is no file.

    Never fatal: without it this run simply behaves as every run did
    before the record existed.
    """
    rows = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # one bad line is not a reason to lose the rest
    except (FileNotFoundError, OSError) as e:
        print(f"  no {path} to check for ungraded lines ({e.__class__.__name__})")
        return []
    return rows


def match_key(m):
    """Identity for de-duplicating a match across runs.

    The source's own id when present. Records written before that id was
    stored fall back to a composite -- and it is a FALLBACK, not a
    scheme: two teams can meet twice in one day, so the composite pulls
    in the score to separate a double-header. Those legacy records age
    out on their own as MATCHES_KEPT_PER_TEAM rolls forward.
    """
    mid = m.get("match_id")
    if mid:
        return ("id", str(mid))
    return ("legacy", m.get("date"), m.get("teamA"), m.get("teamB"), m.get("score"))


def absorb_results(past_matches, fresh):
    """Fold re-fetched results into the list in place.

    Returns (added, refreshed, touched) -- touched being the entries
    that actually landed, in the order they were offered.

    A match already on file is REPLACED, not skipped. Skipping quietly
    undid the whole point of re-fetching: the results asked for are
    asked for precisely because what we hold cannot settle their lines,
    so the fetched copy is the better one by construction. A real run
    proved it -- 60 teams selected, 370 matches fetched, 9 kept, because
    every one that mattered collided with the stale record it was meant
    to replace. It is the same rule merge_past_matches applies below,
    for the same reason: a stored record may predate a fix to what gets
    read out of a box score.

    Two things are refused rather than absorbed, because either would
    turn a source hiccup into data loss:
      - an entry with no player stats, which improves on nothing
      - a record with no per-map breakdown replacing one that has it

    Pure on purpose, like teams_awaiting_results: the fetch around it is
    network-bound and cannot be tested offline, but what gets KEPT is
    the part with the logic in it.
    """
    position = {}
    for i, m in enumerate(past_matches):
        position.setdefault(match_key(m), i)

    added = refreshed = 0
    touched = []
    for entry in fresh or []:
        if not entry or not entry.get("actual"):
            continue
        key = match_key(entry)
        at = position.get(key)
        if at is None:
            position[key] = len(past_matches)
            past_matches.append(entry)
            added += 1
        else:
            if past_matches[at].get("per_game") and not entry.get("per_game"):
                continue
            past_matches[at] = entry
            refreshed += 1
        touched.append(entry)
    return added, refreshed, touched


def merge_past_matches(previous, fresh, per_team=MATCHES_KEPT_PER_TEAM):
    """Fold last run's matches in with this run's, newest first.

    Returns the merged list. A match this run fetched wins over the same
    match stored before, because the stored one may predate a fix to how
    stats are read -- the headshot capture landed exactly that way.

    Without this the roster is only ever as wide as one page of the
    global feed: 51 teams out of the 123 with fixtures, and teams
    dropping out between runs even after being scraped.
    """
    by_key = {}
    for m in list(previous or []) + list(fresh or []):
        if not m or not m.get("teamA") or not m.get("teamB"):
            continue
        by_key[match_key(m)] = m          # fresh overwrites previous
    ordered = sorted(by_key.values(), key=lambda m: (m.get("date") or ""), reverse=True)

    kept, seen_per_team = [], {}
    for m in ordered:
        sides = (m["teamA"], m["teamB"])
        # Kept while EITHER side still has room, so a busy team does not
        # evict the only match a quiet opponent has on file.
        if any(seen_per_team.get(t, 0) < per_team for t in sides):
            kept.append(m)
            for t in sides:
                seen_per_team[t] = seen_per_team.get(t, 0) + 1
    return kept


def load_previous_matches(path="cs2_data.json"):
    """Last run's matches, or [] when there is no readable file.

    Never fatal: a missing or corrupt file means this run behaves exactly
    as every run did before merging existed, which is a worse result but
    a working one.
    """
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        print(f"  no previous cs2_data.json to merge with ({e.__class__.__name__}) — "
              f"this run starts from the feed alone")
        return []
    return ((data.get("regions") or {}).get("CS2") or {}).get("past_matches") or []


def add_team_players(matches, past_matches, teams_payload, color_state, only=None):
    """Give every team appearing in `matches` a roster entry, built from the
    player stats those match records already carry.

    `matches` is what to scan; `past_matches` is what to aggregate rates
    over, which is the full record even when only a slice is being
    scanned. `only` restricts which teams get an entry.

    This was written out twice -- once for the main pass and once for the
    backfill -- and the second copy carried a `continue` that skipped
    every team except the ones being backfilled. A backfilled match record
    carries player stats for BOTH sides, so that discarded a complete
    roster for the opponent every time: 58 teams, ENCE and 3DMAX among
    them, sat in past_matches with full stats and no team entry, and over
    half the fixture list rendered with no data on either side. Two copies
    of a thing that must agree is the bug, same as it was for season_rates.
    """
    for m in matches:
        for side in ("teamA", "teamB"):
            team_name = m[side]
            if only is not None and team_name not in only:
                continue
            if team_name not in teams_payload:
                teams_payload[team_name] = {
                    "color": COLOR_PALETTE[color_state["i"] % len(COLOR_PALETTE)], "players": []}
                color_state["i"] += 1
            existing_names = {p["name"] for p in teams_payload[team_name]["players"]}
            for player_name in (m["actual"].get(team_name) or {}):
                if player_name in existing_names:
                    continue
                cur = season_rates(past_matches, team_name, player_name)
                if cur is None:
                    continue
                teams_payload[team_name]["players"].append({
                    "name": player_name, "role": None, "cur": cur,
                    "hist": None,  # no clean split boundary for CS2 — model falls back to cur alone
                })
                existing_names.add(player_name)


def report_source_fields(payload):
    """Say what the source actually offered, and what got captured.

    The app models kills, deaths and assists because that is what it has
    history for -- but PrizePicks posts lines on more, headshots among
    them, and every one of those is dropped for want of anything to
    project from. Whether that can change is a question about bo3.gg's
    players_stats row, which is read in this file and documented nowhere,
    so the run answers it rather than the next person guessing.

    Printed every time, not just when something is missing: a field that
    quietly disappears upstream should be as visible as one that arrives.
    """
    print("\n  source fields on bo3.gg players_stats:")
    known = {"kills", "death", "assists", "steam_profile_id", "team_clan", "clan_name"}
    extra = sorted(SOURCE_FIELDS_SEEN - known)
    print(f"    all keys seen ({len(SOURCE_FIELDS_SEEN)}): {', '.join(sorted(SOURCE_FIELDS_SEEN)) or 'none'}")
    if extra:
        print(f"    beyond what this scraper already reads: {', '.join(extra)}")
    for our_key, candidates in EXTRA_STAT_FIELDS.items():
        hit = next((c for c in candidates if c in SOURCE_FIELDS_SEEN), None)
        if hit:
            captured = sum(1 for m in payload["past_matches"]
                           for side in (m.get("actual") or {}).values()
                           for row in side.values()
                           if isinstance(row, dict) and our_key in row)
            print(f"    {our_key}: FOUND as {hit!r} — captured on {captured} player-series")
        else:
            print(f"    {our_key}: not offered under any of {candidates} — "
                  f"cannot be projected from this source")


if __name__ == "__main__":
    asyncio.run(main()) 
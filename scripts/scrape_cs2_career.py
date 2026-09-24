#!/usr/bin/env python3
"""
Full player match history for CS2, sourced directly from bo3.gg's raw
API — the CS2 equivalent of scrape_career.py for LoL. Built on a real,
confirmed breakthrough (not guessed): bo3.gg has TWO different numeric
IDs per player — steam_profile.id (Steam-account-specific, e.g. 1420 for
donk) and player.id (the true, stable bo3.gg identity, e.g. 31349 for
donk) — and only the LATTER works for match-history filtering. Every
REST-style endpoint (/players/{id}, /players/{id}/matches,
/players/{id}/transfers — the last one being cs2api's own README
example) 404's regardless of which ID is used; the real, working pattern
is filter[matches.player_ids][overlap]={player.id} on the SAME /matches
endpoint already used for team-scoped history, confirmed via live
testing to return a real, correctly-scoped 390 matches for donk (not the
74,271-match "silently unfiltered" failure mode a wrong parameter name
produced).

Design difference from LoL's career scraper: CS2 has no discrete season
structure (gol.gg's Spring/Summer splits don't have a CS2 equivalent —
it's a continuous tournament calendar), so this uses a TIME-based decay
(days since the match) rather than LoL's season-based decay. Worth
weighing against a real lesson from the LoL side: the properly-backtested
optimal season decay there turned out so aggressive it barely differed
from "just use the current period" — so this should be validated with
its own real backtest once data exists, not assumed to behave like a
deep, meaningful career signal without checking.

Usage:
    python scripts/scrape_cs2_career.py
Reads cs2_data.json (the existing pipeline's own output) to know which
player names to fetch history for -- same pattern as scrape_career.py
reading data.json for LoL.
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

BASE = "https://api.bo3.gg/api/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}
OUTPUT_PATH = "cs2_career_data.json"
# A run that comes back with less than this share of the game history
# already on record is treated as a source outage, not an update. Same
# value, and same reasoning, as scrape_career.MIN_RETAINED_FRACTION.
MIN_RETAINED_FRACTION = 0.5
# Wall-clock budget, deliberately under the workflow step's own
# timeout-minutes: 45. A timeout there SIGKILLs the process before
# write_output ever runs, so the run saves nothing -- including the
# player ids and games it spent forty-five minutes fetching. The next
# run then starts from the same cold cache and times out in the same
# place, forever. Stopping early and writing what we have makes the
# cache converge over a handful of runs instead of never.
#
# This is not hypothetical arithmetic: the roster is ~1,270 players and
# the cache currently covers ~260 of them, so the first run after this
# has roughly a thousand cold players at 40-60 requests each. Every run
# after that is the cheap incremental case the cache was built for.
TIME_BUDGET_SECONDS = int(os.environ.get("CS2_CAREER_TIME_BUDGET_SECONDS", 32 * 60))
_DEADLINE = {"at": None}
BUDGET_SKIPS = {"n": 0}


def start_budget(now=None):
    _DEADLINE["at"] = (time.monotonic() if now is None else now) + TIME_BUDGET_SECONDS


def budget_exhausted(now=None):
    """False when no budget was started, so direct callers and tests of
    process_one_player are never silently starved."""
    at = _DEADLINE["at"]
    return at is not None and (time.monotonic() if now is None else now) >= at
EXISTING_CS2_DATA_PATH = "cs2_data.json"
# This bounds TOTAL concurrent requests to bo3.gg directly (see
# _semaphore below), not just how many players are processed at once —
# the original version only limited player-level tasks while each
# player's own ~40-60 game-stat fetches ran sequentially with an extra
# sleep between each, which was the actual bottleneck, not player-level
# parallelism. 12 is a modest step up from the 6 proven safe elsewhere
# in this project, appropriate since it now bounds real concurrent
# connections directly rather than a looser, less effective limit.
REQUEST_CONCURRENCY = 12
# Wall clock cannot measure a change to a scraper: across three runs of
# identical code the sibling gol.gg step varied by 67%. Requests are what
# this code controls, so they are what it reports.
REQUEST_TOTAL = {"n": 0}
REQUEST_LOG_PATH = "request_counts.txt"  # read by the workflow's last step
MATCHES_PER_PLAYER = 20  # capped meaningfully below the main scraper's own reach -- each match is ~2-3 games, each needing its own players_stats fetch, so this is already 40-60 requests per player; the LoL half-life finding suggests old history contributes little anyway, so there's little value in going deeper at high request cost
DAY_HALF_LIFE = 180  # days -- MEASURED (scripts/sweep_cs2_day_half_life.py), but the honest headline is that this parameter barely matters. Across 3/7/14/.../365/36500-day candidates, MAE moved <0.3% for every stat, and the basin is flat from ~45 days out: kills best at 90 (+0.05% vs the old 60 guess), deaths at 180 (+0.19%), assists at 365 (+0.28%). 180 is at or near optimal for all three, so it's taken as a free marginal gain -- NOT as a finding. The real result is structural: MATCHES_PER_PLAYER caps history at 13-53 games (median 44), so over a window that short a 90-365 day half-life is nearly indistinguishable from a flat average -- note "no decay at all" (36500) scored only marginally worse than optimum everywhere. This decay parameter is largely REDUNDANT with the window cap. Contrast LoL's SEASON_HALF_LIFE, where sweeping genuinely changed the answer; do not assume an unmeasured constant matters just because a sibling one did.
#
# RE-MEASURED at double the depth, after the career scrape was fixed
# and coverage went 20% -> 98% (median 20 -> 40 games per player). Still
# +0.0% for all three stats against the shipped 180, best at 120/365/365
# and flat from 90 days out. The structural reason holds and is now
# confirmed rather than assumed: what a TIME decay can differentiate is
# the SPAN a player's history covers, and the median span is 92 days --
# half the half-life, so the oldest game in a typical record still
# carries 0.70 of the newest's weight. Doubling the game count did not
# lengthen the window, because MATCHES_PER_PLAYER caps it either way.

# Matches scrape_cs2.py's own ACCEPTED_TIERS/MIN_STARS exactly --
# duplicated here rather than imported, since this script is
# deliberately standalone (plain aiohttp, no cs2api/CS2() wrapper
# dependency the main scraper needs). A real, confirmed bug found via a
# live prediction breakdown: without this filter, career_games pulled in
# EVERY match a player has ever played (qualifiers, minor events,
# anything), a fundamentally different and lower-quality population than
# cur/pt_rate (which the main pipeline already scopes to only these
# criteria) -- meaning career_rate was measuring something structurally
# different from the rest of the model, not just a noisier version of
# the same signal. This was the real, dominant cause of a systematic
# under-prediction reported live in the app, not the earlier null-stat
# issue (real and worth fixing, but comparatively minor next to this).
ACCEPTED_TIERS = {"s", "a"}
MIN_STARS = 3

_semaphore = None  # created inside build_career_data(), once the event loop is running
_debug_shape_printed = False  # ensures the tier/star fallback debug dump below fires once total, not once per player


async def bo3_get(session, path, params=None, retries=3):
    async with _semaphore:
        # The budget is enforced HERE, holding the semaphore, because
        # this is the only place time is actually spent.
        #
        # It was checked once at the top of process_one_player, which
        # does not bound anything: asyncio.gather turns all 1,274
        # players into tasks and the event loop runs every one of their
        # synchronous prologues in its first pass, so they all read the
        # deadline before a second has elapsed and then queue on this
        # semaphore for as long as it takes. A live run walked straight
        # past a 32-minute budget on its way to the step's 45-minute
        # timeout, which kills the process before anything is written --
        # the exact outcome the budget exists to prevent.
        #
        # Returning None is a shape every caller already handles: an
        # unresolved id hands back what was on record, no matches keeps
        # the cached games, and a missing game stat skips that game.
        if budget_exhausted():
            BUDGET_SKIPS["n"] += 1
            return None
        url = f"{BASE}{path}"
        for attempt in range(retries):
            try:
                REQUEST_TOTAL["n"] += 1
                async with session.get(url, headers=HEADERS, params=params or {},
                                        timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    if resp.status == 404:
                        return None
                    print(f"  ! {url} -> HTTP {resp.status}", file=sys.stderr)
            except Exception as e:
                print(f"  ! {url} attempt {attempt + 1}/{retries} failed: {e}", file=sys.stderr)
            await asyncio.sleep(1 + attempt)
        return None


def load_tracked_player_names():
    """Reads scrape_cs2.py's own output to get the set of player names
    already tracked -- same pattern as scrape_career.py reading data.json
    for LoL."""
    p = Path(EXISTING_CS2_DATA_PATH)
    if not p.exists():
        print(f"! {EXISTING_CS2_DATA_PATH} not found — run scrape_cs2.py first.", file=sys.stderr)
        return set()
    with open(p) as f:
        data = json.load(f)
    names = set()
    for region in data.get("regions", {}).values():
        for team in region.get("teams", {}).values():
            for player in team.get("players", []):
                if player.get("name"):
                    names.add(player["name"])
    return names


async def resolve_player_id(session, name):
    """/filters/players?search_text=X -- confirmed working live, returns
    the TRUE player.id (distinct from steam_profile.id, which does NOT
    work for match-history filtering).

    filter[players.discipline_id][eq]=1 is passed server-side but a real
    run showed it's NOT actually being honored -- confirmed by "donk"
    resolving to id=77770 ("donk-mlbb", a Mobile Legends player who
    happens to share the nickname) instead of the real CS2 donk
    (id=31349, independently confirmed earlier via direct reconnaissance
    and matching cs2api's own README example). Roughly 15/189 players in
    that run got a wrong-discipline ID this way -- short, common
    nicknames are exactly the ones likely to collide with bo3.gg's other
    tracked games. Filtering discipline_id CLIENT-SIDE now, not trusting
    the server parameter, since this project has hit this same
    "server-side filter silently not applied" failure mode with a few
    different parameters already this session."""
    data = await bo3_get(session, "/filters/players", params={
        "page[offset]": "0", "page[limit]": "10",  # widened from 5 -- with client-side filtering now doing the real work, a few extra candidates improves the odds the right one is actually in the page
        "filter[players.discipline_id][eq]": "1", "search_text": name,
    })
    if not data:
        return None
    results = data.get("results", data) if isinstance(data, dict) else data
    if not results:
        return None
    cs2_only = [p for p in results if p.get("discipline_id") == 1]
    if not cs2_only:
        return None  # every candidate was some other game -- genuinely no CS2 player found, not a "pick the best guess" situation
    exact = next((p for p in cs2_only if p.get("nickname", "").lower() == name.lower()), None)
    return (exact or cs2_only[0]).get("id")


async def fetch_player_matches(session, player_id, limit=MATCHES_PER_PLAYER):
    """filter[matches.player_ids][overlap]={player_id} -- the real,
    confirmed-working pattern found via live testing. Returns match
    objects already including a games list via with=games, so no
    separate per-match fetch is needed to know each match's game IDs.

    Fetches a LARGER candidate pool (3x the target) than actually
    needed, then filters to only tier/star-notable matches (same
    criteria as scrape_cs2.py's is_notable_match) before capping at
    `limit` -- without this, career_games included every match a player
    has ever played regardless of competitive level, a real, confirmed
    cause of systematic under-prediction (see the module-level
    ACCEPTED_TIERS/MIN_STARS comment for the full story). Fetching extra
    upfront compensates for matches the filter will discard, so a player
    doesn't end up with an artificially small sample just because some
    of their most recent games happened to be lower-tier events."""
    fetch_limit = limit * 3
    data = await bo3_get(session, "/matches", params={
        "scope": "widget-map-pool", "page[offset]": "0", "page[limit]": str(fetch_limit),
        "sort": "-start_date", "filter[matches.status][in]": "finished",
        "filter[matches.player_ids][overlap]": str(player_id),
        "filter[matches.discipline_id][eq]": "1", "with": "teams,games",
    })
    if not data:
        return []
    results = data.get("results", data) if isinstance(data, dict) else data
    notable = [
        m for m in results
        if (m.get("tier") or "").lower() in ACCEPTED_TIERS or (m.get("stars") or 0) >= MIN_STARS
    ]
    # Emergency safeguard after a real, confirmed regression: the filter
    # above returned ZERO matches for every single player in a live run,
    # including unquestionably top-tier active pros (ZywOo, m0NESY,
    # Ax1Le, donk) -- meaning tier/stars almost certainly aren't present
    # on this endpoint's response the same way they are on the main
    # pipeline's global-feed matches (that assumption was never actually
    # verified before shipping). Falling back to the unfiltered set
    # rather than leaving the scraper producing zero usable data, and
    # printing the raw shape of one real match once so the actual field
    # names can be confirmed instead of guessed at again.
    if results and not notable:
        global _debug_shape_printed
        if not _debug_shape_printed:
            print(f"  [debug] tier/star filter matched 0/{len(results)} matches — falling back to "
                  f"unfiltered. Raw shape of first match:\n{json.dumps(results[0], indent=2, default=str)[:1500]}",
                  file=sys.stderr)
            _debug_shape_printed = True
        notable = results
    return notable[:limit]


async def fetch_game_stats_for_player(session, game_id, player_id):
    """Reuses the same players_stats endpoint the main cs2 pipeline
    already depends on -- filters down to just this player's own row."""
    stats = await bo3_get(session, f"/games/{game_id}/players_stats")
    if not stats:
        return None
    for row in stats:
        steam_profile = row.get("steam_profile") or {}
        nested_player = steam_profile.get("player") or {}
        if nested_player.get("id") == player_id:
            # A real, confirmed bug: an earlier version used `or 0` here,
            # which coalesces BOTH "key missing" AND "key explicitly
            # null" into the same 0 -- silently treating "bo3.gg hasn't
            # finished processing this map's stats yet" (confirmed
            # earlier this project: some finished-status matches still
            # have null state/rounds_count/scores) the SAME as "this
            # player genuinely went 0 kills this map". Once career
            # weight hit ~1.0 for CS2 kills, a handful of these fake
            # zeros mixed into a player's MOST RECENT games (which carry
            # the heaviest weight under the 60-day decay) was enough to
            # drag the whole weighted average down -- a real, user-
            # reported, systematic under-prediction across every match
            # shown in a live screenshot. Now explicitly distinguishes
            # "null" (exclude this game entirely, not fully processed)
            # from "present and genuinely 0" (real, legitimate data,
            # keep it).
            k, d, a = row.get("kills"), row.get("death"), row.get("assists")
            if k is None or d is None or a is None:
                return None
            return {"k": k, "d": d, "a": a}
    return None


def decayed_baseline(games, half_life_days=DAY_HALF_LIFE, now=None):
    """Time-based exponential decay -- a game N days old gets weight
    0.5^(N/half_life_days). Mirrors LoL's decayed_career_baseline() in
    spirit (weighted blend toward recent activity) but by elapsed time
    instead of season boundaries, since CS2 has no season structure to
    decay across."""
    if not games:
        return None
    now = now or datetime.now(timezone.utc)
    total_weight = 0.0
    weighted = {"k": 0.0, "d": 0.0, "a": 0.0}
    for g in games:
        days_ago = max(0, (now - g["date"]).days)
        weight = 0.5 ** (days_ago / half_life_days)
        total_weight += weight
        for key in ("k", "d", "a"):
            weighted[key] += g[key] * weight
    if total_weight == 0:
        return None
    return {"g": len(games), **{key: weighted[key] / total_weight for key in ("k", "d", "a")}}


def load_previous_output():
    """Last run's cs2_career_data.json, or {} if there isn't one.

    This scraper had no cache at all. Every run re-resolved all ~263
    player ids and re-fetched every one of their last 20 matches
    game-by-game -- 40-60 requests per player, 10,000+ per run, for
    per-game box scores that cannot change once played. It was the single
    largest step in the whole pipeline at over six minutes.
    """
    try:
        with open(OUTPUT_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def parse_stored_date(value):
    """A stored game's date, back as a timezone-aware datetime.

    The cache writes dates with .isoformat() and so reads them back as
    STRINGS, while a freshly fetched game carries a real datetime. The
    two are concatenated into one list, and everything downstream then
    saw a mix: decayed_baseline does `now - g["date"]` and the
    serializer calls g["date"].isoformat(), and each raises on whichever
    kind it did not get.

    It raised inside asyncio.gather, so a SINGLE cached game killed the
    whole run -- which is why the career file sat at 263 records from the
    moment the cache landed, and why the step finished in two seconds
    while reporting 1,274 players to process.

    None for anything unparseable, so the game is re-fetched rather than
    carried forward as a date nothing can do arithmetic on.
    """
    if isinstance(value, datetime):
        when = value
    elif isinstance(value, str):
        try:
            when = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    # Naive dates cannot be subtracted from an aware `now`, which is the
    # same class of crash one layer down.
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def cached_games_by_id(previous_record):
    """{game_id: stored game} for games already on record.

    Keyed on bo3.gg's own game id. Records written before ids were stored
    simply do not match, so the first run after this re-fetches as before
    and every run after is cheap -- the same migration the LoL match
    cache makes, for the same reason: a weaker key risks attaching one
    game's box score to another, which is silent and wrong.

    Dates come back as datetimes, so a cached game and a fetched one are
    the same shape. This is the boundary the cache crosses, so it is the
    one place that conversion belongs.
    """
    out = {}
    for game in (previous_record or {}).get("games") or []:
        game_id = game.get("game_id")
        if game_id is None:
            continue
        when = parse_stored_date(game.get("date"))
        if when is None:
            continue  # unreadable date: re-fetch rather than carry it
        out[game_id] = {**game, "date": when}
    return out

async def process_one_player(session, name, progress, total, previous=None):
    prev = (previous or {}).get(name) or {}
    if budget_exhausted():
        # Every player is scheduled up front and gated by the shared
        # semaphore, so the ones that have not started yet simply hand
        # back what was already on record and cost nothing. Their turn
        # comes next run, with everything fetched so far already cached.
        progress["done"] += 1
        progress["skipped"] = progress.get("skipped", 0) + 1
        return name, (prev or None)
    # A player's bo3.gg id does not change, and it is already stored. The
    # search request that finds it was being made for every player on
    # every run to rediscover a number we had written down.
    player_id = prev.get("player_id") or await resolve_player_id(session, name)
    if not player_id:
        progress["done"] += 1
        print(f"  ! {name!r}: no CS2 player found at all (name may not exist on bo3.gg, or every "
              f"candidate was some other game)", file=sys.stderr)
        return name, None

    matches = await fetch_player_matches(session, player_id)
    if not matches:
        progress["done"] += 1
        # Keep what is already on record. "0 matches" for a player who
        # had forty last run is bo3.gg being unreachable, not a career
        # that vanished, and the empty record this used to return was
        # written straight over the real one -- a single outage would
        # have emptied every player's history while the file stayed the
        # right shape and the step still exited 0.
        if cached_games_by_id(prev):
            print(f"  ! {name!r} (id={player_id}): 0 matches returned — keeping the "
                  f"{len(prev.get('games') or [])} game(s) already on record", file=sys.stderr)
            return name, prev
        print(f"  ! {name!r} (id={player_id}): resolved to a real player, but 0 matches found — "
              f"worth a manual check if this recurs for a player who should have real history",
              file=sys.stderr)
        return name, {"player_id": player_id, "games_fetched": 0, "games": []}

    game_refs = [
        (game.get("id"), game.get("begin_at"))
        for match in matches
        for game in match.get("games", [])
        if game.get("id") and game.get("begin_at")
    ]

    # Only the games not already on record. MATCHES_PER_PLAYER is a
    # rolling window of the most recent 20 matches, so between two runs
    # twelve hours apart the overlap is nearly total -- typically a
    # handful of new games against forty-odd already known.
    cached = cached_games_by_id(prev)
    missing = [(gid, begin) for gid, begin in game_refs if gid not in cached]

    # The ones that are new, fired concurrently and bounded by the shared
    # global semaphore in bo3_get.
    results = await asyncio.gather(*[
        fetch_game_stats_for_player(session, game_id, player_id) for game_id, _ in missing
    ])

    games = []
    fetched = dict(zip([gid for gid, _ in missing], results))
    for game_id, begin_at in game_refs:
        if game_id in cached:
            games.append(cached[game_id])
            continue
        stats = fetched.get(game_id)
        if not stats:
            continue
        try:
            date = datetime.fromisoformat(begin_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        games.append({**stats, "game_id": game_id, "date": date})

    # Store the RAW per-game history (date + k/d/a), not just a single
    # pre-decayed number — a real, confirmed leakage issue found this
    # static-snapshot approach meant "career" for a historical backtest
    # prediction could include games that hadn't happened yet as of that
    # prediction's own date, since it was always "most recent N as of
    # scrape time" with no cutoff awareness at all. Consumers (both the
    # backtest and the live app) now compute the decayed baseline
    # point-in-time from this raw list, the same standard cur/hist/
    # opponent already hold themselves to. `career` here is kept ONLY as
    # a human-readable preview of the full-history baseline, not
    # something any prediction code should actually read from directly.
    games_serializable = [{**g, "date": g["date"].isoformat()} for g in games]
    baseline = decayed_baseline(games)
    progress["done"] += 1
    if progress["done"] % 10 == 0 or progress["done"] == total:
        print(f"  ...{progress['done']}/{total} players processed")
    return name, {
        "player_id": player_id, "games_fetched": len(games),
        "career_preview_only_do_not_use_for_predictions": baseline,
        "games": games_serializable,
    }


async def build_career_data():
    global _semaphore
    _semaphore = asyncio.Semaphore(REQUEST_CONCURRENCY)

    tracked_names = load_tracked_player_names()
    if not tracked_names:
        return {}
    print(f"Loaded {len(tracked_names)} tracked CS2 player names from {EXISTING_CS2_DATA_PATH}")

    output = {}
    progress = {"done": 0}

    # All players scheduled at once too — real concurrency is controlled
    # entirely by the shared semaphore inside bo3_get, not by an
    # additional player-level gate, so work from many players' game
    # fetches can interleave and keep the request pool continuously
    # full instead of processing player-by-player in sequential batches.
    previous = load_previous_output()
    start_budget()
    known_ids = sum(1 for r in previous.values() if (r or {}).get("player_id"))
    known_games = sum(len(cached_games_by_id(r)) for r in previous.values())
    print(f"Processing {len(tracked_names)} players (up to {REQUEST_CONCURRENCY} concurrent requests total)...")
    print(f"  cache: {known_ids} player ids and {known_games} games already on record")
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*[
            process_one_player(session, name, progress, len(tracked_names), previous)
            for name in tracked_names
        ])
    if progress.get("skipped") or BUDGET_SKIPS["n"]:
        print(f"  ! time budget ({TIME_BUDGET_SECONDS}s) reached — "
              f"{progress.get('skipped', 0)} player(s) not started and "
              f"{BUDGET_SKIPS['n']} request(s) declined. What was fetched is kept "
              f"and the rest resumes from this run's cache next time.",
              file=sys.stderr)
    for name, record in results:
        if record:
            output[name] = record
    return output


def main():
    data = asyncio.run(build_career_data())
    line = f"cs2 career scrape: {REQUEST_TOTAL['n']} requests made this run"
    print(f"\n  {line}")
    _write_run_summary(f"- bo3.gg {line}")
    write_output(data)

def _write_run_summary(line):
    """Put the number where it can actually be read.

    Fourth attempt, and the failures are worth recording because each one
    looked right:

      stdout mid-job -- unreachable, the log API returns a job's TAIL and
        the tail is always the git push.
      GITHUB_STEP_SUMMARY alone -- shows in the UI, but nothing fetches it.
      catting GITHUB_STEP_SUMMARY from a later step -- every step gets its
        OWN summary file, so the later step read an empty one and printed
        a header with nothing under it.

    So: a plain file in the workspace that every scraper appends to and
    the job's last step cats. The summary write stays as well, since it
    is genuinely nicer to read in the UI.
    """
    for path in (os.environ.get("GITHUB_STEP_SUMMARY"), REQUEST_LOG_PATH):
        if not path:
            continue
        try:
            with open(path, "a") as f:
                f.write(line + "\n")
        except OSError:
            pass  # never fail a scrape over a progress note


def write_output(data):
    """Write the career file, but never destroy a good one.

    This lived inside _write_run_summary by mis-indentation and referred
    to `data`, which is main()'s local -- so every run raised NameError
    here. It raised AFTER open(..., "w") had already truncated the file,
    which is the part that mattered: the scrape could not save its work
    and quietly emptied what was there. cs2_career_data.json sat at 263
    records while the roster grew past 1400, so two thirds of that game
    was projected without a tier carrying a weight of 1.0.

    What that cost, measured properly once coverage reached 98%: turning
    the tier off across the same rows is +0.5% on kills, +2.4% on deaths
    and +2.1% on assists, and on the rows that specifically lacked it,
    +1.2% / +2.3% / +1.7%.

    NOT the 12.7% / 18.1% / 11.4% first reported here. That compared
    players who HAD career records against players who did not, which is
    mostly a statement about which players bo3.gg had resolved. Scoring
    both groups with the tier switched off for both leaves 6.9% / 10.2%
    / 7.3% of the gap still standing -- the established players were
    simply more predictable to begin with. Real, and a quarter the size.

    Refusing to write a collapsed result is the belt to that braces, and
    it is measured in PLAYERS WITH GAMES rather than players: this
    scraper keeps a cache of player ids, so a bo3.gg outage still
    produces a full-length dict of id-only records. The right-shaped
    empty file is the failure mode that actually reaches the model.
    Threshold matches scrape_career.MIN_RETAINED_FRACTION; the career
    tier degrades gracefully when stale and not at all gracefully when
    blank, so one run behind always beats one run empty.
    """
    if not data:
        print(f"! resolved no players — leaving {OUTPUT_PATH} untouched rather "
              f"than replacing good career data with an empty file", file=sys.stderr)
        return False

    with_games = sum(1 for v in data.values() if (v or {}).get("games"))
    had_games = sum(1 for v in load_previous_output().values() if (v or {}).get("games"))
    if had_games and with_games < had_games * MIN_RETAINED_FRACTION:
        print(f"! REFUSING TO WRITE {OUTPUT_PATH}: this run resolved game history for "
              f"{with_games} player(s) but the existing file holds {had_games}. That is a "
              f"collapse, not an update — almost always bo3.gg being unreachable rather "
              f"than careers genuinely disappearing. Keeping the existing file; the career "
              f"tier will be one run stale, which the model already handles.", file=sys.stderr)
        return False
    # Written to a temporary file and moved into place, so an exception
    # part-way through cannot leave a half-written or empty file where a
    # complete one used to be.
    tmp = OUTPUT_PATH + ".tmp"
    with open(tmp, "w") as f:
        # Minified: machine-generated, never read by hand, and indent=2
        # was about two thirds of the bytes.
        json.dump(data, f, separators=(",", ":"), default=str)
    os.replace(tmp, OUTPUT_PATH)
    print(f"\nWrote {OUTPUT_PATH}: {len(data)} players resolved, "
          f"{with_games} with usable raw game history")
    return True


if __name__ == "__main__":
    main()
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
import sys
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
MATCHES_PER_PLAYER = 20  # capped meaningfully below the main scraper's own reach -- each match is ~2-3 games, each needing its own players_stats fetch, so this is already 40-60 requests per player; the LoL half-life finding suggests old history contributes little anyway, so there's little value in going deeper at high request cost
DAY_HALF_LIFE = 60  # days -- a real starting point, NOT yet backtested; see module docstring's note on the LoL finding this needs its own validation, same as SEASON_HALF_LIFE did

_semaphore = None  # created inside build_career_data(), once the event loop is running


async def bo3_get(session, path, params=None, retries=3):
    async with _semaphore:
        url = f"{BASE}{path}"
        for attempt in range(retries):
            try:
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
    separate per-match fetch is needed to know each match's game IDs."""
    data = await bo3_get(session, "/matches", params={
        "scope": "widget-map-pool", "page[offset]": "0", "page[limit]": str(limit),
        "sort": "-start_date", "filter[matches.status][in]": "finished",
        "filter[matches.player_ids][overlap]": str(player_id),
        "filter[matches.discipline_id][eq]": "1", "with": "teams,games",
    })
    if not data:
        return []
    return data.get("results", data) if isinstance(data, dict) else data


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
            # `or 0`, not just a .get() default -- a real crash showed
            # some rows have kills/death/assists explicitly present but
            # set to null (not simply absent), which .get(key, 0) does
            # NOT catch (its default only applies when the key itself is
            # missing). Likely the same category of "not fully processed
            # yet" issue found earlier in this project for other
            # matches. `or 0` safely coalesces both missing-key and
            # explicit-null cases without affecting a genuine 0 value.
            k = row.get("kills") or 0
            d = row.get("death") or 0
            a = row.get("assists") or 0
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


async def process_one_player(session, name, progress, total):
    player_id = await resolve_player_id(session, name)
    if not player_id:
        progress["done"] += 1
        print(f"  ! {name!r}: no CS2 player found at all (name may not exist on bo3.gg, or every "
              f"candidate was some other game)", file=sys.stderr)
        return name, None

    matches = await fetch_player_matches(session, player_id)
    if not matches:
        progress["done"] += 1
        print(f"  ! {name!r} (id={player_id}): resolved to a real player, but 0 matches found — "
              f"worth a manual check if this recurs for a player who should have real history",
              file=sys.stderr)
        return name, {"player_id": player_id, "games_fetched": 0, "career": None}

    game_refs = [
        (game.get("id"), game.get("begin_at"))
        for match in matches
        for game in match.get("games", [])
        if game.get("id") and game.get("begin_at")
    ]

    # All of this player's game-stat fetches fired concurrently, bounded
    # by the shared global semaphore in bo3_get — not sequential with an
    # extra sleep between each, which was the real bottleneck before.
    results = await asyncio.gather(*[
        fetch_game_stats_for_player(session, game_id, player_id) for game_id, _ in game_refs
    ])

    games = []
    for (game_id, begin_at), stats in zip(game_refs, results):
        if not stats:
            continue
        try:
            date = datetime.fromisoformat(begin_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        games.append({**stats, "date": date})

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
    print(f"Processing {len(tracked_names)} players (up to {REQUEST_CONCURRENCY} concurrent requests total)...")
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*[
            process_one_player(session, name, progress, len(tracked_names))
            for name in tracked_names
        ])
    for name, record in results:
        if record:
            output[name] = record
    return output


def main():
    data = asyncio.run(build_career_data())
    with open(OUTPUT_PATH, "w") as f:
        json.dump(data, f, indent=2, default=str)
    resolved = sum(1 for v in data.values() if v.get("games"))
    print(f"\nWrote {OUTPUT_PATH}: {len(data)} players resolved, {resolved} with usable raw game history")


if __name__ == "__main__":
    main()
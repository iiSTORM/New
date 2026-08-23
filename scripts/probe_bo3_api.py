#!/usr/bin/env python3
"""
Tests whether api.bo3.gg's matches endpoint supports richer relation
includes than what cs2api's get_match_details() wrapper requests. The
confirmed working call uses:
    GET /api/v1/matches/{slug}?with=games,streams,teams,tournament_deep,stage,ai_predictions
...which is a JSON:API-style "include related resources" pattern (further
confirmed by the pagination shape we saw earlier: links.self/next/last).
If that's right, the SAME endpoint may support a richer relation name for
per-game player stats (e.g. "games.players", "games.scoreboard") that
cs2api's wrapper just never requests — worth testing directly with plain
HTTP rather than through the wrapper, which hardcodes the "with" value.

This makes raw requests, not through cs2api, so we have full control over
the query string. Run in the Codespace, redirecting to a file:
    python scripts/probe_bo3_api.py > cs2_probe_output.txt
"""
import asyncio
import json

import aiohttp

BASE = "https://api.bo3.gg/api/v1"
KNOWN_MATCH_SLUG = "legacy-br-vs-spirit-22-08-2026"
KNOWN_GAME_ID = 180726  # de_ancient map from that match, confirmed real

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# Plausible relation names for per-game player stats, tried against the
# matches endpoint's "with" parameter.
CANDIDATE_WITH_VALUES = [
    "games.players",
    "games.player_stats",
    "games.scoreboard",
    "games.teams.players",
    "games.match_players",
    "games.players.stats",
    "player_stats",
    "games,players",
]

# Plausible standalone endpoints for a single game's detail/scoreboard.
CANDIDATE_GAME_ENDPOINTS = [
    f"/games/{KNOWN_GAME_ID}",
    f"/games/{KNOWN_GAME_ID}?with=players",
    f"/games/{KNOWN_GAME_ID}?with=player_stats",
    f"/games/{KNOWN_GAME_ID}?with=scoreboard",
    f"/games/{KNOWN_GAME_ID}/players",
    f"/games/{KNOWN_GAME_ID}/scoreboard",
    f"/games/{KNOWN_GAME_ID}/stats",
    f"/matches/games/{KNOWN_GAME_ID}",
]


async def try_get(session, url, label):
    print("=" * 60)
    print(label)
    print(f"  URL: {url}")
    print("=" * 60)
    try:
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            print(f"  status: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                text = json.dumps(data, indent=2, default=str)
                print(text[:3000])
                if len(text) > 3000:
                    print(f"...[truncated, {len(text)} total chars]")
                return data
            else:
                body = await resp.text()
                print(f"  body: {body[:300]}")
    except Exception as e:
        print(f"  ! request failed: {type(e).__name__}: {e}")
    print()
    return None


async def main():
    async with aiohttp.ClientSession() as session:
        print("### Testing expanded 'with' values on the matches endpoint ###\n")
        for with_value in CANDIDATE_WITH_VALUES:
            url = f"{BASE}/matches/{KNOWN_MATCH_SLUG}?with={with_value}"
            data = await try_get(session, url, f"with={with_value}")
            # If this succeeded, check whether the games array now has
            # per-player data that wasn't there before (kills/deaths/etc).
            if data and isinstance(data, dict):
                games = data.get("games")
                if isinstance(games, list) and games:
                    keys = sorted(games[0].keys()) if isinstance(games[0], dict) else []
                    interesting = [k for k in keys if any(
                        term in k.lower() for term in ("player", "kill", "death", "assist", "score_data", "roster")
                    )]
                    if interesting:
                        print(f"  >>> PROMISING: games[0] has new keys: {interesting}\n")
            await asyncio.sleep(0.5)

        print("\n### Testing standalone game-detail endpoints ###\n")
        for path in CANDIDATE_GAME_ENDPOINTS:
            url = f"{BASE}{path}"
            await try_get(session, url, f"GET {path}")
            await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(main())

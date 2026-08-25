#!/usr/bin/env python3
"""
Uses a real, currently-live match (confirmed by the user to show per-map
player stats on bo3.gg's actual website) to try endpoint patterns not yet
tested. /games/{id}/steam_profiles turned out to be a real, working
sub-resource PATH (not a "with=" query parameter) — this tests whether a
sibling path exists for the stats themselves, following the same
convention, plus a few other candidates.

Run in the Codespace, redirecting to a file:
    python scripts/probe_bo3_fresh_match.py > cs2_probe_output_5.txt
"""
import asyncio
import json

import aiohttp

BASE = "https://api.bo3.gg/api/v1"
MATCH_SLUG = "ex-ruby-vs-black-phoenix-25-08-2026"
TARGET_MAP = "dust2"  # de_dust2, per bo3.gg's URL convention seen before

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


async def try_get(session, url, label, print_full=False):
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
                print(text if print_full else text[:1500])
                if not print_full and len(text) > 1500:
                    print(f"...[truncated, {len(text)} total chars]")
                print()
                return data
            else:
                print(f"  body: {(await resp.text())[:300]}\n")
    except Exception as e:
        print(f"  ! request failed: {type(e).__name__}: {e}\n")
    return None


async def main():
    async with aiohttp.ClientSession() as session:
        match = await try_get(session, f"{BASE}/matches/{MATCH_SLUG}", "GET /matches/{slug} — resolve real match")
        if not match:
            print("! Could not resolve match slug — stopping")
            return

        games = match.get("games", [])
        print(f"Found {len(games)} games in this match: {[(g.get('id'), g.get('map_name')) for g in games]}\n")

        target_game = None
        for g in games:
            if TARGET_MAP in (g.get("map_name") or "").lower():
                target_game = g
                break
        if not target_game and games:
            target_game = games[0]
            print(f"! No exact map_name match for {TARGET_MAP!r} — using first game as fallback\n")

        if not target_game:
            print("! No games found in this match at all — stopping")
            return

        game_id = target_game["id"]
        print(f"Using game_id={game_id} (map: {target_game.get('map_name')})\n")

        # New candidate sub-resource PATHS (not "with=" params), following
        # the confirmed convention from /games/{id}/steam_profiles.
        candidates = [
            f"/games/{game_id}/game_round_players",
            f"/games/{game_id}/player_stats",
            f"/games/{game_id}/players_stats",
            f"/games/{game_id}/scoreboard_players",
            f"/games/{game_id}/game_scoreboard",
            f"/games/{game_id}/round_players",
            f"/games/{game_id}/steam_profiles/stats",
            f"/games/{game_id}/steam_profile_stats",
            f"/games/{game_id}/game_steam_profiles",
        ]
        for path in candidates:
            await try_get(session, f"{BASE}{path}", f"GET {path}")
            await asyncio.sleep(0.4)

        # Full, uncut re-fetch of the base game endpoint, specifically to
        # scan every key name one more time now that we have a genuinely
        # fresh, currently-live match rather than an older one.
        full = await try_get(session, f"{BASE}/games/{game_id}", f"GET /games/{game_id} — full, uncut", print_full=True)
        if full:
            print("\nTop-level keys:", sorted(full.keys()))


if __name__ == "__main__":
    asyncio.run(main())

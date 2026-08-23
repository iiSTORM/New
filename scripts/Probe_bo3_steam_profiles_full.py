#!/usr/bin/env python3
"""
Follow-up to probe_bo3_player_rounds.py. GET /games/{id}/steam_profiles is
confirmed real and returns per-map ROSTER data (real steam_id_64/nickname/
player_id, verified against known real players). But the previous probe
only showed keyword-matched fields, capped at the first 2 of ~10 profiles
— if kills/deaths/assists exist under a name that wasn't in the keyword
list (e.g. "frags", "elims", "kda"), it would have been silently missed.

This prints the FULL, uncut response for ALL profiles, plus every key
name that appears anywhere in the "player" sub-object (43 keys we haven't
actually seen the names of yet), and separately tries a "with" parameter
on this specific endpoint in case per-game performance is a further
relation on the steam_profile entity.

Run in the Codespace, redirecting to a file:
    python scripts/probe_bo3_steam_profiles_full.py > cs2_probe_output_4.txt
"""
import asyncio
import json

import aiohttp

BASE = "https://api.bo3.gg/api/v1"
KNOWN_GAME_ID = 180726

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


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
        data = await try_get(session, f"{BASE}/games/{KNOWN_GAME_ID}/steam_profiles",
                              "GET /games/{id}/steam_profiles — baseline, full uncut")
        if data and isinstance(data, list):
            print(f"  {len(data)} profiles returned total\n")
            print("  Top-level keys on profile[0]:")
            print(f"    {sorted(data[0].keys())}\n")
            print("  ALL keys inside profile[0]['player'] (the 43-key nested object):")
            if "player" in data[0] and isinstance(data[0]["player"], dict):
                print(f"    {sorted(data[0]['player'].keys())}\n")
            print("  Full profile[0], completely uncut:")
            print(json.dumps(data[0], indent=2, default=str))
            print()
            print("  Full profile[1], completely uncut (for comparison):")
            print(json.dumps(data[1], indent=2, default=str))
            print()

        # Try expanding this specific endpoint with a "with" param, in case
        # per-game performance is a further relation on steam_profile.
        for with_value in ["game_round_players", "stats", "game_stats", "round_stats", "performance"]:
            url = f"{BASE}/games/{KNOWN_GAME_ID}/steam_profiles?with={with_value}"
            result = await try_get(session, url, f"GET /games/{{id}}/steam_profiles?with={with_value}")
            if result and isinstance(result, list) and result:
                keys_now = sorted(result[0].keys())
                print(f"  keys with this 'with' value: {keys_now}\n")
            await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
Retries the REST-style player endpoints that 404'd — but using the
CORRECT id this time. The first probe tested steam_profile.id (1420),
which is Steam-account-specific; buried in that same response was
steam_profile.player.id (31349) — the true, stable bo3.gg-internal
player identity, independently confirmed via /filters/players resolving
"donk" directly to id=31349, which also happens to be the exact ID
cs2api's own README uses as its get_player_transfers() example. Worth
one more targeted round with the right ID before concluding REST-style
player endpoints don't exist.

Run in the Codespace:
    python scripts/probe_bo3_player_history_v2.py > player_history_probe_v2.txt
"""
import asyncio
import json

import aiohttp

BASE = "https://api.bo3.gg/api/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

PLAYER_ID = 31349  # donk's real player.id, confirmed via /filters/players and cs2api's own README example


async def bo3_get(session, path, params=None, label=None):
    url = f"{BASE}{path}"
    print("=" * 60)
    print(label or url)
    print(f"  URL: {url}  params={params}")
    print("=" * 60)
    try:
        async with session.get(url, headers=HEADERS, params=params or {},
                                timeout=aiohttp.ClientTimeout(total=15)) as resp:
            print(f"  status: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                text = json.dumps(data, indent=2, default=str)
                print(text[:2500])
                if len(text) > 2500:
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
        await bo3_get(session, f"/players/{PLAYER_ID}",
                       label=f"GET /players/{PLAYER_ID} — bare, full shape")

        await bo3_get(session, f"/players/{PLAYER_ID}/matches",
                       label=f"GET /players/{PLAYER_ID}/matches")

        await bo3_get(session, f"/players/{PLAYER_ID}/games",
                       label=f"GET /players/{PLAYER_ID}/games")

        await bo3_get(session, f"/players/{PLAYER_ID}/transfers",
                       label=f"GET /players/{PLAYER_ID}/transfers (cs2api's own confirmed-working example)")

        await bo3_get(session, f"/players/{PLAYER_ID}/stats",
                       label=f"GET /players/{PLAYER_ID}/stats")

        # Also retry the two filter conventions, but with the correct ID
        await bo3_get(session, "/matches", params={
            "scope": "widget-map-pool", "page[offset]": "0", "page[limit]": "5",
            "sort": "-start_date", "filter[matches.status][in]": "finished",
            "filter[matches.player_ids][overlap]": str(PLAYER_ID),
            "filter[matches.discipline_id][eq]": "1", "with": "teams,games",
        }, label=f"filter[matches.player_ids][overlap]={PLAYER_ID} (correct ID this time)")


if __name__ == "__main__":
    asyncio.run(main())
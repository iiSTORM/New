#!/usr/bin/env python3
"""
Tests whether tier/star competitive-level data becomes available on
player-scoped /matches results with a different `with=` expansion
parameter than the currently-used "teams,games" -- a real run confirmed
tier/stars are simply absent from that response shape. Tries a few
reasonable candidates cheaply (one player, a few requests) before
attempting another full implementation.

Run in the Codespace:
    python scripts/probe_cs2_match_tier_field.py > tier_probe.txt
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
PLAYER_ID = 18452  # ZywOo -- confirmed real, definitely has top-tier matches to find


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
                results = data.get("results", data) if isinstance(data, dict) else data
                if results:
                    print(f"  count: {len(results)}, first result keys: {sorted(results[0].keys())}")
                    text = json.dumps(results[0], indent=2, default=str)
                    print(text[:1200])
                else:
                    print("  no results")
                print()
                return data
            else:
                print(f"  body: {(await resp.text())[:300]}\n")
    except Exception as e:
        print(f"  ! request failed: {type(e).__name__}: {e}\n")
    return None


async def main():
    async with aiohttp.ClientSession() as session:
        for with_value in ["teams,games,tournament", "tournament", "teams,games,league", "teams,games,tier"]:
            await bo3_get(session, "/matches", params={
                "scope": "widget-map-pool", "page[offset]": "0", "page[limit]": "1",
                "sort": "-start_date", "filter[matches.status][in]": "finished",
                "filter[matches.player_ids][overlap]": str(PLAYER_ID),
                "filter[matches.discipline_id][eq]": "1", "with": with_value,
            }, label=f"with={with_value}")


if __name__ == "__main__":
    asyncio.run(main())
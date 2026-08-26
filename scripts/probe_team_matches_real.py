#!/usr/bin/env python3
"""
Validates the real endpoint+params combination found in cs2api's own
get_team_matches() source -- confirmed broken only because of a
self._make_request vs self._api._make_request typo, not because the
underlying request is wrong. This calls the exact same endpoint directly
via our own aiohttp session, bypassing the wrapper's bug entirely.

Uses DENDELE (24956) and Inner Circle (19186) as live test cases -- both
real teams (confirmed via search_teams) that had zero matches in the
global finished() sample, so a working team-scoped lookup should return
real match data for them here where the global-scan approach couldn't.

Run in the Codespace:
    python scripts/probe_team_matches_real.py > team_matches_real.txt
"""
import asyncio
import json
from datetime import datetime, timedelta

import aiohttp

BASE = "https://api.bo3.gg/api/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

TEST_TEAM_IDS = [24956, 19186]  # DENDELE, Inner Circle


async def try_get(session, url, params, label):
    print("=" * 60)
    print(label)
    print(f"  URL: {url}")
    print(f"  params: {params}")
    print("=" * 60)
    try:
        async with session.get(url, headers=HEADERS, params=params,
                                timeout=aiohttp.ClientTimeout(total=15)) as resp:
            print(f"  status: {resp.status}")
            if resp.status == 200:
                data = await resp.json()
                text = json.dumps(data, indent=2, default=str)
                print(text[:3000])
                if len(text) > 3000:
                    print(f"...[truncated, {len(text)} total chars]")
                print()
                return data
            else:
                print(f"  body: {(await resp.text())[:400]}\n")
    except Exception as e:
        print(f"  ! request failed: {type(e).__name__}: {e}\n")
    return None


async def main():
    today = datetime.now().date()
    start_date = today - timedelta(days=180)

    async with aiohttp.ClientSession() as session:
        for team_id in TEST_TEAM_IDS:
            print(f"\n{'#' * 60}\n# TEAM ID {team_id}\n{'#' * 60}\n")

            # Exact params lifted from cs2api's own get_team_matches source.
            params = {
                "scope": "widget-map-pool",
                "page[offset]": "0",
                "page[limit]": "20",
                "sort": "-start_date",
                "filter[matches.status][in]": "finished",
                "filter[matches.team_ids][overlap]": str(team_id),
                "filter[matches.start_date][lt]": today.isoformat(),
                "filter[matches.start_date][gt]": start_date.isoformat(),
                "filter[matches.discipline_id][eq]": "1",
                "with": "teams,tournament,ai_predictions,games,match_maps",
            }
            data = await try_get(session, f"{BASE}/matches", params,
                                  f"GET /matches with real filter[matches.team_ids][overlap]={team_id}")
            if data:
                results = data.get("results", data) if isinstance(data, dict) else data
                count = len(results) if isinstance(results, list) else "?"
                print(f">>> {count} match(es) returned for team_id={team_id}\n")
                if isinstance(results, list) and results:
                    ids_involved = {(m.get("team1_id"), m.get("team2_id")) for m in results}
                    print(f">>> distinct (team1_id, team2_id) pairs seen: {ids_involved}")
                    all_involve_target = all(team_id in pair for pair in ids_involved)
                    print(f">>> every match actually involves team_id={team_id}: {all_involve_target}\n")

            await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(main())
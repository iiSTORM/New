#!/usr/bin/env python3
"""
Probes for a direct, team-specific match-history endpoint on bo3.gg's raw
API — bypassing cs2api's wrapper entirely, same approach that found
players_stats/game_steam_profiles earlier. The goal: replace "scan the
global finished() feed and hope this team's match is in the sample" with
"ask directly for this team's own recent matches", which would fix teams
like DENDELE/Inner Circle that resolve fine via search_teams but never
show up in the 100-match global window the backfill pass currently scans.

Uses two real, confirmed team IDs from this project's own data as live
test cases: DENDELE (24956) and Inner Circle (19186) — both real teams
with zero matches found in the global finished() sample, so a working
direct-lookup endpoint should return real match data for them here.

Run in the Codespace, redirecting to a file:
    python scripts/probe_bo3_team_matches.py > team_matches_probe.txt
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

TEST_TEAM_IDS = [24956, 19186]  # DENDELE, Inner Circle


async def try_get(session, url, params=None, label=None):
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
                print(text[:2000])
                if len(text) > 2000:
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
        for team_id in TEST_TEAM_IDS:
            print(f"\n{'#' * 60}\n# TEAM ID {team_id}\n{'#' * 60}\n")

            # Candidate 1: team detail with a "with" param, matching the
            # /matches/{slug}?with=games convention confirmed working earlier.
            await try_get(session, f"{BASE}/teams/{team_id}", params={"with": "matches"},
                          label=f"GET /teams/{team_id}?with=matches")

            # Candidate 2: direct sub-resource path, matching the
            # /games/{id}/players_stats convention confirmed working earlier.
            await try_get(session, f"{BASE}/teams/{team_id}/matches",
                          label=f"GET /teams/{team_id}/matches")

            # Candidate 3: a few plausible naming variants for the same idea.
            for path in ["finished_matches", "results", "recent_matches", "match_history"]:
                await try_get(session, f"{BASE}/teams/{team_id}/{path}",
                              label=f"GET /teams/{team_id}/{path}")
                await asyncio.sleep(0.3)

            # Candidate 4: bare team detail with no params, to see the full
            # shape of what a team object even contains (may reveal a
            # relation name we haven't guessed yet, e.g. an embedded
            # matches array or a link to one).
            await try_get(session, f"{BASE}/teams/{team_id}",
                          label=f"GET /teams/{team_id} — bare, full shape")

            # Candidate 5: filtering the matches LIST endpoint by team,
            # JSON:API-style query param filtering (a common REST convention
            # this API might follow elsewhere).
            await try_get(session, f"{BASE}/matches", params={"filter[team_id]": team_id, "limit": 10},
                          label=f"GET /matches?filter[team_id]={team_id}")
            await try_get(session, f"{BASE}/matches", params={"team_id": team_id, "limit": 10},
                          label=f"GET /matches?team_id={team_id}")

            await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(main())
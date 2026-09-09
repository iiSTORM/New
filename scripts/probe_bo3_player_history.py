#!/usr/bin/env python3
"""
Probes for a player-scoped match-history endpoint on bo3.gg's raw API —
the CS2 equivalent of what scrape_career.py does for LoL via gol.gg.
Real, confirmed groundwork this builds on:
  - bo3.gg's own frontend has dedicated pages at /players/{slug}/career
    and /players/{slug}/maps showing real per-map K/D/A history,
    confirming this data exists somewhere in their system.
  - The frontend is a JS-rendered SPA (confirmed via a raw HTML fetch
    returning almost nothing) — same as everywhere else in this CS2
    pipeline, meaning the real data has to come from the raw API, not
    scraping rendered pages.
  - The existing pipeline (scrape_cs2.py) ALREADY captures each player's
    steam_profile_id from players_stats — if bo3.gg's /matches endpoint
    supports filtering by player the same way it supports
    filter[matches.team_ids][overlap] for teams (confirmed working
    earlier this session), this could reuse an ID already on hand
    instead of needing to discover a whole separate player-ID system.

Tests several candidate approaches since the real one isn't knowable
without live testing — same situation as the team-history breakthrough
earlier, which needed a live probe to find the actual working filter
parameter name.

Run in the Codespace:
    python scripts/probe_bo3_player_history.py > player_history_probe.txt
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

# donk (Team Spirit) — real, current, high-profile player, easy to
# recognize correct results for. steam_profile_id needs to come from a
# real players_stats fetch first (see step 1 below), since we don't have
# it memorized — everything downstream depends on getting a REAL one.
TEST_PLAYER_NAME = "donk"
TEST_TEAM_ID = 654  # Team Spirit — already confirmed real from earlier this session


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
        # Step 1: get a REAL, current steam_profile_id for donk, via a
        # real recent Spirit match — same team-scoped match-history
        # endpoint already confirmed working earlier this session.
        print("STEP 1: Finding a real recent Spirit match to extract donk's steam_profile_id from\n")
        matches = await bo3_get(session, "/matches", params={
            "scope": "widget-map-pool", "page[offset]": "0", "page[limit]": "3",
            "sort": "-start_date", "filter[matches.status][in]": "finished",
            "filter[matches.team_ids][overlap]": str(TEST_TEAM_ID),
            "filter[matches.discipline_id][eq]": "1", "with": "teams,games",
        }, label="GET /matches for Team Spirit (to find a real match+game id)")

        if not matches or not matches.get("results"):
            print("! Could not find a Spirit match — cannot proceed to steps 2/3 without a real game_id")
            return

        first_match = matches["results"][0]
        games = first_match.get("games", [])
        if not games:
            print("! Match found but no games list — cannot proceed")
            return
        game_id = games[0].get("id")
        print(f">>> Using game_id={game_id} from match {first_match.get('slug')}\n")

        stats = await bo3_get(session, f"/games/{game_id}/players_stats",
                               label=f"GET /games/{game_id}/players_stats (to find donk's steam_profile_id)")
        steam_profile_id = None
        if stats:
            for row in stats:
                if row.get("clan_name") and "spirit" not in row.get("clan_name", "").lower():
                    continue
            # Can't match by name directly (players_stats doesn't include
            # nicknames — that's what game_steam_profiles is for), so
            # just print all Spirit-side steam_profile_ids for manual
            # cross-reference against known Spirit roster order, and use
            # the first one as an actual test subject regardless of
            # whether it's specifically donk.
            spirit_side_ids = [row.get("steam_profile_id") for row in stats
                                if (row.get("team_clan") or {}).get("team_id") == TEST_TEAM_ID]
            print(f">>> Spirit-side steam_profile_ids found: {spirit_side_ids}\n")
            if spirit_side_ids:
                steam_profile_id = spirit_side_ids[0]

        if not steam_profile_id:
            print("! Could not extract a real steam_profile_id — cannot test player-scoped filtering")
            return

        print(f"\nSTEP 2: Testing candidate player-scoped match filters using steam_profile_id={steam_profile_id}\n")

        # Candidate A: same filter convention as team_ids, but for players
        await bo3_get(session, "/matches", params={
            "scope": "widget-map-pool", "page[offset]": "0", "page[limit]": "5",
            "sort": "-start_date", "filter[matches.status][in]": "finished",
            "filter[matches.player_ids][overlap]": str(steam_profile_id),
            "filter[matches.discipline_id][eq]": "1", "with": "teams,games",
        }, label=f"Candidate A: filter[matches.player_ids][overlap]={steam_profile_id}")

        # Candidate B: steam_profile_ids (plural variant)
        await bo3_get(session, "/matches", params={
            "scope": "widget-map-pool", "page[offset]": "0", "page[limit]": "5",
            "sort": "-start_date", "filter[matches.status][in]": "finished",
            "filter[matches.steam_profile_ids][overlap]": str(steam_profile_id),
            "filter[matches.discipline_id][eq]": "1", "with": "teams,games",
        }, label=f"Candidate B: filter[matches.steam_profile_ids][overlap]={steam_profile_id}")

        # Candidate C: a dedicated player-games endpoint, REST-style
        await bo3_get(session, f"/players/{steam_profile_id}/games",
                       label=f"Candidate C: GET /players/{steam_profile_id}/games")

        # Candidate D: a dedicated player-matches endpoint, REST-style
        await bo3_get(session, f"/players/{steam_profile_id}/matches",
                       label=f"Candidate D: GET /players/{steam_profile_id}/matches")

        # Candidate E: bare player detail, to see the full shape of what
        # a player object contains (may reveal a relation name we
        # haven't guessed yet, similar to how team objects revealed
        # useful nested data earlier)
        await bo3_get(session, f"/players/{steam_profile_id}",
                       label=f"Candidate E: GET /players/{steam_profile_id} — bare, full shape")

        # Candidate F: a player search/filter endpoint, mirroring
        # /filters/teams which was confirmed working for team name
        # resolution earlier this session
        await bo3_get(session, "/filters/players", params={
            "page[offset]": "0", "page[limit]": "3",
            "filter[players.discipline_id][eq]": "1", "search_text": TEST_PLAYER_NAME,
        }, label=f"Candidate F: GET /filters/players?search_text={TEST_PLAYER_NAME}")


if __name__ == "__main__":
    asyncio.run(main())
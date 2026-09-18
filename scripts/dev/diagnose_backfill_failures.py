#!/usr/bin/env python3
"""
Diagnoses why ALKA (23043), RED Canids Academy (16743), and Sementes do
Mal (25188) had a 100% failure rate across every backfill match attempted
-- not occasional flakiness, but consistent across multiple different
matches per team, which points at something systematic rather than
per-match randomness. Tests the hypothesis that these specific matches
have genuinely incomplete per-player stats on bo3.gg's end (confirmed to
happen at least once very early in this whole investigation: a real
match came back with "stats": [] despite the match itself being real and
completed).

Run in the Codespace:
    python scripts/diagnose_backfill_failures.py > backfill_failure_diagnosis.txt
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

FAILING_TEAMS = {"ALKA": 23043, "RED Canids Academy": 16743, "Sementes do Mal": 25188}


async def bo3_get(session, path, params=None):
    url = f"{BASE}{path}"
    async with session.get(url, headers=HEADERS, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        print(f"  GET {url} params={params} -> status {resp.status}")
        if resp.status == 200:
            return await resp.json()
        print(f"  body: {(await resp.text())[:300]}")
    return None


async def main():
    from datetime import datetime, timedelta
    today = datetime.now().date()
    start_date = today - timedelta(days=180)

    async with aiohttp.ClientSession() as session:
        for name, team_id in FAILING_TEAMS.items():
            print(f"\n{'#' * 60}\n# {name} (id={team_id})\n{'#' * 60}\n")

            params = {
                "scope": "widget-map-pool", "page[offset]": "0", "page[limit]": "3",
                "sort": "-start_date", "filter[matches.status][in]": "finished",
                "filter[matches.team_ids][overlap]": str(team_id),
                "filter[matches.start_date][lt]": today.isoformat(),
                "filter[matches.start_date][gt]": start_date.isoformat(),
                "filter[matches.discipline_id][eq]": "1",
                "with": "teams,tournament,ai_predictions,games,match_maps",
            }
            data = await bo3_get(session, "/matches", params=params)
            if not data:
                print("  ! no data returned at all\n")
                continue
            results = data.get("results", data) if isinstance(data, dict) else data
            if not results:
                print("  ! zero matches in results\n")
                continue

            m = results[0]
            slug = m.get("slug")
            print(f"\n  Testing first match: {slug}\n")

            match_detail = await bo3_get(session, f"/matches/{slug}", params={"with": "games"})
            if not match_detail:
                print("  ! /matches/{slug}?with=games returned nothing\n")
                continue
            games = match_detail.get("games", [])
            print(f"  games list length: {len(games)}")
            if games:
                print(f"  games raw: {json.dumps(games, indent=2, default=str)[:800]}\n")

            if not games:
                print("  ! THIS MATCH HAS NO GAMES LIST AT ALL — that alone would explain the failure\n")
                continue

            game_id = games[0].get("id")
            print(f"  Testing players_stats for first game_id={game_id}...")
            stats = await bo3_get(session, f"/games/{game_id}/players_stats")
            print(f"  players_stats result: {json.dumps(stats, indent=2, default=str)[:1500] if stats else stats}")
            print(f"  >>> players_stats length: {len(stats) if isinstance(stats, list) else 'N/A (not a list)'}")

            profiles = await bo3_get(session, f"/games/{game_id}/game_steam_profiles")
            print(f"  >>> game_steam_profiles length: {len(profiles) if isinstance(profiles, list) else 'N/A'}\n")

            await asyncio.sleep(0.3)


if __name__ == "__main__":
    asyncio.run(main())
#!/usr/bin/env python3
"""
Follow-up to probe_bo3_game_detail.py. The game_rounds data included a
"steam_profile_mvp_id" field, meaning bo3.gg's schema does track
individual players somewhere — just not in the default /games/{id}
response, which only returned team-level "game_round_team_clans".

Given the API validates "with" relation names against a real whitelist
(confirmed: unknown paths return a specific 422 "unavailable_association_
parameter" error rather than being silently ignored), this tests player-
level relation name candidates following the same naming pattern as the
one that's already confirmed to work by default (game_round_team_clans).

Run in the Codespace, redirecting to a file:
    python scripts/probe_bo3_player_rounds.py > cs2_probe_output_3.txt
"""
import asyncio
import json

import aiohttp

BASE = "https://api.bo3.gg/api/v1"
KNOWN_GAME_ID = 180726
KNOWN_MATCH_SLUG = "legacy-br-vs-spirit-22-08-2026"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# Relation-name candidates for the /games/{id} endpoint, following the
# game_round_team_clans naming pattern but at player granularity.
GAME_WITH_CANDIDATES = [
    "game_rounds.game_round_players",
    "game_rounds.game_round_steam_profiles",
    "game_rounds.players",
    "game_rounds.steam_profiles",
    "game_round_players",
    "game_round_steam_profiles",
    "steam_profiles",
    "player_rounds",
    "game_players",
    "players",
    "match_players",
]

# Standalone endpoint guesses, since bo3.gg clearly models "steam_profile"
# as a real entity (per the mvp_id field).
STANDALONE_CANDIDATES = [
    f"/games/{KNOWN_GAME_ID}/steam_profiles",
    f"/games/{KNOWN_GAME_ID}/game_round_players",
    f"/steam_profiles?filter[game_id]={KNOWN_GAME_ID}",
    f"/matches/{KNOWN_MATCH_SLUG}/players",
    f"/matches/{KNOWN_MATCH_SLUG}/steam_profiles",
]


def find_player_hits(obj, path=""):
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_path = f"{path}.{k}" if path else k
            if any(term in k.lower() for term in ("player", "steam", "nickname", "kill", "death", "assist")):
                hits.append((new_path, v if not isinstance(v, (dict, list)) else f"<{type(v).__name__}, len={len(v)}>"))
            hits.extend(find_player_hits(v, new_path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:2]):
            hits.extend(find_player_hits(v, f"{path}[{i}]"))
    return hits


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
                hits = find_player_hits(data)
                if hits:
                    print(f"  >>> FOUND {len(hits)} player-related field(s):")
                    for path, val in hits[:30]:
                        print(f"      {path} = {val}")
                else:
                    print("  (200 OK, but no player-related keys found anywhere in the response)")
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
        print("### Testing player-level 'with' relations on /games/{id} ###\n")
        for with_value in GAME_WITH_CANDIDATES:
            url = f"{BASE}/games/{KNOWN_GAME_ID}?with={with_value}"
            await try_get(session, url, f"with={with_value}")
            await asyncio.sleep(0.5)

        print("\n### Testing standalone player/steam_profile endpoints ###\n")
        for path in STANDALONE_CANDIDATES:
            url = f"{BASE}{path}"
            await try_get(session, url, f"GET {path}")
            await asyncio.sleep(0.5)


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
Follow-up to probe_bo3_api.py. GET /api/v1/games/{id} is confirmed real
and far richer than the "games" array embedded in the match response —
but the previous probe's 3000-char print limit got eaten by team/image
metadata bloat before reaching whatever comes after it. This strips out
every field we already know is irrelevant (demo internals, image URLs/
versions, social links, team_clans history) and prints top-level keys
first regardless of truncation, so if there's a player-stats section
further into the object, we'll actually see it this time.

Run in the Codespace, redirecting to a file:
    python scripts/probe_bo3_game_detail.py > cs2_probe_output_2.txt
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

NOISE_KEYS = {
    "demo_header", "demo_url", "demo_data", "image_url", "tshirt_image_url",
    "icon_url", "image_data", "image_versions", "facebook", "twitter",
    "youtube_url", "instagram_url", "website_url", "team_clans", "code",
    "created_at", "updated_at", "email", "twitch_url",
}


def strip_noise(obj):
    if isinstance(obj, dict):
        return {k: strip_noise(v) for k, v in obj.items() if k not in NOISE_KEYS}
    if isinstance(obj, list):
        return [strip_noise(v) for v in obj]
    return obj


def find_stat_like_keys(obj, path=""):
    """Recursively searches for any key whose name suggests player-level
    stats (kill/death/assist/adr/rating/player), printing the path to each
    one found — a targeted search instead of hoping truncation doesn't cut
    it off."""
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_path = f"{path}.{k}" if path else k
            if any(term in k.lower() for term in ("kill", "death", "assist", "adr", "rating", "player", "score_data", "stats")):
                hits.append((new_path, v if not isinstance(v, (dict, list)) else f"<{type(v).__name__}, len={len(v)}>"))
            hits.extend(find_stat_like_keys(v, new_path))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:2]):  # only recurse into first couple items to avoid explosion
            hits.extend(find_stat_like_keys(v, f"{path}[{i}]"))
    return hits


async def main():
    async with aiohttp.ClientSession() as session:
        url = f"{BASE}/games/{KNOWN_GAME_ID}"
        print(f"GET {url}\n")
        async with session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            print(f"status: {resp.status}\n")
            if resp.status != 200:
                print(await resp.text())
                return
            data = await resp.json()

        print("=" * 60)
        print("ALL TOP-LEVEL KEYS (uncut)")
        print("=" * 60)
        print(sorted(data.keys()))
        print()

        print("=" * 60)
        print("TARGETED SEARCH: any key path suggesting player-level stats")
        print("=" * 60)
        hits = find_stat_like_keys(data)
        if hits:
            for path, val in hits:
                print(f"  {path} = {val}")
        else:
            print("  (none found anywhere in the response)")
        print()

        print("=" * 60)
        print("FULL RESPONSE, NOISE STRIPPED")
        print("=" * 60)
        cleaned = strip_noise(data)
        text = json.dumps(cleaned, indent=2, default=str)
        print(text[:8000])
        if len(text) > 8000:
            print(f"...[truncated, {len(text)} total chars after stripping]")


if __name__ == "__main__":
    asyncio.run(main())

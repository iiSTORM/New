#!/usr/bin/env python3
"""
Focused follow-up to explore_cs2api_2.py. Two specific goals, since the
previous run's get_match_details() output got truncated right before the
one thing that actually determines the scraper's architecture — whether
per-player K/D/A is embedded directly in the match response, or requires
a separate per-map call:

  1. List EVERY available method on the CS2 class via introspection,
     instead of trusting the PyPI description was exhaustive (it may not
     have listed a per-game/map-stats method at all).
  2. Re-fetch get_match_details() on the same known-good match, but print
     ONLY the "games" key in full (no truncation), skipping all the
     team_clans/tournament/streams noise that ate the character budget
     last time.

Run in the Codespace, redirecting to a file:
    python scripts/explore_cs2api_3.py > cs2_explore_output_3.txt
"""
import asyncio
import json

from cs2api import CS2

KNOWN_MATCH_SLUG = "legacy-br-vs-spirit-22-08-2026"


async def main():
    async with CS2() as cs2:
        print("=" * 60)
        print("ALL PUBLIC METHODS ON THE CS2 CLASS (via introspection)")
        print("=" * 60)
        methods = sorted(m for m in dir(cs2) if not m.startswith("_"))
        for m in methods:
            print(f"  {m}")
        print()

        print("=" * 60)
        print(f"get_match_details({KNOWN_MATCH_SLUG!r}) — 'games' key only, full/uncut")
        print("=" * 60)
        try:
            details = await cs2.get_match_details(KNOWN_MATCH_SLUG)
            print(f"Top-level keys in the response: {sorted(details.keys())}\n")
            games = details.get("games")
            print(f"type(games) = {type(games)}")
            print(json.dumps(games, indent=2, default=str))
            print()

            # If games is a list of dicts, show the full first entry
            # specifically (in case per-player data is nested there but
            # the whole array is too long to print in full).
            if isinstance(games, list) and games:
                print("--- First game entry, full/uncut ---")
                print(json.dumps(games[0], indent=2, default=str))
                print()
                if len(games[0].keys() if isinstance(games[0], dict) else []) :
                    print(f"Keys in first game entry: {sorted(games[0].keys())}")
        except Exception as e:
            print(f"! get_match_details() failed: {type(e).__name__}: {e}\n")
            games = None

        # If a per-game-id method exists (name TBD from the introspection
        # list above), try calling it on the first real game id we find.
        game_id = None
        try:
            if isinstance(games, list) and games and isinstance(games[0], dict):
                for key in ("id", "game_id"):
                    if key in games[0]:
                        game_id = games[0][key]
                        break
        except Exception:
            pass

        if game_id is not None:
            print(f"\nFound real game_id = {game_id} from the games array.")
            candidate_names = [
                "get_game_details", "get_game_stats", "get_map_details",
                "get_map_stats", "get_game", "get_game_players",
                "get_game_scoreboard", "get_map_scoreboard",
            ]
            for name in candidate_names:
                if hasattr(cs2, name):
                    print(f"\n--- Trying {name}({game_id}) — method exists on the class ---")
                    try:
                        method = getattr(cs2, name)
                        result = await method(game_id)
                        print(json.dumps(result, indent=2, default=str)[:4000])
                    except Exception as e:
                        print(f"! {name}({game_id}) failed: {type(e).__name__}: {e}")
                else:
                    print(f"(no method named {name} on the class)")
        else:
            print("\n! No game_id found in the games array — can't test per-game methods")


if __name__ == "__main__":
    asyncio.run(main())

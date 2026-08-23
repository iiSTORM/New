#!/usr/bin/env python3
"""
One-time exploration script — NOT the real scraper. Calls the remaining
cs2api methods we haven't seen output from yet, using KNOWN-GOOD real
identifiers pulled directly from a previous run's output (a match id,
team slug, and player slug that are confirmed to exist) rather than the
auto-extraction logic from the first version of this script, which
silently failed to pull identifiers out of finished()/search_teams()/
search_players() results for a reason that wasn't obvious from code
review alone — sidestepping it entirely is faster than a third guess.

Run in the Codespace, redirecting to a file:
    python scripts/explore_cs2api_2.py > cs2_explore_output_2.txt

Then share that file — this should finally get us get_match_details()
(the critical one, expected to have per-player K/D/A), plus
get_team_upcoming_matches(), get_team_stats(), and get_player_stats().
"""
import asyncio
import json

from cs2api import CS2

# Confirmed real, valid identifiers from the previous exploration run's
# actual output — using these directly instead of re-deriving them.
KNOWN_MATCH_ID = 126441
KNOWN_MATCH_SLUG = "legacy-br-vs-spirit-22-08-2026"
KNOWN_TEAM_ID = 667
KNOWN_TEAM_SLUG = "vitality"
KNOWN_PLAYER_SLUG = "zywoo"

NOISE_KEYS = {
    "bet_updates", "additional_markets", "markets_count", "bet_provider_id",
    "live_coverage", "live_coverage_source", "stars",
}


def strip_noise(obj):
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            if k in NOISE_KEYS:
                continue
            if k in ("coeff", "max_coeff", "aggrement_score", "active"):
                continue
            cleaned[k] = strip_noise(v)
        return cleaned
    if isinstance(obj, list):
        return [strip_noise(v) for v in obj]
    return obj


def show(label, obj, limit=6000):
    print("=" * 60)
    print(label)
    print("=" * 60)
    print(f"  [debug] type(obj) = {type(obj)}")
    cleaned = strip_noise(obj)
    text = json.dumps(cleaned, indent=2, default=str)
    print(text[:limit])
    if len(text) > limit:
        print(f"...[truncated, {len(text)} total chars]")
    print()


async def main():
    async with CS2() as cs2:
        # Try both the numeric id AND the slug for match details — one of
        # them should work, and seeing which (or if both fail) is itself
        # useful diagnostic information.
        for label, identifier in [
            (f"get_match_details({KNOWN_MATCH_ID}) — numeric id", KNOWN_MATCH_ID),
            (f"get_match_details({KNOWN_MATCH_SLUG!r}) — slug", KNOWN_MATCH_SLUG),
        ]:
            try:
                details = await cs2.get_match_details(identifier)
                show(label + " — SUCCESS, THE IMPORTANT ONE", details)
            except Exception as e:
                print(f"! {label} failed: {type(e).__name__}: {e}\n")

        for label, identifier in [
            (f"get_team_upcoming_matches({KNOWN_TEAM_ID}) — numeric id", KNOWN_TEAM_ID),
            (f"get_team_upcoming_matches({KNOWN_TEAM_SLUG!r}) — slug", KNOWN_TEAM_SLUG),
        ]:
            try:
                upcoming = await cs2.get_team_upcoming_matches(identifier)
                show(label + " — SUCCESS", upcoming)
            except Exception as e:
                print(f"! {label} failed: {type(e).__name__}: {e}\n")

        for label, identifier in [
            (f"get_team_matches({KNOWN_TEAM_ID}) — numeric id, past match history", KNOWN_TEAM_ID),
        ]:
            try:
                past = await cs2.get_team_matches(identifier)
                show(label + " — SUCCESS", past)
            except Exception as e:
                print(f"! {label} failed: {type(e).__name__}: {e}\n")

        try:
            stats = await cs2.get_team_stats(KNOWN_TEAM_SLUG)
            show(f"get_team_stats({KNOWN_TEAM_SLUG!r}) — SUCCESS", stats)
        except Exception as e:
            print(f"! get_team_stats() failed: {type(e).__name__}: {e}\n")

        try:
            data = await cs2.get_team_data(KNOWN_TEAM_SLUG)
            show(f"get_team_data({KNOWN_TEAM_SLUG!r}) — SUCCESS (roster expected here)", data)
        except Exception as e:
            print(f"! get_team_data() failed: {type(e).__name__}: {e}\n")

        try:
            pstats = await cs2.get_player_stats(KNOWN_PLAYER_SLUG)
            show(f"get_player_stats({KNOWN_PLAYER_SLUG!r}) — SUCCESS", pstats)
        except Exception as e:
            print(f"! get_player_stats() failed: {type(e).__name__}: {e}\n")

        try:
            pdetails = await cs2.get_player_details(KNOWN_PLAYER_SLUG)
            show(f"get_player_details({KNOWN_PLAYER_SLUG!r}) — SUCCESS (role/team expected here)", pdetails)
        except Exception as e:
            print(f"! get_player_details() failed: {type(e).__name__}: {e}\n")

        try:
            pmatches = await cs2.get_player_matches(18452)  # ZywOo's id from the earlier search_players() output
            show("get_player_matches(18452) — SUCCESS", pmatches)
        except Exception as e:
            print(f"! get_player_matches() failed: {type(e).__name__}: {e}\n")


if __name__ == "__main__":
    asyncio.run(main())

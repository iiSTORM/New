#!/usr/bin/env python3
"""
One-time exploration script — NOT the real scraper. Calls a handful of
cs2api methods and dumps the raw JSON so we can see real field names
before building scrape_cs2.py around them, instead of guessing (the way
the HTML-scraping approach had to for gol.gg/VLR.gg).

Run this once in the Codespace:
    pip install cs2api
    python scripts/explore_cs2api.py

Then send the output back — that tells us exactly what shape
finished()/get_match_details()/get_team_upcoming_matches()/get_player_stats()
actually return, so the real scraper can be built correctly on the first
pass instead of iterating blind.
"""
import asyncio
import json

from cs2api import CS2


async def main():
    async with CS2() as cs2:
        print("=" * 60)
        print("finished() — recently finished matches")
        print("=" * 60)
        try:
            finished = await cs2.finished()
            print(json.dumps(finished, indent=2)[:3000])
        except Exception as e:
            print(f"! finished() failed: {e}")

        print("\n" + "=" * 60)
        print("get_todays_matches()")
        print("=" * 60)
        try:
            today = await cs2.get_todays_matches()
            print(json.dumps(today, indent=2)[:2000])
        except Exception as e:
            print(f"! get_todays_matches() failed: {e}")

        # Try to get a real match slug/id from the finished() results above,
        # so get_match_details() below is a real, valid call rather than a
        # guess at an ID.
        match_slug = None
        try:
            finished = await cs2.finished()
            if isinstance(finished, list) and finished:
                first = finished[0]
                print("\n" + "=" * 60)
                print("First finished match's raw keys (to find the right slug/id field):")
                print("=" * 60)
                print(json.dumps(first, indent=2)[:2000])
                # Try a few common key names for the match identifier
                for key in ("slug", "id", "match_id", "matchId"):
                    if isinstance(first, dict) and key in first:
                        match_slug = first[key]
                        print(f"\nUsing '{key}' = {match_slug!r} for get_match_details() below")
                        break
        except Exception as e:
            print(f"! couldn't extract a match slug: {e}")

        if match_slug is not None:
            print("\n" + "=" * 60)
            print(f"get_match_details({match_slug!r}) — full detail for one real finished match")
            print("=" * 60)
            try:
                details = await cs2.get_match_details(match_slug)
                print(json.dumps(details, indent=2)[:5000])
            except Exception as e:
                print(f"! get_match_details() failed: {e}")
        else:
            print("\n! No match slug found — skipping get_match_details() test")

        print("\n" + "=" * 60)
        print("search_teams('Vitality') — to find a real team_id/slug")
        print("=" * 60)
        team_id, team_slug = None, None
        try:
            teams = await cs2.search_teams("Vitality")
            print(json.dumps(teams, indent=2)[:2000])
            if isinstance(teams, list) and teams:
                first_team = teams[0]
                for key in ("id", "team_id", "teamId"):
                    if isinstance(first_team, dict) and key in first_team:
                        team_id = first_team[key]
                        break
                for key in ("slug", "team_slug"):
                    if isinstance(first_team, dict) and key in first_team:
                        team_slug = first_team[key]
                        break
        except Exception as e:
            print(f"! search_teams() failed: {e}")

        if team_id is not None:
            print("\n" + "=" * 60)
            print(f"get_team_upcoming_matches({team_id!r})")
            print("=" * 60)
            try:
                upcoming = await cs2.get_team_upcoming_matches(team_id)
                print(json.dumps(upcoming, indent=2)[:2500])
            except Exception as e:
                print(f"! get_team_upcoming_matches() failed: {e}")

            print("\n" + "=" * 60)
            print(f"get_team_matches({team_id!r}) — past match history")
            print("=" * 60)
            try:
                past = await cs2.get_team_matches(team_id)
                print(json.dumps(past, indent=2)[:2500])
            except Exception as e:
                print(f"! get_team_matches() failed: {e}")

        if team_slug is not None:
            print("\n" + "=" * 60)
            print(f"get_team_stats({team_slug!r})")
            print("=" * 60)
            try:
                stats = await cs2.get_team_stats(team_slug)
                print(json.dumps(stats, indent=2)[:2500])
            except Exception as e:
                print(f"! get_team_stats() failed: {e}")

        print("\n" + "=" * 60)
        print("search_players('ZywOo') — to find a real player slug")
        print("=" * 60)
        player_slug = None
        try:
            players = await cs2.search_players("ZywOo")
            print(json.dumps(players, indent=2)[:1500])
            if isinstance(players, list) and players:
                first_player = players[0]
                for key in ("slug", "player_slug"):
                    if isinstance(first_player, dict) and key in first_player:
                        player_slug = first_player[key]
                        break
        except Exception as e:
            print(f"! search_players() failed: {e}")

        if player_slug is not None:
            print("\n" + "=" * 60)
            print(f"get_player_stats({player_slug!r})")
            print("=" * 60)
            try:
                pstats = await cs2.get_player_stats(player_slug)
                print(json.dumps(pstats, indent=2)[:2500])
            except Exception as e:
                print(f"! get_player_stats() failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())

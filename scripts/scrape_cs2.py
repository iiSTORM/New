#!/usr/bin/env python3
"""
CS2 scraper. Two data sources combined:
  - cs2api (PyPI package, wraps bo3.gg): match/team discovery. Proven
    reliable for finished()/get_team_upcoming_matches()/search_teams().
    get_team_matches() is confirmed BROKEN in this package (a real
    AttributeError bug on their end, not ours) — worked around below by
    scanning finished() instead of relying on it.
  - Direct calls to api.bo3.gg: the actual per-map player stats, since
    cs2api has no method for this at all. Confirmed working via extensive
    live probing:
      GET /matches/{slug}?with=games       -> match metadata + maps list
      GET /games/{id}/players_stats        -> real per-player K/D/A for that map
      GET /games/{id}/game_steam_profiles  -> steam_profile_id -> nickname

CS2 doesn't have discrete franchised "regions" the way LCS/VCT do — it's
an individually-ranked global scene, tournament-based rather than
league-based. This tracks a curated list of currently top-ranked/notable
teams instead of a region.

Games 1+2 only, matching the app's established convention (see
scrape_valorant.py's identical rule) — a 3rd map, when a Bo3 goes the
distance, is intentionally excluded so "actual" data lines up with what
the model always projects for.

No clean "split" boundary exists for CS2's continuous tournament calendar
the way LoL has Spring/Summer — "hist" is left null for every player
(the model already handles this gracefully, falling back to "cur" alone)
rather than forcing a fake historical window.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone

import aiohttp
from cs2api import CS2

BO3_BASE = "https://api.bo3.gg/api/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# Curated, not exhaustive — confirmed real via live rankings/stats calls
# during development. Update this list periodically as the meta shifts;
# there's no way to auto-derive "the current top N" cheaply from the API
# surface available.
TRACKED_TEAMS = [
    "falcons", "spirit", "furia", "vitality", "mouz", "natus-vincere",
    "faze", "aurora", "9z", "g2", "astralis", "legacy-br", "the-mongolz",
    "fut", "betboom", "parivision", "b8", "pain", "big", "liquid",
]

# How many pages of the global finished() feed to scan for each tracked
# team's recent matches (100 per page). finished() is sorted most-recent-
# first, so this effectively sets the recency window for "cur" stats.
FINISHED_SCAN_PAGES = 15

COLOR_PALETTE = [
    "#e0c341", "#4fa8e0", "#e05f9a", "#5fd97a", "#e07a4f", "#9a7ae0",
    "#4fd9c9", "#d94f7a", "#a8d94f", "#4f7ae0",
]


async def bo3_get(session, path, params=None):
    url = f"{BO3_BASE}{path}"
    for attempt in range(3):
        try:
            async with session.get(url, headers=HEADERS, params=params,
                                    timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status == 200:
                    return await resp.json()
                if resp.status == 404:
                    return None
                print(f"  ! {url} -> HTTP {resp.status}", file=sys.stderr)
        except Exception as e:
            print(f"  ! {url} attempt {attempt + 1}/3 failed: {e}", file=sys.stderr)
        await asyncio.sleep(1 + attempt)
    return None


async def fetch_map_player_stats(session, game_id):
    """Returns {player_name: {"k":.., "d":.., "a":.., "team": clan_name}}
    for one specific map, joining players_stats (K/D/A) with
    game_steam_profiles (names) by steam_profile_id."""
    stats, profiles = await asyncio.gather(
        bo3_get(session, f"/games/{game_id}/players_stats"),
        bo3_get(session, f"/games/{game_id}/game_steam_profiles"),
    )
    if not stats or not profiles:
        return {}

    name_by_profile_id = {}
    for p in profiles:
        sp = p.get("steam_profile") or {}
        nickname = (sp.get("player") or {}).get("nickname") or sp.get("nickname")
        if nickname:
            name_by_profile_id[p.get("steam_profile_id")] = nickname

    result = {}
    for s in stats:
        pid = s.get("steam_profile_id")
        name = name_by_profile_id.get(pid)
        if not name:
            continue  # can't attribute this row to a real player name — skip rather than guess
        result[name] = {
            "k": s.get("kills", 0), "d": s.get("death", 0), "a": s.get("assists", 0),
            "team": s.get("clan_name", ""),
        }
    return result


async def fetch_match_actuals(session, match_slug):
    """Fetches one finished match's games (maps), and per-player K/D/A for
    maps 1+2 only, capped exactly like scrape_valorant.py does — the 3rd
    map of a Bo3 that went the distance is excluded so 'actual' data lines
    up with what the model always projects for (2 games)."""
    match = await bo3_get(session, f"/matches/{match_slug}", params={"with": "games"})
    if not match:
        return None
    games = sorted(match.get("games", []), key=lambda g: g.get("number", 0))[:2]
    if not games:
        return None

    per_map_stats = await asyncio.gather(*[fetch_map_player_stats(session, g["id"]) for g in games])

    totals = {}
    for map_stats in per_map_stats:
        for player_name, row in map_stats.items():
            team = row["team"]
            totals.setdefault(team, {})
            slot = totals[team].setdefault(player_name, {"k": 0, "d": 0, "a": 0})
            slot["k"] += row["k"]
            slot["d"] += row["d"]
            slot["a"] += row["a"]
    return totals, len(games)


def normalize_team_name(name):
    return (name or "").strip()


async def build_region_payload(cs2, session):
    teams_payload = {}
    past_matches = []
    upcoming_matches = []
    team_id_by_slug = {}
    color_index = 0

    print("Resolving tracked team slugs to bo3.gg team IDs...")
    for slug in TRACKED_TEAMS:
        try:
            found = await cs2.search_teams(slug)
        except Exception as e:
            print(f"  ! search_teams({slug!r}) failed: {e}", file=sys.stderr)
            continue
        candidates = found.get("results", found) if isinstance(found, dict) else found
        if not candidates:
            print(f"  ! no team found for slug {slug!r}", file=sys.stderr)
            continue
        # Prefer an exact slug match over the first result, since a name
        # search can return academy/secondary rosters first.
        exact = next((t for t in candidates if t.get("slug") == slug), None)
        team = exact or candidates[0]
        team_id_by_slug[slug] = {"id": team["id"], "name": team["name"]}
        print(f"  {slug} -> id={team['id']} name={team['name']!r}")

    print(f"\nResolved {len(team_id_by_slug)}/{len(TRACKED_TEAMS)} tracked teams\n")

    # ---- Upcoming matches ----
    print("Fetching upcoming matches per tracked team...")
    seen_upcoming_ids = set()
    for slug, info in team_id_by_slug.items():
        try:
            sched = await cs2.get_team_upcoming_matches(info["id"])
        except Exception as e:
            print(f"  ! get_team_upcoming_matches({slug}) failed: {e}", file=sys.stderr)
            continue
        matches = sched.get("results", sched) if isinstance(sched, dict) else sched
        if not matches:
            continue
        for m in matches:
            mid = m.get("id")
            if mid in seen_upcoming_ids:
                continue  # both teams in the match are tracked — avoid duplicate entries
            team1 = normalize_team_name(m.get("team1", {}).get("name") if isinstance(m.get("team1"), dict) else m.get("team1_name"))
            team2 = normalize_team_name(m.get("team2", {}).get("name") if isinstance(m.get("team2"), dict) else m.get("team2_name"))
            if not team1 or not team2 or "TBD" in (team1, team2):
                continue
            seen_upcoming_ids.add(mid)
            upcoming_matches.append({
                "date": m.get("start_date") or m.get("date"),
                "teamA": team1, "teamB": team2,
                "block": None,
            })
    print(f"  {len(upcoming_matches)} real upcoming matches (TBD opponents excluded)\n")

    # ---- Past matches: scan the global finished() feed, filter for tracked teams ----
    print(f"Scanning {FINISHED_SCAN_PAGES} pages of finished() for tracked teams' matches...")
    # Compared as strings — a real, confirmed cause of an earlier run
    # finding zero matches across 1500 scanned despite including several
    # top-ranked teams: search_teams() gives integer IDs, but cs2api's
    # finished() wrapper may return team1_id/team2_id as strings
    # internally, and "654" in {654, ...} is False in Python even though
    # the values represent the same team.
    tracked_ids = {str(v["id"]) for v in team_id_by_slug.values()}
    candidate_matches = []
    first_page_ids = None
    for page in range(FINISHED_SCAN_PAGES):
        try:
            # NOTE: "offset" as the pagination kwarg is a reasonable guess
            # based on the response shape ({"total": {"offset": 0, "limit":
            # 100}, ...}) but was never directly confirmed against cs2api's
            # actual method signature — if this throws a TypeError on the
            # first real run, that's the thing to check/fix first.
            batch = await cs2.finished(offset=page * 100)
        except Exception as e:
            print(f"  ! finished() page {page} failed: {e}", file=sys.stderr)
            continue
        results = batch.get("results", batch) if isinstance(batch, dict) else batch
        if not results:
            break
        page_ids = [m.get("id") for m in results[:5]]
        if page == 0:
            print(f"  [debug] page 0 sample match ids: {page_ids}")
            sample = results[0]
            print(f"  [debug] page 0 first match: team1_id={sample.get('team1_id')!r} "
                  f"(type {type(sample.get('team1_id')).__name__}), "
                  f"team2_id={sample.get('team2_id')!r}, "
                  f"team1={sample.get('team1')!r}, team2={sample.get('team2')!r}")
            first_page_ids = page_ids
        elif page == 1 and page_ids == first_page_ids:
            print(f"  [debug] !! page 1's first 5 match ids are IDENTICAL to page 0's — "
                  f"pagination (offset=) is very likely not actually advancing")
        for m in results:
            if str(m.get("team1_id")) in tracked_ids or str(m.get("team2_id")) in tracked_ids:
                candidate_matches.append(m)
        await asyncio.sleep(0.2)
    print(f"  {len(candidate_matches)} matches found involving a tracked team\n")

    print("Fetching per-map player stats for each match (this is the slow part)...")
    sem = asyncio.Semaphore(6)

    async def process(m):
        async with sem:
            result = await fetch_match_actuals(session, m["slug"])
            if not result:
                return None
            totals, maps_played = result
            return {
                "week": None, "date": (m.get("date") or "")[:10] if m.get("date") else None,
                "patch": None, "teamA": normalize_team_name(m.get("team1")),
                "teamB": normalize_team_name(m.get("team2")),
                "winner": normalize_team_name(m.get("team1") if str(m.get("winner_team_id")) == str(m.get("team1_id")) else m.get("team2")),
                "score": m.get("score", ""), "actual": totals, "games": maps_played,
            }

    processed = await asyncio.gather(*[process(m) for m in candidate_matches])
    for entry in processed:
        if entry and entry["actual"]:
            past_matches.append(entry)
    print(f"  {len(past_matches)} matches with real player stats captured\n")

    # ---- Build teams payload from whoever actually showed up in past_matches ----
    for m in past_matches:
        for side in ("teamA", "teamB"):
            team_name = m[side]
            if team_name not in teams_payload:
                teams_payload[team_name] = {"color": COLOR_PALETTE[color_index % len(COLOR_PALETTE)], "players": []}
                color_index += 1
            existing_names = {p["name"] for p in teams_payload[team_name]["players"]}
            for player_name, stats in (m["actual"].get(team_name) or {}).items():
                if player_name in existing_names:
                    continue
                # Aggregate this player's rate across every match they've
                # appeared in for this team, from past_matches already
                # collected — same "derive it from what we have" pattern
                # used elsewhere in this app.
                total_k = total_d = total_a = total_games = 0
                for mm in past_matches:
                    for s in ("teamA", "teamB"):
                        if mm[s] != team_name:
                            continue
                        row = (mm["actual"].get(team_name) or {}).get(player_name)
                        if row:
                            total_k += row["k"]
                            total_d += row["d"]
                            total_a += row["a"]
                            total_games += mm.get("games", 2)
                if total_games == 0:
                    continue
                teams_payload[team_name]["players"].append({
                    "name": player_name, "role": None,
                    "cur": {
                        "g": total_games,
                        "k": total_k / total_games, "d": total_d / total_games, "a": total_a / total_games,
                        "kp": 0,  # not computed for CS2 — kpMultiplier() treats 0 as "uncomputed" and stays neutral
                    },
                    "hist": None,  # no clean split boundary for CS2 — model falls back to cur alone
                })
                existing_names.add(player_name)

    return {"teams": teams_payload, "past_matches": past_matches, "upcoming_matches": upcoming_matches}


async def main():
    async with CS2() as cs2, aiohttp.ClientSession() as session:
        payload = await build_region_payload(cs2, session)

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regions": {"CS2": payload},
    }
    with open("cs2_data.json", "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nWrote cs2_data.json: {len(payload['teams'])} teams, "
          f"{len(payload['past_matches'])} past matches, {len(payload['upcoming_matches'])} upcoming matches")


if __name__ == "__main__":
    asyncio.run(main())

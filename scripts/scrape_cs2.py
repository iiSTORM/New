#!/usr/bin/env python3
"""
CS2 scraper. Two data sources combined:
  - cs2api (PyPI package, wraps bo3.gg): match/team discovery. Proven
    reliable for finished()/get_todays_matches()/get_team_upcoming_matches()
    /search_teams(). get_team_matches() is confirmed BROKEN in this
    package (a real AttributeError bug on their end, not ours) — worked
    around below by scanning finished() instead of relying on it.
  - Direct calls to api.bo3.gg: the actual per-map player stats, since
    cs2api has no method for this at all. Confirmed working via extensive
    live probing:
      GET /matches/{slug}?with=games       -> match metadata + maps list
      GET /games/{id}/players_stats        -> real per-player K/D/A for that map
      GET /games/{id}/game_steam_profiles  -> steam_profile_id -> nickname

CS2 doesn't have discrete franchised "regions" the way LCS/VCT do — it's
an individually-ranked global scene, tournament-based rather than
league-based. Rather than a fixed curated team list (the original
approach — real limitation: any match involving an untracked opponent was
permanently invisible no matter how much data got fetched), this filters
the global match feed by TIER instead — a real quality signal already
present on every match object (confirmed: "tier": "b", "stars": 1 on real
data), used here as a proxy for "notable enough to be worth projecting" —
similar in spirit to what a prop-betting platform would cover. This is an
approximation, not a confirmed match to any specific platform's exact
coverage (no live access to verify against) — ACCEPTED_TIERS below is the
one knob to adjust if real output looks too broad or too narrow.

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

# Tiers to include, lowercase, when the field is actually populated —
# confirmed via real output that "tier" is frequently None even on
# clearly-important matches (a real match came back tier=None, stars=5 —
# the highest stars value seen in this whole project). stars is the more
# reliably-populated signal, so it's the primary filter below; tier is
# kept as a secondary OR condition for whenever it happens to be present.
ACCEPTED_TIERS = {"s", "a"}
# 1-5 scale inferred from real data (a minor qualifier showed stars=1; the
# match above showed stars=5). 3 is a starting midpoint, not a confirmed
# cutoff — build_region_payload prints the real star distribution across
# the fetched batch, so this is the one number to recalibrate from actual
# output if coverage looks too broad or too narrow.
MIN_STARS = 3

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


async def fetch_map_player_stats(session, game_id, canonical_name_by_team_id):
    """Returns ({player_name: {"k":.., "d":.., "a":.., "team": name}},
    {team_id: name}) for one specific map — the second dict is a reliable
    team_id<->name mapping straight from players_stats' own nested
    team_clan field.

    Team NAME resolution prefers canonical_name_by_team_id (the short form
    used by upcoming/schedule endpoints, e.g. "Falcons") over players_stats'
    own clan_name (a longer/different form, e.g. "Team Falcons") whenever
    already known — confirmed via real output that these two sources
    disagree on naming convention. Falls back to clan_name when a team_id
    isn't in the lookup yet (typical on a team's first-seen match, before
    reconciliation happens as a post-processing pass in build_region_payload)."""
    stats, profiles = await asyncio.gather(
        bo3_get(session, f"/games/{game_id}/players_stats"),
        bo3_get(session, f"/games/{game_id}/game_steam_profiles"),
    )
    if not stats or not profiles:
        return {}, {}

    name_by_profile_id = {}
    for p in profiles:
        sp = p.get("steam_profile") or {}
        nickname = (sp.get("player") or {}).get("nickname") or sp.get("nickname")
        if nickname:
            name_by_profile_id[p.get("steam_profile_id")] = nickname

    result = {}
    resolved_name_by_team_id = {}
    for s in stats:
        pid = s.get("steam_profile_id")
        name = name_by_profile_id.get(pid)
        team_clan = s.get("team_clan") or {}
        team_id = team_clan.get("team_id")
        team_name = canonical_name_by_team_id.get(team_id) or s.get("clan_name", "")
        if team_id is not None:
            resolved_name_by_team_id[team_id] = team_name
        if not name:
            continue  # can't attribute this row to a real player name — skip rather than guess
        result[name] = {"k": s.get("kills", 0), "d": s.get("death", 0), "a": s.get("assists", 0), "team": team_name}
    return result, resolved_name_by_team_id


async def fetch_match_actuals(session, match_slug, canonical_name_by_team_id):
    """Fetches one finished match's games (maps), and per-player K/D/A for
    maps 1+2 only, capped exactly like scrape_valorant.py does — the 3rd
    map of a Bo3 that went the distance is excluded so 'actual' data lines
    up with what the model always projects for (2 games).

    Returns (totals, maps_played, winner_name, team1_name, team2_name,
    score_str, match_date, team1_id, team2_id) — team1_id/team2_id are
    returned so build_region_payload can later reconcile this match's
    team names against the short form used by upcoming-match/schedule
    endpoints."""
    match = await bo3_get(session, f"/matches/{match_slug}", params={"with": "games"})
    if not match:
        return None
    games = sorted(match.get("games", []), key=lambda g: g.get("number", 0))[:2]
    if not games:
        return None

    per_map_results = await asyncio.gather(
        *[fetch_map_player_stats(session, g["id"], canonical_name_by_team_id) for g in games]
    )

    totals = {}
    name_by_team_id = {}
    for map_stats, resolved_map in per_map_results:
        name_by_team_id.update(resolved_map)
        for player_name, row in map_stats.items():
            team = row["team"]
            totals.setdefault(team, {})
            slot = totals[team].setdefault(player_name, {"k": 0, "d": 0, "a": 0})
            slot["k"] += row["k"]
            slot["d"] += row["d"]
            slot["a"] += row["a"]

    team_names = list(totals.keys())
    if len(team_names) != 2:
        return None  # incomplete data for this match — better to skip than build a lopsided entry

    id_by_name = {v: k for k, v in name_by_team_id.items()}
    team1_id = id_by_name.get(team_names[0])
    team2_id = id_by_name.get(team_names[1])

    winner_team_id = match.get("winner_team_id")
    winner_name = name_by_team_id.get(winner_team_id)
    if winner_name not in team_names:
        winner_name = None  # couldn't reliably resolve — leave unset rather than guess

    t1s, t2s = match.get("team1_score"), match.get("team2_score")
    score_str = f"{t1s}-{t2s}" if t1s is not None and t2s is not None else ""
    match_date = match.get("start_date")  # confirmed real field — "date" is not

    return totals, len(games), winner_name, team_names[0], team_names[1], score_str, match_date, team1_id, team2_id


def normalize_team_name(name):
    return (name or "").strip()


def is_notable_match(m):
    """The one predicate deciding what counts as 'worth projecting' —
    stars is the primary signal (more reliably populated than tier, per
    real confirmed output); tier is an OR'd secondary signal for whenever
    it happens to be present."""
    tier = (m.get("tier") or "").lower()
    stars = m.get("stars") or 0
    return tier in ACCEPTED_TIERS or stars >= MIN_STARS


def print_star_distribution(matches, label):
    """Diagnostic — prints how many matches fall at each star value across
    the given batch, so MIN_STARS can be recalibrated from real numbers
    instead of guessed a second time if the current threshold is off."""
    counts = {}
    for m in matches:
        counts[m.get("stars")] = counts.get(m.get("stars"), 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (kv[0] is None, kv[0]))
    print(f"  [debug] {label} star distribution: " + ", ".join(f"{k}★={v}" for k, v in ordered))


def extract_match_team_names(m):
    """Pulls team1/team2 display names out of a raw match record from any
    of finished()/get_todays_matches()/get_team_upcoming_matches() — all
    share the same underlying shape (a nested {"name": ...} object, per
    confirmed real output), unlike players_stats' own clan_name field,
    which uses a different naming convention entirely."""
    t1 = m.get("team1")
    t2 = m.get("team2")
    team1 = normalize_team_name(t1.get("name") if isinstance(t1, dict) else m.get("team1_name"))
    team2 = normalize_team_name(t2.get("name") if isinstance(t2, dict) else m.get("team2_name"))
    return team1, team2


async def build_region_payload(cs2, session):
    teams_payload = {}
    past_matches = []
    upcoming_matches = []
    color_index = 0

    # ---- Past matches: scan the global finished() feed, filter by TIER
    # instead of a curated team list — see the module docstring for why.
    # Deliberately uses ONLY the confirmed-working zero-argument call
    # (page 1, ~100 most recent global matches); a guessed pagination
    # kwarg (offset=) was tested and found to actively break the call
    # rather than being silently ignored. Proper multi-page pagination is
    # a real, known follow-up — not solved here — but shouldn't block
    # getting real data flowing. ----
    print("Fetching finished() — confirmed-working zero-argument call, page 1 only for now...")
    try:
        batch = await cs2.finished()
    except Exception as e:
        print(f"  ! finished() failed: {e}", file=sys.stderr)
        batch = None

    results = (batch.get("results", batch) if isinstance(batch, dict) else batch) if batch else []
    if results:
        sample = results[0]
        print(f"  [debug] first match: tier={sample.get('tier')!r}, stars={sample.get('stars')!r}")
        print_star_distribution(results, "global finished()")

    tier_filtered = [m for m in results if is_notable_match(m)]
    print(f"  {len(tier_filtered)} notable matches (tier in {sorted(ACCEPTED_TIERS)} OR stars >= {MIN_STARS}) "
          f"(out of {len(results)} most-recent global matches scanned)\n")

    MATCH_LIMIT = 60
    matches_to_process = tier_filtered[:MATCH_LIMIT]
    print(f"Fetching per-map player stats for {len(matches_to_process)} matches "
          f"(capped from {len(tier_filtered)} found; this is the slow part)...")
    canonical_name_by_team_id = {}  # starts empty — no curated list to seed it with anymore
    sem = asyncio.Semaphore(6)
    progress = {"done": 0, "total": len(matches_to_process)}

    async def process(m, name_lookup):
        async with sem:
            result = await fetch_match_actuals(session, m["slug"], name_lookup)
            progress["done"] += 1
            if progress["done"] % 10 == 0 or progress["done"] == progress["total"]:
                print(f"  ...{progress['done']}/{progress['total']} matches processed")
            if not result:
                return None
            totals, maps_played, winner_name, team1_name, team2_name, score_str, match_date, t1id, t2id = result
            return {
                "week": None, "date": match_date[:10] if match_date else None,
                "patch": None, "teamA": team1_name, "teamB": team2_name,
                "winner": winner_name,
                "score": score_str, "actual": totals, "games": maps_played,
                "_team1_id": t1id, "_team2_id": t2id,  # dropped before writing final output — see reconciliation below
            }

    processed = await asyncio.gather(*[process(m, canonical_name_by_team_id) for m in matches_to_process])
    for entry in processed:
        if entry and entry["actual"]:
            past_matches.append(entry)
    print(f"  {len(past_matches)} matches with real player stats captured\n")

    # ---- Discover which teams are actually notable right now, from the
    # matches just processed — no hardcoded list, this naturally scales to
    # however many teams are playing at an accepted tier currently. ----
    team_id_by_current_name = {}
    for m in past_matches:
        if m.get("_team1_id") is not None:
            team_id_by_current_name[m["teamA"]] = m["_team1_id"]
        if m.get("_team2_id") is not None:
            team_id_by_current_name[m["teamB"]] = m["_team2_id"]
    discovered_team_ids = set(team_id_by_current_name.values())
    print(f"Discovered {len(discovered_team_ids)} distinct teams currently playing notable matches\n")

    # ---- Upcoming matches: today's global feed (notable-match filtered,
    # catches any team with no recent finished() result) plus full
    # multi-day schedules for every discovered team. Also builds
    # short_name_by_team_id, used below to reconcile teams_payload's
    # clan_name-based (long-form) names against the short form these
    # schedule endpoints use — confirmed via real output that the two
    # disagree (e.g. "Team Falcons" vs "Falcons"). ----
    short_name_by_team_id = {}
    seen_upcoming_ids = set()

    def add_upcoming(m):
        mid = m.get("id")
        if mid in seen_upcoming_ids:
            return
        team1, team2 = extract_match_team_names(m)
        t1id, t2id = m.get("team1_id"), m.get("team2_id")
        if t1id is not None and team1:
            short_name_by_team_id[t1id] = team1
        if t2id is not None and team2:
            short_name_by_team_id[t2id] = team2
        if not team1 or not team2 or "TBD" in (team1, team2):
            return
        seen_upcoming_ids.add(mid)
        upcoming_matches.append({"date": m.get("start_date") or m.get("date"), "teamA": team1, "teamB": team2, "block": None})

    print("Fetching today's global matches (notable-match filtered)...")
    try:
        today_batch = await cs2.get_todays_matches()
    except Exception as e:
        print(f"  ! get_todays_matches() failed: {e}", file=sys.stderr)
        today_batch = None
    today_matches = (today_batch.get("results", today_batch) if isinstance(today_batch, dict) else today_batch) if today_batch else []
    if today_matches:
        print_star_distribution(today_matches, "today's global matches")
    for m in today_matches:
        if is_notable_match(m):
            add_upcoming(m)
    print(f"  {len(upcoming_matches)} from today's notable-match-filtered global feed\n")

    print(f"Fetching multi-day schedules for {len(discovered_team_ids)} discovered teams...")
    for team_id in discovered_team_ids:
        try:
            sched = await cs2.get_team_upcoming_matches(team_id)
        except Exception as e:
            print(f"  ! get_team_upcoming_matches({team_id}) failed: {e}", file=sys.stderr)
            continue
        matches = sched.get("results", sched) if isinstance(sched, dict) else sched
        for m in (matches or []):
            add_upcoming(m)
    print(f"  {len(upcoming_matches)} total upcoming matches (today's global feed + discovered teams' schedules)\n")

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

    # ---- Reconcile names: rename any teams_payload entry to the short
    # form (matching upcoming_matches) wherever a team_id match confirms
    # they're the same team, so future-match lookups succeed. Applied to
    # both teams_payload and past_matches for consistency. ----
    name_rename_map = {}
    for old_name in list(teams_payload.keys()):
        team_id = team_id_by_current_name.get(old_name)
        if team_id is not None and team_id in short_name_by_team_id:
            new_name = short_name_by_team_id[team_id]
            if new_name != old_name:
                name_rename_map[old_name] = new_name

    if name_rename_map:
        print(f"Reconciling {len(name_rename_map)} team name(s) to the short form used by "
              f"upcoming/schedule data: {name_rename_map}\n")

    new_teams_payload = {}
    for old_name, team_data in teams_payload.items():
        new_name = name_rename_map.get(old_name, old_name)
        if new_name in new_teams_payload:
            existing_names = {p["name"] for p in new_teams_payload[new_name]["players"]}
            for p in team_data["players"]:
                if p["name"] not in existing_names:
                    new_teams_payload[new_name]["players"].append(p)
        else:
            new_teams_payload[new_name] = team_data
    teams_payload = new_teams_payload

    for m in past_matches:
        m["teamA"] = name_rename_map.get(m["teamA"], m["teamA"])
        m["teamB"] = name_rename_map.get(m["teamB"], m["teamB"])
        if m["winner"] in name_rename_map:
            m["winner"] = name_rename_map[m["winner"]]
        m["actual"] = {name_rename_map.get(k, k): v for k, v in m["actual"].items()}
        m.pop("_team1_id", None)
        m.pop("_team2_id", None)

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
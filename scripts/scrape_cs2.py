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
from datetime import datetime, timedelta, timezone

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


async def fetch_team_recent_matches(session, team_id, limit=10):
    """Direct, team-scoped match history — the real endpoint+params found
    in cs2api's own get_team_matches() source (confirmed broken only by a
    self._make_request vs self._api._make_request typo in that package,
    not because the request itself is wrong). Confirmed via live testing
    to correctly return a specific team's own matches, unlike scanning the
    global finished() sample, which only surfaces a team if they happened
    to appear in the last ~100 notable results.

    Response shape here nests team info as team1/team2 objects (via the
    with=teams expansion) rather than flat team1_id/team2_id fields used
    elsewhere in this file — but downstream code (process()) only needs
    each match's "slug", which is present either way, so no further
    normalization is needed."""
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=180)
    params = {
        "scope": "widget-map-pool",
        "page[offset]": "0",
        "page[limit]": str(limit),
        "sort": "-start_date",
        "filter[matches.status][in]": "finished",
        "filter[matches.team_ids][overlap]": str(team_id),
        "filter[matches.start_date][lt]": today.isoformat(),
        "filter[matches.start_date][gt]": start_date.isoformat(),
        "filter[matches.discipline_id][eq]": "1",
        "with": "teams,tournament,ai_predictions,games,match_maps",
    }
    data = await bo3_get(session, "/matches", params=params)
    if not data:
        return []
    return data.get("results", data) if isinstance(data, dict) else data


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
    # however many teams are playing at an accepted tier currently.
    # Tracks EVERY name variant seen per team_id (not just one) — clan_name
    # is confirmed NOT stable across different matches for the same team
    # (e.g. "MIBR" vs "MIBR!LOS", "Luminosity" vs "Luminosity Gaming" turned
    # out to be the same team_id with different clan_name strings depending
    # on which specific match record you look at), so reconciliation below
    # needs the full list to catch this, not just one name per ID. ----
    names_seen_by_id = {}
    for m in past_matches:
        if m.get("_team1_id") is not None:
            names_seen_by_id.setdefault(m["_team1_id"], []).append(m["teamA"])
        if m.get("_team2_id") is not None:
            names_seen_by_id.setdefault(m["_team2_id"], []).append(m["teamB"])
    discovered_team_ids = set(names_seen_by_id.keys())
    multi_name_ids = {tid: names for tid, names in names_seen_by_id.items() if len(set(names)) > 1}
    if multi_name_ids:
        print(f"  [debug] {len(multi_name_ids)} team(s) have inconsistent naming across matches "
              f"in the source data itself: { {tid: list(set(n)) for tid, n in multi_name_ids.items()} }")
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
    upcoming_stats = {"raw": 0, "duplicate": 0, "tbd_excluded": 0, "added": 0}

    def add_upcoming(m):
        mid = m.get("id")
        upcoming_stats["raw"] += 1
        if mid in seen_upcoming_ids:
            upcoming_stats["duplicate"] += 1
            return
        team1, team2 = extract_match_team_names(m)
        t1id, t2id = m.get("team1_id"), m.get("team2_id")
        if t1id is not None and team1:
            short_name_by_team_id[t1id] = team1
        if t2id is not None and team2:
            short_name_by_team_id[t2id] = team2
        if not team1 or not team2 or "TBD" in (team1, team2):
            upcoming_stats["tbd_excluded"] += 1
            return
        seen_upcoming_ids.add(mid)
        upcoming_stats["added"] += 1
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
    print(f"  {len(upcoming_matches)} from today's notable-match-filtered global feed "
          f"(of {len(today_matches)} raw matches today, before the notable-match filter)\n")

    print(f"Fetching multi-day schedules for {len(discovered_team_ids)} discovered teams...")
    schedule_fetch_failures = 0
    schedule_raw_total = 0
    for team_id in discovered_team_ids:
        try:
            sched = await cs2.get_team_upcoming_matches(team_id)
        except Exception as e:
            schedule_fetch_failures += 1
            print(f"  ! get_team_upcoming_matches({team_id}) failed: {e}", file=sys.stderr)
            continue
        matches = sched.get("results", sched) if isinstance(sched, dict) else sched
        schedule_raw_total += len(matches or [])
        for m in (matches or []):
            add_upcoming(m)
    print(f"  {schedule_raw_total} raw matches across all team schedules "
          f"({schedule_fetch_failures} team(s) failed to fetch)")
    print(f"  [debug] upcoming funnel: {upcoming_stats['raw']} raw seen -> "
          f"{upcoming_stats['duplicate']} duplicate, {upcoming_stats['tbd_excluded']} TBD-excluded, "
          f"{upcoming_stats['added']} added")
    print(f"  {len(upcoming_matches)} total upcoming matches (today's global feed + discovered teams' schedules)\n")

    # ---- Reconcile names BEFORE aggregating player stats, not after —
    # every name variant seen for a given team_id gets mapped to ONE
    # canonical name (the short form matching upcoming_matches when known,
    # otherwise the most-recently-seen clan_name for that ID) and applied
    # to past_matches directly. This has to happen before the player-stat
    # averaging below, not as a post-hoc merge on teams_payload — merging
    # two already-averaged player lists after the fact would silently drop
    # whichever name variant's matches got processed second, understating
    # a player's real per-game average instead of reflecting their full
    # match history. ----
    canonical_name_by_id = {}
    for tid, names in names_seen_by_id.items():
        canonical_name_by_id[tid] = short_name_by_team_id.get(tid, names[0])

    name_rename_map = {}
    for tid, names in names_seen_by_id.items():
        canonical = canonical_name_by_id[tid]
        for name in set(names):
            if name != canonical:
                name_rename_map[name] = canonical

    if name_rename_map:
        print(f"Reconciling {len(name_rename_map)} team name variant(s) to one canonical name each: "
              f"{name_rename_map}\n")

    for m in past_matches:
        m["teamA"] = name_rename_map.get(m["teamA"], m["teamA"])
        m["teamB"] = name_rename_map.get(m["teamB"], m["teamB"])
        if m["winner"] in name_rename_map:
            m["winner"] = name_rename_map[m["winner"]]
        m["actual"] = {name_rename_map.get(k, k): v for k, v in m["actual"].items()}
        m.pop("_team1_id", None)
        m.pop("_team2_id", None)

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

    # ---- Opponent backfill: any team appearing in upcoming_matches that
    # still has no roster data (genuinely outside the tier/star-notable set
    # that drove discovery — e.g. a team whose OWN recent matches didn't
    # individually clear the notability bar, even though they're currently
    # scheduled against a team that did). Without this, these matches would
    # permanently show "roster data not loaded" no matter how discovery is
    # tuned, since they'd never appear via the tier/star filter on their own.
    # Reuses the SAME global finished() results already fetched (via
    # `results`, still in scope) — no extra scanning needed. ----
    covered_names = set(teams_payload.keys())
    unresolved_opponents = sorted({
        name for m in upcoming_matches for name in (m["teamA"], m["teamB"])
        if name not in covered_names
    })
    if unresolved_opponents:
        print(f"Backfilling roster data for {len(unresolved_opponents)} opponent(s) with no roster yet: "
              f"{unresolved_opponents}")
        BACKFILL_MATCHES_PER_TEAM = 3
        backfill_matches = []
        for name in unresolved_opponents:
            try:
                found = await cs2.search_teams(name)
            except Exception as e:
                print(f"  ! search_teams({name!r}) failed: {e}", file=sys.stderr)
                continue
            candidates = found.get("results", found) if isinstance(found, dict) else found
            if not candidates:
                print(f"  ! no team found for opponent {name!r} — will keep showing "
                      f"'roster data not loaded' for this one")
                continue
            exact = next((t for t in candidates if t.get("name") == name), None)
            team = exact or candidates[0]
            opp_id = team["id"]
            short_name_by_team_id[opp_id] = name
            # Direct, team-scoped lookup — no longer dependent on whether
            # this team happened to appear in the ~100-match global sample
            # (confirmed via live testing: DENDELE and Inner Circle both
            # had zero matches in that sample, but this endpoint correctly
            # returns their real match history directly).
            their_matches = await fetch_team_recent_matches(session, opp_id, limit=BACKFILL_MATCHES_PER_TEAM)
            print(f"  {name!r} -> id={opp_id}, {len(their_matches)} recent match(es) found to backfill from")
            backfill_matches.extend(their_matches)

        if backfill_matches:
            backfill_processed = await asyncio.gather(
                *[process(m, short_name_by_team_id) for m in backfill_matches]
            )
            added = 0
            for entry in backfill_processed:
                if entry and entry["actual"]:
                    entry["teamA"] = short_name_by_team_id.get(entry.pop("_team1_id", None), entry["teamA"])
                    entry["teamB"] = short_name_by_team_id.get(entry.pop("_team2_id", None), entry["teamB"])
                    past_matches.append(entry)
                    added += 1
            print(f"  added {added} backfilled match(es) with real player stats\n")

            # Fold the newly-backfilled matches into teams_payload directly
            # — same aggregation logic as the main pass, scoped to just
            # these new teams so it doesn't redo work already done.
            for m in past_matches[-added:] if added else []:
                for side in ("teamA", "teamB"):
                    team_name = m[side]
                    if team_name not in unresolved_opponents:
                        continue  # only building entries for the teams we just backfilled
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
                            "cur": {"g": total_games, "k": total_k / total_games, "d": total_d / total_games,
                                    "a": total_a / total_games, "kp": 0},
                            "hist": None,
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
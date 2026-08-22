#!/usr/bin/env python3
"""
Scrapes VLR.gg for VCT (Valorant Champions Tour) match data across all four
franchised regions, writing valorant_data.json in the same shape as the LCS
scraper's data.json (regions -> teams + past_matches + upcoming_matches),
so the app can reuse the same UI components across both games.

Architecturally different from the LCS scraper in one useful way: there's no
separate "player list" aggregate page or team-roster page here. Team/player
association comes directly from each match page (which clearly groups
players under team headers), and season aggregates are computed by summing
per-match stats — the same technique the app already uses client-side for
point-in-time projections, just done once here to seed "cur"/"hist" fields.

VCT is organized as discrete events (Kickoff -> Stage 1 -> Stage 2 ->
Champions) rather than a continuous split like LCS/LEC. REGIONS below maps
each region to its current and prior *event* IDs — these need manual
updates as new events start. VLR.gg doesn't expose a "give me whatever's
current" lookup any more than gol.gg does for LCK/LPL splits.

Known uncertainty flagged with diagnostics: the exact CSS class VLR uses to
mark a player's "all rounds combined" K/D/A (as opposed to the attack-only
or defense-only split shown in the same cell) was inferred from general
knowledge of the site rather than verified against raw HTML, since this
script was written without live code execution against the target site.
If diagnostics show 0 players extracted from a match, that's the first
thing to check.
"""
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

BASE = "https://www.vlr.gg"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# Counts matches that still extract 0 players despite the fix, as a safety
# net — should stay at 0 now, but worth flagging loudly if the site's HTML
# structure changes again in the future.
_ZERO_ROW_MATCH_COUNT = 0

# Current (Stage 2) and prior (Stage 1) event IDs per region, 2026 season.
# Update these when a region moves to its next stage/event.
REGIONS = {
    "VCT Americas": {"current": 2977, "historical": 2860},
    "VCT EMEA": {"current": 2976, "historical": 2863},
    "VCT Pacific": {"current": 2776, "historical": 2775},
    "VCT China": {"current": 2978, "historical": 2864},
}

COLOR_PALETTE = [
    "#e0c341", "#8a9bb5", "#4fa8e0", "#7ed957", "#c95050", "#3fbf7f",
    "#1e90c8", "#b06fd1", "#e08a3f", "#5fd9c9", "#d15f9a", "#9fd15f",
]


def get(url, retries=3):
    last_status = None
    for attempt in range(retries):
        r = requests.get(url, headers=HEADERS, timeout=20)
        last_status = r.status_code
        if r.status_code == 200:
            return r.text
        time.sleep(2)
    print(f"  ! GET {url} failed after {retries} tries, last status {last_status}", file=sys.stderr)
    r.raise_for_status()


def parse_match_ids(event_id):
    """Enumerates match paths linked from an event's match-list page. Returns
    the REAL href (with its actual slug), not just the numeric ID — an
    earlier version reconstructed URLs as "/{id}/x/" assuming VLR.gg ignores
    slug text and resolves by ID alone. Live diagnostics showed that
    assumption was wrong: every match came back "unplayed" regardless of
    its actual status, meaning the placeholder slug wasn't resolving to real
    match content. Using the exact scraped path avoids the guess entirely."""
    url = f"{BASE}/event/matches/{event_id}/x/?series_id=all"
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    match_paths = []
    for a in soup.find_all("a", href=re.compile(r"^/\d+/")):
        href = a["href"]
        m = re.match(r"^/(\d+)/", href)
        if not m:
            continue
        mid = int(m.group(1))
        if mid in seen:
            continue
        seen.add(mid)
        match_paths.append({"match_id": mid, "path": href})
    print(f"  event {event_id}: found {len(match_paths)} match links", file=sys.stderr)
    if match_paths:
        print(f"    sample path: {match_paths[0]['path']}", file=sys.stderr)
    return match_paths


def parse_match(match_id, match_path):
    """Fetches one match page using its real scraped path. Always returns a
    dict describing the match; 'played' is False for matches that haven't
    happened yet (no final score available), in which case 'actual'/'patch'
    available), in which case 'actual'/'patch' are None."""
    url = f"{BASE}{match_path}"
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")

    team_links = soup.find_all("a", href=re.compile(r"^/team/\d+/"))
    team_names = []
    for a in team_links:
        name = a.get_text(strip=True)
        if name and name not in team_names:
            team_names.append(name)
        if len(team_names) == 2:
            break
    if len(team_names) < 2:
        print(f"  ! match {match_id}: found {len(team_names)} distinct team links, expected 2", file=sys.stderr)
        return None
    team_a, team_b = team_names[0], team_names[1]

    header_text = soup.get_text(" ", strip=True)[:3000]
    score_m = re.search(r"\b(\d+)\s*:\s*(\d+)\b", header_text)
    played = bool(score_m) and not (score_m and score_m.group(1) == "0" and score_m.group(2) == "0" and "Bo" not in header_text)
    score_a = int(score_m.group(1)) if score_m else None
    score_b = int(score_m.group(2)) if score_m else None
    if not played:
        # Diagnostic for the "every match came back unplayed" failure mode —
        # shows exactly what the page looked like near the top so a wrong
        # 'played' determination is distinguishable from a genuinely
        # unplayed match at a glance in the log.
        print(f"  match {match_id}: played=False (score_m={'matched ' + score_m.group(0) if score_m else 'no match'}) "
              f"— header sample: {header_text[:200]!r}", file=sys.stderr)

    date_iso = None
    ts_m = re.search(r'data-utc-ts="([^"]+)"', html)
    if ts_m:
        try:
            date_iso = datetime.fromisoformat(ts_m.group(1).replace(" ", "T")).strftime("%Y-%m-%d")
        except ValueError:
            digits = re.match(r"(\d+)", ts_m.group(1))
            if digits:
                date_iso = datetime.fromtimestamp(int(digits.group(1)), tz=timezone.utc).strftime("%Y-%m-%d")

    patch_m = re.search(r"Patch\s+(\d+\.\d+)", html)
    patch = patch_m.group(1) if patch_m else None

    result = {
        "match_id": match_id, "teamA": team_a, "teamB": team_b,
        "date": date_iso, "played": False, "patch": patch,
        "scoreA": score_a, "scoreB": score_b, "actual": None, "maps_played": None,
    }

    if not played or score_a is None or (score_a == 0 and score_b == 0):
        return result

    # Confirmed via a real deep-debug dump against live match 706349: each
    # player's row is div.ovw-row (a direct, reliable target — no ancestor-
    # walking needed). Two things were wrong before:
    #   1. The K/D/A cell isn't "N/N/N" — it's "N N N / N N N / N N N"
    #      (All/Attack/Defend values per stat, slash only between K, D, A
    #      groups), so a bare \d+/\d+/\d+ pattern never matched at all.
    #   2. link.get_text() concatenated the name + team-tag divs into one
    #      string (e.g. "NeonLEV") since they're both inside the same <a>
    #      with no separator — names need pulling from .ovw-player-name
    #      specifically, not the whole link's text.
    all_rounds_kda_re = re.compile(
        r"(\d+)\s+\d+\s+\d+\s*/\s*(\d+)\s+\d+\s+\d+\s*/\s*(\d+)\s+\d+\s+\d+"
    )
    player_links = soup.find_all("a", href=re.compile(r"^/player/\d+/"))

    if not player_links:
        print(f"  ! match {match_id}: played but 0 player links found on the whole page "
              f"({len(html)} bytes)", file=sys.stderr)
        return result

    totals = {team_a: {}, team_b: {}}
    map_occurrence_count = {team_a: {}, team_b: {}}  # per-player count of maps counted so far, capped at 2
    tag_to_team = {}  # first distinct team-tag seen -> team_a, second -> team_b
    unresolved = 0
    for link in player_links:
        name_div = link.find(class_="ovw-player-name")
        tag_div = link.find(class_="ovw-player-tag")
        if not name_div or not tag_div:
            unresolved += 1
            continue
        name = name_div.get_text(strip=True)
        tag = tag_div.get_text(strip=True)
        if not name:
            unresolved += 1
            continue

        row = link.find_parent("div", class_="ovw-row")
        if row is None:
            unresolved += 1
            continue
        m = all_rounds_kda_re.search(row.get_text(" ", strip=True))
        if not m:
            unresolved += 1
            continue
        k, d, a = (int(x) for x in m.groups())

        if tag not in tag_to_team:
            if len(tag_to_team) == 0:
                tag_to_team[tag] = team_a
            elif len(tag_to_team) == 1:
                tag_to_team[tag] = team_b
            else:
                # A 3rd distinct tag showing up (roster substitution mid-series?)
                # — fall back to whichever side has fewer entries so far.
                tag_to_team[tag] = team_a if len(totals[team_a]) <= len(totals[team_b]) else team_b
        side_team = tag_to_team[tag]

        slot = totals[side_team].setdefault(name, {"k": 0, "d": 0, "a": 0})
        maps_counted = map_occurrence_count[side_team].setdefault(name, 0)
        if maps_counted >= 2:
            # This is the app's convention across both games: only maps/games
            # 1 and 2 of a series count toward "actual" totals, regardless of
            # whether the series went to a 3rd map — matches series_g1_g2_kills
            # on the LCS side exactly. Document order on this page follows map
            # order (map 1's roster, then map 2's, then map 3's if it happened),
            # so the 3rd occurrence of a given player is always their map-3 stats.
            continue
        slot["k"] += k
        slot["d"] += d
        slot["a"] += a
        map_occurrence_count[side_team][name] = maps_counted + 1

    total_players = sum(len(v) for v in totals.values())
    global _ZERO_ROW_MATCH_COUNT
    if total_players == 0:
        _ZERO_ROW_MATCH_COUNT += 1
        if _ZERO_ROW_MATCH_COUNT <= 3:  # cap verbose output — deep debug above already covers the detail
            print(f"  ! match {match_id}: {len(player_links)} player links found but extracted 0 rows "
                  f"({unresolved} unresolved)", file=sys.stderr)
    elif unresolved > 0:
        print(f"  match {match_id}: {total_players} rows extracted OK, {unresolved} player links unresolved "
              f"(likely duplicate/nav links, not real stat rows)", file=sys.stderr)

    result["played"] = True
    result["actual"] = totals
    # Fixed at 2 to match the "maps/games 1+2 only" convention used across
    # both games — every Bo3/Bo5 series has at least 2 maps by definition,
    # so this is safe even though the series itself may have gone longer.
    result["maps_played"] = 2
    return result



def build_region_payload(region_key, current_event, historical_event):
    print(f"\n=== {region_key} (event {current_event}, prior event {historical_event}) ===")

    def collect(event_id, label):
        print(f"Fetching {label} match list (event {event_id})...")
        matches = parse_match_ids(event_id)
        played, upcoming = [], []
        # Same fix as the LCS scraper: with 40-60+ matches per event and
        # each one a full page fetch, sequential + a fixed sleep between
        # each was the dominant cost. A bounded thread pool cuts wall time
        # roughly in proportion to worker count while staying modest enough
        # not to look like an attack on the site.
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(parse_match, entry["match_id"], entry["path"]): entry for entry in matches}
            for future in as_completed(futures):
                entry = futures[future]
                try:
                    m = future.result()
                except Exception as e:
                    print(f"  ! match {entry['match_id']} failed: {e}", file=sys.stderr)
                    continue
                if m is None:
                    continue
                (played if m["played"] else upcoming).append(m)
        print(f"  {label}: {len(played)} played, {len(upcoming)} upcoming/unplayed")
        return played, upcoming

    cur_played, cur_upcoming = collect(current_event, "current event")
    hist_played, _ = collect(historical_event, "historical event")

    # Build teams payload by aggregating each player's stats across all
    # matches in the CURRENT event (for "cur") and the HISTORICAL event
    # (for "hist") — no separate roster/player-list page needed, since team
    # association is already known per-match.
    def aggregate(matches):
        # name -> {team, k, d, a, games}
        agg = {}
        for m in matches:
            for team, players in m["actual"].items():
                for name, kda in players.items():
                    slot = agg.setdefault(name, {"team": team, "k": 0, "d": 0, "a": 0, "games": 0})
                    slot["team"] = team  # last-seen team wins (handles roster moves reasonably)
                    slot["k"] += kda["k"]
                    slot["d"] += kda["d"]
                    slot["a"] += kda["a"]
                    slot["games"] += m["maps_played"]
        return agg

    cur_agg = aggregate(cur_played)
    hist_agg = aggregate(hist_played)

    teams = {}
    for name, cur in cur_agg.items():
        team = cur["team"]
        if team not in teams:
            color = COLOR_PALETTE[len(teams) % len(COLOR_PALETTE)]
            teams[team] = {"color": color, "players": []}
        hist = hist_agg.get(name)
        g = cur["games"] or 1
        entry = {
            "name": name, "role": None,
            "cur": {"g": cur["games"], "k": cur["k"] / g, "d": cur["d"] / g, "a": cur["a"] / g, "kp": 0},
            "hist": None,
        }
        if hist and hist["games"] > 0:
            hg = hist["games"]
            entry["hist"] = {"g": hg, "k": hist["k"] / hg, "d": hist["d"] / hg, "a": hist["a"] / hg, "kp": 0}
        teams[team]["players"].append(entry)
    print(f"  built payload for {len(teams)} teams: {list(teams.keys())}")

    past_matches = []
    for m in cur_played:
        winner = m["teamA"] if (m["scoreA"] or 0) > (m["scoreB"] or 0) else m["teamB"]
        past_matches.append({
            "week": None, "date": m["date"], "patch": m["patch"],
            "teamA": m["teamA"], "teamB": m["teamB"],
            "winner": winner, "score": f"{m['scoreA']}-{m['scoreB']}",
            "actual": m["actual"], "games": m["maps_played"],
        })

    upcoming_matches = [
        {"date": m["date"], "teamA": m["teamA"], "teamB": m["teamB"], "block": None}
        for m in cur_upcoming if m["teamA"] != "TBD" and m["teamB"] != "TBD"
    ]

    return {"teams": teams, "past_matches": past_matches, "upcoming_matches": upcoming_matches}


def main():
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regions": {},
    }
    for region_key, cfg in REGIONS.items():
        try:
            payload["regions"][region_key] = build_region_payload(
                region_key, cfg["current"], cfg["historical"]
            )
        except Exception as e:
            print(f"! region {region_key} failed entirely: {e}", file=sys.stderr)

    with open("valorant_data.json", "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\nWrote valorant_data.json with regions: {list(payload['regions'].keys())}")
    if _ZERO_ROW_MATCH_COUNT > 0:
        print(f"NOTE: {_ZERO_ROW_MATCH_COUNT} matches extracted 0 player rows total this run "
              f"(see [DEEP DEBUG] block above for the actual DOM structure)", file=sys.stderr)


if __name__ == "__main__":
    main()

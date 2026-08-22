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
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

BASE = "https://www.vlr.gg"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

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
    """Enumerates match IDs linked from an event's match-list page. This is
    the only job of this function — score/date/patch/stats all come from
    the individual match page, which is more reliable than trying to parse
    the compressed list-row text."""
    url = f"{BASE}/event/matches/{event_id}/x/?series_id=all"
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    match_ids = []
    for a in soup.find_all("a", href=re.compile(r"^/\d+/")):
        m = re.match(r"^/(\d+)/", a["href"])
        if not m:
            continue
        mid = int(m.group(1))
        if mid in seen:
            continue
        seen.add(mid)
        match_ids.append(mid)
    print(f"  event {event_id}: found {len(match_ids)} match links", file=sys.stderr)
    return match_ids


def parse_match(match_id):
    """Fetches one match page. Always returns a dict describing the match;
    'played' is False for matches that haven't happened yet (no final score
    available), in which case 'actual'/'patch' are None."""
    url = f"{BASE}/{match_id}/x/"
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

    totals = {team_a: {}, team_b: {}}
    tables = soup.find_all("table")
    stat_tables = [t for t in tables if t.find("a", href=re.compile(r"^/player/\d+/"))]
    if not stat_tables:
        print(f"  ! match {match_id}: played but 0 stat tables found (0 of {len(tables)} tables had a player link)",
              file=sys.stderr)
        return result

    for i, table in enumerate(stat_tables):
        side_team = team_a if i % 2 == 0 else team_b
        for row in table.find_all("tr"):
            name_link = row.find("a", href=re.compile(r"^/player/\d+/"))
            if not name_link:
                continue
            name = name_link.get_text(strip=True)
            kda = None
            both_span = row.find(class_=re.compile("mod-both"))
            if both_span:
                m = re.search(r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)", both_span.get_text(" ", strip=True))
                if m:
                    kda = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if kda is None:
                m = re.search(r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)", row.get_text(" ", strip=True))
                if m:
                    kda = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if kda is None:
                continue
            k, d, a = kda
            slot = totals[side_team].setdefault(name, {"k": 0, "d": 0, "a": 0})
            slot["k"] += k
            slot["d"] += d
            slot["a"] += a

    total_players = sum(len(v) for v in totals.values())
    if total_players == 0:
        print(f"  ! match {match_id}: {len(stat_tables)} stat tables found but extracted 0 players — "
              f"K/D/A cell selector likely needs adjusting", file=sys.stderr)
        first_table_sample = str(stat_tables[0])[:600] if stat_tables else "n/a"
        print(f"    first stat table sample: {first_table_sample}", file=sys.stderr)

    result["played"] = True
    result["actual"] = totals
    result["maps_played"] = (score_a or 0) + (score_b or 0)
    return result


def build_region_payload(region_key, current_event, historical_event):
    print(f"\n=== {region_key} (event {current_event}, prior event {historical_event}) ===")

    def collect(event_id, label):
        print(f"Fetching {label} match list (event {event_id})...")
        ids = parse_match_ids(event_id)
        played, upcoming = [], []
        for mid in ids:
            try:
                m = parse_match(mid)
            except Exception as e:
                print(f"  ! match {mid} failed: {e}", file=sys.stderr)
                continue
            if m is None:
                continue
            (played if m["played"] else upcoming).append(m)
            time.sleep(0.5)
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


if __name__ == "__main__":
    main()

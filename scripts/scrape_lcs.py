#!/usr/bin/env python3
"""
Scrapes gol.gg for the four major LoL regions' current + prior-split player
stats and recent match results, and writes data.json for the kill-projector
app, nested by region: {"LCS": {...}, "LEC": {...}, "LCK": {...}, "LPL": {...}}

Run via GitHub Actions on a schedule. See ../.github/workflows/scrape.yml

Each region's gol.gg tournament naming differs — LCS/LEC still use Spring/
Summer, but LCK is mid "Rounds 3-4" (prior stretch: "Rounds 1-2") and LPL is
on "Split 3" (prior: "Split 2") this year. Update REGIONS below each time a
region's split rolls over — gol.gg doesn't expose a "current tournament"
lookup, so this has to be maintained by hand a few times a year.
"""
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

BASE = "https://gol.gg"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# gol.gg's exact tournament-name strings for each region's current split and
# the immediately prior one (used as the "historical" blend in the app).
REGIONS = {
    "LCS": {"current": "LCS 2026 Summer", "historical": "LCS 2026 Spring"},
    "LEC": {"current": "LEC 2026 Summer Season", "historical": "LEC 2026 Spring Season"},
    "LCK": {"current": "LCK 2026 Rounds 3-4", "historical": "LCK 2026 Rounds 1-2"},
    "LPL": {"current": "LPL 2026 Split 3", "historical": "LPL 2026 Split 2"},
    "LCP": {"current": "LCP 2026 Split 3", "historical": "LCP 2026 Split 2"},
    "CBLOL": {"current": "CBLOL 2026 Split 2", "historical": "CBLOL 2026 Split 1"},
    "TCL": {"current": "TCL 2026 Summer", "historical": "TCL 2026 Spring"},
    # LLA is intentionally omitted — confirmed via research (not an
    # oversight) that it wasn't reinstated as a standalone league for
    # 2026 after the LTA merger dissolved; its former teams (Leviatán,
    # etc.) now compete within LCS/CBLOL directly and will already
    # appear there.
}

# Cycled per-team as new teams are discovered — not hand-picked brand colors
# for every org across 4 regions, just enough visual distinction in the app.
COLOR_PALETTE = [
    "#e0c341", "#8a9bb5", "#4fa8e0", "#7ed957", "#c95050", "#3fbf7f",
    "#1e90c8", "#b06fd1", "#e08a3f", "#5fd9c9", "#d15f9a", "#9fd15f",
]
ROLE_ORDER = {"Top": "TOP", "Jungle": "JNG", "Mid": "MID", "ADC": "BOT", "Support": "SUP"}


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


def parse_player_list(tournament):
    """Pulls the rich per-player stats table (avg K/D/A, KP%, games played).
    Note: this table has no Team/Role columns — those get filled in separately
    via parse_team_rosters()."""
    url = f"{BASE}/players/list/season-ALL/split-ALL/tournament-{tournament.replace(' ', '%20')}/"
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="table_list")
    if not table:
        print(f"  ! no table.table_list found for {tournament}", file=sys.stderr)
        all_tables = soup.find_all("table")
        table = all_tables[0] if all_tables else None
    players = {}
    if not table:
        return players
    rows = table.find_all("tr")
    header_cells = rows[0].find_all(["th", "td"]) if rows else []
    header_labels = [c.get_text(strip=True) for c in header_cells]
    # Locate columns by header text instead of hardcoded positions — more
    # resilient to gol.gg reordering columns in future.
    def col(label):
        try:
            return header_labels.index(label)
        except ValueError:
            return None
    idx_games = col("Games")
    idx_k = col("Avg kills")
    idx_d = col("Avg deaths")
    idx_a = col("Avg assists")
    idx_kp = col("KP%")
    print(f"  {tournament}: columns -> games={idx_games} k={idx_k} d={idx_d} "
          f"a={idx_a} kp={idx_kp}", file=sys.stderr)
    parsed_ok = 0
    for i, row in enumerate(rows[1:], start=1):
        cells = row.find_all("td")
        if len(cells) < 5 or None in (idx_games, idx_k, idx_d, idx_a, idx_kp):
            continue
        try:
            name = cells[0].get_text(strip=True)
            games = int(cells[idx_games].get_text(strip=True))
            k = float(cells[idx_k].get_text(strip=True))
            d = float(cells[idx_d].get_text(strip=True))
            a = float(cells[idx_a].get_text(strip=True))
            kp = float(cells[idx_kp].get_text(strip=True).replace("%", ""))
        except (ValueError, IndexError) as e:
            if i == 1:
                print(f"    row 1 parse failed ({e}); cells: "
                      f"{[c.get_text(strip=True) for c in cells]}", file=sys.stderr)
            continue
        parsed_ok += 1
        # team/role filled in by parse_team_rosters(); placeholder for now
        players[name] = {
            "name": name, "team": None, "role": None,
            "g": games, "k": k, "d": d, "a": a, "kp": kp,
        }
    print(f"    parsed {parsed_ok}/{max(len(rows) - 1, 0)} rows successfully", file=sys.stderr)
    return players


def parse_team_rosters(tournament):
    """gol.gg's team pages list each roster, conventionally in Top/Jungle/Mid/
    ADC/Support order. Used to fill in team/role fields the player-list table
    doesn't provide.

    Note: this page's links are relative to <base href="https://gol.gg/teams/">
    (e.g. "./team-stats/2812/..."), not root-relative — urljoin against that
    base is required, not naive string concatenation.
    """
    base_href = f"{BASE}/teams/"
    url = f"{BASE}/teams/list/season-ALL/split-ALL/tournament-{tournament.replace(' ', '%20')}/"
    html = get(url)
    print(f"  fetched teams-list page, {len(html)} bytes", file=sys.stderr)
    soup = BeautifulSoup(html, "html.parser")
    base_tag = soup.find("base", href=True)
    if base_tag:
        base_href = base_tag["href"]
    team_links = soup.find_all("a", href=re.compile(r"team-stats/\d+"))
    team_urls = {}
    for link in team_links:
        name = link.get_text(strip=True)
        if name and name not in team_urls:
            team_urls[name] = urljoin(base_href, link["href"])
    print(f"  found {len(team_urls)} teams: {list(team_urls.keys())}", file=sys.stderr)
    if not team_urls:
        all_links = soup.find_all("a", href=True)
        print(f"    total <a> tags on page: {len(all_links)}", file=sys.stderr)
        team_stats_like = [a["href"] for a in all_links if "team-stats" in a["href"]][:5]
        print(f"    hrefs containing 'team-stats': {team_stats_like}", file=sys.stderr)

    role_sequence = ["TOP", "JNG", "MID", "BOT", "SUP"]
    roster = {}  # player name -> {"team":..., "role":...}
    first_team_name = list(team_urls.keys())[0] if team_urls else None

    def fetch_roster(team_name, team_url):
        team_html = get(team_url)
        team_soup = BeautifulSoup(team_html, "html.parser")
        player_links = team_soup.find_all("a", href=re.compile(r"player-stats/\d+"))
        seen = []
        for pl in player_links:
            pname = pl.get_text(strip=True)
            if pname and pname not in seen:
                seen.append(pname)
        return team_name, seen

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_roster, name, url): name for name, url in team_urls.items()}
        for future in as_completed(futures):
            team_name = futures[future]
            try:
                team_name, seen = future.result()
            except Exception as e:
                print(f"  ! failed to fetch roster for {team_name}: {e}", file=sys.stderr)
                continue
            if team_name == first_team_name:  # diagnostic for just the first team
                print(f"    {team_name} roster order found: {seen}", file=sys.stderr)
            for idx, pname in enumerate(seen[:5]):
                roster[pname] = {"team": team_name, "role": role_sequence[idx] if idx < 5 else "SUB"}
    return roster


def clean_team_name(raw):
    """gol.gg's team header divs often read like 'Dignitas - LOSS' or
    'Dignitas- WIN' — strip the result suffix to get just the team name."""
    return re.sub(r"\s*-?\s*(WIN|LOSS)\s*$", "", raw, flags=re.IGNORECASE).strip()


def normalize_week_label(raw):
    """gol.gg's week column reads like 'WEEK4' during regular season, but
    during playoffs it's a round name like 'PLAYOFFS', 'QUARTERFINALS', or
    sometimes 'PLAYOFFS - RO8'. Preserve the actual label (title-cased, with
    a space inserted before a trailing week number) instead of stripping to
    digits-only, so playoff rounds don't silently collapse to blank/None."""
    raw = raw.strip()
    if not raw:
        return None
    m = re.match(r"^WEEK\s*(\d+)$", raw, re.IGNORECASE)
    if m:
        return f"Week {m.group(1)}"
    # Title-case everything else ("PLAYOFFS" -> "Playoffs",
    # "QUARTERFINALS" -> "Quarterfinals") but leave existing mixed-case or
    # already-formatted labels alone.
    return raw.title() if raw.isupper() else raw


def parse_match_list(tournament):
    """Returns list of completed series with gol.gg game IDs, most recent first.
    Rows for matches that haven't been played yet (score is a placeholder like
    '-' or 'vs') are skipped — those belong in the schedule, not past results.

    Cells are identified by CONTENT PATTERN (score looks like "2 - 0", date
    looks like "2026-08-16", patch looks like "16.16"), not fixed column
    position. gol.gg has added/reordered columns before without warning (a
    "Patch" column appeared between Week and Date at some point) — content
    matching survives that; position-based indexing silently breaks on it.
    """
    url = f"{BASE}/tournament/tournament-matchlist/{tournament.replace(' ', '%20')}/"
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")
    matches = []
    table = soup.find("table")
    if not table:
        print(f"  ! no table found on matchlist page for {tournament}", file=sys.stderr)
        return matches
    rows = table.find_all("tr")
    print(f"  {tournament} matchlist: {len(rows)} <tr> rows", file=sys.stderr)
    if rows:
        header_cells = rows[0].find_all(["th", "td"])
        print(f"    header: {[c.get_text(strip=True) for c in header_cells]}", file=sys.stderr)

    score_re = re.compile(r"^\d+\s*-\s*\d+$")
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    patch_re = re.compile(r"^\d+\.\d+[a-zA-Z]?$")

    skipped_unplayed = 0
    for i, row in enumerate(rows[1:], start=1):
        cells = row.find_all("td")
        if len(cells) < 5:
            if i == 1:
                print(f"    row 1 has only {len(cells)} cells: "
                      f"{[c.get_text(strip=True) for c in cells]}", file=sys.stderr)
            continue
        link = cells[0].find("a")
        if not link:
            continue
        game_id = re.search(r"/game/stats/(\d+)/", link["href"])
        if not game_id:
            continue

        texts = [c.get_text(strip=True) for c in cells]
        score_idx = next((idx for idx, t in enumerate(texts) if score_re.match(t)), None)
        if score_idx is None or score_idx < 1 or score_idx + 1 >= len(texts):
            skipped_unplayed += 1
            if i == 1:
                print(f"    row 1: no score-shaped cell found in {texts}", file=sys.stderr)
            continue
        score = texts[score_idx]
        team_left = texts[score_idx - 1]
        team_right = texts[score_idx + 1]
        score_norm = re.sub(r"\s+", "", score)

        date = next((t for t in texts if date_re.match(t)), None)
        patch = next((t for t in texts if patch_re.match(t)), None)
        # Week/round label: whatever's left over that isn't game/score/teams/
        # date/patch — typically the cell right after team_right.
        week_raw = texts[score_idx + 2] if score_idx + 2 < len(texts) and texts[score_idx + 2] not in (date, patch) else None
        week = normalize_week_label(week_raw) if week_raw else None

        if date is None:
            if i == 1:
                print(f"    row 1: no date-shaped cell found in {texts}", file=sys.stderr)
            continue

        matches.append({
            "base_game_id": int(game_id.group(1)),
            "team_left": team_left, "score": score_norm, "team_right": team_right,
            "week": week, "date": date, "patch": patch,
        })
    print(f"    {len(matches)} completed, {skipped_unplayed} unplayed/skipped", file=sys.stderr)
    return matches


def parse_game_kills(game_id):
    """Returns {team: {player: {"k":kills,"d":deaths,"a":assists}}} for a
    single game page.

    gol.gg's per-game table nests a lot of extra markup per player (rune and
    item breakdowns), which inflates naive <tr> counts and makes position-based
    row splitting unreliable. Instead: find every row that contains a link to
    a player's profile (unambiguous signal), and within that same row look for
    a standalone "N/N/N" KDA pattern. Each player cell also has a champion-icon
    link before the name link, so we take the *last* link in the cell, not the
    first, when extracting the name.
    """
    url = f"{BASE}/game/stats/{game_id}/page-game/"
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")
    result = {}
    blue = soup.find("div", class_="blue-line-header")
    red = soup.find("div", class_="red-line-header")
    for side in [blue, red]:
        if not side:
            continue
        team_name = clean_team_name(side.get_text(strip=True))
        result[team_name] = {}
    team_names = list(result.keys())
    if len(team_names) != 2:
        print(f"  ! game {game_id}: expected 2 teams from headers, got {team_names}", file=sys.stderr)
        return result

    kda_pattern = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)\s*$")
    parsed = []
    seen_names = set()
    for row in soup.find_all("tr"):
        player_links = row.find_all("a", href=re.compile(r"/players/player-stats/\d+/"))
        if not player_links:
            continue
        name = player_links[-1].get_text(strip=True)  # last link = name, not champion icon
        if not name or name in seen_names:
            continue
        # Look for a cell in this row whose text is EXACTLY a "N/N/N" pattern
        # (avoids accidentally matching item/rune stat text that merely
        # contains digits and slashes).
        kda = None
        for td in row.find_all("td"):
            m = kda_pattern.match(td.get_text(strip=True))
            if m:
                kda = {"k": int(m.group(1)), "d": int(m.group(2)), "a": int(m.group(3))}
                break
        if kda is None:
            continue
        seen_names.add(name)
        parsed.append((name, kda))

    if len(parsed) != 10:
        print(f"  ! game {game_id}: expected 10 players, parsed {len(parsed)}: {parsed}", file=sys.stderr)

    half = len(parsed) // 2 if len(parsed) >= 2 else 0
    for name, kda in parsed[:half]:
        result[team_names[0]][name] = kda
    for name, kda in parsed[half:]:
        result[team_names[1]][name] = kda

    return result


def series_g1_g2_kills(base_id, score):
    """Given the first game's ID, returns combined game-1 + game-2 K/D/A per
    player, per team. Works the same regardless of series length (Bo3 or
    Bo5) since it always fetches exactly the first two individual games."""
    g1 = parse_game_kills(base_id)
    g2 = parse_game_kills(base_id + 1)
    empty = {"k": 0, "d": 0, "a": 0}
    combined = {}
    for team in g1:
        combined[team] = {}
        for p, kda1 in g1[team].items():
            kda2 = g2.get(team, {}).get(p, empty)
            combined[team][p] = {
                "k": kda1["k"] + kda2["k"],
                "d": kda1["d"] + kda2["d"],
                "a": kda1["a"] + kda2["a"],
            }
    return combined


def build_teams_payload(cur_players, hist_players, roster):
    teams = {}
    unmatched = []
    for name, cur in cur_players.items():
        info = roster.get(name)
        if not info:
            unmatched.append(name)
            continue
        team, role = info["team"], info["role"]
        if team not in teams:
            color = COLOR_PALETTE[len(teams) % len(COLOR_PALETTE)]
            teams[team] = {"color": color, "players": []}
        hist = hist_players.get(name)
        entry = {
            "name": name, "role": role,
            "cur": {"g": cur["g"], "k": cur["k"], "d": cur["d"], "a": cur["a"], "kp": cur["kp"]},
            "hist": ({"g": hist["g"], "k": hist["k"], "d": hist["d"], "a": hist["a"], "kp": hist["kp"]}
                      if hist else None),
        }
        teams[team]["players"].append(entry)
    if unmatched:
        print(f"  ! {len(unmatched)} players had stats but no roster match: {unmatched}",
              file=sys.stderr)
    return teams


def scrape_region(region_key, current_tournament, historical_tournament):
    print(f"\n=== {region_key} ({current_tournament}) ===")

    print(f"Fetching team rosters (for team/role assignment)...")
    roster = parse_team_rosters(current_tournament)
    print(f"  {len(roster)} players matched to a team/role")

    print(f"Fetching current-split player stats...")
    cur_players = parse_player_list(current_tournament)
    print(f"  {len(cur_players)} players")

    print(f"Fetching historical-split player stats...")
    hist_players = parse_player_list(historical_tournament)
    print(f"  {len(hist_players)} players")

    teams_payload = build_teams_payload(cur_players, hist_players, roster)
    print(f"  built payload for {len(teams_payload)} teams: {list(teams_payload.keys())}")

    print(f"Fetching match list...")
    matches = parse_match_list(current_tournament)
    print(f"  {len(matches)} completed series found")

    # This is the dominant cost of the whole scrape — each match needs 2
    # page fetches (game 1 + game 2), and with 20-60+ matches per region
    # that's 100+ sequential requests if done one at a time. Fetching
    # several matches concurrently (bounded pool, not unlimited) cuts wall
    # time roughly in proportion to the worker count while staying modest
    # enough not to look like an attack on gol.gg.
    past_matches = []

    def fetch_one(m):
        kills = series_g1_g2_kills(m["base_game_id"], m["score"])
        left_score, right_score = (int(x) for x in m["score"].split("-"))
        winner = m["team_left"] if left_score > right_score else m["team_right"]
        return {
            "week": m["week"], "date": m["date"], "patch": m.get("patch"),
            "teamA": m["team_left"], "teamB": m["team_right"],
            "winner": winner, "score": m["score"],
            "actual": kills,
        }

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_one, m): m for m in matches}
        for future in as_completed(futures):
            m = futures[future]
            try:
                past_matches.append(future.result())
            except Exception as e:
                print(f"  ! skipped {m['team_left']} vs {m['team_right']}: {e}", file=sys.stderr)

    return {"teams": teams_payload, "past_matches": past_matches}


def main():
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regions": {},
    }
    for region_key, cfg in REGIONS.items():
        try:
            payload["regions"][region_key] = scrape_region(
                region_key, cfg["current"], cfg["historical"]
            )
        except Exception as e:
            print(f"! region {region_key} failed entirely: {e}", file=sys.stderr)
            # Leave the region out rather than writing partial garbage —
            # the app falls back to its bundled snapshot for a missing region.

    with open("data.json", "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\nWrote data.json with regions: {list(payload['regions'].keys())}")


if __name__ == "__main__":
    main()

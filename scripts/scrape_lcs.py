#!/usr/bin/env python3
"""
Scrapes gol.gg for LCS Summer 2026 (+ Spring 2026 historical) player stats
and recent match results, and writes data.json for the kill-projector app.

Run via GitHub Actions on a schedule. See ../.github/workflows/scrape.yml
"""
import json
import re
import time
import sys
from datetime import datetime, timezone
import requests
from bs4 import BeautifulSoup

BASE = "https://gol.gg"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; lcs-kill-projector/1.0)"}
SUMMER = "LCS 2026 Summer"
SPRING = "LCS 2026 Spring"

TEAM_COLORS = {
    "LYON": "#e0c341", "Sentinels": "#8a9bb5", "Cloud9": "#4fa8e0",
    "Shopify Rebellion": "#7ed957", "Dignitas": "#c95050", "FlyQuest": "#3fbf7f",
    "Team Liquid": "#1e90c8", "Disguised": "#b06fd1",
}
ROLE_ORDER = {"Top": "TOP", "Jungle": "JNG", "Mid": "MID", "ADC": "BOT", "Support": "SUP"}


def get(url, retries=3):
    for attempt in range(retries):
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code == 200:
            return r.text
        time.sleep(2)
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


def parse_team_rosters():
    """gol.gg's team pages list each roster, conventionally in Top/Jungle/Mid/
    ADC/Support order. Used to fill in team/role fields the player-list table
    doesn't provide. Role is inferred from list position — flagged with a
    diagnostic dump so it can be corrected if a team's page lists it differently."""
    url = f"{BASE}/teams/list/season-ALL/split-ALL/tournament-{SUMMER.replace(' ', '%20')}/"
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")
    team_links = soup.find_all("a", href=re.compile(r"/teams/team-stats/\d+/"))
    team_urls = {}
    for link in team_links:
        name = link.get_text(strip=True)
        if name and name not in team_urls:
            team_urls[name] = link["href"]
    print(f"  found {len(team_urls)} teams: {list(team_urls.keys())}", file=sys.stderr)

    role_sequence = ["TOP", "JNG", "MID", "BOT", "SUP"]
    roster = {}  # player name -> {"team":..., "role":...}
    for team_name, href in team_urls.items():
        team_url = href if href.startswith("http") else f"{BASE}{href}"
        try:
            team_html = get(team_url)
        except Exception as e:
            print(f"  ! failed to fetch roster for {team_name}: {e}", file=sys.stderr)
            continue
        team_soup = BeautifulSoup(team_html, "html.parser")
        player_links = team_soup.find_all("a", href=re.compile(r"/players/player-stats/\d+/"))
        seen = []
        for pl in player_links:
            pname = pl.get_text(strip=True)
            if pname and pname not in seen:
                seen.append(pname)
        if team_name == list(team_urls.keys())[0]:  # diagnostic for just the first team
            print(f"    {team_name} roster order found: {seen}", file=sys.stderr)
        for idx, pname in enumerate(seen[:5]):
            roster[pname] = {"team": team_name, "role": role_sequence[idx] if idx < 5 else "SUB"}
        time.sleep(0.5)
    return roster


def clean_team_name(raw):
    """gol.gg's team header divs often read like 'Dignitas - LOSS' or
    'Dignitas- WIN' — strip the result suffix to get just the team name."""
    return re.sub(r"\s*-?\s*(WIN|LOSS)\s*$", "", raw, flags=re.IGNORECASE).strip()


def parse_match_list(tournament):
    """Returns list of completed series with gol.gg game IDs, most recent first.
    Rows for matches that haven't been played yet (score is a placeholder like
    '-' or 'vs') are skipped — those belong in the schedule, not past results."""
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
    skipped_unplayed = 0
    for i, row in enumerate(rows[1:], start=1):
        cells = row.find_all("td")
        if len(cells) < 6:
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
        team_left = cells[1].get_text(strip=True)
        score = cells[2].get_text(strip=True)
        team_right = cells[3].get_text(strip=True)
        # Skip unplayed matches — score should look like "2 - 0", "1 - 2", etc.
        if not re.match(r"^\d+\s*-\s*\d+$", score):
            skipped_unplayed += 1
            continue
        score = re.sub(r"\s+", "", score)  # normalize "2 - 1" -> "2-1"
        week_digits = re.sub(r"\D", "", cells[4].get_text(strip=True))
        week = int(week_digits) if week_digits else None
        date = cells[5].get_text(strip=True)
        matches.append({
            "base_game_id": int(game_id.group(1)),
            "team_left": team_left, "score": score, "team_right": team_right,
            "week": week, "date": date,
        })
    print(f"    {len(matches)} completed, {skipped_unplayed} unplayed/skipped", file=sys.stderr)
    return matches


def parse_game_kills(game_id):
    """Returns {team: {player: kills}} totals for a single game page.
    gol.gg puts all 10 players in one table.playersInfosLine, first 5 rows
    for the first team, next 5 for the second."""
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

    table = soup.find("table", class_="playersInfosLine")
    if not table:
        print(f"  ! game {game_id}: no table.playersInfosLine found", file=sys.stderr)
        all_tables = soup.find_all("table")
        for t in all_tables[:6]:
            print(f"    table classes: {t.get('class')}", file=sys.stderr)
        return result

    rows = table.find_all("tr")
    data_rows = rows[1:] if rows and rows[0].find_all("th") else rows
    print(f"  game {game_id}: playersInfosLine has {len(data_rows)} data rows", file=sys.stderr)

    parsed = []
    for row in data_rows:
        name_cell = row.find("a", class_="text-decoration-none") or row.find("a")
        kda_cell = row.find("td", class_="kda")
        if not kda_cell:
            # fallback: scan all cells for an "N / N / N" pattern
            for td in row.find_all("td"):
                if re.match(r"\s*\d+\s*/\s*\d+\s*/\s*\d+\s*$", td.get_text(strip=True)):
                    kda_cell = td
                    break
        if not name_cell or not kda_cell:
            continue
        name = name_cell.get_text(strip=True)
        m = re.match(r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)", kda_cell.get_text(strip=True))
        if m:
            parsed.append((name, int(m.group(1))))

    if len(parsed) != 10:
        print(f"  ! game {game_id}: expected 10 players, parsed {len(parsed)}: {parsed}", file=sys.stderr)

    # First half of rows = team_names[0], second half = team_names[1]
    half = len(parsed) // 2 if len(parsed) >= 2 else 0
    for name, kills in parsed[:half]:
        result[team_names[0]][name] = kills
    for name, kills in parsed[half:]:
        result[team_names[1]][name] = kills

    return result


def series_g1_g2_kills(base_id, score):
    """Given the first game's ID and a '2-0'/'2-1' score string, returns
    combined game-1 + game-2 kills per player, per team."""
    is_bo3_full = score.strip() in ("2-1", "1-2")
    g1 = parse_game_kills(base_id)
    g2 = parse_game_kills(base_id + 1)
    combined = {}
    for team in g1:
        combined[team] = {p: g1[team].get(p, 0) + g2.get(team, {}).get(p, 0) for p in g1[team]}
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
            teams[team] = {"color": TEAM_COLORS.get(team, "#888888"), "players": []}
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


def main():
    print("Fetching team rosters (for team/role assignment)...")
    roster = parse_team_rosters()
    print(f"  {len(roster)} players matched to a team/role")

    print("Fetching Summer (current) player stats...")
    cur_players = parse_player_list(SUMMER)
    print(f"  {len(cur_players)} players")

    print("Fetching Spring (historical) player stats...")
    hist_players = parse_player_list(SPRING)
    print(f"  {len(hist_players)} players")

    teams_payload = build_teams_payload(cur_players, hist_players, roster)
    print(f"  built payload for {len(teams_payload)} teams: {list(teams_payload.keys())}")

    print("Fetching match list...")
    matches = parse_match_list(SUMMER)
    print(f"  {len(matches)} completed series found")

    past_matches = []
    for m in matches:
        try:
            kills = series_g1_g2_kills(m["base_game_id"], m["score"])
        except Exception as e:
            print(f"  ! skipped {m['team_left']} vs {m['team_right']}: {e}", file=sys.stderr)
            continue
        left_score, right_score = (int(x) for x in m["score"].split("-"))
        winner = m["team_left"] if left_score > right_score else m["team_right"]
        past_matches.append({
            "week": m["week"], "date": m["date"],
            "teamA": m["team_left"], "teamB": m["team_right"],
            "winner": winner, "score": m["score"],
            "actual": kills,
        })
        time.sleep(1)  # be polite to gol.gg

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "teams": teams_payload,
        "past_matches": past_matches,
        # Upcoming schedule merged in separately — see scrape_schedule.py + merge.py.
    }

    with open("data.json", "w") as f:
        json.dump(payload, f, indent=2)
    print("Wrote data.json")


if __name__ == "__main__":
    main()

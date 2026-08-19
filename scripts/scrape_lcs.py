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
    """Pulls the rich per-player stats table (avg K/D/A, KP%, games played)."""
    url = f"{BASE}/players/list/season-ALL/split-ALL/tournament-{tournament.replace(' ', '%20')}/"
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="table_list")
    players = {}
    if not table:
        return players
    for row in table.find("tbody").find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 10:
            continue
        try:
            name = cells[0].get_text(strip=True)
            team = cells[1].get_text(strip=True)
            role = cells[2].get_text(strip=True)
            games = int(cells[3].get_text(strip=True))
            k = float(cells[5].get_text(strip=True))
            d = float(cells[6].get_text(strip=True))
            a = float(cells[7].get_text(strip=True))
            kp = float(cells[9].get_text(strip=True).replace("%", ""))
        except (ValueError, IndexError):
            continue
        players[(team, name)] = {
            "name": name, "team": team, "role": ROLE_ORDER.get(role, role.upper()[:3]),
            "g": games, "k": k, "d": d, "a": a, "kp": kp,
        }
    return players


def parse_match_list(tournament):
    """Returns list of completed series with gol.gg game IDs, most recent first."""
    url = f"{BASE}/tournament/tournament-matchlist/{tournament.replace(' ', '%20')}/"
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")
    matches = []
    table = soup.find("table")
    if not table:
        return matches
    for row in table.find("tbody").find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 6:
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
        week_digits = re.sub(r"\D", "", cells[4].get_text(strip=True))
        week = int(week_digits) if week_digits else None
        date = cells[5].get_text(strip=True)
        matches.append({
            "base_game_id": int(game_id.group(1)),
            "team_left": team_left, "score": score, "team_right": team_right,
            "week": week, "date": date,
        })
    return matches


def parse_game_kills(game_id):
    """Returns {team: {player: kills}} totals for a single game page."""
    url = f"{BASE}/game/stats/{game_id}/page-game/"
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")
    result = {}
    blue = soup.find("div", class_="blue-line-header")
    red = soup.find("div", class_="red-line-header")
    for side in [blue, red]:
        if not side:
            continue
        team_name = side.get_text(strip=True)
        result[team_name] = {}
    tables = soup.find_all("table", class_="playersInfos")
    team_names = list(result.keys())
    for i, table in enumerate(tables[:2]):
        if i >= len(team_names):
            break
        team = team_names[i]
        for row in table.find_all("tr")[1:]:
            name_cell = row.find("a", class_="text-decoration-none")
            kda_cell = row.find("td", class_="kda")
            if not name_cell or not kda_cell:
                continue
            name = name_cell.get_text(strip=True)
            m = re.match(r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)", kda_cell.get_text(strip=True))
            if m:
                result[team][name] = int(m.group(1))
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


def build_teams_payload(cur_players, hist_players):
    teams = {}
    for (team, name), cur in cur_players.items():
        if team not in teams:
            teams[team] = {"color": TEAM_COLORS.get(team, "#888888"), "players": []}
        hist = hist_players.get((team, name))
        entry = {
            "name": name, "role": cur["role"],
            "cur": {"g": cur["g"], "k": cur["k"], "d": cur["d"], "a": cur["a"], "kp": cur["kp"]},
            "hist": ({"g": hist["g"], "k": hist["k"], "d": hist["d"], "a": hist["a"], "kp": hist["kp"]}
                      if hist else None),
        }
        teams[team]["players"].append(entry)
    return teams


def main():
    print("Fetching Summer (current) player stats...")
    cur_players = parse_player_list(SUMMER)
    print(f"  {len(cur_players)} player-team rows")

    print("Fetching Spring (historical) player stats...")
    hist_players = parse_player_list(SPRING)
    print(f"  {len(hist_players)} player-team rows")

    teams_payload = build_teams_payload(cur_players, hist_players)

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
        winner = m["team_left"] if m["score"].split("-")[0] > m["score"].split("-")[1] else m["team_right"]
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
        # Upcoming schedule isn't reliably on gol.gg early â left for manual/second
        # source integration (see scrape_schedule.py).
    }

    with open("data.json", "w") as f:
        json.dump(payload, f, indent=2)
    print("Wrote data.json")


if __name__ == "__main__":
    main()

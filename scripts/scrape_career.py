#!/usr/bin/env python3
"""
Full-career player history for LoL, sourced from gol.gg — a genuinely
different scraper from scrape_lcs.py: that one tracks CURRENT/PRIOR split
snapshots per region; this one builds a properly decayed CROSS-SEASON
career baseline per player, incrementally cached since past seasons never
change once they're over.

Confirmed via live reconnaissance before writing any of this (not
guessed):
  - Team roster pages (gol.gg/teams/team-stats/{team_id}/split-ALL/tournament-ALL/)
    link each player's name directly to /players/player-stats/{player_id}/...
    -- this is how player IDs get resolved, reusing team IDs the existing
    scraper already has.
  - /players/player-stats/{id}/season-ALL/split-ALL/tournament-ALL/ gives a
    cheap CAREER AGGREGATE ("Record: 193W - 163L") -- used to decide
    whether per-season pagination is even needed, without an expensive
    fetch for every player.
  - /players/player-matchlist/{id}/season-ALL/... caps at "Last 200
    games" -- confirmed via a real veteran player's page (356 total
    career games; a single season-ALL fetch would silently truncate the
    older ~40%). Anyone whose aggregate game count exceeds 200 gets
    per-season fetches instead (season-S6 through CURRENT_SEASON), not
    the single season-ALL call.
  - Per-game rows give real KDA ("K/D/A" string), KP%, and date directly
    -- the actual granularity needed, not just a lifetime average.

Decay model: SEASON-level exponential decay, not per-game. Patches and
metas shift at season boundaries, not smoothly game-by-game, so a
player's 2019 season is meaningfully down-weighted relative to their
current one -- but every game WITHIN a season contributes equally to
that season's own aggregate (no re-decay within a season here; that's
what the model's existing recencyHalfLife already does for cur/hist).

Incremental by design: reads its own previous output, if present, and
only re-fetches:
  - the CURRENT season for every tracked player (the only season whose
    data can still change)
  - any season it has never fetched before for that player
Past, complete seasons are treated as immutable and never re-fetched --
faster, and considerably more polite to gol.gg than re-scraping a
player's whole history every run.

Usage:
    python scripts/scrape_career.py
Requires data.json (scrape_lcs.py's own output) to already exist in the
working directory -- that's where the set of tracked player names comes
from. Player IDs themselves are resolved automatically from gol.gg's
bulk players-list page (no team IDs or manual seeding needed at all);
see resolve_all_tracked_player_ids() below.
"""
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://gol.gg"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
OUTPUT_PATH = "career_data.json"
MATCHLIST_CAP = 200  # confirmed real cap on the season-ALL match list view
CURRENT_SEASON = "S16"  # confirmed current season from live nav — update each split if gol.gg's own season counter advances
CONCURRENCY = 6  # matches scrape_lcs.py's proven-safe concurrency level against this same site
SEASON_HALF_LIFE = 1.5  # seasons — a season 1.5 back gets half weight, 3 back gets a quarter, etc. Not yet backtested; see note below.

# Real team IDs are no longer needed for player discovery — see
# fetch_bulk_player_list() below, which resolves every current player's ID
# directly from gol.gg's own players-list page in one fetch per split,
# confirmed via live reconnaissance to link straight to
# /players/player-stats/{id}/... for each of the ~85+ players listed.
CURRENT_SEASON_SPLITS = ["Pre-Season", "Winter", "Spring", "Summer"]  # scan all of the current season's splits to catch anyone active this year
EXISTING_LOL_DATA_PATH = "data.json"  # scrape_lcs.py's output — used as the filter for which bulk-list players we actually track


def fetch_bulk_player_list(season, split):
    """Returns {player_name: player_id} for every player listed on
    gol.gg's players-list page for one season+split — confirmed via live
    reconnaissance to link each name directly to
    /players/player-stats/{id}/..., the same pattern as team rosters, but
    without needing a team ID at all. Deliberately NOT filtered by
    tournament here (that dropdown's exact URL encoding wasn't confirmed
    for every entry) — instead this fetches broadly and relies on
    load_tracked_player_names() to filter down to players this project
    actually tracks."""
    html = fetch(f"{BASE}/players/list/season-{season}/split-{split}/tournament-ALL/")
    if not html:
        return {}
    soup = BeautifulSoup(html, "html.parser")
    players = {}
    # Matches "player-stats/" without requiring a "/players/" prefix —
    # gol.gg pages carry a <base href="https://gol.gg/players/"> tag
    # (confirmed present on every page fetched during reconnaissance),
    # meaning real hrefs are very likely relative (e.g.
    # "player-stats/48/...") rather than the absolute form assumed
    # originally, which matched zero real links on a live test despite
    # the underlying data being confirmed present.
    for a in soup.select('a[href*="player-stats/"]'):
        m = re.search(r"player-stats/(\d+)/", a.get("href", ""))
        if not m:
            continue
        name = a.get_text(strip=True)
        if name:
            players[name] = int(m.group(1))
    if not players:
        print(f"  [debug] 0 links matched — raw HTML sample (first 1500 chars):\n{html[:1500]}\n", file=sys.stderr)
    return players


def load_tracked_player_names():
    """Reads scrape_lcs.py's own output to get the set of player names
    already tracked across all 7 regions — this is what filters the
    broad bulk player list down to just the players this project cares
    about, rather than fetching career data for every pro player gol.gg
    has ever indexed."""
    p = Path(EXISTING_LOL_DATA_PATH)
    if not p.exists():
        print(f"! {EXISTING_LOL_DATA_PATH} not found — run scrape_lcs.py first, or this script "
              f"has no way to know which players to fetch career data for.", file=sys.stderr)
        return set()
    with open(p) as f:
        data = json.load(f)
    names = set()
    for region in data.get("regions", {}).values():
        for team in region.get("teams", {}).values():
            for player in team.get("players", []):
                if player.get("name"):
                    names.add(player["name"])
    return names


def resolve_all_tracked_player_ids():
    """Combines the bulk player list across all current-season splits
    with the tracked-name filter to produce {player_name: player_id} for
    exactly the players this project needs career data for."""
    tracked_names = load_tracked_player_names()
    if not tracked_names:
        return {}
    print(f"Loaded {len(tracked_names)} tracked player names from {EXISTING_LOL_DATA_PATH}")

    all_players = {}
    for split in CURRENT_SEASON_SPLITS:
        print(f"Fetching bulk player list: season-{CURRENT_SEASON} split-{split}...")
        found = fetch_bulk_player_list(CURRENT_SEASON, split)
        print(f"  {len(found)} players found")
        all_players.update(found)
        time.sleep(0.5)

    resolved = {name: pid for name, pid in all_players.items() if name in tracked_names}
    missing = tracked_names - set(resolved.keys())
    if missing:
        print(f"! {len(missing)} tracked player(s) not found in this season's bulk list "
              f"(may be inactive, renamed, or only appear in a split not scanned): {sorted(missing)}",
              file=sys.stderr)
    print(f"Resolved {len(resolved)}/{len(tracked_names)} tracked players to real gol.gg IDs\n")
    return resolved


def fetch(url, retries=3):
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            if resp.status_code == 200:
                return resp.text
            print(f"  ! {url} -> HTTP {resp.status_code}", file=sys.stderr)
        except Exception as e:
            print(f"  ! {url} attempt {attempt + 1}/{retries} failed: {e}", file=sys.stderr)
        time.sleep(1 + attempt)
    return None


def resolve_player_ids(team_id):
    """Superseded by resolve_all_tracked_player_ids() above, which uses
    gol.gg's bulk players-list page instead — no team ID needed at all.
    Kept here only as a documented fallback: if a tracked player is ever
    missing from the bulk list (e.g. gol.gg's split filters don't happen
    to cover the split they played in), their team's roster page is a
    reliable secondary lookup, confirmed via the same live reconnaissance
    ('Team WE stats' page had every player name hyperlinked directly to
    /players/player-stats/{id}/...)."""
    html = fetch(f"{BASE}/teams/team-stats/{team_id}/split-ALL/tournament-ALL/")
    if not html:
        return {}
    soup = BeautifulSoup(html, "html.parser")
    players = {}
    for a in soup.select('a[href*="player-stats/"]'):
        m = re.search(r"player-stats/(\d+)/", a.get("href", ""))
        if not m:
            continue
        name = a.get_text(strip=True)
        if name:
            players[name] = int(m.group(1))
    return players


def get_career_game_count(player_id):
    """Cheap check before deciding whether per-season pagination is
    needed — parses 'Record: 193W - 163L' from the career-aggregate
    page into a total game count."""
    html = fetch(f"{BASE}/players/player-stats/{player_id}/season-ALL/split-ALL/tournament-ALL/")
    if not html:
        return None
    m = re.search(r"(\d+)W\s*-\s*(\d+)L", html)
    if not m:
        return None
    return int(m.group(1)) + int(m.group(2))


def parse_matchlist_html(html):
    """Parses the per-game table on a player-matchlist page. Table
    columns confirmed via live reconnaissance: Champion | Result |
    Duration | KDA | CSM | DPM | KP% | Build | Date | Game | Tournament.
    Returns a list of {k, d, a, kp, date, opponent_context, tournament}."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 10:
            continue  # header row or malformed row — skip rather than guess
        kda_text = cells[3].get_text(strip=True)
        kda_match = re.match(r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)", kda_text)
        if not kda_match:
            continue
        k, d, a = (int(x) for x in kda_match.groups())
        kp_text = cells[6].get_text(strip=True).replace("%", "")
        try:
            kp = float(kp_text) / 100 if kp_text and kp_text != "-" else None
        except ValueError:
            kp = None
        date_text = cells[8].get_text(strip=True)
        game_text = cells[9].get_text(strip=True)
        tournament_text = cells[10].get_text(strip=True) if len(cells) > 10 else None
        rows.append({"k": k, "d": d, "a": a, "kp": kp, "date": date_text, "game": game_text, "tournament": tournament_text})
    return rows


def fetch_player_season(player_id, season):
    html = fetch(f"{BASE}/players/player-matchlist/{player_id}/season-{season}/split-ALL/tournament-ALL/")
    if not html:
        return []
    return parse_matchlist_html(html)


def season_aggregate(games):
    """Collapses one season's per-game rows into a single {g, k, d, a, kp}
    rate — this is what gets season-decayed, not individual games, since
    within a season the existing model already handles recency via
    recencyHalfLife on cur/hist."""
    if not games:
        return None
    g = len(games)
    kp_values = [row["kp"] for row in games if row["kp"] is not None]
    return {
        "g": g,
        "k": sum(row["k"] for row in games) / g,
        "d": sum(row["d"] for row in games) / g,
        "a": sum(row["a"] for row in games) / g,
        "kp": (sum(kp_values) / len(kp_values)) if kp_values else 0,
    }


def all_seasons_back_to(current_season):
    """S6 through the current season, e.g. ['S6','S7',...,'S16']."""
    current_num = int(current_season.lstrip("S"))
    return [f"S{n}" for n in range(6, current_num + 1)]


def decayed_career_baseline(season_aggregates, current_season):
    """Exponential decay across SEASONS (not games) — a season N seasons
    back gets weight 0.5^(N / SEASON_HALF_LIFE). Returns one {g,k,d,a,kp}
    baseline representing career history properly weighted toward recent
    seasons, or None if there's nothing to blend.

    SEASON_HALF_LIFE=1.5 is a reasonable starting point (last season
    counts meaningfully, three seasons back is a minor signal) but is NOT
    yet backtested — this needs its own pass through
    optimize_weights.py once real career data exists, same as every
    other weight in this model was measured rather than guessed."""
    if not season_aggregates:
        return None
    current_num = int(current_season.lstrip("S"))
    total_weight = 0.0
    weighted = {"k": 0.0, "d": 0.0, "a": 0.0, "kp": 0.0}
    total_games = 0
    for season, agg in season_aggregates.items():
        if not agg:
            continue
        season_num = int(season.lstrip("S"))
        seasons_back = max(0, current_num - season_num)
        weight = 0.5 ** (seasons_back / SEASON_HALF_LIFE)
        total_weight += weight * agg["g"]
        total_games += agg["g"]
        for key in ("k", "d", "a", "kp"):
            weighted[key] += agg[key] * weight * agg["g"]
    if total_weight == 0:
        return None
    return {
        "g": total_games,
        **{key: weighted[key] / total_weight for key in ("k", "d", "a", "kp")},
    }


def load_previous_output():
    p = Path(OUTPUT_PATH)
    if not p.exists():
        return {}
    with open(p) as f:
        return json.load(f)


def process_one_player(player_name, player_id, previous, progress, total):
    """One player's full pipeline — pulled out into its own function so a
    ThreadPoolExecutor can run many of these concurrently, rather than
    the previous fully-sequential loop (one request at a time, with a
    0.5s sleep between each) that made a 320-player run genuinely slow,
    especially for veterans needing all 11 seasons (S6-S16) fetched
    individually."""
    prev_player = previous.get(str(player_id), {})
    prev_seasons = prev_player.get("season_aggregates", {})

    total_games = get_career_game_count(player_id)
    needs_full_history = total_games is not None and total_games > MATCHLIST_CAP

    season_aggregates = dict(prev_seasons)  # reuse cached, immutable past seasons
    seasons_to_fetch = [CURRENT_SEASON]
    if needs_full_history:
        seasons_to_fetch = [s for s in all_seasons_back_to(CURRENT_SEASON) if s not in prev_seasons or s == CURRENT_SEASON]

    for season in seasons_to_fetch:
        games = fetch_player_season(player_id, season)
        agg = season_aggregate(games)
        if agg:
            season_aggregates[season] = agg
        time.sleep(0.5)

    career = decayed_career_baseline(season_aggregates, CURRENT_SEASON)
    progress["done"] += 1
    if progress["done"] % 10 == 0 or progress["done"] == total:
        print(f"  ...{progress['done']}/{total} players processed")
    return str(player_id), {"name": player_name, "season_aggregates": season_aggregates, "career": career}


def build_career_data():
    previous = load_previous_output()
    output = {}

    tracked_players = resolve_all_tracked_player_ids()
    total = len(tracked_players)
    progress = {"done": 0}

    print(f"Processing {total} players ({CONCURRENCY} at a time)...")
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        futures = [
            executor.submit(process_one_player, name, pid, previous, progress, total)
            for name, pid in tracked_players.items()
        ]
        for future in as_completed(futures):
            try:
                player_id_str, record = future.result()
                output[player_id_str] = record
            except Exception as e:
                print(f"  ! a player failed to process: {e}", file=sys.stderr)

    return output


def main():
    if not Path(EXISTING_LOL_DATA_PATH).exists():
        print(f"! {EXISTING_LOL_DATA_PATH} not found — run scrape_lcs.py first. This script needs "
              f"it to know which players to fetch career data for.", file=sys.stderr)
        sys.exit(1)

    data = build_career_data()
    with open(OUTPUT_PATH, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\nWrote {OUTPUT_PATH}: {len(data)} players")


if __name__ == "__main__":
    main()
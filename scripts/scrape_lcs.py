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
import os
import sys
import random
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


# Wall clock is not a usable measure of a change to this scraper. Across
# three runs of IDENTICAL code this step took 4.6, 6.0 and 7.7 minutes --
# a 67% spread that swamps any change worth making, and exactly what made
# a first attempt at speeding things up look like a regression when it
# was really a no-op. Requests are what this code controls, so they are
# what it reports: one number per run, comparable regardless of how the
# site is feeling.
REQUEST_TOTAL = {"n": 0}


def report_requests(label):
    line = f"{label}: {REQUEST_TOTAL['n']} requests made this run"
    print(f"\n  {line}")
    _write_run_summary(f"- gol.gg {line}")

def _write_run_summary(line):
    """Also put the number where it can actually be read.

    The counter was added, printed to stdout mid-job, and then turned out
    to be unreachable: GitHub's job-log API returns the tail of a job, and
    the tail of these jobs is always the git push. A metric you cannot
    read is not a metric. GITHUB_STEP_SUMMARY shows up at the top of the
    run page instead.
    """
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass  # never fail a scrape over a progress note



def get(url, retries=4):
    """Fetch with retries that actually cover the failure modes this
    project has really hit.

    Three fixes over the previous version, each from an observed problem
    rather than speculation:

    1. NETWORK-LEVEL ERRORS ARE RETRIED. requests.get() RAISES on
       connect timeouts / DNS / connection resets, so it never returns a
       response object and the old status-code-only retry loop was
       skipped entirely — the scraper died on the first timeout. A real
       run failed exactly this way across all seven regions when gol.gg
       briefly became unreachable, wiping data.json to empty teams.
       These are now caught and retried like any other failure.

    2. EXPONENTIAL BACKOFF WITH JITTER, instead of a flat 2s. If the
       cause is load or throttling, hammering at a fixed short interval
       is the least useful thing to do. Jitter matters because six
       worker threads retry in lockstep otherwise.

    3. 429 IS TREATED AS RATE LIMITING, honouring Retry-After when the
       server sends it and backing off much harder when it doesn't.
       Relevant now that tournament discovery roughly quadrupled this
       scraper's request volume against gol.gg.
    """
    attempts_made = 0
    last_status = None
    for attempt in range(retries):
        attempts_made = attempt + 1
        try:
            REQUEST_TOTAL["n"] += 1
            r = requests.get(url, headers=HEADERS, timeout=20)
            last_status = r.status_code
            if r.status_code == 200:
                return r.text
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else None
                except ValueError:
                    delay = None
                if delay is None:
                    delay = 10 * (2 ** attempt)
                print(f"  ! rate limited (429) on {url} — backing off {delay:.0f}s "
                      f"(attempt {attempt + 1}/{retries})", file=sys.stderr)
                time.sleep(delay + random.uniform(0, 2))
                continue
            if 400 <= r.status_code < 500 and r.status_code != 408:
                # A genuine client error (404 for a game that doesn't
                # exist, say) will not become a 200 by asking again —
                # retrying just wastes the budget and slows the run.
                break
        except requests.exceptions.RequestException as e:
            last_status = f"{type(e).__name__}"
        time.sleep(min(30, 2 * (2 ** attempt)) + random.uniform(0, 1.5))
    # attempts_made, not `retries` — the 4xx branch above deliberately
    # breaks after ONE attempt, and reporting "failed after 4 tries" for
    # a single-attempt 404 is actively misleading when these logs are the
    # main tool for diagnosing scraper behaviour.
    plural = "try" if attempts_made == 1 else "tries"
    print(f"  ! GET {url} failed after {attempts_made} {plural}, last status {last_status}",
          file=sys.stderr)
    raise requests.exceptions.HTTPError(f"GET {url} failed, last status {last_status}")


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
    # team_urls is also returned (not just roster) so callers can reuse
    # it for tournament discovery (see discover_tournaments) without a
    # second fetch of this same teams-list page.
    return roster, team_urls


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


def discover_tournaments(current_tournament, team_urls):
    """Finds every real tournament for this region/year -- regular
    season AND any playoffs/finals stages -- rather than only ever
    scraping the single regular-season tournament name hardcoded in
    REGIONS. A real, reported gap: "past matches only covers regular
    season in all regions", confirmed via live reconnaissance that
    gol.gg treats playoffs as an ENTIRELY SEPARATE tournament from
    regular season (e.g. "LEC 2026 Summer Season" vs "LEC 2026 Summer
    Playoffs" are two different tournament names, not one tournament
    with a playoffs section) -- and that this varies by region: LCK
    uses a single season-wide "LCK 2026 Season Playoffs" unrelated to
    its "Rounds X-Y" split names, LPL has separate Playoffs AND a
    further "Grand Finals" stage per split. Hardcoding every region's
    naming convention would be fragile and need manual updates every
    split, so this discovers them instead: gol.gg's own team-stats pages
    have a real, server-rendered "Tournament" dropdown listing every
    tournament that team has ever played in (confirmed via direct
    fetch) -- since every team in a region shares the same set of
    tournaments, one team's dropdown gives us the whole region's list.
    (gol.gg's own /tournament/list/ page would be a cleaner source but
    is JS-rendered client-side with an empty table in the raw HTML --
    confirmed via direct fetch -- so it's not usable here.)

    Filtered to the CURRENT YEAR only (parsed from current_tournament),
    not full career history, since a team that's been active for years
    would otherwise dump its entire tournament history into this list."""
    if not team_urls:
        return [current_tournament]
    year_match = re.search(r"\b(20\d{2})\b", current_tournament)
    year = year_match.group(1) if year_match else None

    sample_url = next(iter(team_urls.values()))
    all_tournaments_url = re.sub(r"tournament-[^/]+/?$", "tournament-ALL/", sample_url)
    try:
        html = get(all_tournaments_url)
    except Exception as e:
        print(f"  ! tournament discovery failed ({e}) — falling back to just {current_tournament}", file=sys.stderr)
        return [current_tournament]

    soup = BeautifulSoup(html, "html.parser")
    found = set()
    # Real, confirmed markup (via direct raw-HTML inspection, not
    # guessed): <select id='cbtournament'>, with each real tournament as
    # an <option>. A prior version used generic soup.find("select"),
    # which silently grabbed the WRONG element -- gol.gg's nav bar has
    # its own <select id="selectSearch"> earlier in the page (an empty
    # shell populated later by a JS search-autocomplete library), so
    # find("select") always matched that one first and found zero
    # options, never reaching the real tournament dropdown at all. Using
    # the specific id scopes this correctly.
    select = soup.find("select", id="cbtournament")
    if select:
        for opt in select.find_all("option"):
            text = opt.get_text(strip=True)
            if text and text != "-- ALL --":
                found.add(text)

    if not found:
        print(f"  ! tournament discovery found nothing usable on {all_tournaments_url} — "
              f"falling back to just {current_tournament} (playoffs/finals stages won't be "
              f"covered this run — worth checking whether gol.gg's markup changed if this "
              f"recurs)", file=sys.stderr)
        return [current_tournament]

    # Only tournaments actually prefixed with this region's own name
    # (e.g. "LCS 2026 ...") -- a pure year-filter would also pull in
    # unrelated events a team happened to play this year (confirmed via
    # real data: "EWC 2026 Qualifier NA", "Americas Cup 2026" showed up
    # in LCS's own dropdown), which is broader than what "past results"
    # for this league should mean.
    region_prefix = current_tournament[:year_match.start()].strip() if year_match else None
    if year:
        found = {t for t in found if year in t and (not region_prefix or t.startswith(region_prefix))}
    found.add(current_tournament)  # always include it even if the filters or discovery missed it somehow
    tournaments = sorted(found)
    print(f"  discovered {len(tournaments)} tournament(s) for {year or 'this'} year: {tournaments}",
          file=sys.stderr)
    return tournaments


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


def parse_game_draft(soup, team_names):
    """Extracts draft info from the SAME page parse_game_kills already
    fetches — no extra request needed. Confirmed via live reconnaissance
    on a real current game page (RED Canids vs LOUD, CBLOL Cup 2026):

      - Fearless Draft tournaments are explicitly labeled as such right
        on the page (e.g. "CBLOL Cup 2026 (BR) - Fearless Draft") --
        detecting this is a simple text check, not a hardcoded list of
        which tournaments use the format.
      - Each team has its OWN "Bans" then "Picks" section within that
        team's own block of the page (not one shared "Bans" header
        split between both teams) -- team switching is tracked the same
        way parse_game_kills already reliably does it, via the
        blue-line-header/red-line-header divs, not by alternating on
        "Bans"/"Picks" text alone.
      - Champion names are recoverable from each champion link's
        title/alt text ("Rumble stats" -> "Rumble"), via links to
        /champion/champion-stats/{id}/....
      - A "First Pick" badge marks draft priority for whichever team's
        block it appears in.

    Bans/Picks sections aren't in a confirmed, named container (only
    inspected via rendered content, not raw HTML), so this anchors on
    the literal "Bans"/"Picks" text nodes and collects champion links
    between consecutive anchors within each team's block -- a defensive
    strategy that should survive minor markup differences, but --
    consistent with every other scraper in this project -- needs one
    real run against actual HTML to confirm it actually works."""
    page_text = soup.get_text()
    fearless = "Fearless Draft" in page_text

    champion_link_re = re.compile(r"/champion/champion-stats/(\d+)/")

    def champion_name_from_link(a):
        title = a.get("title", "")
        return title[:-len(" stats")] if title.endswith(" stats") else (a.get_text(strip=True) or None)

    bans = {t: [] for t in team_names}
    picks = {t: [] for t in team_names}
    first_pick_team = None

    if len(team_names) != 2:
        return {"fearless": fearless, "bans": bans, "picks": picks, "first_pick_team": None}

    current_team = None
    current_section = None  # "bans" | "picks" | None

    for el in soup.find_all(["div", "span", "td", "th", "p", "a", "img", "table"]):
        if el.name == "table":
            # Real draft summary data (Bans/Picks per team) only ever
            # appears BEFORE the per-player stat tables in document
            # order — confirmed by a real bug this exact boundary check
            # fixes: without it, champion links inside the per-player
            # tables (needed separately, per-player, for parse_game_kills)
            # kept getting vacuumed into whichever team was last active
            # here, producing duplicated/misattributed picks once the
            # walk continued past the summary section.
            break
        classes = el.get("class") or []
        if "blue-line-header" in classes:
            current_team, current_section = team_names[0], None
            continue
        if "red-line-header" in classes:
            current_team, current_section = team_names[1], None
            continue
        text = el.get_text(strip=True) if el.name != "img" else ""
        if text == "Bans":
            current_section = "bans"
            continue
        if text == "Picks":
            current_section = "picks"
            continue
        if el.name == "img" and "first" in (el.get("src", "") or "").lower():
            first_pick_team = current_team
        if el.name == "a" and current_team and current_section:
            m = champion_link_re.search(el.get("href", ""))
            if m:
                champ = champion_name_from_link(el)
                if champ:
                    (bans if current_section == "bans" else picks)[current_team].append(champ)

    if not any(bans.values()) and not any(picks.values()):
        print(f"  [debug] parse_game_draft found no bans/picks for game — page structure may not "
              f"match what was confirmed via reconnaissance; needs a real look at this page's raw "
              f"HTML", file=sys.stderr)

    return {"fearless": fearless, "bans": bans, "picks": picks, "first_pick_team": first_pick_team}


def parse_game_kills(game_id):
    """Returns {"kda": {team: {player: {"k","d","a","champion"}}}, "draft": {...}}
    for a single game page.

    gol.gg's per-game table nests a lot of extra markup per player (rune and
    item breakdowns), which inflates naive <tr> counts and makes position-based
    row splitting unreliable. Instead: find every row that contains a link to
    a player's profile (unambiguous signal), and within that same row look for
    a standalone "N/N/N" KDA pattern. Each player cell also has a champion-icon
    link before the name link, so we take the *last* link in the cell, not the
    first, when extracting the name — and now also read the FIRST link (the
    champion icon) to capture the champion each player picked, which used to
    be silently discarded here.

    Also parses draft info (bans, picks, Fearless Draft flag) from this same
    page fetch via parse_game_draft() — see that function for how it locates
    the Bans/Picks sections.
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
        return {"kda": result, "draft": None}

    draft = parse_game_draft(soup, team_names)

    champion_link_re = re.compile(r"/champion/champion-stats/(\d+)/")

    def champion_name_from_link(a):
        title = a.get("title", "")
        return title[:-len(" stats")] if title.endswith(" stats") else (a.get_text(strip=True) or None)

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
        # The champion icon link precedes the name link in the same cell —
        # find it via the champion-stats href pattern rather than assuming
        # a fixed position, since other links (runes, items) may also
        # appear in the row.
        champion = None
        for a in row.find_all("a", href=champion_link_re):
            champion = champion_name_from_link(a)
            if champion:
                break
        # Look for a cell in this row whose text is EXACTLY a "N/N/N" pattern
        # (avoids accidentally matching item/rune stat text that merely
        # contains digits and slashes).
        kda = None
        for td in row.find_all("td"):
            m = kda_pattern.match(td.get_text(strip=True))
            if m:
                kda = {"k": int(m.group(1)), "d": int(m.group(2)), "a": int(m.group(3)), "champion": champion}
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

    missing_champs = sum(1 for _, kda in parsed if not kda.get("champion"))
    if missing_champs:
        print(f"  ! game {game_id}: {missing_champs}/{len(parsed)} players had no champion parsed "
              f"— champion-link extraction may need adjustment", file=sys.stderr)

    return {"kda": result, "draft": draft}


def series_format(score):
    """Infers the series length from the final score. The winner's score
    IS the "best of" threshold: 2 wins decides a Bo3, 3 decides a Bo5.

    Returns (label, maps_to_count) where maps_to_count is the PROP
    WINDOW — the maps a player-stat line is normally offered over, which
    is maps 1-2 for a Bo3 and maps 1-3 for a Bo5. That convention is why
    this exists: counting all maps of a Bo5 would mean a line covering
    up to five maps for one series and two for another, which are not
    comparable numbers.

    Falls back to Bo3/2 maps on anything unparseable, matching this
    scraper's long-standing behaviour rather than inventing a new one.
    """
    try:
        a, b = (int(x) for x in str(score).split("-"))
    except (ValueError, AttributeError):
        return "Bo3", 2
    best = max(a, b)
    if best <= 1:
        return "Bo1", 1
    if best == 2:
        return "Bo3", 2
    return "Bo5", 3


def series_prop_window_kills(base_id, score):
    """Given the first game's ID, returns combined K/D/A per player per
    team over the series' PROP WINDOW (maps 1-2 for a Bo3, maps 1-3 for
    a Bo5), plus per-game draft info and the per-game breakdown.

    Previously this always fetched exactly two games regardless of series
    length, so a Bo5 was measured on maps 1-2 while the lines people
    actually care about run through map 3. Now the map count follows the
    real format.

    Champion isn't combined into the summed KDA — unlike k/d/a, "champion
    picked" doesn't make sense to sum across different games (a player
    likely played different champions) — so per-game picks live in
    draft.games[i] instead.
    """
    label, n_maps = series_format(score)
    games = []
    for offset in range(n_maps):
        try:
            games.append(parse_game_kills(base_id + offset))
        except Exception as e:
            # A missing later map is normal and not fatal: a Bo5 that
            # ended 3-0 has no map 4-5, and a map can simply fail to
            # parse. Counting what we did get beats discarding the whole
            # series, but the window we actually measured is recorded
            # below so nothing silently claims 3 maps it never had.
            if offset == 0:
                raise  # no map 1 at all means there is no series to record
            print(f"    map {offset + 1} of {label} series {base_id} unavailable ({e}) — "
                  f"counting the {offset} map(s) that parsed", file=sys.stderr)
            break

    empty = {"k": 0, "d": 0, "a": 0}
    kda_list = [g["kda"] for g in games]
    combined = {}
    for team in kda_list[0]:
        combined[team] = {}
        for p in kda_list[0][team]:
            tot = {"k": 0, "d": 0, "a": 0}
            for kda in kda_list:
                cur = kda.get(team, {}).get(p, empty)
                tot["k"] += cur["k"]
                tot["d"] += cur["d"]
                tot["a"] += cur["a"]
            combined[team][p] = tot

    draft = None
    if any(g.get("draft") for g in games):
        # Fearless flag and first-pick side are per-tournament/per-game
        # properties, not summed — take whichever game actually has them.
        fearless = any((g.get("draft") or {}).get("fearless") for g in games)
        draft = {"fearless": fearless, "games": [g.get("draft") for g in games]}

    # Per-game, per-player breakdown WITH champion attached — this is
    # exactly what parse_game_kills already computes per individual game,
    # just preserved here instead of being discarded once summed into
    # `combined` above. Needed for anything champion-aware (e.g. "how did
    # this player perform on this specific champion"), since neither
    # `combined` (summed, no champion) nor `draft.games[i].picks` (an
    # unordered 5-champion list per team, no explicit per-player link)
    # can answer that question alone. It also drives the per-map view in
    # the app, so it must stay aligned with the maps actually counted.
    per_game = kda_list

    # maps_counted is the number of maps that genuinely parsed, which can
    # be lower than the format implies (a 3-0 Bo5 has no maps 4-5; a map
    # can fail to parse). Reported separately from the label so nothing
    # downstream has to assume Bo5 == 3 maps of data.
    return combined, draft, per_game, label, len(kda_list)


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


def classify_tournament_stage(tournament_name):
    """Normalizes a raw tournament name (e.g. "LCS 2026 Summer Playoffs")
    into a small, consistent category the UI can group/badge by, rather
    than needing to parse the raw string itself everywhere it's
    displayed. Order matters -- checked most-specific first, since e.g.
    "Grand Finals" should win over a generic "Playoffs" match if a name
    somehow contained both."""
    name_lower = tournament_name.lower()
    if "grand final" in name_lower or re.search(r"\bfinals?\b", name_lower):
        return "finals"
    if "playoff" in name_lower:
        return "playoffs"
    if "play-in" in name_lower or "play in" in name_lower:
        return "play-in"
    if "cup" in name_lower:
        return "cup"
    if "lock-in" in name_lower or "lock in" in name_lower or "kickoff" in name_lower:
        return "preseason"
    return "regular_season"


# A completed LoL series' box score does not change, and this scraper was
# re-fetching every one of them, twice a day, forever. 970 matches at two
# page loads each is roughly 1,940 requests to gol.gg per run to rebuild
# data that was already sitting in data.json -- and by the scraper's own
# comment, that fetch is "the dominant cost of the whole scrape".
#
# Two things make reuse safe rather than merely faster:
#
#   base_game_id, not (date, teams). Two teams can meet twice on one day
#   in a round robin, and reusing the wrong box score would be silent and
#   wrong -- far worse than being slow.
#
#   A grace window. gol.gg finalises a page some time after the series
#   ends, and this repo has already been bitten by a source reporting a
#   finished match with fields still null. Anything inside the window is
#   re-fetched regardless, so a result that was incomplete when first
#   seen gets corrected rather than frozen.
REUSE_GRACE_DAYS = 3


def reusable_past_matches(existing_region, today=None):
    """{base_game_id: entry} for series worth trusting from a previous run.

    Excludes anything without an id (records written before this existed),
    anything inside the grace window, and anything whose box score is not
    actually populated -- a half-scraped entry must be re-fetched, not
    kept forever because it happens to be old.
    """
    today = today or datetime.now(timezone.utc).date()
    out = {}
    for entry in (existing_region or {}).get("past_matches") or []:
        game_id = entry.get("base_game_id")
        if game_id is None:
            continue
        actual = entry.get("actual")
        if not isinstance(actual, dict) or not any(
                isinstance(side, dict) and side for side in actual.values()):
            continue  # no real player stats in it
        try:
            played = datetime.fromisoformat(str(entry.get("date"))).date()
        except (TypeError, ValueError):
            continue  # undateable, so the grace window cannot be applied
        if (today - played).days < REUSE_GRACE_DAYS:
            continue
        out[game_id] = entry
    return out

def scrape_region(region_key, current_tournament, historical_tournament, known=None):
    print(f"\n=== {region_key} ({current_tournament}) ===")

    print(f"Fetching team rosters (for team/role assignment)...")
    roster, team_urls = parse_team_rosters(current_tournament)
    print(f"  {len(roster)} players matched to a team/role")

    print(f"Fetching current-split player stats...")
    cur_players = parse_player_list(current_tournament)
    print(f"  {len(cur_players)} players")

    print(f"Fetching historical-split player stats...")
    hist_players = parse_player_list(historical_tournament)
    print(f"  {len(hist_players)} players")

    teams_payload = build_teams_payload(cur_players, hist_players, roster)
    print(f"  built payload for {len(teams_payload)} teams: {list(teams_payload.keys())}")

    print(f"Discovering this year's tournaments (regular season + any playoffs/finals stages)...")
    tournaments = discover_tournaments(current_tournament, team_urls)

    print(f"Fetching match list(s)...")
    matches = []
    seen_base_ids = set()
    for tournament in tournaments:
        t_matches = parse_match_list(tournament)
        for tm in t_matches:
            tm["tournament"] = tournament  # tagged here, before merging, so fetch_one below can carry it through to the final record
        # Dedup by base_game_id, not by team names -- two different
        # tournaments could plausibly list the same series if gol.gg's
        # own data overlaps at a boundary, and game_id is the one
        # genuinely unique identifier available here.
        new_matches = [m for m in t_matches if m["base_game_id"] not in seen_base_ids]
        seen_base_ids.update(m["base_game_id"] for m in new_matches)
        matches.extend(new_matches)
        print(f"  {tournament}: {len(t_matches)} completed series found ({len(new_matches)} new)")
    print(f"  {len(matches)} completed series total across {len(tournaments)} tournament(s)")

    # This is the dominant cost of the whole scrape — each match needs 2
    # page fetches (game 1 + game 2), and with 20-60+ matches per region
    # that's 100+ sequential requests if done one at a time. Fetching
    # several matches concurrently (bounded pool, not unlimited) cuts wall
    # time roughly in proportion to the worker count while staying modest
    # enough not to look like an attack on gol.gg.
    past_matches = []

    def fetch_one(m):
        kills, draft, per_game, fmt, maps_counted = series_prop_window_kills(
            m["base_game_id"], m["score"])
        left_score, right_score = (int(x) for x in m["score"].split("-"))
        winner = m["team_left"] if left_score > right_score else m["team_right"]
        entry = {
            # Stored so the next run can tell it already has this series.
            # gol.gg's own game id, and the only genuinely unique key here
            # -- two teams can play twice on one day in a round robin, so
            # (date, teamA, teamB) is not safe to reuse a box score on.
            "base_game_id": m["base_game_id"],
            "week": m["week"], "date": m["date"], "patch": m.get("patch"),
            "teamA": m["team_left"], "teamB": m["team_right"],
            "winner": winner, "score": m["score"],
            "actual": kills,
            "tournament": m.get("tournament", current_tournament),
            "stage": classify_tournament_stage(m.get("tournament", current_tournament)),
            # Series format and the number of maps "actual" actually sums
            # over. Both are stored because they can disagree: a 3-0 Bo5
            # is labelled Bo5 but only has 3 maps to begin with, and any
            # map can fail to parse. Consumers that need to compare
            # like-for-like should use maps_counted, not the label.
            "series_format": fmt,
            "maps_counted": maps_counted,
        }
        if draft:
            entry["draft"] = draft
        # Only stored when there's real per-player champion data to
        # carry — keeps old-shaped match records (from before this
        # existed) and any genuinely empty result equally harmless to
        # consumers that check for the key defensively.
        # Walks per_game (list of games) -> game (dict of team->players)
        # -> team_players (dict of player->stats) -> the actual
        # "champion" key. An earlier version of this check was one level
        # too shallow (checking team_players itself for "champion"
        # instead of each individual player's stats within it), which
        # meant it always evaluated False and per_game silently never
        # got attached — confirmed by a real run showing 0/241 matches
        # aggregatable despite champion parsing itself working.
        has_champion_data = any(
            player_stats.get("champion")
            for game in per_game if game
            for team_players in game.values()
            for player_stats in team_players.values()
        )
        if has_champion_data:
            entry["per_game"] = per_game
        return entry

    known = known or {}
    to_fetch = [m for m in matches if m["base_game_id"] not in known]
    reused = [known[m["base_game_id"]] for m in matches if m["base_game_id"] in known]
    past_matches.extend(reused)
    print(f"  {len(reused)} reused from the last run, {len(to_fetch)} to fetch"
          f"{' (first run since ids were stored — all of them)' if reused == [] and known == {} else ''}")

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(fetch_one, m): m for m in to_fetch}
        for future in as_completed(futures):
            m = futures[future]
            try:
                past_matches.append(future.result())
            except Exception as e:
                print(f"  ! skipped {m['team_left']} vs {m['team_right']}: {e}", file=sys.stderr)

    return {"teams": teams_payload, "past_matches": past_matches}


def main():
    # Merges into the EXISTING data.json rather than building from
    # scratch — a real, confirmed failure mode showed gol.gg timing out
    # for every single region in one run, which the previous version of
    # this function would have written as a payload with ZERO regions,
    # and if that got committed, would have wiped every region's
    # previously-good data with nothing at all. A transient external
    # outage shouldn't be able to destroy the live site's entire
    # dataset. Any region that fails THIS run now falls back to
    # whatever was already committed for it, with a clear warning that
    # it's stale rather than fresh.
    try:
        with open("data.json") as f:
            existing = json.load(f)
        existing_regions = existing.get("regions", {})
    except FileNotFoundError:
        existing_regions = {}

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regions": {},
    }
    failed = []
    for region_key, cfg in REGIONS.items():
        try:
            payload["regions"][region_key] = scrape_region(
                region_key, cfg["current"], cfg["historical"],
                known=reusable_past_matches(existing_regions.get(region_key)),
            )
        except Exception as e:
            print(f"! region {region_key} failed entirely: {e}", file=sys.stderr)
            failed.append(region_key)
            if region_key in existing_regions:
                print(f"  falling back to last committed data for {region_key} (stale, not "
                      f"refreshed this run)", file=sys.stderr)
                payload["regions"][region_key] = existing_regions[region_key]
            else:
                print(f"  ! no prior data.json data exists for {region_key} either — it will be "
                      f"genuinely missing this run", file=sys.stderr)

    if failed and len(failed) == len(REGIONS):
        print(f"\n! ALL {len(REGIONS)} regions failed this run (see errors above) — likely gol.gg "
              f"itself being unreachable, not a bug in this script.", file=sys.stderr)
        # Do not rewrite data.json in this case.
        #
        # Every region fell back to the copy already committed, so the only
        # thing a write would change is generated_at — stamping stale content
        # as fresh. That is worse than useless: check_data.py reads that
        # timestamp to decide whether a scrape actually happened, so a
        # re-stamped no-op run passed the freshness check while nothing had
        # been refreshed (run 116). Leaving the file untouched keeps its real
        # age, and exiting non-zero makes a total outage visible instead of
        # silently green.
        if existing_regions:
            print(f"  Leaving the committed data.json untouched rather than re-stamping stale "
                  f"content as fresh. Nothing to commit this run.", file=sys.stderr)
            sys.exit(1)
        print(f"  ! No committed data.json to fall back to either — writing what little there "
              f"is so the file exists at all.", file=sys.stderr)

    report_requests("stats scrape")

    with open("data.json", "w") as f:
        # Written minified: these files are machine-generated and never read
        # by hand, and indent=2 was about two thirds of the bytes
        # (data.json: 7.5MB -> 2.4MB). GitHub serves them gzipped, so the
        # win on the wire is smaller (~535KB -> ~340KB), but the browser
        # still parses the full decompressed text, and every run commits a
        # whole fresh copy.
        json.dump(payload, f, separators=(",", ":"))
    print(f"\nWrote data.json with regions: {list(payload['regions'].keys())}"
          f"{f' ({len(failed)} fell back to stale data: {failed})' if failed else ''}")

    # This script writes a FRESH data.json; the career field is folded in
    # afterwards by merge.py, not here. So a standalone manual run of
    # this script silently strips career data that was previously merged
    # — and since the LoL career weight sits at 0.8-0.85, that quietly
    # guts most of the model until merge.py runs again. A real instance
    # of exactly this went unnoticed until a diagnostic happened to trip
    # over it. The scheduled workflow already chains these correctly
    # (scrape_lcs -> scrape_career -> merge), so this warning is aimed at
    # manual runs, which is where the footgun actually lives.
    had_career = any(
        p.get("career")
        for rd in existing_regions.values()
        for team in rd.get("teams", {}).values()
        for p in team.get("players", [])
    )
    now_has_career = any(
        p.get("career")
        for rd in payload["regions"].values()
        for team in rd.get("teams", {}).values()
        for p in team.get("players", [])
    )
    if had_career and not now_has_career:
        # Expected, not alarming: this script writes the raw stats and
        # merge.py folds career in afterwards, so a fresh write never
        # carries it. Worded as a reminder rather than a warning because
        # it fires on EVERY workflow run, and a message that cries wolf
        # twice a day is one nobody reads on the day it matters.
        #
        # The real protection is check_data.py, which runs AFTER merge.py
        # and fails the run if career stopped reaching players. That is
        # the failure this text used to be standing in for, and it was
        # standing in the wrong place: here, the merge has not happened
        # yet, so there is nothing to detect.
        print("\n  (career not in this write yet — merge.py folds it in next; "
              "check_data.py fails the run if it does not)", file=sys.stderr)


if __name__ == "__main__":
    main()
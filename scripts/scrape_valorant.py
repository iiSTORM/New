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
import copy
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

# Set True after the first [MAP-ID DEBUG] print so we don't repeat it for
# every one of hundreds of matches — one real example is enough to confirm
# (or disprove) the data-game-id hypothesis.
_MAP_ID_DEBUG_DONE = False

# Current (Stage 2) and prior (Stage 1) event IDs per region, 2026 season.
# Update these when a region moves to its next stage/event.
REGIONS = {
    "VCT Americas": {"current": 2977, "historical": 2860},
    "VCT EMEA": {"current": 2976, "historical": 2863},
    "VCT Pacific": {"current": 2776, "historical": 2775},
    "VCT China": {"current": 2978, "historical": 2864},

    # Champions is not a region. It is one event that draws qualifying teams
    # out of all four, which does not fit the region-per-league shape -- see
    # playoffs_and_international_roadmap.md section 2a, which laid out this
    # exact choice. Treating it as another region is the option that reuses
    # the whole scraper unchanged, and its cost is that a team appears both
    # here and under its home region. That is harmless: they are separate
    # entries with the same team name, and the props matcher keys on the
    # team, not the region.
    #
    # historical is None on purpose. The prior events above are each one
    # region's previous split, and there is no equivalent for a one-off
    # international event: last year's Champions is a different roster list,
    # and the genuinely comparable form -- each team's own regional season --
    # lives under the four entries above rather than in any single event id.
    # Rather than point this at something that merely looks like a prior,
    # the fetch is skipped and "hist" is left empty, which the app already
    # handles by falling back to current-event form.
    #
    # Event ids are not discoverable programmatically; they come off the
    # vlr.gg URL. Champions 2026 runs Sep 24 - Oct 18 as event 2766.
    "VCT Champions": {"current": 2766, "historical": None},
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


def clean_team_name(name):
    """Normalizes vlr.gg team names that arrive as a full legal/regional
    name with the common display name appended in parentheses, e.g.
    "Guangzhou Huadu Bilibili Gaming(Bilibili Gaming)" or
    "Wuxi Titan Esports Club(Titan Esports Club)". These show up for VCT
    China teams because get_text() concatenates nested elements with no
    separator.

    Keeps the PARENTHESISED form, which is the name the team is actually
    known by and the one other sources use. Beyond cosmetics this guards
    against real data fragmentation: if any page renders only the short
    form, the same team would otherwise be tracked as two distinct
    entries and its stats split between them.

    Deliberately conservative — only fires on a trailing "(...)" with no
    nested parentheses and at least 3 characters inside, and only when
    something precedes it, so ordinary names pass through untouched.
    """
    m = re.match(r"^(.*?)\s*\(([^()]{3,})\)$", name.strip())
    if m and m.group(1).strip():
        return m.group(2).strip()
    return name.strip()


def parse_match(match_id, match_path):
    """Fetches one match page using its real scraped path. Always returns a
    dict describing the match; 'played' is False for matches that haven't
    happened yet (no final score available), in which case 'actual'/'patch'
    are None."""
    url = f"{BASE}{match_path}"
    html = get(url)
    soup = BeautifulSoup(html, "html.parser")

    team_links = soup.find_all("a", href=re.compile(r"^/team/\d+/"))
    team_names = []
    for a in team_links:
        name = clean_team_name(a.get_text(strip=True))
        if name and name not in team_names:
            team_names.append(name)
        if len(team_names) == 2:
            break
    # Fewer than 2 team links usually means a bracket slot whose opponent
    # isn't decided yet (upcoming, not an extraction failure) — keep the
    # match as an upcoming entry with "TBD" filling the unknown side,
    # rather than dropping it. The app's own merge step already filters
    # out true TBD-vs-TBD pairs before showing anything.
    team_a = team_names[0] if len(team_names) >= 1 else "TBD"
    team_b = team_names[1] if len(team_names) >= 2 else "TBD"

    header_text = soup.get_text(" ", strip=True)[:3000]
    # Anchored on the literal word "final", which precedes the real score
    # on every played match page (confirmed directly: "LEVIATÁN final 2 : 1
    # vs. Bo3 FURIA"). A bare "\d+ : \d+" search anywhere in the page also
    # matches scheduled kickoff times like "5:00 PM", which was silently
    # marking every upcoming match as played with a bogus score — this is
    # why upcoming counts were coming back as 0 for every region.
    score_m = re.search(r"\bfinal\s+(\d+)\s*:\s*(\d+)\b", header_text, re.IGNORECASE)
    played = bool(score_m)
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
    #
    # A THIRD issue found afterward: VLR match pages include a combined
    # "All Maps" totals view in addition to each individual map's
    # breakdown, all present in the same raw HTML (client-side JS just
    # toggles which one is visually shown — a non-JS scraper sees all of
    # them). That combined section's rows were appearing FIRST in document
    # order, so "first 2 occurrences = maps 1 and 2" was actually capturing
    # [combined total, real map 1] and dropping real map 2 entirely — which
    # is exactly why totals looked like "the set total" instead of a
    # maps-1+2 sum. VLR's convention (matching common open-source VLR
    # scrapers) wraps each map's section in an element carrying a
    # data-game-id attribute, with the combined view using "all" — real
    # individual maps use a real numeric ID. Skip rows under an "all"
    # container; only count rows under a real per-map container.
    # ---- First-kill / first-death discovery ----
    #
    # vlr.gg's stat table carries FK and FD columns -- who drew first
    # blood and who gave it up. That is the one thing in this data that
    # describes HOW a player plays rather than how much they produce,
    # and it is the missing ingredient for a question this project cannot
    # currently answer: whether style travels to an international field
    # better than raw rates do. Measured on what is scraped today, every
    # style proxy derivable from k/d/a is worth almost nothing (K/D ratio
    # correlates 0.07 at best with the model's errors, champion pool
    # 0.02), because style is already baked into a player's own rates.
    # FK/FD is not.
    #
    # Nothing is parsed out of it yet, deliberately. The column order on
    # this page has never been seen from here, and a guessed regex that
    # silently mis-binds would be far worse than not having the field:
    # k/d/a parsing below is untouched by any of this. So the run reports
    # the shape of a few real rows, and the next one can be written
    # against what it actually says.
    ROW_SHAPE_SAMPLES = 4
    row_shape_seen = []

    def note_row_shape(text):
        if len(row_shape_seen) < ROW_SHAPE_SAMPLES:
            numbers = re.findall(r"-?\d+(?:\.\d+)?%?", text)
            row_shape_seen.append((len(numbers), text[:240]))

    all_rounds_kda_re = re.compile(
        r"(\d+)\s+\d+\s+\d+\s*/\s*(\d+)\s+\d+\s+\d+\s*/\s*(\d+)\s+\d+\s+\d+"
    )
    # Fallback for rows rendered without the all/attack/defense split —
    # see the parse site below for why this exists and why the two
    # patterns are safe to try in sequence.
    combined_kda_re = re.compile(r"(\d+)\s*/\s*(\d+)\s*/\s*(\d+)")
    player_links = soup.find_all("a", href=re.compile(r"^/player/\d+/"))

    if not player_links:
        print(f"  ! match {match_id}: played but 0 player links found on the whole page "
              f"({len(html)} bytes)", file=sys.stderr)
        return result

    def find_game_id_container(node):
        """Walks up from `node` to the nearest ancestor carrying a
        data-game-id attribute (the per-map/all-maps section wrapper)."""
        n = node
        for _ in range(20):
            n = n.parent
            if n is None:
                return None
            if hasattr(n, "get") and n.get("data-game-id") is not None:
                return n
        return None

    global _MAP_ID_DEBUG_DONE
    totals = {team_a: {}, team_b: {}}
    map_occurrence_count = {team_a: {}, team_b: {}}  # per-player count of maps counted so far, capped at 2
    tag_to_team = {}  # first distinct team-tag seen -> team_a, second -> team_b
    unresolved = 0
    unresolved_samples = []  # real row text for anything still unparseable, so a future format change surfaces itself instead of silently dropping rows again
    skipped_all_maps = 0
    skipped_no_game_id = 0
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

        game_id_container = find_game_id_container(row)
        if game_id_container is None:
            # Couldn't find the wrapper at all — don't silently drop the row
            # over an unverified assumption. Fall back to counting it
            # normally (the previously-working behavior) rather than
            # regressing to 0 extracted players if this hypothesis is wrong.
            skipped_no_game_id += 1
            if not _MAP_ID_DEBUG_DONE:
                print(f"  [MAP-ID DEBUG] match {match_id}, player '{name}': no data-game-id "
                      f"ancestor found within 20 levels — dumping row's outer HTML:", file=sys.stderr)
                print(f"    {str(row)[:400]}", file=sys.stderr)
                _MAP_ID_DEBUG_DONE = True
        else:
            game_id_value = game_id_container.get("data-game-id", "")
            if not _MAP_ID_DEBUG_DONE:
                print(f"  [MAP-ID DEBUG] match {match_id}, player '{name}': "
                      f"data-game-id={game_id_value!r}", file=sys.stderr)
                _MAP_ID_DEBUG_DONE = True
            if str(game_id_value).lower() == "all":
                skipped_all_maps += 1
                continue  # the combined "All Maps" totals view, not a real individual map

        row_text = row.get_text(" ", strip=True)
        # vlr.gg renders per-map stat rows in TWO different shapes, and
        # only one of them was ever handled:
        #
        #   triple   "... 18 9 9 / 12 6 6 / 4 2 2 ..."   (all/atk/def per stat)
        #   combined "... 222 11 / 15 / 1 -4"            (combined only)
        #
        # Rows in the combined shape failed the triple regex and were
        # silently counted as `unresolved`, losing real player data. A
        # live run lost 10 matches' worth of rows entirely (VCT China
        # match 701044: 30/30 rows failed).
        #
        # SCOPE, corrected by the re-run rather than assumed: this is
        # China-specific in practice. Fixing it recovered 90 predictions
        # in VCT China (n 549 -> 639) and changed the other three regions
        # not at all (n 571/570/577 before and after). An earlier version
        # of this comment claimed every region was affected, reasoning
        # from match 701038 carrying BOTH shapes on one page — but 701038
        # is itself a China match, so that observation never supported
        # the wider claim. The fallback is still the right fix and is
        # safe everywhere; it just isn't doing anything outside China
        # today.
        #
        # Tried in this order, and the two patterns are verified DISJOINT
        # against real row text: the combined pattern does not match
        # triple rows (the spaces before each slash break it) and the
        # triple pattern does not match combined rows, so neither can
        # silently mis-parse the other's format.
        note_row_shape(row_text)
        m = all_rounds_kda_re.search(row_text) or combined_kda_re.search(row_text)
        if not m:
            unresolved += 1
            if len(unresolved_samples) < 3:
                unresolved_samples.append(row_text[:160])
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
            # so the 3rd occurrence of a given player (among real, non-"all"
            # map sections) is always their map-3 stats.
            continue
        slot["k"] += k
        slot["d"] += d
        slot["a"] += a
        map_occurrence_count[side_team][name] = maps_counted + 1

    if row_shape_seen and not _ROW_SHAPE_REPORTED:
        _report_row_shape(row_shape_seen)

    total_players = sum(len(v) for v in totals.values())
    global _ZERO_ROW_MATCH_COUNT
    if total_players == 0:
        _ZERO_ROW_MATCH_COUNT += 1
        if _ZERO_ROW_MATCH_COUNT <= 3:  # cap verbose output — deep debug above already covers the detail
            print(f"  ! match {match_id}: {len(player_links)} player links found but extracted 0 rows "
                  f"({unresolved} unresolved, {skipped_all_maps} skipped as 'all maps', "
                  f"{skipped_no_game_id} with no game-id wrapper found)", file=sys.stderr)
            for sample in unresolved_samples:
                print(f"      unparsed row text: {sample}", file=sys.stderr)
    elif unresolved > 0 or skipped_no_game_id > 0:
        print(f"  match {match_id}: {total_players} rows extracted OK, {unresolved} unresolved, "
              f"{skipped_all_maps} skipped as 'all maps', {skipped_no_game_id} counted via fallback "
              f"(no game-id wrapper found)", file=sys.stderr)
        for sample in unresolved_samples:
            print(f"      unparsed row text: {sample}", file=sys.stderr)

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
    if historical_event:
        hist_played, _ = collect(historical_event, "historical event")
    else:
        # A one-off international event has no prior split. Fetching a
        # stand-in would put a number in "hist" that looks like prior form
        # and is not, so this leaves it empty and the app falls back to
        # current-event form on its own.
        print("  no prior event configured — 'hist' stays empty and the app "
              "falls back to current-event form (see REGIONS)")
        hist_played = []

    # Build teams payload by aggregating each player's stats across all
    # matches in the CURRENT event (for "cur") and the HISTORICAL event
    # (for "hist") — no separate roster/player-list page needed, since team
    # association is already known per-match.
    def aggregate(matches):
        # name -> {team, k, d, a, games, kp_num, kp_den}
        #
        # kp is now genuinely computed rather than hardcoded to 0. It was
        # previously written as a literal 0 for every Valorant player,
        # which made kp_multiplier()'s `if not cur_kp: return 1.0` guard
        # fire every single time — so the kp weight was STRUCTURALLY
        # INERT for this whole game and could never influence a
        # prediction, no matter what the optimizer chose for it. This is
        # the identical bug already found and fixed on the CS2 side.
        #
        # Scale is 0-100 (a percentage), NOT a 0-1 fraction, to match
        # kp_multiplier's team_avg_kp = 66.0 reference constant. Getting
        # this wrong is not a no-op: CS2 briefly stored a fraction here
        # and the multiplier silently collapsed to a near-constant ~0.9
        # for every player regardless of their real participation.
        agg = {}
        for m in matches:
            for team, players in m["actual"].items():
                team_total_k = sum(kda["k"] for kda in players.values())
                for name, kda in players.items():
                    slot = agg.setdefault(name, {"team": team, "k": 0, "d": 0, "a": 0,
                                                 "games": 0, "kp_num": 0, "kp_den": 0})
                    slot["team"] = team  # last-seen team wins (handles roster moves reasonably)
                    slot["k"] += kda["k"]
                    slot["d"] += kda["d"]
                    slot["a"] += kda["a"]
                    slot["games"] += m["maps_played"]
                    # Accumulated as numerator/denominator rather than
                    # averaging per-match ratios, so matches with more
                    # rounds carry proportionally more weight instead of
                    # every match counting equally.
                    if team_total_k > 0:
                        slot["kp_num"] += kda["k"] + kda["a"]
                        slot["kp_den"] += team_total_k
        return agg

    def kp_pct(slot):
        return (slot["kp_num"] / slot["kp_den"] * 100) if slot["kp_den"] > 0 else 0

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
            "cur": {"g": cur["games"], "k": cur["k"] / g, "d": cur["d"] / g, "a": cur["a"] / g,
                    "kp": kp_pct(cur)},
            "hist": None,
        }
        if hist and hist["games"] > 0:
            hg = hist["games"]
            entry["hist"] = {"g": hg, "k": hist["k"] / hg, "d": hist["d"] / hg, "a": hist["a"] / hg,
                             "kp": kp_pct(hist)}
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


_ROW_SHAPE_REPORTED = False


def _report_row_shape(samples):
    """Print what a real stat row looks like, once per run.

    So the next change to this file can be written against the page as it
    is rather than as it is imagined. Printed once and only once: the
    point is a readable sample, not a transcript of every row scraped.
    """
    global _ROW_SHAPE_REPORTED
    _ROW_SHAPE_REPORTED = True
    print("\n  [row shape] vlr.gg stat rows, for working out where FK/FD sit:")
    for count, text in samples:
        print(f"    {count:3} numbers | {text}")
    print("    (k/d/a is read from the first three triples; FK/FD are not read yet)\n")


def lend_rosters_to_eventless_regions(regions):
    """Give a not-yet-started event the rosters of the regions it drew from.

    Rosters here are built by aggregating player stats out of PLAYED
    matches, so an event whose fixtures exist but whose first map has not
    been played arrives as a schedule with nobody in it -- confirmed on a
    real run: Champions returned 34 fixtures and 0 teams. Every posted line
    on that event then has no player to attach to, which is the whole
    reason for tracking it.

    The players are not missing, they are in their home regions with a
    season of form behind them, and that form is also the RIGHT baseline
    here: playoffs_and_international_roadmap.md 2a settles that a team's
    own regional season is the comparable history for an international
    event, not last year's edition of it. So the entry is copied across
    rather than recomputed from nothing.

    Only ever for a region with fixtures and NO teams at all. A region that
    scraped some teams is mid-event, not missing a roster, and overwriting
    it with regional form would discard the event's own -- which is the
    better data the moment a single map has been played.

    Returns a list of (region, borrowed, missing) for reporting.
    """
    donors = {}
    for region in regions.values():
        for team_name, team in (region.get("teams") or {}).items():
            donors.setdefault(team_name, team)

    report = []
    for key, region in regions.items():
        if region.get("teams"):
            continue
        fixtures = region.get("upcoming_matches") or []
        if not fixtures:
            continue
        wanted = sorted({name for m in fixtures
                         for name in (m.get("teamA"), m.get("teamB"))
                         if name and name != "TBD"})
        borrowed = {n: copy.deepcopy(donors[n]) for n in wanted if n in donors}
        missing = [n for n in wanted if n not in donors]
        if borrowed:
            region["teams"] = borrowed
            # Flagged rather than silent: these players' numbers come from
            # their regional season, not from this event.
            region["rosters_from_home_regions"] = True
        report.append((key, borrowed, missing))
    return report


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

    for key, borrowed, missing in lend_rosters_to_eventless_regions(payload["regions"]):
        print(f"\n{key}: no played matches yet, so rosters come from the teams' "
              f"home regions — {len(borrowed)} team(s) resolved")
        if missing:
            print(f"  {len(missing)} team(s) not found in any region, so their "
                  f"fixtures stay unprojected: {missing}")

    with open("valorant_data.json", "w") as f:
        # Written minified: these files are machine-generated and never read
        # by hand, and indent=2 was about two thirds of the bytes
        # (data.json: 7.5MB -> 2.4MB). GitHub serves them gzipped, so the
        # win on the wire is smaller (~535KB -> ~340KB), but the browser
        # still parses the full decompressed text, and every run commits a
        # whole fresh copy.
        json.dump(payload, f, separators=(",", ":"))
    print(f"\nWrote valorant_data.json with regions: {list(payload['regions'].keys())}")
    if _ZERO_ROW_MATCH_COUNT > 0:
        print(f"NOTE: {_ZERO_ROW_MATCH_COUNT} matches extracted 0 player rows total this run "
              f"(see [DEEP DEBUG] block above for the actual DOM structure)", file=sys.stderr)


if __name__ == "__main__":
    main()
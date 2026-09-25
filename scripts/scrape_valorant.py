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
        "scoreA": score_a, "scoreB": score_b, "actual": None, "per_game": None, "maps_played": None,
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
    # ---- First kills and first deaths ----
    #
    # The one thing on this page describing HOW a player plays rather
    # than how much they produce: who wins the opening duel, and who
    # loses it. Every style proxy derivable from k/d/a was measured
    # against the model's errors and came back near zero (K/D ratio 0.07
    # at best, champion pool 0.02), because style is already inside a
    # player's own rates. Opening duels are not.
    #
    # The column order was read off a real run rather than guessed:
    #
    #   name TAG | rating x3 | ACS x3 | K x3 / D x3 / A x3 | K-D x3
    #        | KAST% x3 | ADR x3 | HS% x3 | FK x3 | FD x3 | FK-FD x3
    #
    # Read from the tail, after the last percent sign, because there are
    # two %-triples on the row (KAST and HS) and anchoring on the first
    # would land three columns early.
    #
    # The FK-FD column is a checksum and is treated as one: a parse is
    # accepted only when fk - fd equals it. That makes a mis-bind
    # self-detecting instead of silent, and it is what refuses the
    # combined-shape rows, which carry no duel columns at all.

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
    # One entry per map, in map order, holding every map the series
    # played. `totals` above stops at two maps on purpose; this does not,
    # because a line posted over map 1 or over maps 1-3 has to be graded
    # against the maps it names rather than refused for want of a
    # breakdown.
    per_game = []
    map_occurrence_count = {team_a: {}, team_b: {}}  # per-player count of maps seen so far
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
        STAT_ROW_COUNTS["rows"] += 1
        extra = parse_stat_row(row_text, m)
        if extra is not None:
            STAT_ROW_COUNTS["parsed"] += 1
        # Document order on this page follows map order (map 1's roster,
        # then map 2's, then map 3's if it happened), so the Nth
        # occurrence of a given player among real, non-"all" map sections
        # is their map-N stats. That index is what files each row into
        # per_game, and what decides whether it also counts toward the
        # series total.
        map_index = map_occurrence_count[side_team].setdefault(name, 0)
        map_occurrence_count[side_team][name] = map_index + 1
        while len(per_game) <= map_index:
            per_game.append({})
        per_game[map_index].setdefault(side_team, {})[name] = {"k": k, "d": d, "a": a}

        if map_index >= MAPS_IN_SERIES_TOTAL:
            # This is the app's convention across both games: only maps/games
            # 1 and 2 of a series count toward "actual" totals, regardless of
            # whether the series went to a 3rd map — matches series_g1_g2_kills
            # on the LCS side exactly. The later maps are not discarded any
            # more, they are filed in per_game above.
            continue
        slot["k"] += k
        slot["d"] += d
        slot["a"] += a
        if extra is not None:
            # Only alongside a map that counted, so these cover exactly
            # the same maps as k/d/a.
            for field, value in extra.items():
                slot[field] = slot.get(field, 0) + value
            slot["rows"] = slot.get("rows", 0) + 1

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
    result["per_game"] = per_game
    # Fixed at 2 to match the "maps/games 1+2 only" convention used across
    # both games — every Bo3/Bo5 series has at least 2 maps by definition,
    # so this is safe even though the series itself may have gone longer.
    # It describes what `actual` sums over, not how long the series ran;
    # the real map count is len(per_game).
    result["maps_played"] = MAPS_IN_SERIES_TOTAL
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
                                                 "games": 0, "kp_num": 0, "kp_den": 0,
                                                 "extra_games": 0, "extra_rows": 0,
                                                 **{f: 0 for f in EXTRA_STAT_FIELDS}})
                    slot["team"] = team  # last-seen team wins (handles roster moves reasonably)
                    slot["k"] += kda["k"]
                    slot["d"] += kda["d"]
                    slot["a"] += kda["a"]
                    slot["games"] += m["maps_played"]
                    # Opening duels carry their OWN game count. A row whose
                    # checksum failed contributes k/d/a and no duels, so
                    # dividing duels by `games` would quietly understate
                    # anyone who had one -- the two rates need separate
                    # divisors or they are not rates of the same thing.
                    if kda.get("rows"):
                        # These carry their own counters. A row whose
                        # checksums failed contributes k/d/a and nothing
                        # else, so sharing the k/d/a divisor would quietly
                        # understate anyone that happened to.
                        for field in EXTRA_STAT_FIELDS:
                            slot[field] = slot.get(field, 0) + kda.get(field, 0)
                        slot["extra_games"] += m["maps_played"]
                        slot["extra_rows"] += kda["rows"]
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
                    "kp": kp_pct(cur), **extra_rates(cur)},
            "hist": None,
        }
        if hist and hist["games"] > 0:
            hg = hist["games"]
            entry["hist"] = {"g": hg, "k": hist["k"] / hg, "d": hist["d"] / hg, "a": hist["a"] / hg,
                             "kp": kp_pct(hist), **extra_rates(hist)}
        teams[team]["players"].append(entry)
    print(f"  built payload for {len(teams)} teams: {list(teams.keys())}")
    report_first_duels()

    def as_record(m):
        winner = m["teamA"] if (m["scoreA"] or 0) > (m["scoreB"] or 0) else m["teamB"]
        return {
            # vlr.gg's own id. Already fetched, never stored, and the only
            # safe key for de-duplicating a match across runs -- two teams
            # can meet twice in one day, so date+teams is not an identity.
            "match_id": m["match_id"],
            "week": None, "date": m["date"], "patch": m["patch"],
            "teamA": m["teamA"], "teamB": m["teamB"],
            "winner": winner, "score": f"{m['scoreA']}-{m['scoreB']}",
            "actual": m["actual"], "games": m["maps_played"],
            # One entry per map actually played. `actual` is the maps 1-2
            # total and stays one; this is what lets a map-1 line and a
            # maps-1-3 line each be graded over the maps they name.
            "per_game": m.get("per_game") or [],
        }

    past_matches = [as_record(m) for m in cur_played]
    # The prior event's matches, which were fetched, aggregated into the
    # "hist" tier, and then thrown away.
    #
    # That left every player's per-match history capped at the CURRENT
    # event: a median of 8 maps, against LoL's 14 and a measured
    # relationship between evidence and accuracy that is steep exactly
    # there (a projection on 4 maps or fewer realises 52% of its edge; on
    # more than 12, 98%). Worse, the cap resets to zero the day an event
    # rolls over, which is precisely when the board is busiest.
    #
    # Kept separate from past_matches rather than appended to it. That
    # list is what THIS event has played and feeds standings and the Past
    # Results tab, where a previous split's games would simply be wrong.
    # historyPoolFor already draws the distinction on the app side; this
    # gives it something to draw from.
    history_matches = [as_record(m) for m in hist_played]

    upcoming_matches = [
        {"date": m["date"], "teamA": m["teamA"], "teamB": m["teamB"], "block": None}
        for m in cur_upcoming if m["teamA"] != "TBD" and m["teamB"] != "TBD"
    ]

    return {"teams": teams, "past_matches": past_matches,
            "history_matches": history_matches, "upcoming_matches": upcoming_matches}


# How many maps a stored `actual` total sums over. Fixed: widening it
# would silently rewrite every per-map rate the model has ever been
# fitted against. Windows other than this one are answered by the
# per-map breakdown instead.
MAPS_IN_SERIES_TOTAL = 2

STAT_ROW_COUNTS = {"rows": 0, "parsed": 0}

# Everything parse_stat_row returns, which is what gets summed per player.
EXTRA_STAT_FIELDS = ("acs", "adr", "kast", "hs", "rating", "fk", "fd")

# Everything a triple-shape vlr.gg stat row carries after the player and
# team tag, in order. Each name is three columns wide -- all rounds, then
# attack, then defence -- and only the all-rounds figure is kept.
#
#   rating | ACS | K / D / A | K-D | KAST% | ADR | HS% | FK | FD | FK-FD
#
# Read off a production run rather than guessed. K/D/A is located by the
# existing regex and everything else is positioned relative to it, which
# is why this cannot drift away from the numbers already trusted.
_TAIL_COLUMNS = ["kd_diff", "kast", "adr", "hs", "fk", "fd", "fd_diff"]

_NUM = re.compile(r"[+-]?\d+(?:\.\d+)?")


def parse_stat_row(row_text, kda_match):
    """Every all-rounds figure on one stat row, or None if unreadable.

    None rather than a guess. The row carries its own arithmetic in two
    places -- kills minus deaths, and first kills minus first deaths --
    and both must agree before anything here is believed. A mis-bound
    column is then self-detecting instead of silently becoming a number
    the model trusts.

    Returns per-map figures for the maps this row covers, EXCEPT rating,
    kast and hs, which are already per-round or per-cent and are carried
    through as they are.
    """
    if not kda_match:
        return None
    k, d, a = (int(x) for x in kda_match.groups())

    head = _NUM.findall(row_text[:kda_match.start()])
    tail = _NUM.findall(row_text[kda_match.end():])
    if len(head) < 6 or len(tail) < len(_TAIL_COLUMNS) * 3:
        return None

    cols = {name: tail[i * 3] for i, name in enumerate(_TAIL_COLUMNS)}
    try:
        kd_diff = int(cols["kd_diff"])
        fk, fd, fd_diff = int(cols["fk"]), int(cols["fd"]), int(cols["fd_diff"])
        out = {
            "acs": float(head[-3]),
            "rating": float(head[-6]),
            "kast": float(cols["kast"]),
            "adr": float(cols["adr"]),
            "hs": float(cols["hs"]),
            "fk": fk,
            "fd": fd,
        }
    except (ValueError, IndexError):
        return None

    # Both checksums, together. Either one failing means the columns are
    # not where this thinks they are.
    if k - d != kd_diff or fk - fd != fd_diff:
        return None
    return out


def parse_first_duels(row_text):
    """(first kills, first deaths), or None. Kept as its own entry point
    because it is the field the style work actually needs; everything
    else on the row is along for the ride."""
    kda = re.search(r"(\d+)\s+\d+\s+\d+\s*/\s*(\d+)\s+\d+\s+\d+\s*/\s*(\d+)\s+\d+\s+\d+",
                    row_text)
    parsed = parse_stat_row(row_text, kda)
    return (parsed["fk"], parsed["fd"]) if parsed else None


def report_first_duels():
    seen, parsed = STAT_ROW_COUNTS["rows"], STAT_ROW_COUNTS["parsed"]
    if not seen:
        return
    print(f"  extra stat columns read on {parsed}/{seen} rows "
          f"({100.0 * parsed / seen:.0f}%): acs, kast, adr, hs, fk, fd")
    if parsed == 0:
        print("  ! no row passed both checksums — the column order has moved, so "
              "these fields will be absent rather than wrong", file=sys.stderr)


def extra_rates(slot):
    """The columns beyond k/d/a as rates, or nothing at all.

    Absent rather than zero when no row was readable: a player who never
    contests an opening is a real and different thing from a player whose
    rows this scraper could not parse, and writing 0 for both would make
    them indistinguishable downstream.

    fk and fd are counts, so they become per-map rates. acs, adr, kast,
    hs and rating are already per-round or per-cent, so they are averaged
    over the rows they came from -- dividing those by maps would halve
    them.

    Module level rather than nested, so a test can call it instead of
    reading the source and hoping. A mutation that made the guard below
    unreachable passed a source-grep version of that test.
    """
    maps = slot.get("extra_games") or 0
    rows = slot.get("extra_rows") or 0
    if not maps or not rows:
        return {}
    out = {"fk": slot["fk"] / maps, "fd": slot["fd"] / maps, "eg": maps}
    for field in ("acs", "adr", "kast", "hs", "rating"):
        out[field] = slot.get(field, 0) / rows
    return out


# How many matches of history each team keeps, beyond what the current
# event has played. Deep enough to cover an event rollover twice over --
# a team plays 10-14 maps in a split -- and bounded so the file cannot
# grow without limit as seasons accumulate. At ~1.3KB per match record
# this caps the history at roughly a megabyte across all five regions.
MATCHES_KEPT_PER_TEAM = 30


def legacy_key(m):
    """What a record written before match_id existed can be identified by.

    A FALLBACK, not a scheme: two teams can meet twice in one day, so it
    pulls in the score to separate a double-header. Every record has one
    of these, including records that also have an id -- which is the
    point. It is the only thing a stored copy and a freshly fetched copy
    of the same match have in common across the transition.
    """
    return ("legacy", m.get("date"), m.get("teamA"), m.get("teamB"), m.get("score"))


def match_key(m):
    """Identity for de-duplicating a match across runs: vlr.gg's own id
    when present, the composite otherwise."""
    mid = m.get("match_id")
    if mid:
        return ("id", str(mid))
    return legacy_key(m)


def merge_history_matches(previous, fresh, current, per_team=MATCHES_KEPT_PER_TEAM):
    """Everything a region's players have on record but have not played
    at the current event, newest first and capped per team.

    `previous` is last run's accumulated history AND last run's
    past_matches together -- an event that has rolled over is exactly the
    case this exists for, and its matches were the current event's a run
    ago. `fresh` is this run's prior-event fetch. `current` is this run's
    past_matches, whose ids are excluded: a match belongs in one list or
    the other, never both, or every consumer that concatenates them
    counts it twice.

    A match this run fetched wins over the same match stored before,
    because the stored one may predate a fix to how stats are read -- the
    opening-duel columns landed exactly that way.
    """
    # BOTH keys per current match, and a candidate is tested on both of
    # its own. The first run after match_id landed proved why: last
    # run's file held the same matches with no ids, so the stored copy
    # keyed as legacy and the fresh copy keyed as id, the exclusion
    # matched neither, and every one of the 247 current matches ended up
    # in BOTH lists -- counted twice in every rate and every evidence
    # count, while the depth it was meant to add looked twice as good as
    # it was.
    held = set()
    for m in current or []:
        held.add(match_key(m))
        held.add(legacy_key(m))

    by_key = {}
    for m in list(previous or []) + list(fresh or []):
        if not m or not m.get("teamA") or not m.get("teamB"):
            continue
        if match_key(m) in held or legacy_key(m) in held:
            continue
        by_key[match_key(m)] = m  # fresh overwrites previous

    # The same collision inside the pool itself: a stored legacy copy and
    # a fetched copy of one match are two entries under two keys. The one
    # carrying the id wins, since it is the one this run fetched.
    identified = {legacy_key(m) for k, m in by_key.items() if k[0] == "id"}
    by_key = {k: m for k, m in by_key.items()
              if k[0] == "id" or k not in identified}

    ordered = sorted(by_key.values(), key=lambda m: (m.get("date") or ""), reverse=True)

    # The current event's matches count against each team's allowance, so
    # a team mid-season keeps a fixed depth rather than growing without
    # bound while a team that has not started yet gets the full window.
    seen_per_team = {}
    for m in current or []:
        for t in (m.get("teamA"), m.get("teamB")):
            seen_per_team[t] = seen_per_team.get(t, 0) + 1

    kept = []
    for m in ordered:
        sides = (m["teamA"], m["teamB"])
        # Kept while EITHER side still has room, so a busy team does not
        # evict the only match a quiet opponent has on file.
        if any(seen_per_team.get(t, 0) < per_team for t in sides):
            kept.append(m)
            for t in sides:
                seen_per_team[t] = seen_per_team.get(t, 0) + 1
    return kept


def load_previous_regions(path="valorant_data.json"):
    """Last run's regions, or {} when there is no readable file.

    Never fatal: a missing or corrupt file means this run behaves exactly
    as every run did before accumulation existed, which is a shallower
    result but a working one.
    """
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        print(f"  no previous valorant_data.json to merge with "
              f"({e.__class__.__name__}) — this run starts from the events alone")
        return {}
    return data.get("regions") or {}


def accumulate_history(regions, previous_regions):
    """Fold last run's matches into each region's history. Reports
    (region, before, after) per region for the run log."""
    report = []
    for key, region in regions.items():
        prior = previous_regions.get(key) or {}
        before = len(region.get("history_matches") or [])
        region["history_matches"] = merge_history_matches(
            list(prior.get("history_matches") or []) + list(prior.get("past_matches") or []),
            region.get("history_matches"),
            region.get("past_matches"),
        )
        report.append((key, before, len(region["history_matches"])))
    return report


def lend_rosters_from_home_regions(regions):
    """Give a fixture's teams the rosters of the regions they drew from.

    Rosters here are built by aggregating player stats out of PLAYED
    matches, so a team that has fixtures at an event but has not yet
    played a map there arrives as a name with nobody behind it. Every
    posted line on that team then has no player to attach to, which is
    the whole reason for tracking the event.

    The players are not missing, they are in their home regions with a
    season of form behind them, and that form is also the RIGHT baseline
    here: playoffs_and_international_roadmap.md 2a settles that a team's
    own regional season is the comparable history for an international
    event, not last year's edition of it. So the entry is copied across
    rather than recomputed from nothing.

    PER TEAM, which is the whole correction. This was all-or-nothing on
    the region -- lend only to an event with no teams at all -- and that
    rule collapsed the moment the event's first map was played. A real
    run caught it: Champions had played exactly one match, so the two
    teams in it were the region's entire roster and the other FOURTEEN
    teams with fixtures, every one of them available in its home region,
    were left unprojectable. An event fills up one match at a time, so
    "has any teams" was never the same question as "has this team".

    A team that HAS an entry here keeps it. That half of the old rule was
    right: once a team has played at the event, the event's own numbers
    are the better data and must not be overwritten with regional form.

    Each borrowed entry carries from_home_region, so the app can say
    whose season these numbers actually are rather than inferring it from
    a region-wide flag that is now true for some teams and false for
    others.

    Returns a list of (region, borrowed, missing) for reporting.
    """
    # Built once, over every region including the one being filled. A
    # team that is already here is skipped by the `name in own` check
    # below before the index is ever consulted, so excluding this region
    # from it as well was a second guard on the same case -- and a branch
    # no test could reach, which is how a guard stops being one.
    # A team can sit in more than one region -- an international event
    # is tracked as its own region, so its participants appear there AND
    # under their league. The donor is the region where they have
    # actually played the most, not whichever came first in REGIONS
    # order: the point of borrowing is a season of form, and the entry
    # with two matches behind it is not the one to copy when the entry
    # with twelve exists.
    played_in = {}
    for donor_key, donor_region in regions.items():
        for m in donor_region.get("past_matches") or []:
            for side in (m.get("teamA"), m.get("teamB")):
                if side:
                    played_in[(donor_key, side)] = played_in.get((donor_key, side), 0) + 1
    donors = {}
    for donor_key, donor_region in regions.items():
        for team_name, team in (donor_region.get("teams") or {}).items():
            best = donors.get(team_name)
            depth = played_in.get((donor_key, team_name), 0)
            if best is None or depth > best[0]:
                donors[team_name] = (depth, donor_key, team)
    donors = {name: (donor_key, team) for name, (_, donor_key, team) in donors.items()}

    report = []
    for key, region in regions.items():
        fixtures = region.get("upcoming_matches") or []
        if not fixtures:
            continue
        own = region.setdefault("teams", {})
        wanted = sorted({name for m in fixtures
                         for name in (m.get("teamA"), m.get("teamB"))
                         if name and name != "TBD"})
        borrowed, missing = {}, []
        for name in wanted:
            if name in own:
                continue
            if name not in donors:
                missing.append(name)
                continue
            donor_key, donor = donors[name]
            entry = copy.deepcopy(donor)
            # Flagged rather than silent: these players' numbers come
            # from their regional season, not from this event.
            entry["from_home_region"] = donor_key
            own[name] = entry
            borrowed[name] = entry
        if borrowed:
            # Kept for the region-level consumers that predate the
            # per-team flag. It now means "some roster here is borrowed",
            # which is what it always meant in practice.
            region["rosters_from_home_regions"] = True
        if borrowed or missing:
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

    for key, before, after in accumulate_history(payload["regions"],
                                                 load_previous_regions()):
        print(f"{key}: {after} match(es) of history beyond this event "
              f"({before} from this run's prior-event fetch)")

    for key, borrowed, missing in lend_rosters_from_home_regions(payload["regions"]):
        print(f"\n{key}: {len(borrowed)} team(s) have fixtures but no maps played "
              f"here yet, so their rosters come from their home regions: "
              f"{sorted(borrowed)}")
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
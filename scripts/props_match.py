#!/usr/bin/env python3
"""Normalising and matching betting props onto this app's players.

Kept separate from the fetching so the hard part — deciding that a line
posted for "Faker" is the same person as gol.gg's "Faker", for maps 1-2, on
the stat this app calls "kills" — is pure logic that can be tested without
touching a provider.

Three things have to line up before a prop can be compared with a
projection, and getting any of them wrong produces a confident, wrong edge:

  player  — names differ between the stats source and the sportsbook.
  stat    — "Kills" and "MAPS 1-2 Kills" are kills; "Kills (Combo)" is
            NOT, it is two players added together.
  window  — and this is the one that silently ruins everything: a line for
            maps 1-2 must be compared against a projection over maps 1-2.
            This repo already learned that lesson once, when predicting two
            maps for every match under-predicted every Bo5 by a third.
"""
import re
import unicodedata

# The stat names this app models, and the provider spellings seen for each.
# Anything unrecognised is reported rather than guessed at — a mis-mapped
# stat is worse than a missing one.
STAT_ALIASES = {
    "kills": {"kills", "kill", "k"},
    "deaths": {"deaths", "death", "d"},
    "assists": {"assists", "assist", "a"},
    # CS2 only. It sat in UNMODELLED_STATS below until a scrape run
    # established that bo3.gg's players_stats does carry headshots, which
    # backfilled 1779 player-series in one pass because that scraper
    # rebuilds its history rather than appending. The model was tuned on
    # the resulting 911 backtest rows before this moved.
    "headshots": {"headshots", "headshot", "hs"},
}

# Stats this app reads perfectly well and does not MODEL, because there is
# no history anywhere in the repo to project them from -- the per-player
# record is k, d, a and nothing else.
#
# Separated from "unrecognised" because the two mean opposite things to
# whoever reads the funnel. An unrecognised label is a parsing gap and a
# bug to chase; a headshots line is a working provider, a correctly parsed
# label, and a stat we have chosen not to cover. Lumping them together
# hides both: real parse failures get excused as "probably just
# headshots", and the size of what is not covered never shows up at all.
#
# Headshots used to be the example here and has since made exactly that
# journey: scrape_cs2.py started recording them, the history backfilled,
# the weights were tuned on it, and it moved to STAT_ALIASES. The same
# route is open to anything left in this table.
UNMODELLED_STATS = {
    "points": {"points", "point", "pts"},
}

# A "Combo" projection is two or more PLAYERS added together into one line,
# not a variant of a single player's stat. Comparing it against one player's
# projection is not slightly wrong: a two-player line sits at roughly double
# a single player's, so a 9.5 projection against a 19.5 combo reads as a
# ten-kill edge on the under, which is the most confident wrong number this
# code can produce.
#
# It has to be checked BEFORE the window patterns. "MAPS 1-2 Kills (Combo)"
# matches the maps 1-2 pattern first, and the loop below breaks on the first
# hit, so the combo marker would never be reached -- which is exactly how 18
# of these passed as ordinary kills lines in a real payload.
COMBO_PATTERN = re.compile(r"\bcombo\b", re.I)

# Map-window phrasing -> how many maps the line covers. Providers write this
# into the stat label rather than a separate field.
WINDOW_PATTERNS = [
    (re.compile(r"\bmaps?\s*1\s*[-–]\s*3\b", re.I), 3),
    (re.compile(r"\bmaps?\s*1\s*[-–]\s*2\b", re.I), 2),
    (re.compile(r"\bmaps?\s*1\s*[-–]\s*5\b", re.I), 5),
    (re.compile(r"\bmap\s*1\b", re.I), 1),
    (re.compile(r"\bmap\s*2\b", re.I), 1),
    (re.compile(r"\bmap\s*3\b", re.I), 1),
]


def normalize_name(name):
    """Fold a player handle to a comparable key.

    Handles are not names: "sh1ro", "ZywOo", "Leviatán". Case, accents,
    spacing and punctuation all vary between sources, so they are stripped
    rather than trusted. Digits are KEPT — "sh1ro" and "shiro" could be
    different people, and silently merging two players is exactly the kind
    of error that produces a confident wrong number.
    """
    if not name or not isinstance(name, str):
        return ""
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", folded.lower())


def parse_stat(label):
    """('kills', 2) from 'MAPS 1-2 Kills'. (None, None) if not understood.

    Returns the window as a map count, or None when the label does not say
    — an unstated window is not an assumption worth making.
    """
    if not label or not isinstance(label, str):
        return None, None
    # Refused here as well as in match_props, so the function is safe to
    # call on its own: a combo is not a stat this app models, whatever
    # window it states.
    if COMBO_PATTERN.search(label):
        return None, None
    window = None
    for pattern, maps in WINDOW_PATTERNS:
        if pattern.search(label):
            window = maps
            break
    cleaned = re.sub(r"\bmaps?\s*\d+\s*[-–]?\s*\d*\b", " ", label, flags=re.I)
    cleaned = re.sub(r"[^a-zA-Z ]", " ", cleaned).lower()
    words = set(cleaned.split())
    for stat, aliases in STAT_ALIASES.items():
        if words & aliases:
            return stat, window
    return None, window


def unmodelled_stat(label):
    """The name of a stat this app understands but does not model, or None.

    Runs the same cleaning as parse_stat so the window prefix cannot
    prevent a match: "MAPS 1-2 Headshots" is headshots.
    """
    if not label or not isinstance(label, str):
        return None
    cleaned = re.sub(r"\bmaps?\s*\d+\s*[-–]?\s*\d*\b", " ", label, flags=re.I)
    cleaned = re.sub(r"[^a-zA-Z ]", " ", cleaned).lower()
    words = set(cleaned.split())
    for stat, aliases in UNMODELLED_STATS.items():
        if words & aliases:
            return stat
    return None


def build_roster_index(regions):
    """{normalized handle: [(region, team, real name), ...]} for every player.

    A handle can sit on two teams -- a stand-in, a transfer a roster file
    has not caught up with, or two players who picked the same name. A real
    CS2 board had two of them, both with lines posted.

    Every entry is kept rather than the handle being dropped, because the
    provider states the team on each prop and that resolves most of them
    exactly: both of those two named a team that appears verbatim in the
    roster file. match_props does the resolving and refuses what it cannot
    settle -- dropping here would throw away a line that is perfectly
    attributable, and guessing would put it on the wrong team's match.
    """
    index = {}
    for region_key, region in (regions or {}).items():
        for team_name, team in (region.get("teams") or {}).items():
            for player in (team.get("players") or []):
                key = normalize_name(player.get("name"))
                if not key:
                    continue
                entry = (region_key, team_name, player.get("name"))
                if entry not in index.setdefault(key, []):
                    index[key].append(entry)
    ambiguous = sorted(k for k, entries in index.items()
                       if len({e[1] for e in entries}) > 1)
    return index, ambiguous


def match_props(raw_props, roster_index):
    """Attach each prop to a rostered player.

    Returns (matched, unmatched). Nothing is dropped quietly: every prop
    that fails to match comes back with the reason, because a silent drop
    rate is indistinguishable from a provider outage.
    """
    matched, unmatched = [], []
    for prop in raw_props or []:
        label = prop.get("stat_label")
        # Before anything else, and reported by its own name rather than as
        # an unrecognised stat: a combo is perfectly recognisable, it just
        # belongs to more than one player.
        if isinstance(label, str) and COMBO_PATTERN.search(label):
            unmatched.append({**prop, "reason": "combo line covers more than one player"})
            continue
        stat, window = parse_stat(label)
        key = normalize_name(prop.get("player_name"))
        target = roster_index.get(key)
        if stat is None:
            known = unmodelled_stat(label)
            reason = (f"{known} is not a stat this app projects yet"
                      if known else "unrecognised stat")
            unmatched.append({**prop, "reason": reason})
            continue
        if window is None:
            unmatched.append({**prop, "reason": "map window not stated"})
            continue
        if not target:
            unmatched.append({**prop, "reason": "player not on any roster"})
            continue
        if len({entry[1] for entry in target}) > 1:
            # The handle is on more than one team. The provider names the
            # team on the prop, so use it: without that the line would land
            # on whichever match happened to be nearest in time, which is
            # the wrong team's opponent half the time.
            team_key = normalize_name(prop.get("team"))
            narrowed = [e for e in target if team_key and normalize_name(e[1]) == team_key]
            if len(narrowed) != 1:
                unmatched.append({**prop, "reason": "handle is on more than one roster"})
                continue
            target = narrowed
        entry = target[0]
        try:
            line = float(prop.get("line"))
        except (TypeError, ValueError):
            unmatched.append({**prop, "reason": "line is not a number"})
            continue
        region, team, real_name = entry
        matched.append({
            "player": real_name,
            "region": region,
            "team": team,
            "stat": stat,
            "maps": window,
            "line": line,
            # Kept so the frontend can tell the market line from the
            # alternate-payout lines posted beside it, and tell one match's
            # line from another's when a player appears in two on one day.
            "odds_type": prop.get("odds_type"),
            "provider": prop.get("provider"),
            "start_time": prop.get("start_time"),
        })
    return matched, unmatched

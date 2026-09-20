#!/usr/bin/env python3
"""Normalising and matching betting props onto this app's players.

Kept separate from the fetching so the hard part — deciding that a line
posted for "Faker" is the same person as gol.gg's "Faker", for maps 1-2, on
the stat this app calls "kills" — is pure logic that can be tested without
touching a provider.

Three things have to line up before a prop can be compared with a
projection, and getting any of them wrong produces a confident, wrong edge:

  player  — names differ between the stats source and the sportsbook.
  stat    — "Kills", "Kills (Combo)", "MAPS 1-2 Kills" all mean kills here.
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
}

# Map-window phrasing -> how many maps the line covers. Providers write this
# into the stat label rather than a separate field.
WINDOW_PATTERNS = [
    (re.compile(r"\bmaps?\s*1\s*[-–]\s*3\b", re.I), 3),
    (re.compile(r"\bmaps?\s*1\s*[-–]\s*2\b", re.I), 2),
    (re.compile(r"\bmaps?\s*1\s*[-–]\s*5\b", re.I), 5),
    (re.compile(r"\bmap\s*1\b", re.I), 1),
    (re.compile(r"\bmap\s*2\b", re.I), 1),
    (re.compile(r"\bmap\s*3\b", re.I), 1),
    (re.compile(r"\bcombo\b", re.I), None),   # ambiguous — refuse to guess
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


def build_roster_index(regions):
    """{normalized name: (region, team, real name)} for every rostered player.

    A handle appearing on two rosters is dropped rather than resolved: an
    ambiguous match attached to the wrong player is worse than no line.
    """
    index, seen_twice = {}, set()
    for region_key, region in (regions or {}).items():
        for team_name, team in (region.get("teams") or {}).items():
            for player in (team.get("players") or []):
                key = normalize_name(player.get("name"))
                if not key:
                    continue
                if key in index and index[key][2] != player.get("name"):
                    seen_twice.add(key)
                index[key] = (region_key, team_name, player.get("name"))
    for key in seen_twice:
        index.pop(key, None)
    return index, sorted(seen_twice)


def match_props(raw_props, roster_index):
    """Attach each prop to a rostered player.

    Returns (matched, unmatched). Nothing is dropped quietly: every prop
    that fails to match comes back with the reason, because a silent drop
    rate is indistinguishable from a provider outage.
    """
    matched, unmatched = [], []
    for prop in raw_props or []:
        stat, window = parse_stat(prop.get("stat_label"))
        key = normalize_name(prop.get("player_name"))
        target = roster_index.get(key)
        if stat is None:
            unmatched.append({**prop, "reason": "unrecognised stat"})
            continue
        if window is None:
            unmatched.append({**prop, "reason": "map window not stated"})
            continue
        if target is None:
            unmatched.append({**prop, "reason": "player not on any roster"})
            continue
        try:
            line = float(prop.get("line"))
        except (TypeError, ValueError):
            unmatched.append({**prop, "reason": "line is not a number"})
            continue
        region, team, real_name = target
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

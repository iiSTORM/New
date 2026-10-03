"""LoL international events: which one is on, what shape it has, and whose
players are in it.

An international event is tracked as a region of its own ("Demacia Cup",
"Worlds"), the way VCT Champions is for Valorant. Three things make it
different from a league, and each has its own function here:

  1. It is not always on. pick_tournament() decides whether a league
     (the LoL Esports API's "worlds", "demacia_cup", ...) has an event
     close enough to show, so a region appears a little before an event
     and disappears a little after it, rather than sitting empty all year.

  2. It has a SHAPE -- Swiss rounds, groups, a knockout bracket -- and
     that shape is the thing people want to look at. build_event() turns
     the API's getStandings (stages -> sections -> matches) and
     getSchedule (times, best-of, round names) into one structure the app
     draws. The API does not link bracket matches to each other
     (previousMatchIds is empty on every event checked, Worlds 2025
     included), so the app infers the links; this file only records the
     matches in the order the API lists them, which for a knockout is
     bracket order (QF1..QF4, SF1, SF2, Final -- verified against the
     Worlds 2025 results).

  3. Its teams' form lives somewhere else. lend_home_rosters() copies each
     participant's roster from its home league, ALWAYS, even after it has
     played at the event. playoffs_and_international_roadmap.md 2a: a
     team's own season is the comparable history for an international
     event. Valorant keeps a team's event-only entry once it has played,
     which works for a sport whose event entries are rebuilt from a
     season of maps; a LoL event roster would be built from one Bo1, and
     the event's own games still reach the projection anyway, through the
     region's past_matches.
"""
import copy
from datetime import datetime, timedelta, timezone

#: Our region key -> the LoL Esports API league slug. Slugs, not names: the
#: Demacia Cup's league is NAMED "DCGI" in the API, and a name match on
#: "Demacia" finds nothing.
INTERNATIONAL_EVENTS = {
    "First Stand": "first_stand",
    "MSI": "msi",
    "EWC": "ewc_lol",
    "Demacia Cup": "demacia_cup",
    "Worlds": "worlds",
}

#: How long before an event its region appears (bracket shell, fixtures as
#: they are drawn), and how long after the final it stays up.
SHOW_BEFORE = timedelta(days=21)
SHOW_AFTER = timedelta(days=5)


def _day(value):
    """'2026-10-02' or an ISO timestamp -> aware datetime at that UTC day."""
    text = str(value or "")[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def pick_tournament(tournaments, now=None):
    """The tournament worth showing now, or None.

    One that is running beats one that is coming, which beats one that just
    finished; inside a group, the nearest wins. A league's list holds every
    year's edition, so "the most recent" alone would show last year's Worlds
    for eleven months.
    """
    now = now or datetime.now(timezone.utc)
    running, coming, done = [], [], []
    for t in tournaments or []:
        start, end = _day(t.get("startDate")), _day(t.get("endDate"))
        if not start:
            continue
        end = (end or start) + timedelta(days=1)       # endDate is inclusive
        if start <= now < end:
            running.append((start, t))
        elif now < start <= now + SHOW_BEFORE:
            coming.append((start - now, t))
        elif end <= now < end + SHOW_AFTER:
            done.append((now - end, t))
    for group in (running, coming, done):
        if group:
            return sorted(group, key=lambda pair: pair[0])[0][1]
    return None


def in_window(start_time, tournament):
    """Whether a schedule event belongs to this tournament's dates.

    getSchedule is per LEAGUE, so the Worlds page carries 2025's matches too.
    """
    when = _day(start_time)
    start, end = _day(tournament.get("startDate")), _day(tournament.get("endDate"))
    if not when or not start:
        return False
    return start - timedelta(days=1) <= when <= (end or start) + timedelta(days=1)


def _team(t):
    result = t.get("result") or {}
    record = t.get("record") or None
    out = {"name": t.get("name") or "TBD", "code": t.get("code") or "TBD"}
    if result.get("outcome"):
        out["outcome"] = result["outcome"]
    if result.get("gameWins") is not None:
        out["wins"] = result.get("gameWins")
    if record and record.get("wins") is not None:
        out["record"] = [record.get("wins"), record.get("losses")]
    return out


def build_event(league_slug, label, tournament, standings, schedule_events):
    """The event's structure, in the shape the app draws.

    {"league", "name", "tournament_id", "start", "end",
     "stages": [{"name", "slug", "sections": [{"name", "matches": [...],
                 "rankings": [...]}]}]}

    Each match: {"id", "state", "start", "block", "best_of", "teams": [two of
    {"name", "code", "wins"?, "outcome"?, "record"?}]}. "start", "block" and
    "best_of" come from the schedule, joined on the match id; a match the
    schedule page does not carry keeps None for them rather than a guess.
    """
    by_id = {}
    for e in schedule_events or []:
        m = e.get("match") or {}
        if m.get("id"):
            by_id[m["id"]] = e
    stages = []
    for st in (standings or {}).get("standings") or []:
        for stage in st.get("stages") or []:
            sections = []
            for sec in stage.get("sections") or []:
                matches = []
                for m in sec.get("matches") or []:
                    e = by_id.get(m.get("id")) or {}
                    strategy = ((e.get("match") or {}).get("strategy") or {})
                    teams = [_team(t) for t in (m.get("teams") or [])]
                    # The schedule's copy carries each side's running record,
                    # which getStandings does not; same match, same order.
                    sched_teams = ((e.get("match") or {}).get("teams") or [])
                    for mine, theirs in zip(teams, sched_teams):
                        rec = (theirs or {}).get("record")
                        if rec and rec.get("wins") is not None and "record" not in mine:
                            mine["record"] = [rec.get("wins"), rec.get("losses")]
                    matches.append({
                        "id": m.get("id"),
                        "state": m.get("state") or e.get("state"),
                        "start": e.get("startTime"),
                        "block": e.get("blockName"),
                        "best_of": strategy.get("count") if strategy.get("type") == "bestOf" else None,
                        "teams": teams,
                    })
                rankings = []
                for r in sec.get("rankings") or []:
                    rankings.append({"ordinal": r.get("ordinal"),
                                     "teams": [_team(t) for t in r.get("teams") or []]})
                sections.append({"name": sec.get("name"), "matches": matches, "rankings": rankings})
            stages.append({"name": stage.get("name"), "slug": stage.get("slug"), "sections": sections})
    return {"league": league_slug, "name": label, "tournament_id": tournament.get("id"),
            "slug": tournament.get("slug"),
            "start": tournament.get("startDate"), "end": tournament.get("endDate"),
            "stages": stages}


def event_team_names(event):
    """Every named participant, in first-seen order, TBD excluded."""
    seen = []
    for stage in (event or {}).get("stages") or []:
        for sec in stage.get("sections") or []:
            for m in sec.get("matches") or []:
                for t in m.get("teams") or []:
                    name = t.get("team") or t.get("name")
                    if name and name != "TBD" and name not in seen:
                        seen.append(name)
    return seen


def home_donors(regions, skip=()):
    """{team name: (home region, team entry)} from the leagues.

    The donor is the region a team has PLAYED the most in, the same rule
    scrape_valorant uses, so a team present in two places resolves to its
    league rather than to whichever came first. Regions in `skip` (the
    international ones) are never donors: a roster lent there is a copy.
    """
    played = {}
    for key, region in regions.items():
        if key in skip:
            continue
        for m in region.get("past_matches") or []:
            for side in (m.get("teamA"), m.get("teamB")):
                if side:
                    played[(key, side)] = played.get((key, side), 0) + 1
    best = {}
    for key, region in regions.items():
        if key in skip:
            continue
        for name, team in (region.get("teams") or {}).items():
            depth = played.get((key, name), 0)
            if name not in best or depth > best[name][0]:
                best[name] = (depth, key, team)
    return {name: (key, team) for name, (_, key, team) in best.items()}


def lend_home_rosters(region, wanted, donors):
    """Give every wanted team its home league's roster, freshly.

    Borrowed entries from an earlier run are dropped first, so a roster
    change in the league reaches the event on the next run instead of the
    first copy living forever. Returns (borrowed, missing).
    """
    own = region.setdefault("teams", {})
    for name in [n for n, t in own.items() if isinstance(t, dict) and t.get("from_home_region")]:
        del own[name]
    borrowed, missing = [], []
    for name in wanted:
        if not name or name == "TBD" or name in own:
            continue
        if name not in donors:
            missing.append(name)
            continue
        home, team = donors[name]
        entry = copy.deepcopy(team)
        entry["from_home_region"] = home
        own[name] = entry
        borrowed.append(name)
    if borrowed:
        region["rosters_from_home_regions"] = True
    return borrowed, missing

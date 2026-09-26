#!/usr/bin/env python3
"""Fixtures the posted board implies but the schedule has not published yet.

A line the app cannot place on a fixture is invisible. collectEdges walks
upcoming_matches and nothing else, so a player with no fixture produces no
row however many lines the provider posted on them. On the board of
2026-09-26 that cost 23 of 70 LoL lines and 82 of 246 CS2 lines — and every
one of those lines was on a team this app already rosters and already has
history for. Nothing was missing but the fixture.

The board knows things the schedule does not. It names the player, the
league and the kickoff, and between them those are enough to say "this team
plays at this time" while the schedule provider still has the bracket slot
marked TBD, or has not published the region's round at all.

What the board does NOT know is who they play. It carries no opponent and
no match id, so the only pairing evidence available is "two teams share a
kickoff" — and a kickoff routinely holds several concurrent matches: on
2026-09-26 the CS2 slot at 09:00-04:00 held six teams, three games. Pairing
on a shared kickoff would invent a matchup, print it beside a projection as
fact, and be both checkable and wrong. A wrong opponent is worse than no
opponent, so this module never pairs two teams on its own evidence:

  - it FILLS an undecided side only where the schedule already asserts that
    fixture exists and the board leaves exactly one candidate for the hole;
  - otherwise it emits `team vs TBD`, which the app is already built for —
    collectEdges needs only the player's own team rostered and falls back to
    a neutral opponent term deliberately, for this case.

Should the provider ever start carrying its own matchup id, pairing becomes
a lookup rather than a guess and belongs here; until then the absence of one
is the reason there is no pairing, not an oversight.

Every record added or changed is stamped `inferred`, so the audit trail is
in the data rather than in a log line nobody will have kept.
"""
import collections
from datetime import datetime, timedelta, timezone

# Kept equal to PROP_MATCH_WINDOW_HOURS in src/app.jsx, and pinned to it by
# tests/test_board_fixtures.py. It is the same question asked from the other
# end: the app decides which fixture a line belongs to, this decides which
# fixture a slot of lines is already covered by. If they disagree, this can
# publish a fixture the app then refuses to hang those very lines on.
BOARD_MATCH_WINDOW_HOURS = 2

# Mirrors UPCOMING_MAX_AGE_HOURS in scrape_cs2.py: a match already under way
# is still worth showing, so a kickoff is "past" only well after it passed.
BOARD_MAX_AGE_HOURS = 12

# A hand-refreshed board can sit still for days (props.json is captured from
# a logged-in browser, not fetched by the workflow). Past this, its kickoffs
# are no longer evidence about the fixture list — the same judgement, and the
# same limit, as MAX_SCHEDULE_AGE_DAYS in merge.py.
MAX_BOARD_AGE_DAYS = 3

PLACEHOLDER_TEAMS = {"", "tbd", "?"}


def is_placeholder(name):
    return str(name or "").strip().lower() in PLACEHOLDER_TEAMS


def parse_stamp(raw):
    """An aware datetime, or None. Naive input is read as UTC."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace(" ", "T"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def team_index(region_data):
    """Lowercased player handle -> the roster teams carrying that handle.

    A set, not a name, because a handle on more than one roster is common
    enough in CS2 to matter: 1396 rostered players, 232 handles shared.
    """
    index = collections.defaultdict(set)
    for team_name, team in (region_data.get("teams") or {}).items():
        for player in team.get("players") or []:
            name = player.get("name")
            if isinstance(name, str) and name.strip():
                index[name.strip().lower()].add(team_name)
    return index


def resolve_team(row, index):
    """The roster team a board row belongs to, or None.

    Keyed on the PLAYER, not on the row's team name, and that is the whole
    trick. The board, the schedule and this app's rosters are three separate
    naming authorities: the board says 'The Huns' where the roster says 'The
    Huns Esports', and asking which team the player is on settles it without
    a name map to maintain.

    The row's team name is still used, as the tiebreak for a handle on more
    than one roster — which is not rare and not benign. 'tatu' is on paiN and
    on paiN Academy; guessing there projects a line off the wrong history and
    prints an academy fixture as a main-roster one. Where the board names
    neither candidate, this refuses.
    """
    handle = str(row.get("player") or "").strip().lower()
    if not handle:
        return None
    candidates = index.get(handle)
    if not candidates:
        return None
    if len(candidates) == 1:
        return next(iter(candidates))
    stated = str(row.get("team") or "").strip().lower()
    # Exactly one, and matched on equality rather than containment. Every
    # roster pair this has to separate is a containment pair — paiN and paiN
    # Academy, The Huns and The Huns Esports, Nemesis and NemNemesis — so a
    # substring rule matches both and then returns whichever the set happened
    # to yield first.
    exact = [team for team in candidates if team.lower() == stated]
    return exact[0] if len(exact) == 1 else None


def board_slots(props, game, regions, now, fetched_at=None,
                max_age_days=MAX_BOARD_AGE_DAYS):
    """The kickoffs this board implies, per region of one game's data file.

    Returns (slots_by_region, counts). A slot is {"when": datetime, "teams":
    {team: line_count}}. Kickoffs already gone are dropped here rather than
    downstream, so a stale board cannot resurrect a played fixture.

    The whole board is walked once and each row assigned to the regions it
    could belong to, rather than the board being re-read per region. Per
    region, a handle this app does not roster is unresolved in six of seven
    regions by definition, and the log said "6 unresolved" for one line.
    """
    counts = collections.Counter()
    slots_by_region = {key: [] for key in regions}
    if max_age_days is not None:
        captured = parse_stamp(fetched_at)
        if captured is not None:
            age_days = (now - captured).total_seconds() / 86400
            if age_days > max_age_days:
                counts["stale_board"] = 1
                return slots_by_region, counts

    indexes = {key: team_index(data) for key, data in regions.items()}
    known = {str(key).strip().lower() for key in regions}
    horizon = now - timedelta(hours=BOARD_MAX_AGE_HOURS)
    by_slot = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))

    for rows in (props.get(game) or {}).values():
        for row in rows or ():
            # A row's stated league only scopes it when this file actually
            # has a region by that name. Valorant's board labels its lines
            # with the players' home region while the fixture is listed under
            # 'VCT Champions'; without this a Pacific line could conjure a
            # Champions fixture, or the reverse.
            stated = str(row.get("region") or "").strip().lower()
            scoped = [key for key in regions
                      if stated not in known or str(key).strip().lower() == stated]
            placements = [(key, resolve_team(row, indexes[key])) for key in scoped]
            placements = [(key, team) for key, team in placements if team is not None]
            if not placements:
                counts["unresolved"] += 1
                continue
            when = parse_stamp(row.get("start_time"))
            if when is None:
                counts["undated"] += 1
                continue
            if when < horizon:
                counts["past"] += 1
                continue
            for region_key, team in placements:
                by_slot[region_key][when][team] += 1

    for region_key, per_when in by_slot.items():
        slots_by_region[region_key] = [
            {"when": when, "teams": dict(teams)}
            for when, teams in sorted(per_when.items())]
        counts["slots"] += len(slots_by_region[region_key])
    return slots_by_region, counts


def stamp_text(when):
    """A kickoff written the way the schedule sources already write one.

    UTC with a Z and no sub-second part, which is both what the LoL Esports
    API emits ("2026-09-26T20:00:00Z") and exactly what the JS port produces.
    isoformat() would keep the board's own offset here and JS cannot, so the
    two sides would write the same instant as different text and
    tests/board_parity.test.mjs could only compare them loosely.
    """
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fixture_matches_slot(fixture_date, slot_when, window):
    """Is this fixture the game the board put at `slot_when`?

    Mirrors propsFor() in src/app.jsx, including its asymmetry, and for the
    same reason. bo3.gg and the LoL Esports API state a kickoff; vlr.gg
    states a bare date. Comparing a board slot at 05:00-04:00 against a bare
    date read as midnight puts it nine hours out and calls every Valorant
    fixture missing — which would have published twelve phantom regional
    fixtures beside the eight real Champions ones they duplicate. A date
    carries no time, so it is matched at the resolution it actually has.
    """
    when = parse_stamp(fixture_date)
    if when is None:
        return False
    has_clock = ":" in str(fixture_date)
    if has_clock:
        return abs(when - slot_when) <= window
    return when.date() == slot_when.astimezone(timezone.utc).date()


def augment_regions(regions, slots_by_region, window_hours=BOARD_MATCH_WINDOW_HOURS):
    """Fills undecided sides and adds missing fixtures, across a whole file.

    Whole-file rather than region-by-region because "does this team already
    have a fixture then" is a question about the game, not about the bucket
    the fixture is filed under. Valorant rosters a Champions team under both
    its home region and the event, the board labels its lines with the home
    region, and a per-region check therefore saw twelve uncovered slots that
    were all eight of the real fixtures.

    Mutates regions[*]["upcoming_matches"] in place. Returns
    (counts, by_region).
    """
    window = timedelta(hours=window_hours)
    counts = collections.Counter()
    by_region = collections.defaultdict(collections.Counter)

    def sides(fixture):
        return (fixture.get("teamA"), fixture.get("teamB"))

    # (region, fixture) pairs, so a hole is filled in the list it lives in
    # while coverage is judged across all of them.
    existing = [(region_key, fixture)
                for region_key, region_data in regions.items()
                for fixture in (region_data.get("upcoming_matches") or ())]
    ordered = sorted(
        ((region_key, slot) for region_key, slots in slots_by_region.items()
         for slot in slots),
        key=lambda pair: (pair[1]["when"], pair[0]))

    for region_key, slot in ordered:
        near = [(rk, f) for rk, f in existing
                if fixture_matches_slot(f.get("date"), slot["when"], window)]
        committed = {name for _, f in near for name in sides(f)
                     if not is_placeholder(name)}
        # A fixture near this slot with one side named and the other still a
        # placeholder. The named side has to be one the board also puts here,
        # or this is a different game that merely kicks off nearby.
        holes = [(rk, f) for rk, f in near
                 if sum(is_placeholder(n) for n in sides(f)) == 1
                 and any(n in slot["teams"] for n in sides(f)
                         if not is_placeholder(n))]
        leftover = sorted(t for t in slot["teams"] if t not in committed)

        if not leftover:
            counts["already_covered"] += 1
            continue

        if holes:
            # The leftovers belong to these holes — that is what a hole is.
            # Fill one only when the board leaves no choice about which team
            # goes in it; otherwise leave the holes alone AND add nothing,
            # because a new fixture here would publish the same game twice.
            if len(holes) == 1 and len(leftover) == 1:
                (hole_region, hole), team = holes[0], leftover[0]
                key = "teamA" if is_placeholder(hole.get("teamA")) else "teamB"
                # Mutated in place, and `existing` holds this same dict, so
                # a later slot for the other side's region sees the team as
                # committed and does not raise the game a second time.
                hole[key] = team
                hole["inferred"] = "board:opponent"
                counts["filled"] += 1
                by_region[hole_region]["filled"] += 1
            else:
                counts["ambiguous"] += 1
                by_region[region_key]["ambiguous"] += 1
            continue

        # No fixture at all for these teams. One entry each, opponent left
        # undecided: who they play is exactly what the board cannot say.
        # Not setdefault: a region whose upcoming_matches is present but null
        # (or anything else non-list) would hand back that value to .append.
        # Every reader in the pipeline already tolerates it as `or ()`.
        target = regions[region_key].get("upcoming_matches")
        if not isinstance(target, list):
            target = []
            regions[region_key]["upcoming_matches"] = target
        for team in leftover:
            fixture = {"date": stamp_text(slot["when"]), "teamA": team,
                       "teamB": "TBD", "block": "", "inferred": "board:team"}
            target.append(fixture)
            # Into `existing` as well as the region's own list: a team
            # rostered in two regions raises the same kickoff twice, and the
            # second pass has to see the fixture the first one just added or
            # the one game is published twice.
            existing.append((region_key, fixture))
            counts["added"] += 1
            by_region[region_key]["added"] += 1
    return counts, by_region


def describe(counts):
    """A one-line summary, or '' when there is nothing to report."""
    order = ("filled", "added", "ambiguous", "already_covered", "unresolved",
             "undated", "past", "stale_board")
    parts = [f"{counts[k]} {k.replace('_', ' ')}" for k in order if counts.get(k)]
    return ", ".join(parts)

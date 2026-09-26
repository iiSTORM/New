#!/usr/bin/env python3
"""Combines data.json (regions -> teams + past_matches) and schedule.json
(regions -> upcoming) into the final payload the app fetches.

Team-name spelling differs between gol.gg and the LoL Esports API in two
ways: pure capitalization (handled automatically via case-insensitive
matching below) and genuinely different names — e.g. the API includes a
city/sponsor prefix gol.gg drops ("Beijing JDG Esports" -> "JD Gaming"), or
the two sources just picked different official names ("Gen.G Esports" vs
"Gen.G"). Those need an explicit entry in TEAM_NAME_MAP (lowercase key ->
gol.gg's exact casing). Add more here as the logs report new mismatches —
"TBD vs TBD" entries are always correctly dropped (that match's teams
aren't determined yet, so it isn't projectable regardless)."""
import collections
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from team_aliases import alias_key

# Only the names the two sources genuinely SPELL DIFFERENTLY -- a sponsor or
# city word one carries and the other drops, or an abbreviation. Pure spelling
# (case, accents, punctuation, spacing) needs no entry: resolve_side below
# falls back to team_aliases.alias_key, which reduces both sides to the same
# key. Five entries came out when that was added and are recorded here rather
# than deleted, because the knowledge in them is what says the alias fallback
# is doing the right thing and not merely something:
#
#   "gen.g esports"       -> "Gen.G"              punctuation + a dropped word
#   "nongshim red force"  -> "Nongshim RedForce"  a space gol.gg does not have
#   "thunder talk gaming" -> "ThunderTalk Gaming" same
#   "anyone's legend"     -> "Anyone s Legend"    gol.gg drops the apostrophe
#   "leviatán"            -> "Leviatan"           the API keeps the accent
#
# That last one had a comment saying its gol.gg spelling was inferred from a
# page title rather than confirmed, and to double-check it resolved. The
# fallback resolves it whichever way gol.gg writes it, which is a better
# answer than a guess that happened to be right.
TEAM_NAME_MAP = {
    # LCS — sponsor suffixes
    "cloud9 kia": "Cloud9",
    "team liquid alienware": "Team Liquid",
    # LPL — API includes city/sponsor prefixes gol.gg doesn't use
    "xi'an team we": "Team WE",
    "shenzhen ninjas in pyjamas": "Ninjas in Pyjamas",
    "beijing jdg esports": "JD Gaming",
    # CBLOL — confirmed against a real gol.gg game page titled "RED Canids
    # vs Los Grandes": the API uses "RED Kalunga" (drops "Canids") while
    # gol.gg drops the sponsor "Kalunga" instead; "LOS" is API shorthand
    # for the full "Los Grandes".
    "red kalunga": "RED Canids",
    "los": "Los Grandes",
    # TCL — confirmed against a real current TCL 2026 Summer team list:
    # "PCIFIC Esports" (unusual spelling, genuinely correct on both sides,
    # not a typo) and "SU Esports" (API adds sponsor prefix "Avella" that
    # gol.gg drops, same pattern as the LPL entries above).
    "avella su esports": "SU Esports",
}


def normalize(name):
    mapped = TEAM_NAME_MAP.get(name.lower())
    return mapped if mapped else name


def build_lookup(known_teams):
    """Case-insensitive lookup: API casing (e.g. 'KIWOOM DRX') resolves to
    gol.gg's exact casing (e.g. 'Kiwoom DRX') as long as the letters match.

    Two tracked teams differing only in case are left out rather than one
    silently winning on dict order. The comprehension this replaced answered
    with whichever was iterated last, which is the arbitrary-winner problem
    the alias fold exists to remove -- rare in the LoL data, and free to not
    have.
    """
    by_key = collections.defaultdict(list)
    for team in known_teams:
        by_key[team.lower()].append(team)
    return {key: names[0] for key, names in by_key.items() if len(names) == 1}


def build_alias_lookup(known_teams):
    """{alias_key: the one team with that key, or None if several share it}.

    Punctuation, accents and spacing are spelling, not identity, and every
    entry TEAM_NAME_MAP used to hold for them was a line of hand-maintained
    knowledge that had to be added after a fixture had already been dropped.

    A shared key maps to None rather than being left out, so resolve_side can
    tell "several tracked teams look like this" -- which is a real thing to
    look at -- from "nothing here looks like it", which is not.
    """
    by_key = collections.defaultdict(list)
    for team in known_teams:
        key = alias_key(team)
        if key:
            by_key[key].append(team)
    return {key: (names[0] if len(names) == 1 else None)
            for key, names in by_key.items()}


def tracked_by_key(regions):
    """{alias_key: [(region, team)]} across every region in the file.

    So a name that resolves nowhere can be told apart from one that resolves
    in a DIFFERENT region. The two look identical in a log that only counts
    "unknown team", which is how eight qualifier and cross-league teams read
    as missing TEAM_NAME_MAP entries when they were nothing of the kind.
    """
    found = collections.defaultdict(list)
    for region_key, region_data in (regions or {}).items():
        for team in (region_data.get("teams") or {}):
            key = alias_key(team)
            if key:
                found[key].append((region_key, team))
    return found


# Why a named side could not be resolved. Only UNKNOWN_SPELLING is a bug in
# this file; the other two are facts about what the roster scrape covers.
ELSEWHERE = "tracked in another region"
UNCOVERED = "not tracked in any region"
AMBIGUOUS = "two tracked teams share its spelling"


def resolve_side(name, lookup, aliases, elsewhere=None):
    """(resolved name, None) or (None, why not).

    Three tries, narrowest first: the exact name, the name TEAM_NAME_MAP
    gives for it, then its spelling-insensitive key.

    The second and third overlap on every entry the map currently holds --
    "Cloud9 Kia" resolves either by the map pointing at "Cloud9" or by
    alias_key("Cloud9") finding it -- so removing the middle try changes no
    answer today. Both stay because they fail differently: the map is the
    only thing that can reach a genuinely different name, and the alias key
    is the only thing that still works when gol.gg respells the target the
    map points at. The overlap is the point, not an oversight.
    """
    for candidate in (name, normalize(name)):
        hit = lookup.get(str(candidate or "").lower())
        if hit:
            return hit, None
    key = alias_key(normalize(name))
    if not key:
        return None, UNCOVERED
    hit = aliases.get(key)
    if hit:
        return hit, None
    if key in aliases:
        # Present but shared. Picking one is the normalisation collision the
        # fallback exists to avoid, and calling it "not tracked" would send
        # you looking in the wrong place.
        return None, AMBIGUOUS
    where = (elsewhere or {}).get(key) or []
    if len(where) > 1:
        return None, AMBIGUOUS
    if where:
        return None, f"{ELSEWHERE} ({where[0][0]}/{where[0][1]})"
    return None, UNCOVERED


def merge_career_data(data):
    """Folds each player's decayed career baseline (from scrape_career.py's
    output) into their existing record in data.json. career_data.json is
    keyed by gol.gg's numeric player ID, but data.json's players are keyed
    by name within each team — so this builds a name-based lookup rather
    than an ID-based one, since data.json has no gol.gg IDs stored at all.

    Gracefully optional: if career_data.json doesn't exist yet (career
    history hasn't been run, or isn't wired into this particular
    workflow), every player just gets career=None and the rest of the
    pipeline is unaffected — same pattern as schedule.json being missing
    above."""
    try:
        with open("career_data.json") as f:
            career_data = json.load(f)
    except FileNotFoundError:
        print("! career_data.json not found — skipping career merge (every player gets career=None; "
              "run scrape_career.py first if this wasn't intentional)", file=sys.stderr)
        career_data = {}

    # Multiple gol.gg player IDs sharing an identical short handle across
    # different currently-tracked players is possible in principle (two
    # different pros both going by the same short name) though rare —
    # logged rather than silently overwritten so it's visible if it
    # happens.
    career_by_name = {}
    for player_id, record in career_data.items():
        name = record.get("name")
        if not name:
            continue
        if name in career_by_name and career_by_name[name][0] != player_id:
            print(f"  ! duplicate career-data name '{name}' (ids {career_by_name[name][0]} and {player_id}) "
                  f"— keeping the later one", file=sys.stderr)
        career_by_name[name] = (player_id, record.get("career"))

    matched = 0
    unmatched = []
    for region_key, region_data in data.get("regions", {}).items():
        for team_name, team in region_data.get("teams", {}).items():
            for player in team.get("players", []):
                name = player.get("name")
                entry = career_by_name.get(name)
                if entry and entry[1]:
                    player["career"] = entry[1]
                    matched += 1
                else:
                    player["career"] = None
                    unmatched.append(f"{region_key}/{team_name}/{name}")

    total = matched + len(unmatched)
    print(f"Merged career data: {matched}/{total} player(s) matched")
    if unmatched:
        shown = unmatched[:20]
        more = f" ... (+{len(unmatched) - 20} more)" if len(unmatched) > 20 else ""
        print(f"  ! unmatched (no career data — career=None, falls back to cur/hist only): {shown}{more}",
              file=sys.stderr)


# scrape_schedule.py is continue-on-error in the workflow, so when it fails
# the schedule.json left on disk is whatever git checked out. Reusing a
# recent one is a reasonable fallback; reusing an old one is not, because
# every fixture in it has since been played and would be published as
# "upcoming". Past that age we drop upcoming matches entirely, which the
# frontend already renders as simply having no fixtures listed.
MAX_SCHEDULE_AGE_DAYS = 3


def load_schedule():
    """schedule.json, or an empty schedule if it is missing or too old."""
    try:
        with open("schedule.json") as f:
            schedule = json.load(f)
    except FileNotFoundError:
        print("No schedule.json — upcoming matches will be empty", file=sys.stderr)
        return {"regions": {}}

    stamp = schedule.get("generated_at")
    if not stamp:
        # Written before scrape_schedule.py stamped its output. Nothing to
        # judge it by, so use it, but say so.
        print("WARNING: schedule.json has no generated_at — using it, but its "
              "age is unknown", file=sys.stderr)
        return schedule

    try:
        generated = datetime.fromisoformat(stamp)
    except ValueError:
        print(f"WARNING: schedule.json has an unparseable generated_at ({stamp!r}) "
              f"— using it anyway", file=sys.stderr)
        return schedule

    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - generated).total_seconds() / 86400
    if age_days > MAX_SCHEDULE_AGE_DAYS:
        print(f"WARNING: schedule.json is {age_days:.1f} days old (limit "
              f"{MAX_SCHEDULE_AGE_DAYS}) — the schedule scrape has been failing. "
              f"Dropping upcoming matches rather than publishing fixtures that "
              f"have already been played.", file=sys.stderr)
        return {"regions": {}}
    print(f"Schedule is {age_days:.1f} days old")
    return schedule


def resolve_upcoming(region_schedule, lookup, region_key="?", aliases=None,
                     elsewhere=None):
    """The fixtures a region can actually show, and why the rest cannot.

    Returns (upcoming, counts). Pure, and pulled out of main() for the
    reason absorb_results was in scrape_cs2: this is the part with the
    decision in it, the surrounding step is file I/O, and while it sat
    inline the only thing tests could reach was normalize(). A rule that
    discarded every playoff fixture with an undecided opponent lived here
    untested for as long as it took a real board to lose 26 lines to it.
    """
    if aliases is None:
        aliases = build_alias_lookup(lookup.values())
    upcoming = []
    dropped_tbd = 0        # expected: bracket slots whose teams aren't decided yet
    dropped_uncovered = 0  # expected too: a team the roster scrape does not cover
    dropped_unknown = 0    # real problem: a name we failed to resolve
    kept_half = 0          # one side decided, the other an undecided bracket slot
    for m in region_schedule:
        a, why_a = resolve_side(m["teamA"], lookup, aliases, elsewhere)
        b, why_b = resolve_side(m["teamB"], lookup, aliases, elsewhere)
        if a and b:
            upcoming.append({"date": m["date"], "teamA": a, "teamB": b, "block": m.get("block", "")})
            continue
        # These two cases were previously summed into one "dropped"
        # count, which made the number impossible to act on: a
        # playoff bracket legitimately full of undecided slots looked
        # identical to the scraper silently failing to recognise real
        # teams. During playoffs the TBD count is expected to be
        # LARGE and is not a defect; the unknown count should be zero
        # and every entry is a genuine bug worth chasing.
        side_is_tbd = (
            m["teamA"] == "TBD" or m["teamB"] == "TBD"
            or not m["teamA"].strip() or not m["teamB"].strip()
        )
        # ONE side decided and the other a genuine placeholder is a
        # real fixture, and it was being thrown away.
        #
        # LYON played at 20:00 on 2026-09-26 against a bracket slot
        # that had not resolved yet, and the provider posted 13
        # maps 1-3 lines on their players. Every one of them had
        # nowhere to land, because this required BOTH sides to
        # resolve -- during playoffs, which is exactly when half a
        # bracket is undecided and when the lines are busiest.
        #
        # The app is already built for it: collectEdges needs only
        # the player's own team rostered and falls back to a neutral
        # opponent term, deliberately, because a CS2 board stranded
        # five lines this way. Kept with the undecided side left as
        # TBD so the projection carries that caveat rather than
        # disappearing.
        #
        # Still requires the unresolved side to be a LITERAL
        # placeholder. A named team this scraper failed to recognise
        # falls through to the unknown-team path below, where it is
        # reported as the bug it is rather than quietly relabelled
        # TBD and hidden.
        if (a or b) and side_is_tbd:
            upcoming.append({"date": m["date"], "teamA": a or "TBD",
                             "teamB": b or "TBD", "block": m.get("block", "")})
            kept_half += 1
            continue
        if side_is_tbd:
            dropped_tbd += 1
            continue
        # A name this app tracks nowhere is a fact about the roster scrape's
        # coverage, not a bug in the mapping above, and lumping the two
        # together is how "8 UNKNOWN TEAM" read as eight missing
        # TEAM_NAME_MAP entries. Every one of them was an LCS Regional
        # Qualifier or an LLA/CBLOL-Academy side that gol.gg's league pages
        # do not list at all, so no entry could have pointed anywhere.
        unresolved = [(n, why) for n, r, why in ((m["teamA"], a, why_a),
                                                 (m["teamB"], b, why_b)) if not r]
        if all(why == UNCOVERED for _, why in unresolved):
            dropped_uncovered += 1
            continue
        dropped_unknown += 1
        detail = ", ".join(f"{n!r} ({why})" for n, why in unresolved)
        print(f"  ! {region_key}: dropped '{m['teamA']}' vs '{m['teamB']}' — "
              f"{detail}. A spelling difference resolves on its own; add to "
              f"TEAM_NAME_MAP only when the two sources use genuinely "
              f"different names.", file=sys.stderr)
    return upcoming, {"tbd": dropped_tbd, "unknown": dropped_unknown,
                      "uncovered": dropped_uncovered, "half": kept_half}


def main():
    with open("data.json") as f:
        data = json.load(f)
    schedule = load_schedule()

    elsewhere = tracked_by_key(data.get("regions"))
    for region_key, region_data in data.get("regions", {}).items():
        known_teams = set(region_data.get("teams", {}).keys())
        lookup = build_lookup(known_teams)
        region_schedule = schedule.get("regions", {}).get(region_key, [])
        upcoming, counts = resolve_upcoming(region_schedule, lookup, region_key,
                                           build_alias_lookup(known_teams), elsewhere)
        dropped_tbd, dropped_unknown, kept_half = (
            counts["tbd"], counts["unknown"], counts["half"])
        region_data["upcoming_matches"] = upcoming
        detail = []
        if kept_half:
            detail.append(f"{kept_half} kept with an undecided opponent")
        if dropped_tbd:
            detail.append(f"{dropped_tbd} TBD vs TBD")
        if counts["uncovered"]:
            detail.append(f"{counts['uncovered']} not covered by the roster scrape")
        if dropped_unknown:
            detail.append(f"{dropped_unknown} UNKNOWN TEAM")
        suffix = f" ({', '.join(detail)})" if detail else ""
        print(f"{region_key}: merged {len(upcoming)}/{len(region_schedule)} upcoming matches{suffix}")

    merge_career_data(data)

    with open("data.json", "w") as f:
        # Written minified: these files are machine-generated and never read
        # by hand, and indent=2 was about two thirds of the bytes
        # (data.json: 7.5MB -> 2.4MB). GitHub serves them gzipped, so the
        # win on the wire is smaller (~535KB -> ~340KB), but the browser
        # still parses the full decompressed text, and every run commits a
        # whole fresh copy.
        json.dump(data, f, separators=(",", ":"))


if __name__ == "__main__":
    main()
"""LoL international events: picking the event, its structure, and its rosters.

The fixtures here are cut down from what the LoL Esports API returned for
the 2026 Demacia Cup (league "DCGI", slug demacia_cup) on 2026-10-03, the
day it started: 12 teams, a Swiss stage opening with six Bo1s and turning
Bo3, then an 8-team Bo5 bracket. Worlds 2026 was twelve days out with
every slot TBD.
"""
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import international as intl
import merge

NOW = datetime(2026, 10, 3, 6, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------- picking

def tour(slug, start, end, tid=None):
    return {"id": tid or slug, "slug": slug, "startDate": start, "endDate": end}


def test_a_running_event_is_picked_over_last_years():
    tours = [tour("worlds_2025", "2025-10-14", "2025-11-09"), tour("demacia_cup_2026", "2026-10-02", "2026-10-17")]
    assert intl.pick_tournament(tours, NOW)["slug"] == "demacia_cup_2026"


def test_an_event_twelve_days_out_is_shown_as_coming():
    tours = [tour("worlds_2024", "2024-09-24", "2024-11-02"), tour("worlds_2025", "2025-10-14", "2025-11-09"),
             tour("worlds_2026", "2026-10-15", "2026-11-15")]
    assert intl.pick_tournament(tours, NOW)["slug"] == "worlds_2026"


def test_an_event_long_over_or_far_off_is_not_shown():
    assert intl.pick_tournament([tour("msi_2026", "2026-06-27", "2026-07-12")], NOW) is None
    assert intl.pick_tournament([tour("first_stand_2027", "2027-03-10", "2027-03-20")], NOW) is None


def test_the_end_date_is_inclusive_and_the_region_lingers_after():
    t = [tour("x", "2026-09-20", "2026-10-02")]
    assert intl.pick_tournament(t, NOW) is not None           # finished yesterday: still up
    late = datetime(2026, 10, 9, tzinfo=timezone.utc)
    assert intl.pick_tournament(t, late) is None               # a week later: gone


def test_schedule_events_from_another_year_are_out_of_window():
    t = tour("worlds_2026", "2026-10-15", "2026-11-15")
    assert intl.in_window("2026-10-15T18:00:00Z", t)
    assert intl.in_window("2026-11-14T19:00:00Z", t)
    assert not intl.in_window("2025-10-15T18:00:00Z", t)


# ------------------------------------------------------------- structure

def api_team(name, code, outcome=None, wins=None, record=None):
    t = {"name": name, "code": code, "result": None if outcome is None and wins is None
         else {"outcome": outcome, "gameWins": wins}}
    if record:
        t["record"] = {"wins": record[0], "losses": record[1]}
    return t


TBD = {"name": "TBD", "code": "TBD", "result": None}

STANDINGS = {"standings": [{"stages": [
    {"name": "Swiss", "slug": "swiss", "sections": [{"name": "Swiss", "rankings": [], "matches": [
        {"id": "m1", "state": "completed",
         "teams": [api_team("Beijing JDG Esports", "JDG", "win", 1), api_team("HANJIN BRION", "BRO", "loss", 0)]},
        {"id": "m2", "state": "unstarted", "teams": [api_team("FlyQuest", "FLY"), api_team("LGD GAMING", "LGD")]},
        {"id": "m3", "state": "unstarted", "teams": [TBD, TBD]},
    ]}]},
    {"name": "Playoffs", "slug": "playoffs", "sections": [{"name": "Playoffs", "rankings": [], "matches": [
        {"id": f"p{i}", "state": "unstarted", "teams": [TBD, TBD]} for i in range(7)]}]},
]}]}

SCHEDULE = [
    {"startTime": "2026-10-03T13:00:00Z", "state": "completed", "blockName": "Swiss",
     "match": {"id": "m1", "strategy": {"type": "bestOf", "count": 1},
               "teams": [api_team("Beijing JDG Esports", "JDG", record=(1, 0)),
                         api_team("HANJIN BRION", "BRO", record=(0, 1))]}},
    {"startTime": "2026-10-03T09:00:00Z", "state": "unstarted", "blockName": "Swiss",
     "match": {"id": "m2", "strategy": {"type": "bestOf", "count": 1},
               "teams": [api_team("FlyQuest", "FLY"), api_team("LGD GAMING", "LGD")]}},
    {"startTime": "2026-10-04T07:00:00Z", "state": "unstarted", "blockName": "Swiss",
     "match": {"id": "m3", "strategy": {"type": "bestOf", "count": 3}, "teams": [TBD, TBD]}},
    {"startTime": "2026-10-17T09:00:00Z", "state": "unstarted", "blockName": "Finals",
     "match": {"id": "p6", "strategy": {"type": "bestOf", "count": 5}, "teams": [TBD, TBD]}},
]

DCGI = tour("demacia_cup_2026", "2026-10-02", "2026-10-17", "117133773242499009")


def test_build_event_joins_times_series_length_and_results():
    ev = intl.build_event("demacia_cup", "Demacia Cup", DCGI, STANDINGS, SCHEDULE)
    assert [s["name"] for s in ev["stages"]] == ["Swiss", "Playoffs"]
    m1 = ev["stages"][0]["sections"][0]["matches"][0]
    assert m1["best_of"] == 1 and m1["start"] == "2026-10-03T13:00:00Z" and m1["block"] == "Swiss"
    assert m1["teams"][0] == {"name": "Beijing JDG Esports", "code": "JDG", "outcome": "win",
                              "wins": 1, "record": [1, 0]}
    final = ev["stages"][1]["sections"][0]["matches"][6]
    assert final["best_of"] == 5 and final["block"] == "Finals"
    # Bracket order is the API's order; nothing is reordered.
    assert [m["id"] for m in ev["stages"][1]["sections"][0]["matches"]] == [f"p{i}" for i in range(7)]


def test_a_match_the_schedule_does_not_carry_keeps_none_not_a_guess():
    ev = intl.build_event("demacia_cup", "Demacia Cup", DCGI, STANDINGS, [])
    m = ev["stages"][0]["sections"][0]["matches"][0]
    assert m["start"] is None and m["best_of"] is None and m["block"] is None


def test_event_team_names_skip_tbd():
    ev = intl.build_event("demacia_cup", "Demacia Cup", DCGI, STANDINGS, SCHEDULE)
    assert intl.event_team_names(ev) == ["Beijing JDG Esports", "HANJIN BRION", "FlyQuest", "LGD GAMING"]


# --------------------------------------------------------------- rosters

def roster(*names):
    return {"color": "#fff", "players": [{"name": n, "cur": {"k": 3}} for n in names]}


def leagues():
    return {
        "LPL": {"teams": {"JD Gaming": roster("Ruler"), "LGD Gaming": roster("Shaoye")},
                "past_matches": [{"teamA": "JD Gaming", "teamB": "LGD Gaming"}] * 20},
        "LCK": {"teams": {"HANJIN BRION": roster("Clozer")}, "past_matches": [{"teamA": "HANJIN BRION", "teamB": "x"}] * 18},
        "LCS": {"teams": {"FlyQuest": roster("Quad")}, "past_matches": [{"teamA": "FlyQuest", "teamB": "y"}] * 15},
    }


def test_lending_marks_the_home_league_and_copies_rather_than_shares():
    regions = leagues()
    event = {"teams": {}}
    borrowed, missing = intl.lend_home_rosters(event, ["FlyQuest", "Nobody", "TBD"],
                                               intl.home_donors(regions))
    assert borrowed == ["FlyQuest"] and missing == ["Nobody"]
    assert event["teams"]["FlyQuest"]["from_home_region"] == "LCS"
    event["teams"]["FlyQuest"]["players"].append({"name": "intruder"})
    assert len(regions["LCS"]["teams"]["FlyQuest"]["players"]) == 1


def test_a_borrowed_roster_is_refreshed_every_run_not_frozen():
    regions = leagues()
    event = {"teams": {"FlyQuest": {**roster("OldGuy"), "from_home_region": "LCS"}}}
    intl.lend_home_rosters(event, ["FlyQuest"], intl.home_donors(regions))
    assert [p["name"] for p in event["teams"]["FlyQuest"]["players"]] == ["Quad"]


def test_an_event_region_is_never_a_donor():
    regions = leagues()
    regions["Demacia Cup"] = {"teams": {"FlyQuest": roster("Copy")},
                              "past_matches": [{"teamA": "FlyQuest", "teamB": "z"}] * 99}
    donors = intl.home_donors(regions, skip={"Demacia Cup"})
    assert donors["FlyQuest"][0] == "LCS"


# ------------------------------------------------------------------ merge

def schedule_json():
    ev = intl.build_event("demacia_cup", "Demacia Cup", DCGI, STANDINGS, SCHEDULE)
    return {"regions": {"Demacia Cup": [
        {"date": "2026-10-03T09:00:00Z", "teamA": "FlyQuest", "teamB": "LGD GAMING",
         "block": "Swiss", "best_of": 1, "match_id": "m2"},
        {"date": "2026-10-04T07:00:00Z", "teamA": "TBD", "teamB": "TBD", "block": "Swiss", "best_of": 3}]},
        "events": {"Demacia Cup": ev}}


def test_merge_resolves_api_names_against_every_league_and_lends_rosters():
    data = {"regions": leagues()}
    merge.merge_international(data, schedule_json())
    region = data["regions"]["Demacia Cup"]
    assert region["upcoming_matches"] == [{"date": "2026-10-03T09:00:00Z", "teamA": "FlyQuest",
                                           "teamB": "LGD Gaming", "block": "Swiss", "best_of": 1,
                                           "match_id": "m2"}]
    # "Beijing JDG Esports" is TEAM_NAME_MAP's; "LGD GAMING" is only a case difference.
    assert set(region["teams"]) == {"JD Gaming", "HANJIN BRION", "FlyQuest", "LGD Gaming"}
    assert all(t["from_home_region"] for t in region["teams"].values())
    m1 = region["event"]["stages"][0]["sections"][0]["matches"][0]
    assert [t["team"] for t in m1["teams"]] == ["JD Gaming", "HANJIN BRION"]


def test_merge_leaves_league_fixtures_alone_and_skips_the_event_in_the_league_loop():
    data = {"regions": leagues()}
    merge.merge_international(data, schedule_json())
    assert "upcoming_matches" not in data["regions"]["LCS"]


def test_an_event_off_the_schedule_is_removed_but_only_when_the_schedule_says_so():
    data = {"regions": {**leagues(), "Worlds": {"teams": {}, "past_matches": []}}}
    merge.merge_international(data, {"regions": {}})             # an old schedule.json: no "events"
    assert "Worlds" in data["regions"]
    merge.merge_international(data, {"regions": {}, "events": {}})
    assert "Worlds" not in data["regions"]


def test_merge_carries_series_length_for_league_fixtures_too():
    lookup = merge.build_lookup(["T1", "Gen.G"])
    rows, _ = merge.resolve_upcoming([{"date": "d", "teamA": "T1", "teamB": "Gen.G", "best_of": 5}], lookup)
    assert rows[0]["best_of"] == 5
    rows, _ = merge.resolve_upcoming([{"date": "d", "teamA": "T1", "teamB": "Gen.G"}], lookup)
    assert "best_of" not in rows[0]


# ---------------------------------------------------------------- gol.gg

def test_gol_gg_event_tournaments_are_found_by_pattern_and_recency():
    pytest.importorskip("bs4")
    import scrape_lcs
    rows = [{"trname": "Worlds 2025 Main Event", "lastgame": "2025-11-09"},
            {"trname": "Demacia Cup 2026", "lastgame": "2026-10-17"},
            {"trname": "Demacia Cup 2025", "lastgame": "2026-10-03"},
            {"trname": "MSI 2026", "lastgame": "2026-07-12"},
            {"trname": "LPL 2026 Split 3", "lastgame": "2026-10-01"},
            {"trname": "Worlds 2026 Play-In", "lastgame": "2026-10-18"},
            {"trname": "Worlds 2026 Main Event", "lastgame": "bad"}]
    got = scrape_lcs.pick_event_tournaments(rows, date(2026, 10, 18))
    assert got == {"Demacia Cup": ["Demacia Cup 2026"], "Worlds": ["Worlds 2026 Play-In"]}
    assert scrape_lcs.season_code(date(2026, 10, 3)) == "S16"


def test_the_two_event_lists_name_the_same_regions():
    pytest.importorskip("bs4")
    import scrape_lcs
    assert set(scrape_lcs.GOLGG_EVENTS) == set(intl.INTERNATIONAL_EVENTS)

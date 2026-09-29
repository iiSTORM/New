"""The curated handle aliases, and the rules that keep one from doing harm.

An alias is a human saying "these two strings are this one player". The danger
is not that a row is missing — that is just a dropped line, and the funnel
reports it. The danger is a row that quietly puts a line on the WRONG player,
which produces a confident number with nothing behind it. Every test here is
about that.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import player_aliases as pa
import props_match as pm


# ---------------------------------------------------------------- the table

def test_every_row_is_complete_and_says_where_it_came_from():
    assert pa.ALIASES, "the table is empty; delete it rather than shipping a stub"
    for row in pa.ALIASES:
        for field in ("game", "team", "provider", "roster", "note"):
            assert row.get(field), f"{field} missing from {row!r}"
        # The note is the part that lets the next reader check the claim
        # instead of trusting it, so a placeholder is worse than nothing.
        assert len(row["note"]) > 30, f"note is too thin to verify: {row!r}"


def test_the_index_normalises_both_sides():
    index = pm.build_alias_index([
        {"game": "CS2", "team": "Team Spirit", "provider": "Dónk!",
         "roster": "d0nk", "note": "x" * 40}])
    assert index == {("cs2", "teamspirit", "donk"): "d0nk"}


def test_two_spellings_that_normalise_alike_are_refused_as_self_mapping():
    """"Dónk" and "donk!" are the same key, so a row pairing them says
    nothing and would sit in the table looking like it did."""
    with pytest.raises(ValueError, match="itself"):
        pm.build_alias_index([{"game": "cs2", "team": "T", "provider": "Dónk",
                               "roster": "donk!", "note": "x" * 40}])


def test_a_row_mapping_a_handle_to_itself_is_refused():
    """It can only be a typo, and a silent no-op is a row nobody prunes."""
    with pytest.raises(ValueError, match="itself"):
        pm.build_alias_index([{"game": "cs2", "team": "T", "provider": "Ab",
                               "roster": "ab", "note": "x" * 40}])


def test_two_rows_disagreeing_about_one_handle_are_refused():
    """Last-one-wins here is how a line lands on the wrong player."""
    rows = [{"game": "cs2", "team": "T", "provider": "Ab", "roster": "Cd",
             "note": "x" * 40},
            {"game": "cs2", "team": "T", "provider": "Ab", "roster": "Ef",
             "note": "x" * 40}]
    with pytest.raises(ValueError, match="disagree"):
        pm.build_alias_index(rows)


def test_the_same_row_twice_is_not_a_disagreement():
    row = {"game": "cs2", "team": "T", "provider": "Ab", "roster": "Cd",
           "note": "x" * 40}
    assert len(pm.build_alias_index([row, dict(row)])) == 1


def test_an_incomplete_row_is_refused_rather_than_skipped():
    with pytest.raises(ValueError, match="incomplete"):
        pm.build_alias_index([{"game": "cs2", "team": "T", "provider": "Ab"}])


# ------------------------------------------------------------- the matching

ROSTER = {"EU": {"teams": {
    "Butterfly": {"players": [{"name": "Kuruma"}, {"name": "ayano"}]},
    "Other": {"players": [{"name": "Kurama"}]},
}}}
TABLE = [{"game": "cs2", "team": "Butterfly", "provider": "Kurama",
          "roster": "Kuruma", "note": "x" * 40}]


def prop(**over):
    base = {"player_name": "Kurama", "team": "Butterfly", "line": 15.5,
            "stat_label": "MAPS 1-2 Kills", "odds_type": "standard"}
    base.update(over)
    return base


def test_an_alias_rescues_a_line_the_roster_spelling_would_drop():
    index, _ = pm.build_roster_index(ROSTER)
    matched, unmatched = pm.match_props([prop()], index, game="cs2",
                                        alias_index=pm.build_alias_index(TABLE))
    assert not unmatched
    assert matched[0]["player"] == "Kuruma"
    assert matched[0]["team"] == "Butterfly"


def test_without_the_table_the_line_lands_on_the_other_org_s_player():
    """The hazard the table exists to fix, pinned so it cannot come back.

    "Kurama" really is a handle on Other. A unique handle is matched without
    regard to the team the provider states -- deliberately, because team
    names differ between sources and that is what team_aliases.py is for. So
    with no row for Butterfly, Butterfly's line is handed to Other's player:
    silently, plausibly, and wrong.
    """
    index, _ = pm.build_roster_index(ROSTER)
    matched, unmatched = pm.match_props([prop()], index, game="cs2",
                                        alias_index={})
    assert not unmatched
    assert (matched[0]["player"], matched[0]["team"]) == ("Kurama", "Other")


def test_a_handle_on_nobody_at_all_is_refused_with_a_reason():
    index, _ = pm.build_roster_index(
        {"EU": {"teams": {"Butterfly": {"players": [{"name": "Kuruma"}]}}}})
    matched, unmatched = pm.match_props([prop(player_name="Nobody")], index,
                                        game="cs2", alias_index={})
    assert not matched
    assert unmatched[0]["reason"] == "player not on any roster"


def test_an_alias_never_redirects_a_handle_the_roster_already_has():
    """The ordering IS the safety property.

    "Kurama" really is on Other. A prop naming Other must land on Other, not
    be dragged to Butterfly's Kuruma by a row scoped to a different team.
    """
    index, _ = pm.build_roster_index(ROSTER)
    matched, _ = pm.match_props([prop(team="Other")], index, game="cs2",
                                alias_index=pm.build_alias_index(TABLE))
    assert matched[0]["player"] == "Kurama"
    assert matched[0]["team"] == "Other"


def test_a_row_does_not_fire_for_a_team_it_does_not_name():
    """Scoping proved by its absence: the board behaves as if the table were
    empty, landing on Other exactly as the previous test shows it does."""
    index, _ = pm.build_roster_index(ROSTER)
    matched, _ = pm.match_props([prop(team="Somebody Else")], index,
                                game="cs2",
                                alias_index=pm.build_alias_index(TABLE))
    assert (matched[0]["player"], matched[0]["team"]) == ("Kurama", "Other")


def test_a_row_does_not_fire_for_another_game():
    index, _ = pm.build_roster_index(ROSTER)
    matched, _ = pm.match_props([prop()], index, game="valorant",
                                alias_index=pm.build_alias_index(TABLE))
    assert (matched[0]["player"], matched[0]["team"]) == ("Kurama", "Other")


def test_a_stale_row_is_reported_as_stale_not_as_a_missing_player():
    """Which is what makes pruning possible instead of guesswork."""
    index, _ = pm.build_roster_index(
        {"EU": {"teams": {"Butterfly": {"players": [{"name": "ayano"}]}}}})
    _, unmatched = pm.match_props([prop()], index, game="cs2",
                                  alias_index=pm.build_alias_index(TABLE))
    assert unmatched[0]["reason"] == ("alias points at a name that is not on "
                                      "any roster")


def test_match_props_still_works_with_no_game_and_no_table():
    """The signature grew two optional arguments; the old call must behave."""
    index, _ = pm.build_roster_index(ROSTER)
    matched, _ = pm.match_props([prop(player_name="ayano")], index)
    assert matched[0]["player"] == "ayano"


# --------------------------------------------------- the rows, against data

GAME_FILES = {"cs2": "cs2_data.json", "valorant": "valorant_data.json",
              "lol": "data.json"}


@pytest.mark.parametrize("row", pa.ALIASES,
                         ids=[f'{r["game"]}:{r["team"]}:{r["provider"]}'
                              for r in pa.ALIASES])
def test_each_row_still_points_at_somebody_really_on_that_team(row):
    """A failure here means PRUNE THE ROW, not loosen the test.

    The row says a real player is spelled two ways. If the roster no longer
    carries that spelling on that team, the player moved or retired and the
    row has stopped being true. It is harmless in operation — nothing matches
    it — and misleading to read, which is reason enough to remove it.

    The team going missing entirely is a different thing (a tier filter, an
    event rolling over) and skips rather than fails.
    """
    path = os.path.join(ROOT, GAME_FILES[row["game"]])
    if not os.path.exists(path):
        pytest.skip(f"{path} is not in this checkout")
    with open(path) as handle:
        regions = json.load(handle).get("regions") or {}

    wanted = pm.normalize_name(row["team"])
    rosters = [team for region in regions.values()
               for name, team in (region.get("teams") or {}).items()
               if pm.normalize_name(name) == wanted]
    if not rosters:
        pytest.skip(f'{row["team"]} is not in {GAME_FILES[row["game"]]} today')

    spellings = {pm.normalize_name(p.get("name"))
                 for team in rosters for p in (team.get("players") or [])}
    assert pm.normalize_name(row["roster"]) in spellings, (
        f'{row["roster"]} is no longer on {row["team"]} — prune this row')
    # And the provider's spelling must still be the one that is MISSING,
    # otherwise the row has become a no-op the matcher never reaches.
    assert pm.normalize_name(row["provider"]) not in spellings, (
        f'{row["team"]} now carries {row["provider"]} itself — this row is '
        "dead weight")

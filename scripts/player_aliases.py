#!/usr/bin/env python3
"""
Hand-curated spellings: one provider's handle for a player, and the roster's.

WHY THIS IS A TABLE AND NOT FUZZY MATCHING. props_match.normalize_name folds
case, accents and punctuation but deliberately keeps digits, because "sh1ro"
and "shiro" could be two people and silently merging two players is exactly
how you get a confident wrong number. Edit distance has the same problem one
step further out: "Kurama" and "Kuruma" differ by one character and are the
same player, while "s1mple" and "simple" differ by one character and are not
necessarily. There is no threshold that separates those, so every entry here
is a human saying "these two strings are this one player", scoped to the team
they play for, with a note saying where it was seen.

A team-scoped entry cannot merge two players globally. "Flash" on Butterfly
being an alias says nothing about "Flash" on any other roster, and
props_match still refuses a resolved handle that does not sit on the team the
provider named.

HOW TO ADD ONE. Run the board and read the funnel. scrape_props.py reports
refused props grouped by team and separates the teams this app tracks from
the ones it does not:

    cs2: 88 raw prop(s) -> 86 matched across 25 player(s), 2 unmatched
           2  player not on any roster
          1 of those are on teams this app DOES track -- the names are not
            matching:
            Butterfly: Kurama

That second group is this table's whole purpose: the lines are sitting right
there and one spelling is keeping them out. Look the player up on the source
the roster comes from, confirm it is the same person rather than assuming,
and add a row. If it is NOT the same person, the right fix is upstream in the
scraper's roster, not here.

WHEN TO REMOVE ONE. tests/test_player_aliases.py checks every row against the
committed roster files. A failure saying the roster spelling is no longer on
that team means the player moved or retired and the row is stale -- prune it
rather than loosening the test. A stale row is harmless in operation (nothing
matches it) and misleading to read, which is reason enough to keep the list
honest.
"""

#: Each row is one human statement. `provider` is what the prop feed calls the
#: player, `roster` is what the scraped roster calls them, `team` scopes it,
#: and `note` records how it was established so the next reader does not have
#: to re-derive it.
ALIASES = [
    {
        "game": "cs2",
        "team": "Butterfly",
        "provider": "Kurama",
        "roster": "Kuruma",
        "note": "One character apart. bo3.gg's Butterfly roster reads Kuruma "
                "beside ayano, Forester, k1ssly and nitzie; the provider "
                "posted Kurama on the 2026-09-29 board and the line was "
                "dropped for it.",
    },
]

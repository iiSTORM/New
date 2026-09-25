"""Fetching results for lines we already posted.

The loop was open at one end. This scraper takes the most recent
NOTABLE matches -- tier a/s or 3+ stars -- while the prop provider posts
lines on a far wider field, so a fixture could be projected, played, and
never scraped. The board could not learn whether its own edges won.

Measured on the committed history: of 724 CS2 lines old enough to grade,
646 were refused for "no completed match on that date", and 645 of those
were dated AFTER the latest match we held for that team. 63 missing
results across 51 teams unlock all 646 -- the record going from 8 graded
matches to about 70.

Which results are worth asking for is the part with logic in it, so that
part is pure and tested here. The fetch that follows reuses the roster
backfill's own proven path.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
pytest.importorskip("aiohttp")
import scrape_cs2 as sc

TODAY = "2026-09-24"


def line(team, date, player="p", game="cs2", maps=2):
    # maps defaults to 2 because every real posted line names a window
    # and maps 1-2 is the one a stored total answers. A row without one
    # is not a normal row, and is covered explicitly below.
    return {"game": game, "team": team, "player": player,
            "start_time": f"{date}T10:00:00+00:00", "stat": "kills", "maps": maps}


def played(team, date, opp="Other"):
    return {"teamA": team, "teamB": opp, "date": date, "match_id": f"{team}-{date}"}


class TestWhichTeamsToAsk:
    def test_a_played_fixture_with_no_result_is_wanted(self):
        got = sc.teams_awaiting_results([line("A", "2026-09-22")], [], {"A"}, TODAY)
        assert got == ["A"]

    def test_a_fixture_we_already_have_is_not(self):
        got = sc.teams_awaiting_results([line("A", "2026-09-22")],
                                        [played("A", "2026-09-22")], {"A"}, TODAY)
        assert got == []

    def test_a_result_on_a_different_date_does_not_count(self):
        """The whole failure mode: we hold matches for the team, just not
        the one the line was posted on."""
        got = sc.teams_awaiting_results([line("A", "2026-09-22")],
                                        [played("A", "2026-09-18")], {"A"}, TODAY)
        assert got == ["A"]

    def test_a_fixture_that_has_not_happened_is_not_missing(self):
        got = sc.teams_awaiting_results([line("A", "2026-09-27")], [], {"A"}, TODAY)
        assert got == []

    def test_a_held_match_that_cannot_settle_the_line_is_still_wanted(self):
        """The second failure mode, found after the first was fixed.

        A record with no per-map breakdown answers maps 1-2 and nothing
        else. Holding one and calling the line settled left 202 map-1
        lines stranded: the match was on file, so the backfill skipped
        it on every subsequent run, and the only path that could have
        re-fetched it -- the notable-match feed -- never covered those
        fixtures. Nothing converted them, and no amount of re-running
        would have."""
        got = sc.teams_awaiting_results([line("A", "2026-09-22", maps=1)],
                                        [played("A", "2026-09-22")], {"A"}, TODAY)
        assert got == ["A"]

    def test_and_stops_being_wanted_once_the_breakdown_lands(self):
        """Otherwise the fix trades a stranded line for an endless
        re-fetch of the same match, every run, forever."""
        with_maps = dict(played("A", "2026-09-22"), per_game=[{}, {}])
        got = sc.teams_awaiting_results([line("A", "2026-09-22", maps=1)],
                                        [with_maps], {"A"}, TODAY)
        assert got == []

    def test_a_maps_one_to_three_line_needs_three_maps_on_file(self):
        two = dict(played("A", "2026-09-22"), per_game=[{}, {}])
        three = dict(played("A", "2026-09-22"), per_game=[{}, {}, {}])
        assert sc.teams_awaiting_results([line("A", "2026-09-22", maps=3)],
                                         [two], {"A"}, TODAY) == ["A"]
        assert sc.teams_awaiting_results([line("A", "2026-09-22", maps=3)],
                                         [three], {"A"}, TODAY) == []

    def test_a_line_naming_no_window_is_not_worth_a_request(self):
        """It can never be graded whatever comes back, so chasing it
        would spend the bounded budget on nothing."""
        for bad in (None, 0, -1, "2"):
            assert sc.teams_awaiting_results([line("A", "2026-09-22", maps=bad)],
                                             [], {"A"}, TODAY) == []

    def test_one_settleable_line_does_not_cover_an_unsettleable_one(self):
        """Two windows on one fixture. The maps 1-2 line is answered by
        the stored total and the map-1 line is not, so the team is still
        worth asking about -- once."""
        rows = [line("A", "2026-09-22", maps=2), line("A", "2026-09-22", maps=1)]
        got = sc.teams_awaiting_results(rows, [played("A", "2026-09-22")], {"A"}, TODAY)
        assert got == ["A"]

    def test_a_double_header_is_covered_by_whichever_leg_settles_it(self):
        """Two matches share (team, date). The line is settleable if
        EITHER can settle it -- reading only the first would re-fetch a
        team whose result is already on file."""
        first = dict(played("A", "2026-09-22", opp="X"), match_id="A-1")
        second = dict(played("A", "2026-09-22", opp="Y"), match_id="A-2",
                      per_game=[{}, {}])
        got = sc.teams_awaiting_results([line("A", "2026-09-22", maps=1)],
                                        [first, second], {"A"}, TODAY)
        assert got == []

    def test_today_still_counts(self):
        """A match played earlier today is exactly what the next run
        should pick up."""
        got = sc.teams_awaiting_results([line("A", TODAY)], [], {"A"}, TODAY)
        assert got == ["A"]

    def test_a_team_we_do_not_track_is_skipped(self):
        """Nothing to project them with, so a result buys nothing."""
        got = sc.teams_awaiting_results([line("Z", "2026-09-22")], [], {"A"}, TODAY)
        assert got == []

    def test_another_game_is_not_our_problem(self):
        got = sc.teams_awaiting_results([line("A", "2026-09-22", game="valorant")],
                                        [], {"A"}, TODAY)
        assert got == []

    def test_the_busiest_team_comes_first(self):
        """The run is capped, so the order decides how many answers a
        capped run buys.

        Names chosen so alphabetical order DISAGREES with count order --
        the first version used "Busy" and "Quiet", which sort the same
        way and let a plain sorted() pass."""
        rows = ([line("Aardvark", "2026-09-22")]
                + [line("Zebra", "2026-09-22", player=f"p{i}") for i in range(5)])
        got = sc.teams_awaiting_results(rows, [], {"Aardvark", "Zebra"}, TODAY)
        assert got == ["Zebra", "Aardvark"]

    def test_ties_are_ordered_predictably(self):
        rows = [line("B", "2026-09-22"), line("A", "2026-09-22")]
        assert sc.teams_awaiting_results(rows, [], {"A", "B"}, TODAY) == ["A", "B"]

    def test_a_team_is_asked_for_once_however_many_lines_wait(self):
        rows = [line("A", "2026-09-22", player=f"p{i}") for i in range(9)]
        assert sc.teams_awaiting_results(rows, [], {"A"}, TODAY) == ["A"]

    def test_both_sides_of_a_stored_match_count_as_having_it(self):
        got = sc.teams_awaiting_results([line("B", "2026-09-22")],
                                        [{"teamA": "A", "teamB": "B", "date": "2026-09-22"}],
                                        {"B"}, TODAY)
        assert got == []

    def test_a_timestamped_stored_date_still_matches(self):
        """Match dates arrive both ways; comparing a date to a timestamp
        would make every stored match look like a different day."""
        got = sc.teams_awaiting_results(
            [line("A", "2026-09-22")],
            [{"teamA": "A", "teamB": "X", "date": "2026-09-22T18:00:00+00:00"}],
            {"A"}, TODAY)
        assert got == []


class TestWhatGetsKept:
    """What the fetched results do once they arrive.

    The third failure mode in this chain, and the one that made the
    other two fixes buy almost nothing. Selection was right and the
    fetch was right; the results were then dropped as duplicates of the
    very records they were meant to replace. A real run: 60 teams
    selected, 370 matches fetched, 9 kept.
    """

    def stale(self, mid="m1"):
        return {"match_id": mid, "teamA": "A", "teamB": "B", "date": "2026-09-22",
                "actual": {"A": {"p": {"k": 20}}}}

    def fresh(self, mid="m1", maps=3):
        return {"match_id": mid, "teamA": "A", "teamB": "B", "date": "2026-09-22",
                "actual": {"A": {"p": {"k": 20}}},
                "per_game": [{"A": {"p": {"k": 7}}} for _ in range(maps)]}

    def test_a_refetched_match_replaces_the_stored_one(self):
        past = [self.stale()]
        added, refreshed, touched = sc.absorb_results(past, [self.fresh()])
        assert (added, refreshed) == (0, 1)
        assert len(past) == 1
        assert len(past[0]["per_game"]) == 3
        assert touched == [past[0]]

    def test_it_does_not_duplicate_the_match(self):
        """Appending instead of replacing would leave two records for one
        fixture, and the grader would then see a double-header that
        never happened."""
        past = [self.stale()]
        sc.absorb_results(past, [self.fresh()])
        assert len(past) == 1

    def test_a_match_not_on_file_is_still_added(self):
        past = [self.stale("m1")]
        added, refreshed, _ = sc.absorb_results(past, [self.fresh("m2")])
        assert (added, refreshed) == (1, 0)
        assert len(past) == 2

    def test_an_entry_with_no_player_stats_never_lands(self):
        """Overwriting a good record with an empty one turns a source
        hiccup into data loss."""
        past = [self.stale()]
        empty = dict(self.stale(), actual={})
        added, refreshed, touched = sc.absorb_results(past, [empty, None])
        assert (added, refreshed, touched) == (0, 0, [])
        assert past[0]["actual"]["A"]["p"]["k"] == 20

    def test_a_breakdown_is_never_traded_for_a_record_without_one(self):
        """Same match, same source, so this should not arise -- and if
        the source ever serves a thinner answer, losing the finer record
        to it is the one outcome worth refusing outright."""
        past = [self.fresh()]
        added, refreshed, _ = sc.absorb_results(past, [self.stale()])
        assert (added, refreshed) == (0, 0)
        assert len(past[0]["per_game"]) == 3

    def test_a_shorter_breakdown_still_replaces_a_longer_one(self):
        """Not the same question. A series really can be re-read as
        fewer maps -- a 2-1 corrected to 2-0 -- and refusing that would
        pin a wrong result in place forever."""
        past = [self.fresh(maps=3)]
        added, refreshed, _ = sc.absorb_results(past, [self.fresh(maps=2)])
        assert (added, refreshed) == (0, 1)
        assert len(past[0]["per_game"]) == 2

    def test_the_touched_list_is_what_landed_not_a_tail_slice(self):
        """It feeds roster building. A refreshed match stays where it
        already was, so a tail slice would scan the wrong records -- and
        with nothing added, the whole list."""
        past = [self.stale("m1"), self.stale("m2")]
        _, _, touched = sc.absorb_results(past, [self.fresh("m1")])
        assert [t["match_id"] for t in touched] == ["m1"]

    def test_two_copies_in_one_batch_do_not_double_count(self):
        past = []
        added, refreshed, _ = sc.absorb_results(past, [self.fresh("m9"), self.fresh("m9")])
        assert (added, refreshed) == (1, 1) and len(past) == 1

    def test_nothing_to_absorb_is_not_an_error(self):
        past = [self.stale()]
        assert sc.absorb_results(past, []) == (0, 0, [])
        assert sc.absorb_results(past, None) == (0, 0, [])


class TestTheFetchIsWired:
    """That the results absorbed are the ones this block just fetched.

    Not a style check. An edit to the block dropped its
    `asyncio.gather(process(...))` while leaving the variable name
    `processed` in place -- and there is another `processed` in the same
    function, from the notable-match pass. So the run selected 60 teams,
    fetched nothing, re-absorbed the main block's results over
    themselves, and reported "refreshed 72 already on file". Exactly one
    record in the whole file changed.

    Nothing could catch that at runtime: no exception, no empty result,
    a plausible-looking log line, and a green suite. The only thing that
    distinguishes it is the shape of the code, so that is what is
    asserted -- the fetch feeding absorb_results has to exist, be
    awaited, and be the thing absorb_results is handed.
    """

    @staticmethod
    def tree():
        import ast
        return ast.parse((ROOT / "scripts" / "scrape_cs2.py").read_text())

    def absorb_call(self):
        import ast
        calls = [n for n in ast.walk(self.tree())
                 if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "absorb_results"]
        assert len(calls) == 1, "expected exactly one absorb_results call site"
        return calls[0]

    def test_absorb_is_handed_a_plain_name(self):
        import ast
        second = self.absorb_call().args[1]
        assert isinstance(second, ast.Name), \
            "the results absorbed should be a named variable, so the assertions below can follow it"

    def test_that_name_is_assigned_exactly_once_in_the_file(self):
        """A name assigned twice is a name that can be read stale, which
        is the whole failure this guards."""
        import ast
        want = self.absorb_call().args[1].id
        assigns = [n for n in ast.walk(self.tree())
                   if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == want for t in n.targets)]
        assert len(assigns) == 1, f"{want!r} is assigned {len(assigns)} times; it must be unambiguous"

    def test_it_is_assigned_from_an_awaited_gather_over_process(self):
        import ast
        want = self.absorb_call().args[1].id
        assign = next(n for n in ast.walk(self.tree())
                      if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == want for t in n.targets))
        assert isinstance(assign.value, ast.Await), f"{want!r} is not awaited — nothing was fetched"
        call = assign.value.value
        assert isinstance(call, ast.Call) and ast.unparse(call.func) == "asyncio.gather", \
            f"{want!r} does not come from asyncio.gather"
        assert "process(" in ast.unparse(call), \
            f"{want!r} is gathered from something other than process()"

    def test_it_is_gathered_over_the_matches_that_were_selected(self):
        """The last link: fetched from result_matches, the list the
        per-team search above fills. Gathering over anything else would
        pass every assertion here and still answer the wrong question."""
        import ast
        want = self.absorb_call().args[1].id
        assign = next(n for n in ast.walk(self.tree())
                      if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == want for t in n.targets))
        assert "result_matches" in ast.unparse(assign.value)


class TestItNeverBreaksAScrape:
    def test_no_history_file_is_not_an_error(self, tmp_path):
        assert sc.load_props_history(str(tmp_path / "nope.jsonl")) == []

    def test_one_corrupt_line_does_not_lose_the_rest(self, tmp_path):
        f = tmp_path / "h.jsonl"
        f.write_text(json.dumps(line("A", "2026-09-22")) + "\n{ not json\n"
                     + json.dumps(line("B", "2026-09-22")) + "\n")
        assert len(sc.load_props_history(str(f))) == 2

    def test_blank_lines_are_skipped(self, tmp_path):
        f = tmp_path / "h.jsonl"
        f.write_text("\n" + json.dumps(line("A", "2026-09-22")) + "\n\n")
        assert len(sc.load_props_history(str(f))) == 1

    def test_malformed_rows_do_not_crash_the_selection(self):
        rows = [{}, {"game": "cs2"}, {"game": "cs2", "team": "A"},
                {"game": "cs2", "team": "A", "start_time": "nonsense"},
                line("A", "2026-09-22")]
        assert sc.teams_awaiting_results(rows, [], {"A"}, TODAY) == ["A"]

    def test_a_match_with_no_date_is_ignored_not_fatal(self):
        got = sc.teams_awaiting_results([line("A", "2026-09-22")],
                                        [{"teamA": "A", "teamB": "B"}], {"A"}, TODAY)
        assert got == ["A"]


class TestTheRunIsBounded:
    def test_there_is_a_cap(self):
        """51 teams needed results on the day this was written, and an
        unbounded per-team fetch is how a scrape step starts timing out."""
        assert isinstance(sc.MAX_RESULT_BACKFILL_TEAMS, int)
        assert 0 < sc.MAX_RESULT_BACKFILL_TEAMS <= 60

    def test_both_backfills_share_the_per_team_limit(self):
        """It used to be defined inside the roster backfill's own if
        block, so the results backfill could not see it."""
        assert isinstance(sc.BACKFILL_MATCHES_PER_TEAM, int)

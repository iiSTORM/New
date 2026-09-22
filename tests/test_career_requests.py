"""How many requests the LoL career scrape actually makes.

Wall clock here is requests x latency / concurrency, and gol.gg is slow
(~3s a page). The scrape took 5.7 minutes of a 11.9-minute critical path,
so a request that changes no output is worth roughly a minute of every
run, twice a day, forever.

These pin BOTH halves of that: the output must be identical to what the
previous shape produced, and the request count must actually drop. Either
one alone is worthless -- a faster scrape that returns different data is
a regression, and identical data fetched the same way is not a fix.
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("requests")
pytest.importorskip("bs4")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scrape_career as sc


@pytest.fixture
def spy(monkeypatch):
    """Counts what the scraper asks for, and answers instantly."""
    calls = {"game_count": 0, "seasons": []}

    def fake_game_count(player_id):
        calls["game_count"] += 1
        return 500  # over MATCHLIST_CAP, so the full-history branch is live

    def fake_season(player_id, season):
        calls["seasons"].append(season)
        return [{"k": 4, "d": 2, "a": 6, "g": 1}]

    monkeypatch.setattr(sc, "season_aggregate", lambda games: {"g": 1, "k": 4, "d": 2, "a": 6, "kp": 60})
    def fake_sleep(_seconds):
        # Written out rather than squeezed into a lambda: the first draft
        # was `calls.setdefault("slept", 0) or calls.__setitem__(...)`,
        # which stops counting after the first sleep because setdefault
        # then returns a truthy value and short-circuits the `or`. It
        # reported 1 for every case and passed the test that mattered
        # least.
        calls["slept"] = calls.get("slept", 0) + 1

    monkeypatch.setattr(sc, "get_career_game_count", fake_game_count)
    monkeypatch.setattr(sc, "fetch_player_season", fake_season)
    monkeypatch.setattr(sc.time, "sleep", fake_sleep)
    return calls


def cached(count=None, seasons=None):
    """A previous run's record for one player."""
    record = {"name": "Faker", "season_aggregates": {
        s: {"g": 10, "k": 4, "d": 2, "a": 6, "kp": 60}
        for s in (seasons if seasons is not None else [sc.CURRENT_SEASON])}}
    if count is not None:
        record["career_game_count"] = count
    return {"1": record}


def all_seasons():
    return sc.all_seasons_back_to(sc.CURRENT_SEASON)


class TestTheCountIsRemembered:
    """The game count only decides one thing -- whether this player needs
    every season fetched individually -- and the answer barely changes.

    An earlier version skipped the request only when every past season was
    already cached. Correct, and nearly useless: 14 of 320 players
    qualified, because a player only ACCUMULATES old seasons after
    crossing the cap once, so most have a single cached season and never
    match. A full run measured no improvement at all. Caching the count
    is what answers the question for the other 306.
    """

    def test_a_player_well_under_the_cap_costs_no_request(self, spy):
        sc.process_one_player("Faker", 1, cached(count=40), {"done": 0}, 1)
        assert spy["game_count"] == 0
        assert spy["seasons"] == [sc.CURRENT_SEASON]

    def test_a_player_already_over_the_cap_costs_no_request_either(self, spy):
        """More games cannot bring a total back under the cap, so the
        answer is already known -- it is 'yes, fetch every season'."""
        sc.process_one_player("Faker", 1, cached(count=500, seasons=all_seasons()),
                              {"done": 0}, 1)
        assert spy["game_count"] == 0
        assert spy["seasons"] == [sc.CURRENT_SEASON], "all past seasons were cached"

    def test_over_the_cap_with_seasons_missing_still_fetches_them(self, spy):
        sc.process_one_player("Faker", 1, cached(count=500, seasons=all_seasons()[:3]),
                              {"done": 0}, 1)
        assert spy["game_count"] == 0
        assert set(spy["seasons"]) == set(all_seasons()[3:]) | {sc.CURRENT_SEASON}

    def test_a_count_near_the_cap_is_re_asked(self, spy):
        """Close enough that a run's worth of games could have crossed it.
        Guessing here would silently stop fetching a veteran's history."""
        sc.process_one_player("Faker", 1, cached(count=sc.MATCHLIST_CAP - 1), {"done": 0}, 1)
        assert spy["game_count"] == 1

    def test_the_margin_edge_is_where_it_says_it_is(self, spy):
        sc.process_one_player("Faker", 1,
                              cached(count=sc.MATCHLIST_CAP - sc.COUNT_STALENESS_MARGIN),
                              {"done": 0}, 1)
        assert spy["game_count"] == 0

    def test_a_player_with_no_remembered_count_is_asked(self, spy):
        sc.process_one_player("Faker", 1, cached(count=None), {"done": 0}, 1)
        assert spy["game_count"] == 1

    def test_the_count_is_written_back_for_next_time(self, spy):
        _, record = sc.process_one_player("Faker", 1, cached(count=None), {"done": 0}, 1)
        assert record["career_game_count"] == 500, "the fetched count must persist"

    def test_a_remembered_count_survives_a_run_that_did_not_re_ask(self, spy):
        _, record = sc.process_one_player("Faker", 1, cached(count=40), {"done": 0}, 1)
        assert record["career_game_count"] == 40, (
            "dropping it would make the next run ask again, undoing the saving")

    def test_the_margin_is_big_enough_to_matter(self):
        assert sc.COUNT_STALENESS_MARGIN >= 10, (
            "a margin this small stops covering a run's worth of games")
        assert sc.COUNT_STALENESS_MARGIN < sc.MATCHLIST_CAP


class TestSleeping:
    def test_a_single_season_sleeps_not_at_all(self, spy):
        sc.process_one_player("Faker", 1, cached(count=40), {"done": 0}, 1)
        assert spy.get("slept", 0) == 0, "the sleep belongs between fetches, not after the last"

    def test_several_seasons_sleep_between_them_only(self, spy):
        sc.process_one_player("Faker", 1, cached(count=500, seasons=all_seasons()[:3]),
                              {"done": 0}, 1)
        assert spy.get("slept", 0) == len(spy["seasons"]) - 1


class TestBrandNewPlayer:
    def test_a_player_with_no_cache_at_all_is_handled(self, spy):
        _, record = sc.process_one_player("Rookie", 99, {}, {"done": 0}, 1)
        assert spy["game_count"] == 1
        assert sc.CURRENT_SEASON in record["season_aggregates"]


class TestOutputIsUnchanged:
    """The optimisation must be invisible in the result.

    Stated directly rather than by diffing two runs: the record for a
    fully-cached player must hold every cached season, a freshly fetched
    current season, and a career baseline derived from exactly those. An
    earlier draft tried to re-run the old code path by monkeypatching
    all_seasons_back_to, which changed WHICH seasons were fetched and so
    compared two different things.
    """

    def test_every_cached_season_survives_and_current_is_refreshed(self, spy):
        previous = cached(count=500, seasons=all_seasons())
        _, record = sc.process_one_player("Faker", 1, previous, {"done": 0}, 1)
        before = previous["1"]["season_aggregates"]
        for season, agg in before.items():
            if season == sc.CURRENT_SEASON:
                continue
            assert record["season_aggregates"][season] == agg, f"{season} was not preserved"
        assert spy["seasons"] == [sc.CURRENT_SEASON]

    def test_the_career_baseline_matches_the_seasons_it_was_built_from(self, spy):
        _, record = sc.process_one_player("Faker", 1, cached(count=500, seasons=all_seasons()), {"done": 0}, 1)
        expected = sc.decayed_career_baseline(record["season_aggregates"], sc.CURRENT_SEASON)
        assert record["career"] == expected

    def test_the_name_is_carried_through(self, spy):
        _, record = sc.process_one_player("Faker", 1, cached(count=500, seasons=all_seasons()), {"done": 0}, 1)
        assert record["name"] == "Faker"


class TestRequestCounting:
    """The measurement that made the first attempt look like a regression.

    Across three runs of identical code the gol.gg stats step took 4.6,
    6.0 and 7.7 minutes. A 67% spread on unchanged code swamps any change
    worth making, and it is why a no-op optimisation read as a 30%
    slowdown. Requests are what this code controls; wall clock is what
    the site controls.
    """

    def test_the_counter_starts_at_zero_and_is_reported(self, capsys):
        sc.REQUEST_TOTAL["n"] = 0
        sc.report_requests("career scrape")
        out = capsys.readouterr().out
        assert "0 requests" in out and "career scrape" in out

    def test_every_fetch_is_counted(self, monkeypatch):
        sc.REQUEST_TOTAL["n"] = 0

        class Resp:
            status_code = 200
            text = "<html></html>"

        monkeypatch.setattr(sc.requests, "get", lambda *a, **k: Resp())
        for _ in range(3):
            sc.fetch("https://example.invalid/x")
        assert sc.REQUEST_TOTAL["n"] == 3

    def test_retries_are_counted_too(self, monkeypatch):
        """A run that spent its time being retried should say so, rather
        than reporting the number of pages it wanted."""
        sc.REQUEST_TOTAL["n"] = 0
        calls = {"n": 0}

        def flaky(*a, **k):
            calls["n"] += 1
            raise sc.requests.RequestException("boom")

        monkeypatch.setattr(sc.requests, "get", flaky)
        monkeypatch.setattr(sc.time, "sleep", lambda *_: None)
        sc.fetch("https://example.invalid/x", retries=3)
        assert sc.REQUEST_TOTAL["n"] == calls["n"] > 1


class TestTheCountIsReadable:
    """A metric you cannot read is not a metric.

    The counter was added, printed to stdout mid-job, and turned out to be
    unreachable: GitHub's job-log API returns the tail of a job, and the
    tail of these jobs is always the git push. It goes to the run summary
    now, which appears at the top of the run page.
    """

    def test_it_lands_in_the_step_summary(self, tmp_path, monkeypatch, capsys):
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        sc.REQUEST_TOTAL["n"] = 4321
        sc.report_requests("career scrape")
        assert "4321 requests" in summary.read_text()

    def test_it_still_prints_to_stdout(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "s.md"))
        sc.REQUEST_TOTAL["n"] = 7
        sc.report_requests("career scrape")
        assert "7 requests" in capsys.readouterr().out

    def test_outside_a_workflow_it_is_a_no_op(self, monkeypatch, capsys):
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        sc.REQUEST_TOTAL["n"] = 3
        sc.report_requests("career scrape")  # must not raise
        assert "3 requests" in capsys.readouterr().out

    def test_an_unwritable_summary_never_fails_the_scrape(self, monkeypatch, capsys):
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", "/proc/nonexistent/nope.md")
        sc.REQUEST_TOTAL["n"] = 5
        sc.report_requests("career scrape")  # must not raise
        assert "5 requests" in capsys.readouterr().out

    def test_several_runs_append_rather_than_overwrite(self, tmp_path, monkeypatch):
        summary = tmp_path / "s.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        sc.REQUEST_TOTAL["n"] = 1
        sc.report_requests("career scrape")
        sc.REQUEST_TOTAL["n"] = 2
        sc.report_requests("career scrape")
        assert summary.read_text().count("requests made") == 2, (
            "each scraper in a job writes its own line; overwriting would hide the others")

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


def cached_all_seasons():
    """A player whose every past season is already in the cache."""
    return {"1": {"name": "Faker", "season_aggregates": {
        s: {"g": 10, "k": 4, "d": 2, "a": 6, "kp": 60}
        for s in sc.all_seasons_back_to(sc.CURRENT_SEASON)}}}


class TestFullyCachedPlayer:
    def test_no_game_count_request_is_made(self, spy):
        sc.process_one_player("Faker", 1, cached_all_seasons(), {"done": 0}, 1)
        assert spy["game_count"] == 0, (
            "the game count only decides whether to refetch seasons already cached")

    def test_only_the_current_season_is_fetched(self, spy):
        sc.process_one_player("Faker", 1, cached_all_seasons(), {"done": 0}, 1)
        assert spy["seasons"] == [sc.CURRENT_SEASON]

    def test_nothing_is_slept_for_a_single_season(self, spy):
        sc.process_one_player("Faker", 1, cached_all_seasons(), {"done": 0}, 1)
        assert spy.get("slept", 0) == 0, "the sleep belongs between fetches, not after the last"

    def test_the_cached_seasons_survive(self, spy):
        _, record = sc.process_one_player("Faker", 1, cached_all_seasons(), {"done": 0}, 1)
        assert set(record["season_aggregates"]) == set(sc.all_seasons_back_to(sc.CURRENT_SEASON))
        assert record["career"] is not None


class TestPartiallyCachedPlayer:
    def partial(self):
        seasons = sc.all_seasons_back_to(sc.CURRENT_SEASON)
        return {"1": {"name": "Faker", "season_aggregates": {
            s: {"g": 10, "k": 4, "d": 2, "a": 6, "kp": 60} for s in seasons[:3]}}}

    def test_the_game_count_is_still_consulted(self, spy):
        sc.process_one_player("Faker", 1, self.partial(), {"done": 0}, 1)
        assert spy["game_count"] == 1, "with seasons missing, the decision is real again"

    def test_the_missing_seasons_are_fetched(self, spy):
        sc.process_one_player("Faker", 1, self.partial(), {"done": 0}, 1)
        seasons = sc.all_seasons_back_to(sc.CURRENT_SEASON)
        assert set(spy["seasons"]) == set(seasons[3:]) | {sc.CURRENT_SEASON}

    def test_it_sleeps_between_them_but_not_after_the_last(self, spy):
        sc.process_one_player("Faker", 1, self.partial(), {"done": 0}, 1)
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
        previous = cached_all_seasons()
        _, record = sc.process_one_player("Faker", 1, previous, {"done": 0}, 1)
        cached = previous["1"]["season_aggregates"]
        for season, agg in cached.items():
            if season == sc.CURRENT_SEASON:
                continue
            assert record["season_aggregates"][season] == agg, f"{season} was not preserved"
        assert spy["seasons"] == [sc.CURRENT_SEASON]

    def test_the_career_baseline_matches_the_seasons_it_was_built_from(self, spy):
        _, record = sc.process_one_player("Faker", 1, cached_all_seasons(), {"done": 0}, 1)
        expected = sc.decayed_career_baseline(record["season_aggregates"], sc.CURRENT_SEASON)
        assert record["career"] == expected

    def test_the_name_is_carried_through(self, spy):
        _, record = sc.process_one_player("Faker", 1, cached_all_seasons(), {"done": 0}, 1)
        assert record["name"] == "Faker"

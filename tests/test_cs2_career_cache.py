"""Not re-downloading CS2 per-game history that is already on record.

This scraper had no cache at all. Every run re-resolved ~263 player ids
and re-fetched each player's last 20 matches game by game -- 40-60
requests per player, well over 10,000 per run, for per-game box scores
that cannot change once played. It was the largest single step in the
pipeline at over six minutes.

MATCHES_PER_PLAYER is a ROLLING window of the most recent 20 matches, so
between two runs twelve hours apart the overlap is nearly total. The
saving is the overlap; the risk is getting the identity wrong, which is
what most of these are about.
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scrape_cs2_career as sc


def record(*games, player_id=42):
    return {"player_id": player_id, "games_fetched": len(games), "games": list(games)}


def game(game_id, k=20):
    return {"game_id": game_id, "k": k, "d": 15, "a": 5, "date": "2026-01-01T00:00:00+00:00"}


class TestCachedGames:
    def test_games_are_indexed_by_their_own_id(self):
        got = sc.cached_games_by_id(record(game(1), game(2)))
        assert set(got) == {1, 2}
        assert got[1]["k"] == 20

    def test_two_games_keep_their_own_box_scores(self):
        """The reason the key is bo3.gg's game id. Attaching one game's
        stats to another would be silent and wrong -- exactly the failure
        that makes being slow the cheaper option."""
        got = sc.cached_games_by_id(record(game(1, k=30), game(2, k=5)))
        assert got[1]["k"] == 30 and got[2]["k"] == 5

    def test_games_written_before_ids_existed_are_not_reused(self):
        """One migration run, then permanently cheap. Guessing a weaker
        key instead would risk the wrong box score forever."""
        old = {"k": 20, "d": 15, "a": 5, "date": "2026-01-01T00:00:00+00:00"}
        assert sc.cached_games_by_id({"games": [old]}) == {}

    def test_a_mixed_record_reuses_only_what_is_identifiable(self):
        old = {"k": 1, "d": 1, "a": 1, "date": "2026-01-01T00:00:00+00:00"}
        got = sc.cached_games_by_id({"games": [old, game(7)]})
        assert set(got) == {7}

    def test_an_empty_or_missing_record_is_not_an_error(self):
        for value in (None, {}, {"games": None}, {"games": []}, {"player_id": 1}):
            assert sc.cached_games_by_id(value) == {}


class TestPreviousOutput:
    def test_a_missing_file_is_an_empty_cache(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert sc.load_previous_output() == {}

    def test_an_unreadable_file_is_an_empty_cache(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        Path(sc.OUTPUT_PATH).write_text("{ not json")
        assert sc.load_previous_output() == {}, (
            "a corrupt cache must cost a slow run, never a crashed one")

    def test_a_real_file_round_trips(self, tmp_path, monkeypatch):
        import json
        monkeypatch.chdir(tmp_path)
        Path(sc.OUTPUT_PATH).write_text(json.dumps({"donk": record(game(1))}))
        assert sc.load_previous_output()["donk"]["player_id"] == 42


class TestTheSavingIsReal:
    def test_a_full_overlap_needs_no_game_fetches(self):
        cached = sc.cached_games_by_id(record(*[game(i) for i in range(40)]))
        refs = [(i, "2026-01-01T00:00:00+00:00") for i in range(40)]
        missing = [(g, b) for g, b in refs if g not in cached]
        assert missing == []

    def test_only_the_new_games_are_fetched(self):
        cached = sc.cached_games_by_id(record(*[game(i) for i in range(40)]))
        refs = [(i, "2026-01-01T00:00:00+00:00") for i in range(45)]
        missing = [g for g, _ in refs if g not in cached]
        assert missing == [40, 41, 42, 43, 44], "five new games, not forty-five"

    def test_an_empty_cache_fetches_everything(self):
        cached = sc.cached_games_by_id({})
        refs = [(i, "x") for i in range(40)]
        assert len([g for g, _ in refs if g not in cached]) == 40


class TestCachedAndFreshAreTheSameShape:
    """The bug that stopped this scraper dead.

    The cache writes dates with .isoformat(), so it reads them back as
    STRINGS, while a freshly fetched game carries a real datetime. Both
    go into one list. decayed_baseline does `now - g["date"]` and the
    serializer calls g["date"].isoformat(); each raises on whichever
    kind it did not get -- and it raised inside asyncio.gather, so one
    cached game killed the whole run.

    It did, on every run since the cache landed: the career file sat at
    263 records while the roster grew past 1,400, and the step finished
    in two seconds after reporting 1,274 players to process.
    """

    def test_a_stored_date_comes_back_as_a_datetime(self):
        import datetime as dt
        got = sc.cached_games_by_id(record(game(1)))
        assert isinstance(got[1]["date"], dt.datetime)

    def test_a_date_that_is_already_a_datetime_is_left_alone(self):
        import datetime as dt
        when = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        got = sc.cached_games_by_id({"games": [{"game_id": 1, "date": when}]})
        assert got[1]["date"] == when

    def test_a_naive_date_is_made_comparable(self):
        """`now` is timezone-aware, and subtracting a naive datetime from
        it raises -- the same crash one layer down."""
        got = sc.cached_games_by_id({"games": [{"game_id": 1, "k": 1, "d": 1, "a": 1,
                                                "date": "2026-01-01T00:00:00"}]})
        assert sc.decayed_baseline(list(got.values())) is not None

    def test_a_z_suffixed_date_parses(self):
        """bo3.gg writes +00:00 and this scraper writes isoformat, so a
        Z is not expected -- but it is what every other date reader here
        accepts, and stdlib support for it is version-dependent."""
        got = sc.cached_games_by_id({"games": [{"game_id": 1, "k": 1, "d": 1, "a": 1,
                                                "date": "2026-01-01T00:00:00Z"}]})
        assert sc.decayed_baseline(list(got.values())) is not None

    def test_an_unreadable_date_is_dropped_rather_than_carried(self):
        for bad in ("not a date", "", None, 12345):
            got = sc.cached_games_by_id({"games": [{"game_id": 1, "date": bad}]})
            assert got == {}, f"{bad!r} must not reach the arithmetic"

    def test_a_cached_game_can_be_decayed(self):
        """What decayed_baseline does to every game, cached or not."""
        assert sc.decayed_baseline(list(sc.cached_games_by_id(
            record(game(1), game(2))).values()))["g"] == 2

    def test_a_cached_game_can_be_serialised_back(self):
        """And what process_one_player does to every game on the way
        out. A string date has no .isoformat()."""
        games = list(sc.cached_games_by_id(record(game(1))).values())
        assert [{**g, "date": g["date"].isoformat()} for g in games][0]["date"]

    def test_a_run_that_is_entirely_cache_still_produces_a_record(self):
        """End to end, the way the failing run went: every game already
        on record, nothing to fetch, and it died anyway."""
        import asyncio

        async def no_matches(session, player_id):
            return [{"games": [{"id": 1, "begin_at": "2026-01-01T00:00:00+00:00"},
                               {"id": 2, "begin_at": "2026-01-02T00:00:00+00:00"}]}]

        async def never(*a, **kw):
            raise AssertionError("nothing should be fetched — it is all cached")

        orig = sc.fetch_player_matches, sc.fetch_game_stats_for_player
        sc.fetch_player_matches, sc.fetch_game_stats_for_player = no_matches, never
        try:
            name, rec = asyncio.run(sc.process_one_player(
                None, "donk", {"done": 0}, 1, {"donk": record(game(1), game(2))}))
        finally:
            sc.fetch_player_matches, sc.fetch_game_stats_for_player = orig
        assert rec["games_fetched"] == 2
        assert all(isinstance(g["date"], str) for g in rec["games"]), (
            "what goes back to the file must be JSON, not datetimes")


class TestRequestCounting:
    def test_the_counter_exists_and_starts_countable(self):
        assert isinstance(sc.REQUEST_TOTAL["n"], int)

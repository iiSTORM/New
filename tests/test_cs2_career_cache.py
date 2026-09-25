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


def record(*games, player_id=42, schema=sc.CAREER_GAME_SCHEMA):
    """A record as a real run writes it, current schema by default.

    `schema` is a parameter because "a record from an older schema" is
    its own case -- it must be re-fetched rather than reused -- and a
    fixture that silently omitted the field made every cache test
    exercise that path instead of the one it named."""
    rec = {"player_id": player_id, "games_fetched": len(games), "games": list(games)}
    if schema is not None:
        rec["games_schema"] = schema
    return rec


def game(game_id, k=20):
    return {"game_id": game_id, "k": k, "d": 15, "a": 5, "date": "2026-01-01T00:00:00+00:00"}


class TestSchemaMigration:
    """A record from before a field was captured is re-fetched once.

    Cached games are never otherwise re-fetched, so adding headshots to
    the per-game record would have been useless without this: every game
    already on file -- a median of 41 per player -- would have stayed
    without one forever, and the tier would have had only matches played
    from that day onward to work with.
    """

    def test_a_record_from_an_older_schema_is_not_reused(self):
        old = record(game(1), game(2), schema=None)
        assert sc.cached_games_by_id(old) == {}

    def test_a_current_record_is_reused(self):
        assert set(sc.cached_games_by_id(record(game(1), game(2)))) == {1, 2}

    def test_a_record_from_a_FUTURE_schema_is_also_not_reused(self):
        """Only equality is safe. A file written by a newer version and
        read by an older one is a rollback, and guessing at a shape this
        code has never seen is how one game's box score ends up attached
        to another."""
        ahead = record(game(1), schema=sc.CAREER_GAME_SCHEMA + 1)
        assert sc.cached_games_by_id(ahead) == {}

    def test_a_re_fetched_game_is_accepted_without_the_new_field(self):
        """Otherwise a match the source genuinely has no headshots for
        is re-fetched on every run, forever. The schema marks that the
        question was ASKED, not that the answer was yes."""
        no_hs = record(game(1))          # current schema, no "hs" key
        assert set(sc.cached_games_by_id(no_hs)) == {1}


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
        assert sc.cached_games_by_id({"games_schema": sc.CAREER_GAME_SCHEMA, "games": [old]}) == {}

    def test_a_mixed_record_reuses_only_what_is_identifiable(self):
        old = {"k": 1, "d": 1, "a": 1, "date": "2026-01-01T00:00:00+00:00"}
        got = sc.cached_games_by_id({"games_schema": sc.CAREER_GAME_SCHEMA, "games": [old, game(7)]})
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
        got = sc.cached_games_by_id({"games_schema": sc.CAREER_GAME_SCHEMA, "games": [{"game_id": 1, "date": when}]})
        assert got[1]["date"] == when

    def test_a_naive_date_is_made_comparable(self):
        """`now` is timezone-aware, and subtracting a naive datetime from
        it raises -- the same crash one layer down."""
        got = sc.cached_games_by_id({"games_schema": sc.CAREER_GAME_SCHEMA, "games": [{"game_id": 1, "k": 1, "d": 1, "a": 1,
                                                "date": "2026-01-01T00:00:00"}]})
        assert sc.decayed_baseline(list(got.values())) is not None

    def test_a_z_suffixed_date_parses(self):
        """bo3.gg writes +00:00 and this scraper writes isoformat, so a
        Z is not expected -- but it is what every other date reader here
        accepts, and stdlib support for it is version-dependent."""
        got = sc.cached_games_by_id({"games_schema": sc.CAREER_GAME_SCHEMA, "games": [{"game_id": 1, "k": 1, "d": 1, "a": 1,
                                                "date": "2026-01-01T00:00:00Z"}]})
        assert sc.decayed_baseline(list(got.values())) is not None

    def test_an_unreadable_date_is_dropped_rather_than_carried(self):
        for bad in ("not a date", "", None, 12345):
            got = sc.cached_games_by_id({"games_schema": sc.CAREER_GAME_SCHEMA, "games": [{"game_id": 1, "date": bad}]})
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


class TestHeadshotsAreCaptured:
    """Why headshots, and why it is not part of the completeness gate.

    Across every line this app has recorded, CS2 headshots is 592 of
    1,519 -- 39% of the market, second only to CS2 kills and more than
    Valorant and LoL combined. Deaths and assists, both modelled as
    first-class stats, have four lines between them, ever. The career
    tier is worth +2.4% to +3.3% on the stats that have one, and
    headshots has never had one.
    """

    @staticmethod
    def _capture(row):
        """The real capture, driven through the real function."""
        import asyncio

        async def fake_get(session, path, **kw):
            return [{"steam_profile": {"player": {"id": 7}}, **row}]

        import scrape_cs2_career as mod
        original, mod.bo3_get = mod.bo3_get, fake_get
        try:
            return asyncio.run(mod.fetch_game_stats_for_player(None, 1, 7))
        finally:
            mod.bo3_get = original

    def test_headshots_are_captured_when_the_source_has_them(self):
        got = self._capture({"kills": 20, "death": 15, "assists": 5, "headshots": 9})
        assert got == {"k": 20, "d": 15, "a": 5, "hs": 9}

    def test_a_game_without_them_is_still_kept(self):
        """It is good history for three stats out of four. Dropping it
        would throw away history in order to acquire history."""
        got = self._capture({"kills": 20, "death": 15, "assists": 5})
        assert got == {"k": 20, "d": 15, "a": 5}

    def test_a_null_headshot_figure_is_not_stored_as_zero(self):
        """The documented fake-zero failure: bo3.gg returns null for a
        map it has not finished processing, and a zero there is
        indistinguishable from a real one that drags the average down."""
        got = self._capture({"kills": 20, "death": 15, "assists": 5, "headshots": None})
        assert "hs" not in got

    def test_the_kda_gate_is_unchanged(self):
        """Headshots must not become a fourth reason to drop a game."""
        assert self._capture({"kills": 20, "death": None, "assists": 5, "headshots": 9}) is None


class TestTheCareerTierSkipsAMissingStat:
    """Mirrors the JS assertions in tests/render_smoke.test.mjs.

    Both ports had `missing -> 0`, so the parity harness agreed with
    itself while both were wrong -- and it will keep agreeing, because
    no career game currently lacks a stat. Each port is therefore
    asserted on its own rather than against the other.
    """

    @staticmethod
    def rate(games, stat):
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "dev"))
        import optimize_weights as ow
        return ow.point_in_time_cs2_career_rate({"career_games": games}, stat, "2026-09-01")

    G = "2026-08-01T00:00:00+00:00"

    def test_a_game_without_the_stat_is_skipped(self):
        got = self.rate([{"date": self.G, "k": 20, "hs": 10},
                         {"date": self.G, "k": 20, "hs": 10},
                         {"date": self.G, "k": 20}], "hs")
        assert abs(got - 10) < 1e-9, "a missing figure counted as zero gives ~6.67"

    def test_a_real_zero_still_counts(self):
        got = self.rate([{"date": self.G, "k": 20, "hs": 10},
                         {"date": self.G, "k": 20, "hs": 0}], "hs")
        assert abs(got - 5) < 1e-9

    def test_a_stat_nothing_records_has_no_rate(self):
        games = [{"date": self.G, "k": 20}, {"date": self.G, "k": 20}]
        assert self.rate(games, "hs") is None
        assert self.rate(games, "k") == 20, "k/d/a behaviour must be unchanged"

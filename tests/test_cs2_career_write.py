"""The CS2 career file actually getting written, and never getting wiped.

cs2_career_data.json carries the CS2 career tier, weighted 1.0 on all
three stats. Turning it off across the same rows costs +0.5% on kills,
+2.4% on deaths and +2.1% on assists. It sat at 263 records while the
roster grew past 1,400 because the write block had been mis-indented
into _write_run_summary and referred to `data`, a local of main() --
every run raised NameError, and it raised AFTER open(..., "w") had
already truncated the file. The step still exited 0.

So these cover the two halves of the bug: the write has to happen, and
it must never be able to leave less behind than it found.
"""
import ast
import builtins
import json
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import scrape_cs2_career as sc


def record(n_games, player_id=42):
    return {
        "player_id": player_id,
        "games_fetched": n_games,
        "games": [{"game_id": i, "k": 20, "d": 15, "a": 5,
                   "date": "2026-01-01T00:00:00+00:00"} for i in range(n_games)],
    }


def written(tmp_path):
    return json.loads((tmp_path / sc.OUTPUT_PATH).read_text())


class TestTheWriteHappens:
    def test_a_resolved_run_lands_on_disk(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert sc.write_output({"donk": record(3)}) is True
        assert written(tmp_path)["donk"]["games_fetched"] == 3

    def test_what_was_written_is_what_the_next_run_reads(self, tmp_path, monkeypatch):
        """The file is only worth anything if it round-trips through the
        cache loader -- that is the sole consumer inside this script."""
        monkeypatch.chdir(tmp_path)
        sc.write_output({"donk": record(2)})
        assert set(sc.cached_games_by_id(sc.load_previous_output()["donk"])) == {0, 1}

    def test_main_writes_what_the_scrape_built(self, tmp_path, monkeypatch):
        """The regression itself: build_career_data returned real records
        and main dropped them on the floor."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sc, "build_career_data",
                            lambda: _immediately({"donk": record(4)}))
        sc.main()
        assert written(tmp_path)["donk"]["games_fetched"] == 4

    def test_dates_that_are_not_strings_do_not_abort_the_write(self, tmp_path, monkeypatch):
        """Records carry datetime objects on some paths; json.dump would
        raise on them, and the old code raised after truncating."""
        from datetime import datetime, timezone
        monkeypatch.chdir(tmp_path)
        rec = record(1)
        rec["games"][0]["date"] = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert sc.write_output({"donk": rec}) is True
        assert "2026-01-01" in written(tmp_path)["donk"]["games"][0]["date"]


class TestNothingGoodIsDestroyed:
    def test_an_empty_run_leaves_a_good_file_alone(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sc.write_output({f"p{i}": record(5) for i in range(100)})
        assert sc.write_output({}) is False
        assert len(written(tmp_path)) == 100

    def test_an_empty_run_on_a_first_ever_run_writes_nothing_and_says_so(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert sc.write_output({}) is False
        assert not (tmp_path / sc.OUTPUT_PATH).exists()

    def test_a_collapse_is_refused_even_when_the_shape_looks_fine(self, tmp_path, monkeypatch):
        """The failure mode that gets past a len() check: player ids come
        from the cache, so an outage returns a full-length dict of
        records with no games in them at all."""
        monkeypatch.chdir(tmp_path)
        sc.write_output({f"p{i}": record(5) for i in range(100)})
        hollow = {f"p{i}": {"player_id": i, "games_fetched": 0, "games": []}
                  for i in range(100)}
        assert sc.write_output(hollow) is False
        assert sum(1 for v in written(tmp_path).values() if v["games"]) == 100

    def test_a_partial_collapse_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sc.write_output({f"p{i}": record(5) for i in range(100)})
        assert sc.write_output({f"p{i}": record(5) for i in range(20)}) is False
        assert len(written(tmp_path)) == 100

    def test_an_ordinary_run_that_lost_a_few_players_still_writes(self, tmp_path, monkeypatch):
        """Rosters churn. Only a collapse is refused, not a bad day."""
        monkeypatch.chdir(tmp_path)
        sc.write_output({f"p{i}": record(5) for i in range(100)})
        assert sc.write_output({f"p{i}": record(5) for i in range(90)}) is True
        assert len(written(tmp_path)) == 90

    def test_growth_is_never_mistaken_for_a_collapse(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sc.write_output({f"p{i}": record(5) for i in range(100)})
        assert sc.write_output({f"p{i}": record(5) for i in range(400)}) is True
        assert len(written(tmp_path)) == 400

    def test_an_unreadable_existing_file_does_not_block_a_write(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / sc.OUTPUT_PATH).write_text("{ not json")
        assert sc.write_output({"donk": record(3)}) is True
        assert written(tmp_path)["donk"]["games_fetched"] == 3


class TestOutageKeepsHistory:
    """A player whose run returned 0 matches had, until this was fixed,
    their whole history replaced by an empty record -- and since the dict
    stayed full-length, that wrote cleanly over a good file."""

    @staticmethod
    def _run(previous, matches):
        import asyncio
        calls = {"n": 0}

        async def fake_matches(session, player_id):
            calls["n"] += 1
            return matches

        import scrape_cs2_career as m
        orig = m.fetch_player_matches
        m.fetch_player_matches = fake_matches
        try:
            return asyncio.run(m.process_one_player(
                None, "donk", {"done": 0}, 1, previous))
        finally:
            m.fetch_player_matches = orig

    def test_zero_matches_keeps_the_games_already_on_record(self):
        name, rec = self._run({"donk": record(6)}, [])
        assert name == "donk"
        assert len(sc.cached_games_by_id(rec)) == 6, (
            "an unreachable source must not empty a real career")

    def test_zero_matches_for_a_genuinely_new_player_is_still_empty(self):
        _, rec = self._run({"donk": {"player_id": 42}}, [])
        assert rec["player_id"] == 42
        assert sc.cached_games_by_id(rec) == {}

    def test_an_empty_record_uses_the_games_key_the_readers_look_for(self):
        _, rec = self._run({"donk": {"player_id": 42}}, [])
        assert "games" in rec, "consumers read `games`; a `career` key is dead weight"


class TestTheRunAlwaysGetsToSaveItsWork:
    """The workflow step is capped at 45 minutes and a timeout there
    SIGKILLs the process before write_output runs -- so a run that is
    too big to finish saves nothing, and the next run starts from the
    same cold cache and dies in the same place. With ~1,000 cold players
    at 40-60 requests each, that is the run this is about."""

    @pytest.fixture(autouse=True)
    def _clean_budget(self):
        sc._DEADLINE["at"] = None
        sc.BUDGET_SKIPS["n"] = 0
        yield
        sc._DEADLINE["at"] = None
        sc.BUDGET_SKIPS["n"] = 0

    def test_no_budget_started_never_starves_a_player(self):
        assert sc.budget_exhausted() is False

    def test_a_fresh_budget_is_not_exhausted(self):
        sc.start_budget(now=0.0)
        assert sc.budget_exhausted(now=0.0) is False
        assert sc.budget_exhausted(now=sc.TIME_BUDGET_SECONDS - 1) is False

    def test_the_budget_runs_out(self):
        sc.start_budget(now=0.0)
        assert sc.budget_exhausted(now=sc.TIME_BUDGET_SECONDS) is True

    def test_the_budget_is_under_the_workflow_timeout(self):
        """Pointless if the step is killed before the budget fires."""
        import re
        wf = (Path(__file__).resolve().parent.parent
              / ".github/workflows/scrape.yml").read_text()
        block = wf[wf.index("scripts/scrape_cs2_career.py"):]
        minutes = int(re.search(r"timeout-minutes:\s*(\d+)", block).group(1))
        assert sc.TIME_BUDGET_SECONDS < minutes * 60, (
            f"budget {sc.TIME_BUDGET_SECONDS}s must leave room under the "
            f"{minutes}-minute step timeout")

    def test_an_out_of_budget_player_makes_no_requests(self):
        import asyncio
        calls = {"n": 0}

        async def boom(*a, **kw):
            calls["n"] += 1
            return None

        sc.start_budget(now=0.0)
        sc._DEADLINE["at"] = time.monotonic() - 1  # already spent
        orig = sc.resolve_player_id, sc.fetch_player_matches
        sc.resolve_player_id, sc.fetch_player_matches = boom, boom
        try:
            name, rec = asyncio.run(sc.process_one_player(
                None, "donk", {"done": 0}, 1, {"donk": record(6)}))
        finally:
            sc.resolve_player_id, sc.fetch_player_matches = orig
        assert calls["n"] == 0, "a skipped player must cost nothing"
        assert len(sc.cached_games_by_id(rec)) == 6, (
            "and must hand back what was already on record, not an empty record")
        # The other route to the same place, once the budget bites
        # mid-run: bo3_get declines, fetch_player_matches comes back
        # empty, and TestOutageKeepsHistory pins that an empty result
        # keeps the cached games rather than replacing them.

    def test_the_budget_stops_requests_not_just_player_starts(self):
        """Where the first version of this went wrong.

        asyncio.gather turns every player into a task and the event loop
        runs all their synchronous prologues in its first pass, so a
        check at the top of process_one_player is read by all 1,274
        before a second has elapsed. The budget has to bite where time
        is actually spent, which is at the request.
        """
        import asyncio

        async def call():
            sc._semaphore = asyncio.Semaphore(1)
            return await sc.bo3_get(object(), "/anything")

        sc._DEADLINE["at"] = time.monotonic() - 1
        before = sc.REQUEST_TOTAL["n"]
        got = asyncio.run(call())
        assert got is None, "a declined request returns the shape callers handle"
        assert sc.REQUEST_TOTAL["n"] == before, "and costs nothing"
        assert sc.BUDGET_SKIPS["n"] > 0, "and says so"

    def test_a_request_inside_the_budget_is_not_declined(self):
        """The guard must not be a permanent off switch."""
        import asyncio

        async def call():
            sc._semaphore = asyncio.Semaphore(1)
            # retries=1 so the backoff sleep does not pad the suite; a
            # bare object() fails on .get either way.
            return await sc.bo3_get(object(), "/anything", retries=1)

        sc.start_budget()
        try:
            asyncio.run(call())
        except Exception:
            pass  # a bare object() has no .get -- reaching it is the point
        assert sc.BUDGET_SKIPS["n"] == 0

    def test_an_out_of_budget_unknown_player_is_simply_absent(self):
        import asyncio
        sc._DEADLINE["at"] = time.monotonic() - 1
        progress = {"done": 0}
        name, rec = asyncio.run(sc.process_one_player(None, "nobody", progress, 1, {}))
        assert rec is None
        assert progress["skipped"] == 1


class TestNoOtherFunctionCanRepeatThisBug:
    """The bug was a NameError that only fired on the real, network-bound
    path -- invisible to every test and to a successful exit code. A free
    name that is neither global nor builtin is that bug, statically."""

    def test_no_function_in_the_scrapers_reads_an_undefined_name(self):
        scripts = Path(__file__).resolve().parent.parent / "scripts"
        offenders = []
        for path in sorted(scripts.glob("scrape*.py")):
            tree = ast.parse(path.read_text())
            # MODULE scope only: names bound at the top level, not every
            # local in the file. Counting function locals as module names
            # is exactly what would let `data` look defined -- it is a
            # local of main(), and that is the whole bug.
            module_names = set(dir(builtins)) | _names_bound_in(tree.body)
            # Top-level functions only. A nested def closing over its
            # parent's locals is ordinary and correct; walking each one
            # separately would report every closure as undefined.
            for fn in tree.body:
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                # Every arg anywhere inside, so a lambda's or a nested
                # def's parameters count as bound too.
                bound = {a.arg for a in ast.walk(fn) if isinstance(a, ast.arg)}
                bound |= _names_bound_in(list(ast.walk(fn)))
                for n in ast.walk(fn):
                    if (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                            and n.id not in bound and n.id not in module_names):
                        offenders.append(f"{path.name}:{n.lineno} {fn.name}() reads {n.id!r}")
        assert offenders == [], "\n".join(offenders)


def _immediately(value):
    """build_career_data is a coroutine; main() runs it with asyncio.run."""
    async def go():
        return value
    return go()


def _names_bound_in(nodes):
    """Names these statements bind, without descending into nested
    function or class bodies."""
    bound = set()
    for node in nodes:
        for sub in ast.walk(node) if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else [node]:
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                bound.add(sub.id)
            elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(sub.name)
            elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                bound |= {(a.asname or a.name).split(".")[0] for a in sub.names}
            elif isinstance(sub, ast.ExceptHandler) and sub.name:
                bound.add(sub.name)
            elif isinstance(sub, (ast.Global, ast.Nonlocal)):
                bound |= set(sub.names)
    return bound

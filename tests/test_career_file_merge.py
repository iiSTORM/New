"""Two overlapping scrapes must not let the later one know less.

Run 146 started from a 263-record cache, spent forty minutes and
committed 1,246 players with game history. Run 147 had already checked
out before that landed, so its base was the same stale 263; it reached
871 in its budget and its snapshot replaced the 1,246 wholesale.

  cs2_career_data.json   263 -> 1,246 (run 146)  ->  871 (run 147)

Nothing was broken. The later finisher simply knew less, and the commit
step's "whoever pushes last wins with their snapshot" rule handed it the
file anyway. That rule is right for cs2_data.json, which every run
regenerates in full, and wrong for a cache that ACCUMULATES.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import merge_career_files as mc


def rec(n_games, player_id=1):
    return {"player_id": player_id, "games_fetched": n_games,
            "games": [{"game_id": i, "k": 1, "d": 1, "a": 1,
                       "date": "2026-01-01T00:00:00+00:00"} for i in range(n_games)]}


class TestKeepingTheBetterRecord:
    def test_history_beats_no_history(self):
        got = mc.merge({"donk": rec(0)}, {"donk": rec(42)})
        assert len(got["donk"]["games"]) == 42, (
            "a run that got cut off must not erase one that did not")

    def test_the_longer_history_wins(self):
        """MATCHES_PER_PLAYER is a rolling window, so a short list means
        a run that ran out of budget, not a player who stopped."""
        assert len(mc.merge({"donk": rec(12)}, {"donk": rec(42)})["donk"]["games"]) == 42
        assert len(mc.merge({"donk": rec(42)}, {"donk": rec(12)})["donk"]["games"]) == 42

    def test_this_run_wins_a_tie(self):
        """Same depth means the fresher fetch, which may carry fields an
        older record predates."""
        mine = rec(2); mine["marker"] = "fresh"
        got = mc.merge({"donk": mine}, {"donk": rec(2)})
        assert got["donk"].get("marker") == "fresh"

    def test_it_is_a_union_not_an_overwrite(self):
        got = mc.merge({"a": rec(3)}, {"b": rec(3)})
        assert sorted(got) == ["a", "b"]

    def test_a_player_only_this_run_has_is_added(self):
        assert "new" in mc.merge({"new": rec(5)}, {"old": rec(5)})

    def test_a_player_only_the_committed_file_has_survives(self):
        """The 375 that went missing. They were not in this run's output
        with any history, and the file still holds them."""
        got = mc.merge({"a": rec(0)}, {"a": rec(40), "b": rec(40)})
        assert len(got["b"]["games"]) == 40

    def test_the_real_shape_of_the_incident(self):
        mine = {f"p{i}": rec(40 if i < 871 else 0) for i in range(1274)}
        base = {f"p{i}": rec(40 if i < 1246 else 0) for i in range(1274)}
        got = mc.merge(mine, base)
        assert sum(1 for v in got.values() if v["games"]) == 1246, (
            "the merge must never come out below what was already committed")


class TestItNeverFailsAScrape:
    def test_a_missing_base_is_empty(self, tmp_path):
        assert mc.load(str(tmp_path / "nope.json")) == {}

    def test_a_corrupt_base_is_empty(self, tmp_path):
        bad = tmp_path / "b.json"; bad.write_text("{ not json")
        assert mc.load(str(bad)) == {}

    def test_a_base_that_is_not_an_object_is_empty(self, tmp_path):
        odd = tmp_path / "b.json"; odd.write_text("[1, 2, 3]")
        assert mc.load(str(odd)) == {}

    def test_empty_inputs_are_not_an_error(self):
        assert mc.merge({}, {}) == {}
        assert mc.merge(None, None) == {}


class TestTheCommandLine:
    def test_it_writes_the_union(self, tmp_path):
        mine = tmp_path / "mine.json"; base = tmp_path / "base.json"
        out = tmp_path / "out.json"
        mine.write_text(json.dumps({"a": rec(0), "c": rec(5)}))
        base.write_text(json.dumps({"a": rec(40), "b": rec(40)}))
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "merge_career_files.py"),
             str(mine), str(base), str(out)],
            capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        got = json.loads(out.read_text())
        assert sorted(got) == ["a", "b", "c"]
        assert len(got["a"]["games"]) == 40
        assert "with games" in r.stdout, "the run has to say what it did"

    def test_wrong_arguments_exit_nonzero(self):
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "merge_career_files.py")],
            capture_output=True, text=True)
        assert r.returncode != 0


class TestTheWorkflowUsesIt:
    """A merge script nothing calls is a file, not a fix."""

    @staticmethod
    def workflow():
        return (ROOT / ".github/workflows/scrape.yml").read_text()

    @staticmethod
    def commit_step_script():
        """The `run:` body of the CS2 commit step, dedented."""
        import re
        wf = TestTheWorkflowUsesIt.workflow()
        start = wf.index("      - name: Commit cs2_data.json")
        body = wf[wf.index("run: |", start) + len("run: |"):]
        lines, out = body.split("\n"), []
        for line in lines[1:]:
            if line.strip() and not line.startswith("          "):
                break
            out.append(line[10:])
        return "\n".join(out)

    def test_the_cs2_commit_step_merges_before_staging(self, tmp_path):
        """Run it, rather than grep for the script's name.

        A grep passes against `if false; then python merge...` -- the
        name is still in the file and the merge never happens. That is
        the same shape as several bugs this repo has already paid for,
        so this executes the step's own shell with git stubbed out and
        checks what ends up staged.
        """
        import os
        import shutil
        import subprocess

        repo = tmp_path / "repo"
        shutil.copytree(ROOT / "scripts", repo / "scripts")
        (repo / "cs2_data.json").write_text("{}")
        # This run: knows about a player the committed file does not, and
        # has lost the history of one it does.
        (repo / "cs2_career_data.json").write_text(json.dumps(
            {"kept": rec(0), "new": rec(7)}))
        committed = json.dumps({"kept": rec(40), "only_committed": rec(40)})

        # A git that succeeds at everything and serves the committed file.
        bindir = tmp_path / "bin"
        bindir.mkdir()
        (bindir / "git").write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "show" ]; then cat "$GIT_STUB_BASE"; fi\n'
            "exit 0\n")
        (bindir / "git").chmod(0o755)

        env = dict(os.environ)
        env["PATH"] = f"{bindir}:{env['PATH']}"
        env["GIT_STUB_BASE"] = str(tmp_path / "committed.json")
        (tmp_path / "committed.json").write_text(committed)

        r = subprocess.run(["bash", "-c", self.commit_step_script()],
                           cwd=repo, env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

        got = json.loads((repo / "cs2_career_data.json").read_text())
        assert len(got["kept"]["games"]) == 40, (
            "the committed history must survive a run that lost it")
        assert "only_committed" in got, "and so must a player this run never saw"
        assert len(got["new"]["games"]) == 7, "while this run's new player is added"

    def test_runs_do_not_overlap(self):
        """The root cause. Two scrapes in flight at once means the later
        one checked out before the earlier one committed."""
        wf = self.workflow()
        assert "concurrency:" in wf
        assert "cancel-in-progress: false" in wf, (
            "a scrape that is part way through a 40-minute career fetch "
            "must be allowed to finish and commit, not cancelled")

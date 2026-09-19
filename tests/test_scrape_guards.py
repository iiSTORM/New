"""The scrapers' refusal-to-destroy guards.

These two scripts are network-bound end to end and are not otherwise unit
tested, but the guards themselves are pure logic and are exactly the code
that stops a source outage from destroying committed data — so they are
worth pinning down. Run 116 is the case they exist for: gol.gg was
unreachable, and the run replaced a 359KB career cache with `{}` while
reporting success.
"""
import json

import pytest

# These modules import requests/bs4 at module level.
pytest.importorskip("requests")
pytest.importorskip("bs4")

import scrape_career


class TestCareerCacheGuard:
    @staticmethod
    def _setup(tmp_path, monkeypatch, resolved, existing):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data.json").write_text(json.dumps({"regions": {}}))
        if existing is not None:
            (tmp_path / "career_data.json").write_text(json.dumps(existing))
        monkeypatch.setattr(scrape_career, "build_career_data", lambda: resolved)

    def test_refuses_to_overwrite_a_good_cache_with_nothing(self, tmp_path, monkeypatch):
        existing = {str(i): {} for i in range(320)}
        self._setup(tmp_path, monkeypatch, {}, existing)
        with pytest.raises(SystemExit) as exc:
            scrape_career.main()
        assert exc.value.code != 0
        kept = json.loads((tmp_path / "career_data.json").read_text())
        assert len(kept) == 320, "the existing cache must survive"

    def test_refuses_a_partial_collapse(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch,
                    {str(i): {} for i in range(10)},
                    {str(i): {} for i in range(320)})
        with pytest.raises(SystemExit):
            scrape_career.main()
        assert len(json.loads((tmp_path / "career_data.json").read_text())) == 320

    def test_writes_when_the_result_is_healthy(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch,
                    {str(i): {} for i in range(318)},
                    {str(i): {} for i in range(320)})
        scrape_career.main()
        assert len(json.loads((tmp_path / "career_data.json").read_text())) == 318

    def test_writes_on_a_first_ever_run(self, tmp_path, monkeypatch):
        """No cache yet is not a collapse."""
        self._setup(tmp_path, monkeypatch, {"1": {}}, None)
        scrape_career.main()
        assert len(json.loads((tmp_path / "career_data.json").read_text())) == 1

    def test_unreadable_cache_does_not_block_a_write(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, {"1": {}}, None)
        (tmp_path / "career_data.json").write_text("{ this is not json")
        scrape_career.main()
        assert len(json.loads((tmp_path / "career_data.json").read_text())) == 1

"""Tests for the LoL merge step.

The team-name mapping is hand-maintained knowledge about two sources that
spell the same team differently (gol.gg vs the LoL Esports API). A wrong
entry does not crash anything — it silently drops a fixture from the
upcoming list — so it is worth pinning down.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import merge


class TestNormalize:
    def test_maps_a_known_alias_to_the_golgg_spelling(self):
        assert merge.normalize("Cloud9 Kia") == "Cloud9"

    def test_is_case_insensitive(self):
        assert merge.normalize("CLOUD9 KIA") == "Cloud9"
        assert merge.normalize("cloud9 kia") == "Cloud9"

    def test_passes_through_a_name_it_does_not_know(self):
        assert merge.normalize("T1") == "T1"

    def test_every_mapping_target_is_stable(self):
        """Mapping to another key would mean a name normalizes in two hops
        depending on which spelling arrived first."""
        for alias, target in merge.TEAM_NAME_MAP.items():
            assert merge.normalize(target) == target, (
                f"{alias!r} maps to {target!r}, which is itself remapped"
            )


class TestBuildLookup:
    def test_resolves_api_casing_to_golgg_casing(self):
        lookup = merge.build_lookup({"Kiwoom DRX", "T1"})
        assert lookup["kiwoom drx"] == "Kiwoom DRX"

    def test_empty_roster_gives_empty_lookup(self):
        assert merge.build_lookup(set()) == {}


class TestLoadSchedule:
    """scrape_schedule.py is allowed to fail, so merge.py falls back to
    whatever schedule.json is on disk. It must not fall back to an old one:
    those fixtures have already been played."""

    @staticmethod
    def _write(tmp_path, payload):
        (tmp_path / "schedule.json").write_text(json.dumps(payload))

    def test_uses_a_fresh_schedule(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        now = datetime.now(timezone.utc).isoformat()
        self._write(tmp_path, {"generated_at": now, "regions": {"LCS": [{"teamA": "T1"}]}})
        assert merge.load_schedule()["regions"]["LCS"] == [{"teamA": "T1"}]

    def test_drops_a_schedule_past_the_age_limit(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        old = datetime.now(timezone.utc) - timedelta(days=merge.MAX_SCHEDULE_AGE_DAYS + 1)
        self._write(tmp_path, {"generated_at": old.isoformat(), "regions": {"LCS": [{"teamA": "T1"}]}})
        assert merge.load_schedule() == {"regions": {}}

    def test_keeps_a_schedule_just_inside_the_age_limit(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        recent = datetime.now(timezone.utc) - timedelta(days=merge.MAX_SCHEDULE_AGE_DAYS - 0.5)
        self._write(tmp_path, {"generated_at": recent.isoformat(), "regions": {"LCS": []}})
        assert merge.load_schedule()["regions"] == {"LCS": []}

    def test_uses_an_unstamped_file_rather_than_regressing(self, tmp_path, monkeypatch):
        """Files written before generated_at existed must keep working."""
        monkeypatch.chdir(tmp_path)
        self._write(tmp_path, {"regions": {"LCS": [{"teamA": "T1"}]}})
        assert merge.load_schedule()["regions"]["LCS"] == [{"teamA": "T1"}]

    def test_tolerates_an_unparseable_timestamp(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write(tmp_path, {"generated_at": "last tuesday", "regions": {"LCS": []}})
        assert merge.load_schedule()["regions"] == {"LCS": []}

    def test_missing_file_is_an_empty_schedule(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert merge.load_schedule() == {"regions": {}}

    def test_naive_timestamp_is_treated_as_utc(self, tmp_path, monkeypatch):
        """Not currently written, but must not raise if it ever is."""
        monkeypatch.chdir(tmp_path)
        naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        self._write(tmp_path, {"generated_at": naive, "regions": {"LCS": []}})
        assert merge.load_schedule()["regions"] == {"LCS": []}


class TestMergeEndToEnd:
    """Runs merge.py the way the workflow does — as a script, against files
    in the working directory — rather than poking at its internals."""

    @staticmethod
    def _fixture(tmp_path, schedule_age_days=0.0):
        data = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "regions": {
                "LCS": {
                    "teams": {"Cloud9": {"color": "#fff",
                                         "players": [{"name": "p1", "role": "TOP",
                                                      "cur": {}, "hist": {}}]}},
                    "past_matches": [],
                    "upcoming_matches": [],
                }
            },
        }
        (tmp_path / "data.json").write_text(json.dumps(data))
        stamp = datetime.now(timezone.utc) - timedelta(days=schedule_age_days)
        (tmp_path / "schedule.json").write_text(json.dumps({
            "generated_at": stamp.isoformat(),
            # "Cloud9 Kia" is the API spelling; it must resolve to gol.gg's "Cloud9".
            "regions": {"LCS": [{"teamA": "Cloud9 Kia", "teamB": "Cloud9 Kia",
                                 "date": "2026-09-20", "week": "W1"}]},
        }))
        (tmp_path / "career_data.json").write_text(json.dumps({}))

    def test_writes_minified_output(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._fixture(tmp_path)
        merge.main()
        raw = (tmp_path / "data.json").read_bytes()
        assert b"\n  " not in raw, "merge.py should write minified JSON"
        json.loads(raw)  # still valid

    def test_resolves_api_team_names_into_upcoming(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._fixture(tmp_path)
        merge.main()
        merged = json.loads((tmp_path / "data.json").read_text())
        assert len(merged["regions"]["LCS"]["upcoming_matches"]) == 1

    def test_stale_schedule_yields_no_upcoming(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._fixture(tmp_path, schedule_age_days=merge.MAX_SCHEDULE_AGE_DAYS + 1)
        merge.main()
        merged = json.loads((tmp_path / "data.json").read_text())
        assert merged["regions"]["LCS"]["upcoming_matches"] == []

"""Checks the committed data files still have the shape index.html reads.

These run against the files actually in the repo, so they fail if a scraper
starts emitting a different structure. Freshness is deliberately not checked
here — that is scripts/check_data.py's job inside the workflow, where the
data has just been scraped. Here the committed files are expected to be as
old as the last run.
"""
import json
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# The files the frontend fetches, and the per-player field each one adds on
# top of the shared core. The games genuinely differ here: LoL carries a
# blended career baseline, CS2 only a game count, Valorant neither.
SERVED_FILES = {
    "data.json": "career",
    "valorant_data.json": None,
    "cs2_data.json": "career_games",
}
CORE_PLAYER_FIELDS = {"name", "role", "cur", "hist"}


def load(name):
    path = REPO_ROOT / name
    if not path.exists():
        pytest.skip(f"{name} not present")
    with open(path) as f:
        return json.load(f)


@pytest.mark.parametrize("name", sorted(SERVED_FILES))
class TestServedFileContract:
    def test_top_level_shape(self, name):
        data = load(name)
        assert isinstance(data.get("generated_at"), str)
        datetime.fromisoformat(data["generated_at"])  # raises if malformed
        assert isinstance(data.get("regions"), dict) and data["regions"]

    def test_region_shape(self, name):
        for region_name, region in load(name)["regions"].items():
            assert isinstance(region.get("teams"), dict), region_name
            assert isinstance(region.get("past_matches"), list), region_name
            assert isinstance(region.get("upcoming_matches"), list), region_name

    def test_team_shape(self, name):
        for region_name, region in load(name)["regions"].items():
            for team_name, team in region["teams"].items():
                where = f"{name}:{region_name}:{team_name}"
                assert isinstance(team.get("color"), str), where
                assert isinstance(team.get("players"), list), where

    def test_player_shape(self, name):
        extra = SERVED_FILES[name]
        for region_name, region in load(name)["regions"].items():
            for team_name, team in region["teams"].items():
                for player in team["players"]:
                    where = f"{name}:{region_name}:{team_name}:{player.get('name')}"
                    missing = CORE_PLAYER_FIELDS - set(player)
                    assert not missing, f"{where} missing {missing}"
                    assert player["name"], f"{where} has a blank name"
                    assert isinstance(player["cur"], dict), where
                    if extra:
                        assert extra in player, f"{where} missing {extra}"

    def test_has_actual_content(self, name):
        """Guards against a file that is structurally valid but empty."""
        data = load(name)
        players = sum(
            len(team["players"])
            for region in data["regions"].values()
            for team in region["teams"].values()
        )
        assert players > 0, f"{name} has no players at all"


class TestAuxiliaryFiles:
    def test_champion_stats_shape(self):
        stats = load("champion_stats.json")
        assert set(stats) >= {"champions", "player_champions"}
        assert stats["champions"], "no champions aggregated"

    def test_schedule_shape(self):
        assert isinstance(load("schedule.json").get("regions"), dict)

"""Tests for the pre-commit data check.

This script's job is to fail, so the cases that matter are the ones where
it must reject data — particularly the partial collapse, which passes both
the freshness and structure checks and is only caught by comparing against
the previously committed copy.
"""
from datetime import datetime, timedelta, timezone

import check_data


def stamp(hours_ago=0.0):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def sample(players_per_team=5, teams=2, past=10, regions=("LCS",), when=0.0):
    return {
        "generated_at": stamp(when),
        "regions": {
            region: {
                "teams": {
                    f"Team{i}": {
                        "color": "#fff",
                        "players": [{"name": f"p{j}"} for j in range(players_per_team)],
                    }
                    for i in range(teams)
                },
                "past_matches": [{"teamA": "Team0"} for _ in range(past)],
                "upcoming_matches": [],
            }
            for region in regions
        },
    }


class TestCounts:
    def test_totals_across_regions(self):
        got = check_data.counts(sample(teams=3, players_per_team=5, past=7,
                                       regions=("LCS", "LEC")))
        assert got == {"regions": 2, "teams": 6, "players": 30,
                       "past_matches": 14, "upcoming_matches": 0}

    def test_handles_missing_and_null_sections(self):
        assert check_data.counts({}) == {
            "regions": 0, "teams": 0, "players": 0,
            "past_matches": 0, "upcoming_matches": 0,
        }
        assert check_data.counts({"regions": {"LCS": {"teams": None,
                                                     "past_matches": None}}})["teams"] == 0


class TestFreshness:
    def test_recent_data_passes(self):
        errors = []
        check_data.check_freshness(sample(when=0.5), 6.0, errors)
        assert errors == []

    def test_stale_data_fails(self):
        errors = []
        check_data.check_freshness(sample(when=13.0), 6.0, errors)
        assert len(errors) == 1 and "13.0h old" in errors[0]

    def test_missing_timestamp_fails(self):
        errors = []
        check_data.check_freshness({"regions": {}}, 6.0, errors)
        assert len(errors) == 1 and "generated_at" in errors[0]

    def test_unparseable_timestamp_fails(self):
        errors = []
        check_data.check_freshness({"generated_at": "yesterday"}, 6.0, errors)
        assert len(errors) == 1

    def test_naive_timestamp_is_treated_as_utc(self):
        errors = []
        naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        check_data.check_freshness({"generated_at": naive}, 6.0, errors)
        assert errors == []


class TestStructure:
    def test_healthy_data_passes(self):
        errors = []
        check_data.check_structure(sample(), errors)
        assert errors == []

    def test_no_regions_fails(self):
        errors = []
        check_data.check_structure({"regions": {}}, errors)
        assert len(errors) == 1

    def test_regions_present_but_all_empty_fails(self):
        """The shape looks right but the scrape matched nothing."""
        errors = []
        gutted = sample()
        for region in gutted["regions"].values():
            region["teams"] = {}
        check_data.check_structure(gutted, errors)
        assert len(errors) == 1 and "parsed nothing" in errors[0]

    def test_one_empty_region_is_only_a_warning(self):
        """An off-season region is normal and must not fail the run."""
        errors = []
        data = sample(regions=("LCS", "LEC"))
        data["regions"]["LEC"]["teams"] = {}
        check_data.check_structure(data, errors)
        assert errors == []


class TestRegression:
    def test_stable_counts_pass(self):
        errors = []
        counts = check_data.counts(sample())
        check_data.check_regression(counts, counts, 50.0, errors)
        assert errors == []

    def test_collapse_fails(self):
        errors = []
        before = check_data.counts(sample(teams=10, past=100))
        now = check_data.counts(sample(teams=1, past=5))
        check_data.check_regression(now, before, 50.0, errors)
        assert len(errors) == 2  # players and past_matches

    def test_partial_collapse_fails(self):
        """The case freshness and structure checks both pass: a parser that
        still returns one region's worth of data."""
        errors = []
        before = check_data.counts(sample(regions=("LCS", "LEC", "LCK", "LPL")))
        now = check_data.counts(sample(regions=("LCS",)))
        check_data.check_regression(now, before, 50.0, errors)
        assert errors and "players" in errors[0]

    def test_ordinary_churn_passes(self):
        """Rosters move between runs; that must not fail a run."""
        errors = []
        before = check_data.counts(sample(teams=10, past=100))
        now = check_data.counts(sample(teams=9, past=95))
        check_data.check_regression(now, before, 50.0, errors)
        assert errors == []

    def test_growth_passes(self):
        errors = []
        before = check_data.counts(sample(teams=5, past=50))
        now = check_data.counts(sample(teams=20, past=200))
        check_data.check_regression(now, before, 50.0, errors)
        assert errors == []

    def test_empty_baseline_is_not_a_division_error(self):
        errors = []
        before = check_data.counts({"regions": {}})
        check_data.check_regression(check_data.counts(sample()), before, 50.0, errors)
        assert errors == []


class TestAuxEntryCount:
    def test_counts_players_in_a_flat_map(self):
        assert check_data.aux_entry_count("career_data.json", {"1": {}, "2": {}}) == 2

    def test_champion_stats_counts_its_contents_not_its_two_keys(self):
        """Counting top-level keys would return 2 for an empty file."""
        stats = {"champions": {"Ryze": {}, "Ezreal": {}}, "player_champions": {"a": {}}}
        assert check_data.aux_entry_count("champion_stats.json", stats) == 3
        assert check_data.aux_entry_count("champion_stats.json",
                                          {"champions": {}, "player_champions": {}}) == 0

    def test_non_dict_is_zero(self):
        assert check_data.aux_entry_count("career_data.json", []) == 0


class TestAuxFileCollapse:
    """The failure that actually happened: gol.gg unreachable, scrape_career
    resolved 0 of 320 players, wrote {} over a good cache and exited 0."""

    @staticmethod
    def _run(tmp_path, monkeypatch, current, baseline, name="career_data.json"):
        import json as _json
        monkeypatch.chdir(tmp_path)
        (tmp_path / name).write_text(_json.dumps(current))
        monkeypatch.setattr(check_data, "load_baseline", lambda path, ref: baseline)
        monkeypatch.setattr(check_data, "AUX_FILES", {"lol": [name]})
        errors = []
        check_data.check_aux_files("lol", 50.0, "HEAD", errors)
        return errors

    def test_emptied_cache_fails(self, tmp_path, monkeypatch):
        errors = self._run(tmp_path, monkeypatch, {}, {str(i): {} for i in range(320)})
        assert len(errors) == 1 and "100%" in errors[0]

    def test_stable_cache_passes(self, tmp_path, monkeypatch):
        players = {str(i): {} for i in range(320)}
        assert self._run(tmp_path, monkeypatch, players, players) == []

    def test_ordinary_churn_passes(self, tmp_path, monkeypatch):
        """Players do leave rosters; that is not a collapse."""
        errors = self._run(tmp_path, monkeypatch,
                           {str(i): {} for i in range(300)},
                           {str(i): {} for i in range(320)})
        assert errors == []

    def test_growth_passes(self, tmp_path, monkeypatch):
        errors = self._run(tmp_path, monkeypatch,
                           {str(i): {} for i in range(400)},
                           {str(i): {} for i in range(320)})
        assert errors == []

    def test_missing_file_is_skipped_not_failed(self, tmp_path, monkeypatch):
        """These steps are continue-on-error and may produce nothing."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(check_data, "AUX_FILES", {"lol": ["career_data.json"]})
        errors = []
        check_data.check_aux_files("lol", 50.0, "HEAD", errors)
        assert errors == []

    def test_no_baseline_is_skipped(self, tmp_path, monkeypatch):
        errors = self._run(tmp_path, monkeypatch, {"1": {}}, None)
        assert errors == []


class TestCareerMerged:
    """That career data actually REACHED the players.

    career_data.json can be perfectly healthy, pass the auxiliary check,
    and still never reach a single player, because merge.py failed or was
    skipped. data.json then commits with career=None on all 320 players
    and the run goes green, since every other number in it is fine. The
    career tier is worth 2.5% of LoL's accuracy and 7-14% of CS2's, so
    that is a real degradation with no visible symptom.
    """

    def payload(self, with_career, total):
        players = [{"name": f"p{i}", "cur": {"g": 10, "k": 4, "d": 2, "a": 6},
                    **({"career": {"g": 50, "k": 4, "d": 2, "a": 6}} if i < with_career else {})}
                   for i in range(total)]
        return {"regions": {"LCS": {"teams": {"T": {"players": players}},
                                    "past_matches": [], "upcoming_matches": []}}}

    def test_full_coverage_passes(self):
        errors = []
        check_data.check_career_merged("lol", self.payload(320, 320), self.payload(320, 320), 20.0, errors)
        assert errors == []

    def test_a_collapse_is_caught(self):
        errors = []
        check_data.check_career_merged("lol", self.payload(0, 320), self.payload(320, 320), 20.0, errors)
        assert len(errors) == 1
        assert "merge" in errors[0].lower()

    def test_a_small_dip_is_tolerated(self):
        """Rosters move; a handful of new players with no career history yet
        is normal and must not fail a run."""
        errors = []
        check_data.check_career_merged("lol", self.payload(300, 320), self.payload(320, 320), 20.0, errors)
        assert errors == []

    def test_a_game_that_never_had_career_does_not_start_failing(self):
        errors = []
        check_data.check_career_merged("lol", self.payload(0, 320), self.payload(0, 320), 20.0, errors)
        assert errors == []

    def test_valorant_is_exempt_because_it_has_no_career_scraper(self):
        errors = []
        check_data.check_career_merged("valorant", self.payload(0, 320), self.payload(320, 320), 20.0, errors)
        assert errors == []

    def test_cs2_counts_its_own_career_shape(self):
        """CS2 players carry career_games, not career — a different field
        for the same tier, and counting only `career` would report 0% for
        a perfectly healthy CS2 file."""
        cs2 = {"regions": {"CS2": {"teams": {"T": {"players": [
            {"name": "p", "cur": {}, "career_games": [{"k": 20, "date": "2026-01-01"}]}]}},
            "past_matches": [], "upcoming_matches": []}}}
        assert check_data.career_coverage(cs2) == (1, 1)

    def test_no_baseline_is_not_an_error(self):
        errors = []
        check_data.check_career_merged("lol", self.payload(320, 320), None, 20.0, errors)
        assert errors == []

    def test_an_empty_payload_is_left_to_the_structure_check(self):
        errors = []
        check_data.check_career_merged("lol", {"regions": {}}, self.payload(320, 320), 20.0, errors)
        assert errors == []

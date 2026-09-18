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

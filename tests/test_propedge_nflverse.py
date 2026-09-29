"""Usage measured from nflverse weekly stats, rather than hand-set.

The prototype's shares carry a comment saying they are "2026 wks1-2 targets
blended with 2025 roles", which is honest and is also the single reason that
model cannot be pointed at another fixture. These tests cover the two mistakes
that made the first measured version produce numbers that were not football:
counting only the starter's dropbacks when a team used two quarterbacks (Chicago
came out at a 34% pass rate), and taking "who threw the most over three weeks"
as the answer to "who is starting on Sunday".

No network: every test builds its own rows.
"""
import csv
import io

import pytest

from propedge import nflverse
from propedge.nfl import OTHER

COLUMNS = ["player_display_name", "position", "team", "season", "week",
           "season_type", "completions", "attempts", "passing_yards", "carries",
           "rushing_yards", "receptions", "targets", "receiving_yards"]


def row(name, team="CHI", week=1, position="WR", **kw):
    made = {c: 0 for c in COLUMNS}
    made.update({"player_display_name": name, "team": team, "week": str(week),
                 "season": "2026", "season_type": "REG", "position": position})
    made.update({k: v for k, v in kw.items()})
    return made


def as_csv(rows):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS)
    writer.writeheader()
    for made in rows:
        writer.writerow(made)
    buffer.seek(0)
    return list(csv.DictReader(buffer))


def one_team():
    rows = []
    for week in (1, 2, 3):
        rows += [
            row("Starter", week=week, position="QB", attempts=30, completions=20,
                passing_yards=250, carries=3, rushing_yards=15),
            row("Backup", week=week, position="QB", attempts=10, completions=6,
                passing_yards=60),
            row("Receiver", week=week, targets=12, receptions=8, receiving_yards=100),
            row("Tight End", week=week, position="TE", targets=6, receptions=4,
                receiving_yards=40),
            row("Back", week=week, position="RB", carries=15, rushing_yards=70,
                targets=4, receptions=3, receiving_yards=20),
            row("Other Guy", week=week, targets=1, receptions=1, receiving_yards=5),
        ]
    return as_csv(rows)


# ------------------------------------------------------------------ shares

def test_shares_are_measured_not_assumed():
    measured = nflverse.usage(one_team(), "CHI")
    assert measured["games"] == 3
    total = 12 + 6 + 4 + 1
    assert measured["targets"]["Receiver"] == pytest.approx(12 / total)
    assert measured["rushers"]["Back"] == pytest.approx(15 / 18)


def test_the_pass_rate_counts_every_dropback_not_just_the_starters():
    """Chicago used two quarterbacks and came out at a 34% pass rate, which is
    not a football number."""
    measured = nflverse.usage(one_team(), "CHI")
    assert measured["pass_attempts_per_game"] == 40      # 30 + 10, not 30
    setup, _ = nflverse.team_setup(measured)
    assert 0.45 < setup.pass_rate < 0.8


def test_the_starter_can_be_overridden():
    """Measured usage answers who threw most over three weeks. The question is
    who is starting on Sunday, and in Chicago those were different people."""
    rows = one_team()
    assert nflverse.usage(rows, "CHI")["quarterback"] == "Starter"
    setup, _ = nflverse.team_setup(nflverse.usage(rows, "CHI", quarterback="Backup"),
                                  quarterback="Backup")
    assert setup.quarterback == "Backup"


def test_the_long_tail_becomes_other_and_the_shares_still_sum():
    setup, _ = nflverse.team_setup(nflverse.usage(one_team(), "CHI"), keep=2)
    assert OTHER in setup.targets
    assert sum(setup.targets.values()) == pytest.approx(1.0)
    assert len(setup.targets) == 3


def test_a_quarterback_is_not_listed_among_the_rushers():
    """He has his own carry model; counting him twice inflates the backfield."""
    setup, _ = nflverse.team_setup(nflverse.usage(one_team(), "CHI"))
    assert "Starter" not in setup.rushers
    assert setup.qb_rush_attempts == pytest.approx(3.0)


def test_rates_are_shrunk_toward_a_prior_when_the_sample_is_thin():
    """Two catches for 60 yards is not a 30-yard-a-catch receiver."""
    rows = as_csv([row("Deep", week=1, targets=2, receptions=2, receiving_yards=60),
                   row("Volume", week=1, targets=40, receptions=30,
                       receiving_yards=390)])
    rates = nflverse.usage(rows, "CHI")
    assert rates["ypr"]["Deep"] < 25          # measured 30, pulled toward 10.5
    # 30 receptions is not a large sample either -- the standard error on yards
    # per reception there is still about 1.8 yards -- so a measured 13.0 lands
    # at 12.3 rather than being taken at face value. Volume is trusted MORE
    # than Deep, which is the property that matters.
    assert 12.0 < rates["ypr"]["Volume"] < 13.0
    assert rates["ypr"]["Volume"] > rates["ypr"]["Deep"] - 12


def test_a_catch_rate_is_shrunk_too():
    rows = as_csv([row("Lucky", week=1, targets=2, receptions=2, receiving_yards=20)])
    assert nflverse.usage(rows, "CHI")["catch_rate"]["Lucky"] < 0.95


def test_weeks_can_be_narrowed():
    assert nflverse.usage(one_team(), "CHI", weeks=[3])["games"] == 1


def test_a_team_with_no_rows_is_none():
    assert nflverse.usage(one_team(), "SEA") is None
    assert nflverse.team_setup(None) == (None, {})


def test_postseason_rows_are_left_out_by_default():
    rows = one_team() + as_csv([row("Receiver", week=4, targets=99, receptions=99,
                                    receiving_yards=999)])
    rows[-1]["season_type"] = "POST"
    assert nflverse.usage(rows, "CHI")["games"] == 3


def test_a_setup_built_this_way_actually_simulates():
    """The point of all of it: a TeamSetup the simulator will accept."""
    from propedge import nfl
    home, _ = nflverse.team_setup(nflverse.usage(one_team(), "CHI"))
    away, _ = nflverse.team_setup(nflverse.usage(one_team(), "CHI"))
    away.code = "OPP"
    out = nfl.simulate(nfl.GameSetup(home=home, away=away), runs=200, seed=2)
    assert out["Receiver"][nfl.REC_YARDS]
    assert len(out["Receiver"][nfl.REC_YARDS]) == 200


def test_reading_from_a_file_needs_no_network(tmp_path):
    path = tmp_path / "weekly.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerow(row("Receiver", targets=10, receptions=7,
                            receiving_yards=95))
    assert len(nflverse.fetch_weekly(2026, path=str(path))) == 1

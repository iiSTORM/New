"""Shrinkage pulls toward the player's POSITION when the roster names one.

Shrinking a Dota carry and a hard support toward the same league-average
player flattened them (position 1 out-killed its projection by 14%, position
5 fell 17% short). The prior is now the position's pooled share of team
output times the league's pace, and falls back to the league's average
player when there is no position, which leaves League, CS2 and Valorant
exactly as they were.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "dev"))
import pytest  # noqa: E402

pytest.importorskip("requests")
import optimize_weights as ow  # noqa: E402


def world(roles=True):
    # Two teams, two maps each, a carry with 10 kills and four others with 2:
    # the carry holds 10 of every 18 kills.
    def side(team):
        return {f"{team}{i}": {"k": 10 if i == 1 else 2, "d": 3, "a": 5} for i in range(1, 6)}
    matches = [{"date": f"2026-10-0{d}", "teamA": "A", "teamB": "B", "games": 1,
                "actual": {"A": side("A"), "B": side("B")}} for d in (1, 2)]
    teams = {t: {"players": [{"name": f"{t}{i}", "role": (f"POS{i}" if roles else None)}
                             for i in range(1, 6)]} for t in ("A", "B")}
    return matches, teams


def test_the_prior_is_the_positions_share_of_team_output():
    matches, teams = world()
    ow.reset_caches() if hasattr(ow, "reset_caches") else None
    assert ow.position_share(matches, teams, "POS1", "k", "2026-10-09") == pytest.approx(10 / 18)
    assert ow.position_share(matches, teams, "POS5", "k", "2026-10-09") == pytest.approx(2 / 18)
    assert ow.position_share(matches, teams, None, "k", "2026-10-09") is None


def test_a_support_with_no_history_is_shrunk_toward_supports_not_the_league():
    matches, teams = world()
    pace = ow.league_pace_per_map(matches, teams, "k", "2026-10-09")          # 18 a map
    support = ow.shrink_to_prior(9.0, 4, 0, matches, teams, "k", "2026-10-09", "POS5")
    league = ow.shrink_to_prior(9.0, 4, 0, matches, teams, "k", "2026-10-09")
    assert support == pytest.approx(pace * 2 / 18) and league == pytest.approx(pace / 5)


def test_without_positions_nothing_changes():
    matches, teams = world(roles=False)
    assert ow.shrink_to_prior(9.0, 4, 2, matches, teams, "k", "2026-10-09", None) == \
        ow.shrink_to_prior(9.0, 4, 2, matches, teams, "k", "2026-10-09")

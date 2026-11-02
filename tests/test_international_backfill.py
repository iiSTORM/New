"""The past-events backfill picks finished events only, and the factor report reads them."""
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "dev"))

pytest.importorskip("bs4")
import backfill_lol_events as bf  # noqa: E402
import international_factors as itf  # noqa: E402


def test_only_finished_international_events_are_backfilled():
    rows = [{"trname": "Worlds 2025 Main Event", "lastgame": "2025-11-09"},
            {"trname": "MSI 2026", "lastgame": "2026-07-12"},
            {"trname": "Worlds 2026", "lastgame": "2026-11-01"},      # still on
            {"trname": "LCK 2026 Rounds 3-4", "lastgame": "2026-09-01"},
            {"trname": "First Stand 2026", "lastgame": "not a date"}]
    assert bf.finished_events(rows, date(2026, 11, 2)) == [
        ("MSI", "MSI 2026"), ("Worlds", "Worlds 2025 Main Event")]


def test_the_report_splits_pace_by_stage_and_the_gap_by_class():
    def game(a, ka, b, kb):
        return {a: {"p1": {"k": ka}}, b: {"p2": {"k": kb}}}
    events = {"MSI 2026": {"past_matches": [
        {"week": "Bracket Stage", "per_game": [game("Gen.G", 20, "FlyQuest", 10)]},
        {"week": "Round 1", "per_game": [game("T1", 15, "Gen.G", 15),
                                         game("FlyQuest", 6, "Gen.G", 18)]}]}}
    home = {"geng": "LCK", "t1": "LCK", "flyquest": "LCS"}
    (row,) = itf.summarise(events, home)
    assert row["knockout"] == (30, 1) and row["swiss/groups"] == (27, 2)
    assert row["knockout_vs_groups"] == pytest.approx(30 / 27)
    assert row["gap"] == (pytest.approx(38 / 16), 2)

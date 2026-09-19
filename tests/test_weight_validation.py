"""Walk-forward validation helpers in the weight optimizer.

The optimizer used to choose weights by minimising error over the same
rows it then reported, which on this project's data reliably improved the
reported number while making held-out error worse. These cover the
machinery that replaced it.
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("requests")  # optimize_weights imports scrape_career
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts" / "dev"))

import optimize_weights as ow


class TestFoldBoundaries:
    def test_splits_the_tail_into_contiguous_folds(self):
        dates = [f"2026-01-{d:02d}" for d in range(1, 21)]
        folds = ow.fold_boundaries(dates, 4, start_frac=0.5)
        assert len(folds) == 4
        # contiguous: each fold starts where the previous ended
        for (_, end), (nxt, _) in zip(folds, folds[1:]):
            assert end == nxt
        assert folds[-1][1] is None, "the last fold must run to the end"

    def test_earlier_history_is_excluded_from_scoring(self):
        dates = [f"2026-01-{d:02d}" for d in range(1, 21)]
        folds = ow.fold_boundaries(dates, 2, start_frac=0.5)
        assert folds[0][0] >= "2026-01-11"

    def test_empty_input_is_not_an_error(self):
        assert ow.fold_boundaries([], 4) == []


class TestWindowRows:
    ROWS = [("R", "2026-01-01", 1, 2), ("R", "2026-02-01", 1, 2), ("R", "2026-03-01", 1, 2)]

    def test_lower_bound_is_inclusive_upper_exclusive(self):
        got = ow.window_rows(self.ROWS, "2026-01-01", "2026-03-01")
        assert [r[1] for r in got] == ["2026-01-01", "2026-02-01"]

    def test_open_ended_window_runs_to_the_end(self):
        got = ow.window_rows(self.ROWS, "2026-02-01", None)
        assert [r[1] for r in got] == ["2026-02-01", "2026-03-01"]

    def test_windows_partition_without_overlap(self):
        folds = [("2026-01-01", "2026-02-01"), ("2026-02-01", None)]
        seen = [r for lo, hi in folds for r in ow.window_rows(self.ROWS, lo, hi)]
        assert len(seen) == len(self.ROWS)


class TestMaeDated:
    def test_mean_absolute_error(self):
        rows = [("R", "d", 5.0, 7.0), ("R", "d", 4.0, 3.0)]
        assert ow.mae_dated(rows) == pytest.approx(1.5)

    def test_no_rows_is_none_not_zero(self):
        """Zero would read as a perfect score for an empty fold."""
        assert ow.mae_dated([]) is None


class TestCompareOutOfSample:
    """The comparison must count folds won, not just average — an average
    can be carried by one fold while the change is worse in most."""

    @staticmethod
    def _stub(monkeypatch, baseline, candidate):
        def fake(region_data, stat_type, weights, folds):
            return candidate if weights.get("_cand") else baseline
        monkeypatch.setattr(ow, "per_fold_mae", fake)

    def test_consistent_improvement(self, monkeypatch):
        self._stub(monkeypatch, [2.0, 2.0, 2.0], [1.8, 1.9, 1.9])
        change, wins, n = ow.compare_out_of_sample({}, "kills", {}, {"_cand": 1}, [1, 2, 3])
        assert change < 0 and wins == 3 and n == 3

    def test_one_fold_carrying_the_average_is_visible(self, monkeypatch):
        self._stub(monkeypatch, [2.0, 2.0, 2.0], [1.0, 2.1, 2.1])
        change, wins, n = ow.compare_out_of_sample({}, "kills", {}, {"_cand": 1}, [1, 2, 3])
        assert change < 0, "average improves"
        assert wins == 1, "but only one fold actually improved"

    def test_empty_folds_do_not_crash(self, monkeypatch):
        self._stub(monkeypatch, [None, None], [None, None])
        change, wins, n = ow.compare_out_of_sample({}, "kills", {}, {"_cand": 1}, [1, 2])
        assert change is None and n == 0

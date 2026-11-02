"""The probability-quality harness: its distribution, its fit and its verdict."""
import math
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "dev"))
sys.path.insert(0, str(ROOT / "scripts"))

import probability_quality as pq  # noqa: E402


def test_negative_binomial_matches_its_closed_form_and_falls_back_to_poisson():
    # mean 4, variance 8 -> r = 4, p = 0.5: P(X = 0) = 0.5^4
    assert pq._nb_cdf(0, 4.0, 8.0) == pytest.approx(0.0625)
    assert pq._nb_cdf(1, 4.0, 8.0) == pytest.approx(0.0625 + 4 * 0.5 ** 5)
    poisson = sum(math.exp(-3) * 3 ** k / math.factorial(k) for k in range(3))
    assert pq._nb_cdf(2, 3.0, 2.0) == pytest.approx(poisson)


def test_count_probabilities_sum_to_one_and_push_only_on_whole_lines():
    over, push, under = pq.count_probabilities(20.0, 6.0, 19.5)
    assert push == 0 and over + under == pytest.approx(1)
    over, push, under = pq.count_probabilities(20.0, 6.0, 20.0)
    assert push > 0 and over + push + under == pytest.approx(1)
    assert pq.count_probabilities(20.0, 0, 19.5) is None


def test_logistic_fit_recovers_a_known_slope():
    rng = random.Random(3)
    xs = [[rng.uniform(-3, 3)] for _ in range(4000)]
    ys = [1 if rng.random() < pq.sigmoid(0.6 * x[0]) else 0 for x in xs]
    assert pq.fit_logistic(xs, ys, prior=[1.0])[0] == pytest.approx(0.6, abs=0.08)


def test_only_a_gain_whose_interval_clears_zero_ships():
    results = {"normal": {},
               "normal+slope": {"gain": {"mean": 0.0004, "low": -0.003, "high": 0.003}},
               "count": {"gain": {"mean": 0.004, "low": 0.001, "high": 0.007}}}
    assert pq.ships(results) == ["count"]


def test_walk_forward_never_fits_on_a_date_it_scores():
    rows = [{"when": f"2026-10-{d:02d}", "team": "a", "opponent": str(i), "game": "cs2",
             "under": (d + i) % 2, "z_normal": 0.1, "z_count": 0.1, "stat": "kills",
             "maps": 2, "odds_type": "standard"} for d in range(1, 29) for i in range(3)]
    seen = []
    original = pq.predict

    def spy(train, test, variant):
        seen.append((max((r["when"] for r in train), default=""), min(r["when"] for r in test)))
        return original(train, test, variant)
    pq.predict = spy
    try:
        pq.walk_forward(rows, "normal+slope")
    finally:
        pq.predict = original
    assert seen and all(last < first for last, first in seen)

"""The model has to keep using the signal that exists.

MAE cannot see this defect, and MAE is the only thing the weight search has
ever measured. Pulling a projection toward the league mean always lowers
absolute error when the signal is noisy -- that is what shrinkage is for -- so
a search that minimises MAE will flatten the model until it barely
distinguishes players and report an improvement the whole way. It did: CS2
kills shipped with shrink 8 against a median sample of 6 prior series, so 57%
of every projection was the league average and sd(projection) was 0.47 of the
spread that demonstrably exists between players.

That is fatal for prop selection specifically. Ranking props is ENTIRELY a
question of between-player spread: with none, the projection is a constant, the
"edge" is just the line's own deviation from average, and ranking by edge ranks
the market's information rather than ours. On 616 graded CS2 kills lines across
71 matches the market split 52/48 over and the model projected over on 37%.

So the spread is a shipped property with a floor, checked the way the weights
themselves are. The floor binds where the provider has actually posted lines,
because that is where between-player spread is the product; a stat with no
market is reported and not enforced, since for a number that is only ever read
off a card MAE is the right objective.
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("requests")  # the optimizer chain imports it

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "dev"))

import optimize_weights as ow
import spread_check as sc

GAMES = ("cs2", "valorant", "lol")


@pytest.fixture(scope="module")
def measured():
    """{(game, stat): measurement} for every shipped stat, computed once."""
    out = {}
    for game in GAMES:
        data = ow.load_region_data(sc.SOURCES[game])
        if not data:
            continue
        for stat in ow.STAT_TYPES:
            if not ow.stat_applies_to(stat, game):
                continue
            got = sc.measure(game, stat, data=data)
            if got:
                out[(game, stat)] = got
    return out


@pytest.fixture(scope="module")
def with_a_market():
    """The (game, stat) pairs the provider has actually posted lines on."""
    return {(game, stat) for game in GAMES for stat in ow.STAT_TYPES
            if ow.stat_applies_to(stat, game)
            and sc.pick_balance(game, stat) is not None}


class TestTheSpreadIsMeasurable:
    def test_something_was_measured_at_all(self, measured):
        assert measured, "no game/stat produced a spread measurement"

    def test_the_denominator_is_the_between_player_spread(self, measured):
        """Not sd(actual), which carries game-to-game noise no model should
        reproduce. A model matching sd(actual) would be overfitting to it."""
        for (game, stat), got in measured.items():
            assert got["sd_between"] < got["sd_actual"], (
                f"{game}/{stat}: between-player spread {got['sd_between']:.3f} is not "
                f"below the raw outcome spread {got['sd_actual']:.3f} — the "
                f"denominator has stopped meaning what it should")


class TestStatsWithAMarketKeepTheirSpread:
    def test_every_posted_stat_is_above_the_floor(self, measured, with_a_market):
        assert with_a_market, "no stat has a posted market — has props_results.json gone?"
        flat = {f"{game}/{stat}": round(measured[(game, stat)]["ratio"], 3)
                for (game, stat) in with_a_market
                if (game, stat) in measured
                and measured[(game, stat)]["ratio"] < sc.MIN_SPREAD_RATIO}
        assert not flat, (
            f"below the {sc.MIN_SPREAD_RATIO} spread floor: {flat}. The model has "
            f"given up more than that fraction of the between-player signal, which "
            f"makes its ranking of props the market's information rather than ours. "
            f"Run scripts/dev/spread_check.py and look at shrink first.")

    def test_cs2_kills_specifically(self, measured):
        """The stat this was found on, and 47% of the market. Named rather than
        left to the sweep above, because it is the one that regressed."""
        got = measured.get(("cs2", "kills"))
        if got is None:
            pytest.skip("no cs2 data")
        assert got["ratio"] >= 0.55, (
            f"cs2/kills spread ratio is {got['ratio']:.2f}; it was 0.47 with shrink 8 "
            f"and 0.61 with shrink 4")


class TestTheGamesWithNoShrinkAreTheCalibratedOnes:
    def test_a_stat_shipping_no_shrink_tracks_the_real_spread(self, measured):
        """The signature that identified the mechanism: every stat shipping
        shrink 0 lands at 0.91-1.01, and shrink is the only weight that
        differs. If one of these drifts, the diagnosis has changed."""
        checked = 0
        for (game, stat), got in measured.items():
            if got["shrink"]:
                continue
            checked += 1
            assert got["ratio"] >= 0.85, (
                f"{game}/{stat} ships shrink 0 and its ratio is {got['ratio']:.2f}, "
                f"not near 1.0 — something other than shrink is now flattening it")
        assert checked >= 3, f"only {checked} stat(s) ship shrink 0"

    def test_shrink_is_not_set_above_the_sample_it_shrinks(self, measured):
        """shrink is k in priorGames/(priorGames+k), so k above the typical
        sample makes the league average the MAJORITY of the projection. That
        is how 8 got shipped against a median of 6 prior series."""
        for (game, stat), got in measured.items():
            k = got["shrink"] or 0
            if not k:
                continue
            assert got["ratio"] >= 0.45, (
                f"{game}/{stat} shrink {k} leaves ratio {got['ratio']:.2f} — the "
                f"league average is more than half of every projection")


class TestTheFloorIsNotVacuous:
    def test_the_floor_would_have_caught_the_defect_it_was_written_for(self):
        """shrink 8 on cs2/kills has to FAIL. A floor the old value passes is
        a floor that would not have found this."""
        got = sc.measure("cs2", "kills", {"shrink": 8.0})
        if got is None:
            pytest.skip("no cs2 data")
        assert got["ratio"] < sc.MIN_SPREAD_RATIO, (
            f"shrink 8 measures {got['ratio']:.2f}, which passes the "
            f"{sc.MIN_SPREAD_RATIO} floor — the floor has stopped binding")

    def test_the_floor_leaves_room_for_legitimate_shrinkage(self):
        """Not 1.0: pulling a genuinely thin sample toward a prior is correct,
        and a floor that forbade it would force shrink 0 everywhere."""
        assert 0.4 < sc.MIN_SPREAD_RATIO < 0.9

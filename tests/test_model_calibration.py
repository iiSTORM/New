"""Is the model systematically HIGH or LOW, and does the KP layer cut both ways?

Every weight in this repo was chosen by minimising MAE, and MAE cannot
see a constant offset: a model 6% low on every single row and a model 6%
off in random directions score the same. That blind spot hid a real bug
for as long as it existed — kpMultiplier measured every player against a
hardcoded 66, which is League of Legends' kill-participation scale. CS2
and Valorant run at roughly 27, and their best players top out near 40,
so in those two games `relative` could never reach 1 and the "multiplier"
was a one-sided haircut on every projection. It showed up only when the
projections were put next to posted lines and recommended the under on
literally every player.

So these assert the two properties MAE was never going to check:

  1. the KP layer is two-sided in every game — some real player must
     score above 1.0 and some below, or it is an offset wearing a
     multiplier's clothes;
  2. the shipped model's predictions sum to roughly the actuals on the
     point-in-time backtest, per game and per stat.

Both run against the real committed data, because the bug was a mismatch
between a constant and the data's actual scale — a synthetic fixture
would have been written to whatever scale the test author had in mind and
would have missed it exactly the way the unit tests already here did.
"""
import json
import statistics
import sys
from pathlib import Path

import pytest

pytest.importorskip("requests")  # optimize_weights imports scrape_career

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "dev"))

import diagnose_calibration as dc  # noqa: E402
import optimize_weights as ow  # noqa: E402

GAMES = tuple(dc.GAMES)
# Derived rather than listed, so a new stat is covered by these checks the
# day it ships instead of quietly not being. Headshots was added to
# STAT_TYPES and every calibration assertion here simply ignored it.
STATS = tuple(ow.STAT_TYPES)


@pytest.fixture(scope="module")
def shipped():
    return dc.shipped_weights()


def _players(game):
    data = json.loads((REPO_ROOT / dc.GAMES[game]).read_text(encoding="utf-8"))
    return [p for region in data["regions"].values()
            for team in (region.get("teams") or {}).values()
            for p in team.get("players") or []]


@pytest.mark.parametrize("game", GAMES)
def test_the_kp_baseline_comes_from_the_data_not_a_constant(game):
    """The baseline has to come from the same population it divides.

    Two assertions, because one alone is not enough. The first is that
    the derived baseline lands inside the game's observed range. The
    second is the one that actually names the bug: for CS2 and Valorant
    the old literal 66 is nowhere near that range, so anyone who pins a
    constant back in — for any of the perfectly reasonable-sounding
    reasons a constant gets pinned in — trips this rather than shipping
    another silent one-sided layer. LoL is exempt from the second check
    by measurement, not by exception: 66 really is its scale, which is
    exactly why the bug survived so long.
    """
    kps = [p["cur"]["kp"] for p in _players(game)
           if isinstance(p.get("cur", {}).get("kp"), (int, float)) and p["cur"]["kp"] > 0]
    assert kps, f"{game} has no kill-participation data at all"
    teams = {"all": {"players": [{"cur": {"kp": v}} for v in kps]}}
    baseline = ow.league_avg_kp(teams)
    assert min(kps) <= baseline <= max(kps), (
        f"{game}: baseline {baseline:.1f} sits outside the observed range "
        f"{min(kps):.1f}-{max(kps):.1f}")
    fallback_fits = min(kps) <= ow.LEAGUE_AVG_KP_FALLBACK <= max(kps)
    if game == "lol":
        assert fallback_fits, "the fallback should still suit the game it was written for"
    else:
        assert not fallback_fits, (
            f"{game}: kp runs {min(kps):.1f}-{max(kps):.1f}, so the "
            f"{ow.LEAGUE_AVG_KP_FALLBACK} fallback cannot be used as a baseline here")


@pytest.mark.parametrize("game", GAMES)
def test_the_kp_multiplier_can_go_both_ways(game):
    """A multiplier that never exceeds 1 is a haircut, not an adjustment.

    Asserted on real rosters and at a deliberately nonzero kp strength,
    since at kp=0 the layer is inert and every player scores exactly 1.
    """
    players = [p for p in _players(game) if p.get("cur", {}).get("kp")]
    teams = {"all": {"players": players}}
    mults = [ow.kp_multiplier(p, 0.3, 0.3, teams) for p in players]
    assert max(mults) > 1.0, (
        f"{game}: no player scores above 1.0 (max {max(mults):.4f}) — the kp "
        f"baseline is on the wrong scale for this game")
    assert min(mults) < 1.0, f"{game}: no player scores below 1.0"


@pytest.mark.parametrize("game", GAMES)
@pytest.mark.parametrize("stat", STATS)
def test_the_shipped_model_is_calibrated(game, stat, shipped):
    """Predictions should total roughly what actually happened.

    The tolerance is deliberately loose. This is not a demand that the
    model be good — MAE is where accuracy is measured, and a 6-kill MAE
    on CS2 lives happily inside this band. It is a tripwire for the one
    failure MAE is structurally incapable of reporting: a whole game
    silently shifted off its zero. At the width below, the kp bug
    (0.939x on CS2 kills) fails and nothing else currently does.
    """
    if not ow.stat_applies_to(stat, game):
        pytest.skip(f"{game} does not record {stat}")
    weights = dict(shipped[game][stat])
    weights.setdefault("careerRamp", 0)
    rows = dc.rows_for(game, stat, weights)
    assert len(rows) > 100, f"{game}/{stat}: only {len(rows)} backtest rows"
    predicted = statistics.fmean(p for _, p, _ in rows)
    actual = statistics.fmean(a for _, _, a in rows)
    ratio = predicted / actual
    assert 0.95 <= ratio <= 1.05, (
        f"{game}/{stat}: predicts {ratio:.3f}x the actual "
        f"({predicted:.2f} vs {actual:.2f} over {len(rows)} rows) — the model "
        f"is systematically {'low' if ratio < 1 else 'high'}, which becomes a "
        f"standing {'under' if ratio < 1 else 'over'} recommendation on every line")


def test_the_two_ports_agree_on_which_games_record_which_stats():
    """STAT_TYPES exists twice, and the `games` list is the part that
    silently does nothing when it drifts.

    If JS says headshots is CS2-only and Python does not, the backtest
    quietly scores a stat the app never offers -- or worse, the app offers
    a tab the measurements never covered. Parsed out of src/app.jsx rather
    than mirrored here, for the same reason the weights are.
    """
    src = (REPO_ROOT / "src" / "app.jsx").read_text(encoding="utf-8")
    block = src[src.index("const STAT_TYPES = {"):]
    block = block[:block.index("\n};")]
    for stat, cfg in ow.STAT_TYPES.items():
        assert f"{stat}:" in block, f"{stat} is modelled in Python but absent from src/app.jsx"
        # Find this stat's line(s) in the JS table.
        line = block[block.index(f"{stat}:"):]
        line = line[:line.index("},") + 1]
        js_games = "games:" in line
        py_games = cfg.get("games") is not None
        assert js_games == py_games, (
            f"{stat}: src/app.jsx {'declares' if js_games else 'does not declare'} a games "
            f"list, the Python port {'does' if py_games else 'does not'}")
        if py_games:
            for game in cfg["games"]:
                assert f'"{game}"' in line, f"{stat}: {game} missing from src/app.jsx's games list"


def test_the_two_copies_of_the_shipped_weights_agree(shipped, capsys):
    """optimize_weights.SHIPPED_WEIGHTS is hand-mirrored from src/app.jsx.

    Nothing enforces that by construction, so when they drift every
    measurement made with the Python port describes a model nobody runs.
    """
    for game, stats in ow.SHIPPED_WEIGHTS.items():
        for stat, mirrored in stats.items():
            live = shipped[game][stat]
            assert all(live.get(k) == v for k, v in mirrored.items()), (
                f"{game}/{stat}: optimize_weights.py says {mirrored}, "
                f"src/app.jsx says {live}")

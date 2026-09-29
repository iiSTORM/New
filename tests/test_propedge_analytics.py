"""Calibration, ROI and closing-line value -- and the rule that takes Kelly away.

The load-bearing test here is the sizing policy. Kelly is a function of the
probabilities fed to it: staking half Kelly on a number that claims 65% and
realises 52% is not half Kelly, it is roughly double it, and it compounds. So
when calibration is off by more than five points over enough legs, the stake
stops being Kelly and becomes a flat fraction. That has to actually change the
stake, not just print a banner, and the test asserts the stake.

Amounts and slips here are invented -- this repo is public.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from propedge import analytics
from propedge.money import to_cents
from propedge.sizing import FLAT_UNIT, outcome_table, recommend
from propedge.slips import Leg, Slip
from propedge.store import Tracker

NOW = datetime(2026, 9, 28, 19, 0, tzinfo=timezone.utc)


def ts(hours):
    return (NOW + timedelta(hours=hours)).isoformat()


def leg(player="P", team="Alpha", prob=None, result="won", sport="cs2",
        stat="kills", line=20.5, side="over", opponent="Beta", maps=2, **kw):
    # maps defaults to 2 because the board history keys on the window: a leg
    # with maps=None never matches a maps-2 posting, which is correct behaviour
    # and made the first draft of these CLV tests silently measure nothing.
    return Leg(sport=sport, player=player, team=team, opponent=opponent, stat=stat,
               line=line, side=side, model_prob=prob, result=result, maps=maps, **kw)


def clv_slip(**kw):
    """A slip whose FIRST leg is the one under test and whose second is a filler
    on another team, there only to make the slip legal. Assertions name the
    first leg, and the filler's own (missing) history is expected."""
    first = leg(**kw)
    return slip(first, leg(player="FILLER", team="Beta"),
                placed_at=kw.get("placed_at") or ts(0))


def slip(*legs, stake="1", mode="power", multiplier="3", payout=None,
         placed_at=None, status=None):
    made = Slip(mode=mode, stake_cents=to_cents(stake), multiplier=multiplier,
                legs=list(legs), placed_at=placed_at or ts(0))
    if payout is not None:
        made.payout_cents = to_cents(payout)
        made.status = status or ("won" if to_cents(payout) else "lost")
    return made


# ------------------------------------------------------------------- wilson

def test_wilson_has_width_at_the_edges():
    """The naive interval is zero-width at 0 of 8, which reads as certainty."""
    lo, hi = analytics.wilson(0, 8)
    assert lo == 0.0 and 0.2 < hi < 0.45
    lo, hi = analytics.wilson(8, 8)
    assert hi == 1.0 and 0.55 < lo < 0.8


def test_wilson_narrows_with_evidence():
    thin = analytics.wilson(30, 50)
    thick = analytics.wilson(300, 500)
    assert (thick[1] - thick[0]) < (thin[1] - thin[0])


def test_wilson_on_nothing_is_the_whole_range():
    assert analytics.wilson(0, 0) == (0.0, 1.0)


# -------------------------------------------------------------- calibration

def test_only_decided_legs_with_a_probability_are_judged():
    mixed = slip(leg("A", prob=0.6, result="won"),
                 leg("B", prob=0.6, result="lost"),
                 leg("C", prob=0.6, result="push"),
                 leg("D", prob=0.6, result="dnp"),
                 leg("E", prob=0.6, result="unknown"),
                 leg("F", prob=0.6, result="pending"),
                 leg("G", prob=None, result="won"))
    assert len(analytics.graded_legs([mixed])) == 2


def test_a_band_reports_claimed_against_realised():
    legs = [leg(f"P{i}", prob=0.62, result="won" if i < 3 else "lost")
            for i in range(10)]
    band, = analytics.calibration([slip(*legs)])
    assert (band.lo, band.hi) == (0.60, 0.65)
    assert band.n == 10
    assert band.claimed == pytest.approx(0.62)
    assert band.realised == pytest.approx(0.30)
    assert band.error == pytest.approx(-0.32)
    assert not band.straddles_its_claim


def test_a_band_that_lands_on_its_claim_says_so():
    legs = [leg(f"P{i}", prob=0.60, result="won" if i < 6 else "lost")
            for i in range(10)]
    band, = analytics.calibration([slip(*legs)])
    assert band.realised == pytest.approx(0.60)
    assert band.straddles_its_claim


def test_legs_are_counted_by_match_as_well_as_by_leg():
    """Ten legs off one match are one observation of the match, not ten."""
    one_match = slip(*[leg(f"P{i}", team="Alpha", opponent="Beta", prob=0.6)
                       for i in range(5)])
    band, = analytics.calibration([one_match])
    assert band.n == 5 and band.matches == 1


def test_two_matches_count_as_two():
    both = slip(leg("P", team="Alpha", opponent="Beta", prob=0.6),
                leg("Q", team="Gamma", opponent="Delta", prob=0.6))
    band, = analytics.calibration([both])
    assert band.matches == 2


def test_the_same_fixture_named_either_way_round_is_one_match():
    both = slip(leg("P", team="Alpha", opponent="Beta", prob=0.6),
                leg("Q", team="Beta", opponent="Alpha", prob=0.6))
    band, = analytics.calibration([both])
    assert band.matches == 1


def test_bands_below_fifty_percent_are_kept_not_hidden():
    """A leg the model thought was a loser should be visible, not dropped."""
    band, = analytics.calibration([slip(leg("P", prob=0.42), leg("Q", prob=0.45))])
    assert (band.lo, band.hi) == (0.0, 0.50) and band.n == 2


def test_empty_bands_are_omitted():
    assert analytics.calibration([slip(leg("P", prob=0.62), leg("Q", prob=0.63))]) \
        .__len__() == 1


# ------------------------------------------------------------ sizing policy

def calibrated(n, claimed, realised_fraction):
    """n legs claiming `claimed`, of which `realised_fraction` actually won."""
    wins = round(n * realised_fraction)
    return [slip(*[leg(f"P{i}", team=f"T{i}", opponent=f"O{i}", prob=claimed,
                       result="won" if i < wins else "lost")
                   for i in range(n)])]


def test_below_the_leg_floor_kelly_stands_but_unverified():
    policy, why = analytics.sizing_policy(calibrated(20, 0.60, 0.50))
    assert policy == "kelly"
    assert "20 graded leg(s)" in why and "untested, not verified" in why


def test_calibration_error_is_none_below_the_floor():
    error, legs, matches = analytics.calibration_error(calibrated(20, 0.6, 0.5))
    assert error is None and legs == 20 and matches == 20


def test_a_well_calibrated_record_keeps_kelly():
    policy, why = analytics.sizing_policy(calibrated(80, 0.60, 0.58))
    assert policy == "kelly" and "within" in why


def test_a_badly_calibrated_record_takes_kelly_away():
    policy, why = analytics.sizing_policy(calibrated(80, 0.65, 0.52))
    assert policy == "flat"
    assert "off by" in why and "flat 2% units" in why


def test_the_threshold_is_five_points():
    assert analytics.sizing_policy(calibrated(100, 0.60, 0.56))[0] == "kelly"
    assert analytics.sizing_policy(calibrated(100, 0.60, 0.54))[0] == "flat"


def test_being_better_than_claimed_is_still_mis_calibration():
    """A model that says 55% and hits 70% is wrong about its own edge, and Kelly
    sized off it UNDERBETS -- less dangerous, but still not Kelly."""
    assert analytics.sizing_policy(calibrated(100, 0.55, 0.70))[0] == "flat"


def test_the_flat_policy_actually_changes_the_stake():
    """The whole point: a banner that does not move the money is decoration."""
    table = outcome_table([(0.65, 0.0), (0.65, 0.0)], "power", "3")
    kelly = recommend(table, 100_000, policy="kelly")
    flat = recommend(table, 100_000, policy="flat", policy_reason="off by 9%")
    assert flat.stake_cents == int(100_000 * float(FLAT_UNIT))
    assert flat.stake_cents < kelly.stake_cents
    assert flat.binding == "flat units"
    assert any("off by 9%" in r for r in flat.reasons)


def test_flat_units_still_refuse_a_slip_with_no_edge():
    """Flat sizing is about HOW MUCH, never about whether."""
    flat = recommend(outcome_table([(0.4, 0.0), (0.4, 0.0)], "power", "3"),
                     100_000, policy="flat")
    assert (flat.bet, flat.stake_cents, flat.binding) == (False, 0, "no edge")


def test_flat_units_still_obey_the_caps():
    flat = recommend(outcome_table([(0.9, 0.0), (0.9, 0.0)], "power", "3"),
                     100_000, staked_tonight_cents=24_500, policy="flat")
    assert flat.stake_cents == 500 and flat.binding == "per-night cap"


def test_an_unknown_policy_is_refused():
    with pytest.raises(ValueError, match="policy must be"):
        recommend(outcome_table([(0.6, 0.0), (0.6, 0.0)], "power", "3"), 1000,
                  policy="vibes")


# ------------------------------------------------------------------------ ROI

def test_roi_splits_by_sport():
    slips = [slip(leg("A", sport="cs2"), leg("B", sport="cs2", team="Beta"),
                  stake="1", payout="3"),
             slip(leg("C", sport="lol"), leg("D", sport="lol", team="Beta"),
                  stake="1", payout="0")]
    splits = {s.label: s for s in analytics.roi(slips, "sport")}
    assert splits["cs2"].net_cents == 200 and splits["cs2"].roi == pytest.approx(2.0)
    assert splits["lol"].net_cents == -100 and splits["lol"].roi == pytest.approx(-1.0)


def test_a_mixed_slip_counts_whole_against_every_sport_it_touches():
    """Not divided. A parlay does not risk a third of the stake per leg -- any
    one leg can bust the whole thing, so each sport is fully implicated."""
    mixed = [slip(leg("A", sport="cs2"), leg("B", sport="lol", team="Beta"),
                  stake="3", payout="0")]
    splits = {s.label: s for s in analytics.roi(mixed, "sport")}
    assert splits["cs2"].staked_cents == splits["lol"].staked_cents == 300
    assert sum(s.staked_cents for s in splits.values()) == 600   # deliberately > 300
    assert analytics.overall(mixed).staked_cents == 300          # the truth


def test_unsettled_slips_are_left_out_of_roi():
    assert analytics.roi([slip(leg("A"), leg("B", team="Beta"))], "sport") == []
    assert analytics.overall([slip(leg("A"), leg("B", team="Beta"))]).staked_cents == 0


def test_roi_by_size_and_odds_type():
    slips = [slip(leg("A", odds_type="goblin"), leg("B", team="Beta"),
                  stake="1", payout="0")]
    assert {s.label for s in analytics.roi(slips, "size")} == {"2-pick power"}
    assert {s.label for s in analytics.roi(slips, "odds_type")} == {"goblin", "standard"}


def test_an_unknown_roi_key_is_refused():
    with pytest.raises(ValueError, match="key must be"):
        analytics.roi([], "astrology")


def test_roi_is_none_rather_than_a_divide_by_zero():
    assert analytics.Split("x", 0, 0, 0).roi is None


# -------------------------------------------------------- closing line value

def history_file(tmp_path, rows):
    path = tmp_path / "props_history.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(path)


def observation(line, at, player="P", stat="kills", maps=2, game="cs2",
                start=None):
    return {"game": game, "player": player, "stat": stat, "maps": maps,
            "line": line, "observed_at": at, "start_time": start or ts(6)}


def test_a_line_that_moved_is_measured(tmp_path):
    path = history_file(tmp_path, [observation(20.5, ts(-2)), observation(21.5, ts(2))])
    moves, coverage = analytics.closing_lines(
        [clv_slip(line=20.5, side="over", start_time=ts(6))], path)
    assert coverage["measured"] == 1
    assert moves[0].placed_line == 20.5 and moves[0].closing_line == 21.5
    assert moves[0].toward_us is True        # an over that closes higher is value


def test_direction_is_read_per_side(tmp_path):
    path = history_file(tmp_path, [observation(20.5, ts(-2)), observation(21.5, ts(2))])
    moves, _ = analytics.closing_lines(
        [clv_slip(line=20.5, side="under", start_time=ts(6))], path)
    assert moves[0].toward_us is False      # an under wants the line to FALL


def test_a_line_that_held_is_a_measurement_not_a_gap(tmp_path):
    """One row means one distinct line, so the line never moved -- provided the
    board was looked at again, which the later capture of another prop proves."""
    path = history_file(tmp_path, [observation(20.5, ts(-2)),
                                   observation(9.5, ts(2), player="OTHER")])
    moves, coverage = analytics.closing_lines(
        [clv_slip(line=20.5, start_time=ts(6))], path)
    assert coverage["measured"] == 1
    assert moves[0].moved == 0 and moves[0].toward_us is None
    assert moves[0].captures_after_placing == 1


def test_no_capture_between_placing_and_lock_is_unmeasurable(tmp_path):
    """The one thing that really is unknowable: nobody looked."""
    path = history_file(tmp_path, [observation(20.5, ts(-2))])
    moves, coverage = analytics.closing_lines(
        [clv_slip(line=20.5, start_time=ts(6))], path)
    assert moves == []
    assert coverage["unmeasured"]["the board was not captured again before lock"] == 1


def test_a_line_posted_after_lock_does_not_count(tmp_path):
    """A line posted once the game has started is not a closing line.

    The pre-lock capture of another prop is what makes this leg measurable at
    all -- without it the correct answer is "nobody looked", which is a separate
    test. So the fixture has both: a look before lock, and a later line after.
    """
    path = history_file(tmp_path, [observation(20.5, ts(-2)),
                                   observation(9.5, ts(2), player="OTHER"),
                                   observation(30.5, ts(8))])
    moves, _ = analytics.closing_lines(
        [clv_slip(line=20.5, start_time=ts(6))], path)
    assert moves and moves[0].closing_line == 20.5 and moves[0].moved == 0


def test_the_same_prop_on_two_matches_stays_two_series(tmp_path):
    """Without the start time in the key, Friday's line would close Tuesday's
    bet -- a number about a different game."""
    path = history_file(tmp_path, [
        observation(20.5, ts(-2), start=ts(6)),
        observation(28.5, ts(2), start=ts(80)),      # a different match entirely
    ])
    by_match, _ = analytics.board_history(path)
    assert len(by_match) == 2
    moves, _ = analytics.closing_lines(
        [clv_slip(line=20.5, start_time=ts(6))], path)
    assert moves and moves[0].closing_line == 20.5   # not 28.5


def test_a_leg_with_no_start_time_takes_the_next_match(tmp_path):
    path = history_file(tmp_path, [observation(20.5, ts(-2), start=ts(6)),
                                   observation(28.5, ts(-2), start=ts(80))])
    rows, problem = analytics.resolve_prop(leg(line=20.5),
                                          analytics.parse_ts(ts(0)),
                                          analytics.board_history(path)[0])
    assert problem is None and rows[0]["line"] == 20.5


def test_two_matches_starting_together_are_ambiguous_not_guessed(tmp_path):
    path = history_file(tmp_path, [observation(20.5, ts(-2), start=ts(6)),
                                   observation(28.5, ts(-2), start=ts(7))])
    rows, problem = analytics.resolve_prop(leg(line=20.5),
                                          analytics.parse_ts(ts(0)),
                                          analytics.board_history(path)[0])
    assert rows is None and "unclear" in problem


def test_a_prop_never_on_the_board_says_so(tmp_path):
    path = history_file(tmp_path, [observation(20.5, ts(-2), player="SOMEONE")])
    _, coverage = analytics.closing_lines([clv_slip()], path)
    assert coverage["unmeasured"]["never seen on a board we captured"] == 2


def test_a_missing_history_file_is_not_a_crash(tmp_path):
    moves, coverage = analytics.closing_lines(
        [clv_slip()], str(tmp_path / "nope.jsonl"))
    assert moves == [] and coverage["captures"] == 0


def test_a_truncated_line_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "props_history.jsonl"
    path.write_text(json.dumps(observation(20.5, ts(-2))) + "\n{\"game\": \"cs2\"")
    by_match, captures = analytics.board_history(str(path))
    assert len(by_match) == 1 and len(captures) == 1


def test_timestamps_from_three_different_clocks_compare_as_instants():
    """The history is UTC, a start time carries the venue's offset, placed_at is
    local. Compared as strings, 2026-09-28T23:00-04:00 sorts before
    2026-09-29T01:00+00:00 -- which is two hours EARLIER, not later."""
    later = analytics.parse_ts("2026-09-28T23:00:00-04:00")
    earlier = analytics.parse_ts("2026-09-29T01:00:00+00:00")
    assert earlier < later
    assert analytics.parse_ts(None) is None and analytics.parse_ts("nonsense") is None


# ------------------------------------------------------------ the real files

def test_the_committed_board_history_reads():
    by_match, captures = analytics.board_history("props_history.jsonl")
    assert len(by_match) > 500, "props_history.jsonl looks empty or reshaped"
    assert len(captures) > 5
    # one row per DISTINCT line, so no series repeats a line
    for key, rows in by_match.items():
        assert len({r["line"] for r in rows}) == len(rows), key


def test_the_report_runs_on_an_empty_tracker(tmp_path):
    lines = analytics.report(Tracker(tmp_path / "store.json"),
                            str(tmp_path / "nope.jsonl"))
    assert any("nothing has settled yet" in l for l in lines)
    assert any("SIZING: KELLY" in l for l in lines)

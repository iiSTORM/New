"""The slate: what is legal, what clears the bar, and what gets left out.

Two rules here are the ones that cost money when they were left to judgement.
A leg on two slips is not two bets, it is one bet at twice the stake -- on
2026-09-28 a single goalkeeper leg sat on two entries and one number busted
both. And ranking by the blend rather than the worst case promotes exactly the
slips whose models disagree, which are the ones least worth backing.
"""
from datetime import datetime, timedelta, timezone

import pytest

from propedge import builder as bld
from propedge.model import Projection
from propedge.money import to_cents
from propedge.store import Tracker

EAST = timezone(timedelta(hours=-4))
EVENING = datetime(2026, 9, 29, 17, 0, tzinfo=EAST)


def at(hour, minute=0):
    return datetime(2026, 9, 29, hour, minute, tzinfo=EAST).isoformat()


UNSET = object()


def proj(prop_id, player, team, opponent, p=0.62, start=UNSET, **kw):
    kw.setdefault("stat", "kills")
    kw.setdefault("line", 20.5)
    kw.setdefault("side", "over")
    # `start or at(21)` would turn an explicitly empty start time back into a
    # real one, which quietly made the no-lock-time test test nothing.
    return Projection(prop_id=prop_id, sport="cs2", player=player, team=team,
                      opponent=opponent, maps=2, p_win=p,
                      start_time=at(21) if start is UNSET else start, **kw)


def scenarios(prop_id, player, team, opponent, low, high, **kw):
    return proj(prop_id, player, team, opponent, p=None,
                scenarios={"usage": low, "market": high}, **kw)


def strong(n=4, p=0.70):
    """n legs on n/2 different fixtures, all good enough to clear the bar."""
    out, fixtures = [], ["Alpha/Beta", "Gamma/Delta", "Eps/Zeta", "Eta/Theta"]
    for i in range(n):
        home, away = fixtures[i // 2].split("/")
        out.append(proj(f"p{i}", f"P{i}", home if i % 2 == 0 else away,
                        away if i % 2 == 0 else home, p=p))
    return out


# ----------------------------------------------------------------- the rules

def test_two_legs_on_one_team_are_not_a_slip():
    assert bld.is_legal([proj("a", "P", "Alpha", "Beta"),
                         proj("b", "Q", "Alpha", "Beta")])


def test_the_same_player_twice_is_not_a_slip():
    assert bld.is_legal([proj("a", "P", "Alpha", "Beta"),
                         proj("b", "P", "Beta", "Alpha", line=25.5)])


def test_two_teams_two_players_is_a_slip():
    assert bld.is_legal([proj("a", "P", "Alpha", "Beta"),
                         proj("b", "Q", "Beta", "Alpha")]) == []


def test_an_illegal_combination_never_reaches_the_slate():
    one_team = [proj("a", "P", "Alpha", "Beta", p=0.9),
                proj("b", "Q", "Alpha", "Beta", p=0.9)]
    assert bld.search(one_team) == []


# ------------------------------------------------------- the worst-case rule

def test_ranking_is_by_the_worst_scenario_not_the_blend():
    """The settled slip loses on the blend and wins on the worst case, which is
    the point: a slip whose models disagree is not the one to back."""
    settled = [scenarios("s1", "S1", "Alpha", "Beta", 0.66, 0.68),
               scenarios("s2", "S2", "Beta", "Alpha", 0.66, 0.68)]
    disputed = [scenarios("d1", "D1", "Gamma", "Delta", 0.50, 0.90),
                scenarios("d2", "D2", "Delta", "Gamma", 0.50, 0.90)]
    # No bar: the disputed slip is -23% on its worst scenario and would be
    # filtered out, and the point here is the ORDER, not the filter.
    found = bld.search(settled + disputed, sizes=(2,), min_worst_ev=-1.0)
    pairs = {tuple(sorted(c.legs)): c for c in found}
    settled_slip = pairs[("s1", "s2")]
    disputed_slip = pairs[("d1", "d2")]
    assert disputed_slip.blend_ev > settled_slip.blend_ev
    assert settled_slip.worst_ev > disputed_slip.worst_ev
    assert found[0] is settled_slip          # ranked by the worst case


def test_the_worst_scenario_is_named():
    # 0.60/0.80 a side is +9.9% on the worst scenario, just UNDER the default
    # bar, which is its own small lesson about how thin these get.
    found = bld.search([scenarios("a", "A", "Alpha", "Beta", 0.60, 0.80),
                        scenarios("b", "B", "Beta", "Alpha", 0.60, 0.80)],
                       sizes=(2,), min_worst_ev=0.05)
    assert found and found[0].worst_scenario == "usage"
    assert found[0].by_scenario["usage"] < 0.10 < found[0].by_scenario["market"]


def test_a_leg_missing_a_scenario_falls_back_rather_than_narrowing_it():
    """One model with fewer worlds must not quietly shrink the stress test."""
    mixed = [scenarios("a", "A", "Alpha", "Beta", 0.60, 0.80),
             proj("b", "B", "Beta", "Alpha", p=0.70)]
    # The single-number leg contributes no world of its own: "blend" is dropped
    # once real scenarios exist, because a world where the two-scenario leg uses
    # the average of its own worlds is not a world any model produced.
    assert bld.scenarios_of(mixed) == ["market", "usage"]
    assert bld.scenarios_of([proj("b", "B", "Beta", "Alpha", p=0.70)]) == ["blend"]
    found = bld.search(mixed, sizes=(2,))
    assert found and set(found[0].by_scenario) == {"usage", "market"}


def test_nothing_clears_the_bar_means_nothing_is_offered():
    weak = [proj("a", "A", "Alpha", "Beta", p=0.40),
            proj("b", "B", "Beta", "Alpha", p=0.40)]
    assert bld.search(weak) == []
    slate = bld.build_slate(weak, bankroll_cents=100_000, now=EVENING)
    assert slate["early"] == [] and slate["late"] == []
    assert any("No slip is the answer" in line for line in bld.render(slate))


def test_the_bar_is_adjustable_but_binds():
    legs = [proj("a", "A", "Alpha", "Beta", p=0.58),
            proj("b", "B", "Beta", "Alpha", p=0.58)]
    assert bld.search(legs, min_worst_ev=0.50) == []
    assert bld.search(legs, min_worst_ev=0.0)


# ------------------------------------------------------- one leg, one slip

def test_a_leg_is_never_on_two_recommended_slips():
    """The 2026-09-28 lesson, as a rule the search cannot break."""
    taken = bld.take_disjoint(bld.search(strong(4), sizes=(2,)))
    seen = [leg for candidate in taken for leg in candidate.legs]
    assert len(seen) == len(set(seen))


def test_the_best_slip_wins_the_contested_leg():
    """Greedy, best first: the leg goes to the slip that ranked highest."""
    found = bld.search(strong(4), sizes=(2,))
    taken = bld.take_disjoint(found)
    assert taken[0] is found[0]


def test_dropping_a_slip_does_not_drop_its_unused_legs():
    taken = bld.take_disjoint(bld.search(strong(4, p=0.72), sizes=(2,)))
    assert len(taken) >= 2, "four independent strong legs should make two slips"


# -------------------------------------------------------------- early / late

def test_a_slip_locking_before_the_cutoff_is_early():
    early, late = bld.split_by_lock(
        [bld.evaluate([proj("a", "A", "Alpha", "Beta", start=at(20)),
                       proj("b", "B", "Beta", "Alpha", start=at(20))],
                      multiplier="3")], now=EVENING)
    assert len(early) == 1 and late == []


def test_a_slip_locking_after_the_cutoff_is_late():
    tomorrow = (EVENING + timedelta(hours=8)).isoformat()
    early, late = bld.split_by_lock(
        [bld.evaluate([proj("a", "A", "Alpha", "Beta", start=tomorrow),
                       proj("b", "B", "Beta", "Alpha", start=tomorrow)],
                      multiplier="3")], now=EVENING)
    assert early == [] and len(late) == 1


def test_a_slip_locks_when_its_FIRST_leg_starts():
    """It is live the moment any game begins, so that is the deadline."""
    candidate = bld.evaluate([proj("a", "A", "Alpha", "Beta", start=at(19)),
                              proj("b", "B", "Beta", "Alpha", start=at(22))],
                             multiplier="3")
    assert candidate.locks_at.hour == 19


def test_a_slip_with_no_lock_time_is_late_not_early():
    """The early set exists to be placed before going out; something that might
    already have locked does not belong in it."""
    candidate = bld.evaluate([proj("a", "A", "Alpha", "Beta", start=""),
                              proj("b", "B", "Beta", "Alpha", start="")],
                             multiplier="3")
    early, late = bld.split_by_lock([candidate], now=EVENING)
    assert early == [] and len(late) == 1


def test_past_the_cutoff_the_cutoff_is_tomorrows():
    """At 11.30pm, 'before 11pm' cannot mean two and a half hours ago."""
    late_night = datetime(2026, 9, 29, 23, 30, tzinfo=EAST)
    candidate = bld.evaluate([proj("a", "A", "Alpha", "Beta", start=at(20) .replace("T20", "T23")),
                              proj("b", "B", "Beta", "Alpha", start=at(23, 45))],
                             multiplier="3")
    early, late = bld.split_by_lock([candidate], now=late_night)
    assert len(early) == 1 and late == []


# ------------------------------------------------------------------ the slate

def test_the_slate_sizes_every_slip_it_offers():
    slate = bld.build_slate(strong(4), bankroll_cents=100_000, now=EVENING)
    offered = slate["early"] + slate["late"]
    assert offered and all(c.stake is not None for c in offered)
    assert all(c.stake.bet for c in offered)


def test_stakes_accumulate_against_the_nightly_cap():
    """Each slip is sized against what the ones before it already committed."""
    # Pairs only: four legs make one 3-pick and a leftover, which is one slip
    # and tests nothing about accumulation.
    slate = bld.build_slate(strong(4, p=0.85), bankroll_cents=10_000, now=EVENING,
                            sizes=(2,))
    offered = [c for c in slate["early"] + slate["late"] if c.stake.bet]
    assert len(offered) >= 2
    assert sum(c.stake.stake_cents for c in offered) <= 2_500   # the 25% cap


def test_a_slip_can_clear_the_bar_and_still_be_no_bet():
    """+EV is not the same as playable: the $1 minimum can exceed full Kelly.

    At $10 this pair is +17% and full Kelly still only wants 86 cents, so the
    minimum entry would be an overbet and the answer is no.
    """
    slate = bld.build_slate(strong(2, p=0.62), bankroll_cents=1000, now=EVENING)
    offered = slate["early"] + slate["late"]
    assert offered and offered[0].worst_ev > 0.10
    assert not offered[0].stake.bet
    assert any("full Kelly" in r for r in offered[0].stake.reasons)


def test_the_tracker_decides_the_sizing_policy(tmp_path):
    tracker = Tracker(tmp_path / "store.json")
    tracker.ledger.add("deposit", to_cents("500"))
    slate = bld.build_slate(strong(4), tracker, now=EVENING)
    assert slate["policy"] == "kelly"
    assert "calibration" in slate["policy_reason"]
    assert slate["bankroll_cents"] == 50_000


# ------------------------------------------------------------- the breakdown

def breakdown_of(*projections, **kw):
    candidate = bld.evaluate(list(projections), multiplier=kw.pop("multiplier", "3"))
    return "\n".join(bld.explain(candidate, 100_000))


def test_the_breakdown_names_what_sinks_it():
    text = breakdown_of(scenarios("a", "A", "Alpha", "Beta", 0.45, 0.80),
                        proj("b", "B", "Beta", "Alpha", p=0.75))
    assert "what sinks it: A" in text and "45%" in text


def test_a_wide_scenario_spread_is_called_out():
    text = breakdown_of(scenarios("a", "A", "Alpha", "Beta", 0.45, 0.80),
                        proj("b", "B", "Beta", "Alpha", p=0.75))
    assert "least settled: A" in text and "quarter" in text


def test_a_whole_line_is_flagged_as_pushable_and_a_two_pick_as_refundable():
    text = breakdown_of(proj("a", "A", "Alpha", "Beta", line=20, p_push=0.06),
                        proj("b", "B", "Beta", "Alpha"))
    assert "can push: A 20" in text
    assert "which is refunded" in text


def test_a_three_pick_says_it_pays_as_a_two_pick_instead():
    text = breakdown_of(proj("a", "A", "Alpha", "Beta", line=20, p_push=0.06),
                        proj("b", "B", "Beta", "Alpha"),
                        proj("c", "C", "Gamma", "Delta"), multiplier="6")
    assert "paying as a 2-pick" in text


def test_half_point_lines_say_nothing_can_push():
    assert "no leg can push" in breakdown_of(proj("a", "A", "Alpha", "Beta"),
                                             proj("b", "B", "Beta", "Alpha"))


def test_a_goblin_leg_is_flagged_for_the_multiplier():
    text = breakdown_of(proj("a", "A", "Alpha", "Beta", odds_type="goblin"),
                        proj("b", "B", "Beta", "Alpha"))
    assert "goblin" in text and "changes the multiplier" in text


def test_the_breakdown_always_says_to_read_the_multiplier_off_the_slip():
    assert "read the multiplier off the slip" in breakdown_of(
        proj("a", "A", "Alpha", "Beta"), proj("b", "B", "Beta", "Alpha"))


def test_the_model_s_own_reasons_are_carried_through():
    text = breakdown_of(proj("a", "A", "Alpha", "Beta",
                             reasons=("24.1 kills a map over 6 series",)),
                        proj("b", "B", "Beta", "Alpha"))
    assert "24.1 kills a map over 6 series" in text


def test_how_it_was_priced_is_stated():
    assert "measured same-match correlation" in breakdown_of(
        proj("a", "A", "Alpha", "Beta"), proj("b", "B", "Beta", "Alpha"))

"""The PrizePicks rules, and what a slip pays once its legs are graded.

Every case here is a rule that has already cost money when it was left to
memory: a whole-number line pushing and shrinking the slip, a shrunk slip
paying at a multiplier that was never printed, a single surviving leg being
refunded rather than lost, and a DNP not being a loss.

No real bets appear in this file. This repository is public; the slips here are
made up to exercise the arithmetic.
"""
from decimal import Decimal

import pytest

from propedge.payouts import PayoutTable
from propedge.slips import Leg, Slip, settle


def leg(player, line, side="over", team="A", result="pending", sport="cs2",
        stat="kills", **kw):
    return Leg(sport=sport, player=player, team=team, stat=stat, line=line,
               side=side, result=result, **kw)


def slip(*legs, mode="power", stake="1", multiplier=None, **kw):
    from propedge.money import to_cents
    return Slip(mode=mode, stake_cents=to_cents(stake), multiplier=multiplier,
                legs=list(legs), **kw)


# ------------------------------------------------------------------ grading

@pytest.mark.parametrize("side,line,actual,result", [
    ("over", 15.5, 16, "won"), ("over", 15.5, 15, "lost"),
    ("under", 15.5, 15, "won"), ("under", 15.5, 16, "lost"),
    ("over", 15, 15, "push"), ("under", 15, 15, "push"),
    ("over", 15, 16, "won"), ("under", 15, 14, "won"),
    ("over", 15, 14, "lost"), ("under", 15, 16, "lost"),
    ("over", 0.5, 1, "won"), ("over", 0.5, 0, "lost"),
])
def test_a_leg_grades_from_the_number_put_up(side, line, actual, result):
    assert leg("P", line, side).grade(actual) == result


def test_only_a_whole_line_can_push():
    assert leg("P", 15).can_push
    assert not leg("P", 15.5).can_push
    # 15.5 with an actual of 15.5 is impossible, but the rule is what is tested
    assert leg("P", 15.5).grade(15.5) == "lost"


def test_a_player_who_does_not_play_is_removed_not_lost():
    assert leg("P", 15.5).grade(dnp=True) == "dnp"


def test_grading_keeps_the_line_it_was_placed_at():
    l = leg("P", 15.5)
    assert l.line_at_placement == 15.5
    l.closing_line = 16.5
    assert l.line == 15.5   # the placed line is what settles the leg


# ------------------------------------------------------------- slip validity

def test_a_slip_needs_two_teams():
    both_on_A = slip(leg("P", 1, team="A"), leg("Q", 1, team="A"))
    assert "at least 2 different teams" in " ".join(both_on_A.problems())
    assert slip(leg("P", 1, team="A"), leg("Q", 1, team="B")).is_valid()


def test_one_prop_per_player():
    doubled = slip(leg("P", 1, team="A"), leg("P", 2, team="B"))
    assert "one prop per player" in " ".join(doubled.problems())


def test_the_same_name_in_two_sports_is_two_players():
    ok = slip(leg("P", 1, team="A", sport="cs2"),
              leg("P", 1, team="B", sport="lol"))
    assert ok.is_valid()


def test_in_tennis_each_player_is_their_own_team():
    singles = slip(leg("Alcaraz", 20, team="", sport="tennis", stat="games"),
                   leg("Sinner", 20, team="", sport="tennis", stat="games"))
    assert singles.is_valid(), singles.problems()


def test_a_missing_team_outside_tennis_is_a_problem():
    assert "needs a team" in " ".join(slip(leg("P", 1, team=""),
                                          leg("Q", 1, team="")).problems())


def test_every_problem_is_reported_at_once():
    bad = slip(leg("P", 1, team="A"), leg("P", 2, team="A"), stake="0.50")
    assert len(bad.problems()) == 3   # minimum entry, repeated player, one team


def test_a_slip_needs_two_legs():
    assert "at least 2 legs" in " ".join(slip(leg("P", 1, team="A")).problems())


# ---------------------------------------------------------------- settlement

def test_a_clean_power_win_pays_the_printed_multiplier():
    won = slip(leg("P", 1, result="won", team="A"),
               leg("Q", 1, result="won", team="B"), stake="1", multiplier="3")
    got = settle(won)
    assert (got.status, got.payout_cents, got.estimated) == ("won", 300, False)


def test_one_lost_leg_loses_a_power_slip():
    got = settle(slip(leg("P", 1, result="won", team="A"),
                      leg("Q", 1, result="lost", team="B"), multiplier="3"))
    assert (got.status, got.payout_cents) == ("lost", 0)


def test_a_push_shrinks_the_slip_and_repriced_at_the_table():
    """3-pick with one push pays as a 2-pick, at a multiplier nobody printed."""
    got = settle(slip(leg("P", 1, result="won", team="A"),
                      leg("Q", 1, result="won", team="B"),
                      leg("R", 15, result="push", team="C"),
                      stake="1", multiplier="6"))
    assert got.status == "won"
    assert got.multiplier == Decimal("3")     # the 2-pick multiplier
    assert got.payout_cents == 300            # not 600
    assert got.estimated is True
    assert any("shrinks" in r for r in got.reasons)
    assert any("confirm it against what actually paid" in r for r in got.reasons)


def test_a_dnp_shrinks_the_slip_the_same_way():
    got = settle(slip(leg("P", 1, result="won", team="A"),
                      leg("Q", 1, result="won", team="B"),
                      leg("R", 15.5, result="dnp", team="C"),
                      stake="1", multiplier="6"))
    assert (got.payout_cents, got.estimated) == (300, True)
    assert any("did not play" in r for r in got.reasons)


def test_a_slip_shrunk_to_one_leg_is_refunded():
    got = settle(slip(leg("P", 1, result="won", team="A"),
                      leg("Q", 15, result="push", team="B"), stake="1.50",
                      multiplier="3"))
    assert (got.status, got.payout_cents) == ("refunded", 150)


def test_a_slip_shrunk_to_one_LOSING_leg_is_still_refunded():
    """The surviving leg does not matter: one pick is not a slip."""
    got = settle(slip(leg("P", 1, result="lost", team="A"),
                      leg("Q", 15, result="push", team="B"), stake="1",
                      multiplier="3"))
    assert (got.status, got.payout_cents) == ("refunded", 100)


def test_every_leg_pushing_is_a_refund():
    got = settle(slip(leg("P", 15, result="push", team="A"),
                      leg("Q", 15, result="push", team="B"), stake="1"))
    assert (got.status, got.payout_cents) == ("refunded", 100)


def test_a_shrunk_slip_that_misses_still_loses():
    got = settle(slip(leg("P", 1, result="won", team="A"),
                      leg("Q", 1, result="lost", team="B"),
                      leg("R", 15, result="push", team="C"), multiplier="6"))
    assert (got.status, got.payout_cents) == ("lost", 0)


def test_pending_legs_cannot_settle():
    with pytest.raises(ValueError, match="still pending"):
        settle(slip(leg("P", 1, result="won", team="A"),
                    leg("Q", 1, team="B"), multiplier="3"))


# ------------------------------------------------------------------ flex play

def test_flex_pays_a_partial_hit():
    got = settle(slip(leg("P", 1, result="won", team="A"),
                      leg("Q", 1, result="won", team="B"),
                      leg("R", 1, result="lost", team="C"),
                      mode="flex", stake="1"))
    assert got.multiplier == Decimal("1.25")
    assert (got.status, got.payout_cents) == ("partial", 125)


def test_a_flex_payout_under_the_stake_is_called_partial_not_won():
    got = settle(slip(*[leg(p, 1, result="won", team=p) for p in "ABC"],
                      leg("D", 1, result="lost", team="D"),
                      leg("E", 1, result="lost", team="E"),
                      mode="flex", stake="1"))
    assert got.multiplier == Decimal("0.4")
    assert (got.status, got.payout_cents) == ("partial", 40)


def test_flex_missing_too_many_pays_nothing():
    got = settle(slip(leg("P", 1, result="won", team="A"),
                      leg("Q", 1, result="lost", team="B"),
                      leg("R", 1, result="lost", team="C"),
                      mode="flex", stake="1"))
    assert (got.status, got.payout_cents) == ("lost", 0)


# ------------------------------------------------- the real payout is the truth

def test_the_recorded_payout_beats_the_estimate_and_says_so():
    """A goblin or promo leg moves the multiplier in a way no table knows."""
    got = settle(slip(leg("P", 1, result="won", team="A"),
                      leg("Q", 1, result="won", team="B"),
                      leg("R", 1, result="won", team="C"),
                      stake="2.50", multiplier="6"), actual_payout="8.13")
    assert (got.payout_cents, got.estimated, got.status) == (813, False, "won")
    assert any("actually paid 813" in r for r in got.reasons)


def test_a_recorded_zero_is_a_loss():
    got = settle(slip(leg("P", 1, result="won", team="A"),
                      leg("Q", 1, result="won", team="B"), multiplier="3"),
                 actual_payout="0")
    assert (got.status, got.payout_cents) == ("lost", 0)


def test_an_unknown_leg_cannot_be_estimated():
    backfill = slip(leg("P", 1, result="won", team="A"),
                    leg("Q", 1, result="unknown", team="B"), multiplier="3")
    with pytest.raises(ValueError, match="never written down|unknown"):
        settle(backfill)
    got = settle(backfill, actual_payout="0")
    assert (got.status, got.payout_cents, got.estimated) == ("lost", 0, False)


# ---------------------------------------------------------- the payout table

def test_a_custom_table_overrides_only_what_it_names():
    table = PayoutTable(power={3: "5"})
    assert table.multiplier("power", 3) == Decimal("5")
    assert table.multiplier("power", 2) == Decimal("3")


def test_the_table_is_only_used_where_no_multiplier_was_printed():
    printed = slip(leg("P", 1, result="won", team="A"),
                   leg("Q", 1, result="won", team="B"), multiplier="2.7")
    assert settle(printed, PayoutTable(power={2: "3"})).payout_cents == 270


def test_json_round_trips_a_slip_with_its_legs():
    original = slip(leg("P", 15.5, result="won", team="A", odds_type="goblin", maps=2),
                    leg("Q", 1, result="push", team="B"), stake="1.50", multiplier="3")
    back = Slip.from_json(original.as_json())
    assert back.as_json() == original.as_json()
    assert back.legs[0].odds_type == "goblin" and back.legs[0].maps == 2

"""The esports model: probabilities from projections, and the market prior.

Three things here are not arithmetic checks but findings that cost real props
when they were wrong, and each has a test so it cannot come back:

  * the maps 1-2 division must NOT be applied twice. The shipped model already
    divides by the maps each entry covers; doing it again halves every
    projection;
  * a player listed in their league AND at an international event is one player,
    not an ambiguity. Treating it as ambiguous dropped 78 of 79 Valorant props;
  * a prop's identity includes its START TIME. The same player had the same
    kills line on three matches on 2026-09-29.
"""
import json
import math

import pytest

from propedge import esports
from propedge.esports import (EsportsModel, outcome_probabilities, prior_under,
                              residual_scale, shrink_toward_line, under_rates)


# ------------------------------------------------------- probabilities

def test_a_half_point_line_cannot_push():
    over, push, under = outcome_probabilities(17, 4.9, 15.5)
    assert push == 0.0
    assert over + under == pytest.approx(1.0)
    assert over > under          # projection above the line


def test_a_whole_line_carries_real_push_mass():
    """Kills are integers, so 15 exactly happens and the outcome table needs it.
    A continuous normal puts zero there."""
    over, push, under = outcome_probabilities(17, 4.9, 15)
    assert push > 0.05
    assert over + push + under == pytest.approx(1.0)


def test_the_push_band_is_the_integer_either_side_of_the_line():
    """Exactly the mass between 14.5 and 15.5, not a fudge factor."""
    over, push, under = outcome_probabilities(15, 4.9, 15)
    expected = (esports.EXACT.cdf(0.5 / 4.9) - esports.EXACT.cdf(-0.5 / 4.9))
    assert push == pytest.approx(expected)


def test_a_projection_on_the_line_is_a_coin_flip():
    over, push, under = outcome_probabilities(20.5, 7.0, 20.5)
    assert over == pytest.approx(under, abs=1e-9)


@pytest.mark.parametrize("sigma", [0, -1, None])
def test_no_scale_means_no_probability(sigma):
    assert outcome_probabilities(20, sigma, 15.5) is None


def test_a_wider_error_pulls_everything_toward_a_coin_flip():
    tight = outcome_probabilities(25, 2.0, 20.5)[0]
    loose = outcome_probabilities(25, 12.0, 20.5)[0]
    assert tight > loose > 0.5


# ------------------------------------------------------------ the shrink

def test_no_evidence_means_no_disagreement():
    assert shrink_toward_line(30, 20, 0) == 20


def test_more_evidence_buys_more_of_the_disagreement():
    at = [shrink_toward_line(30, 20, maps, shrink_maps=12) for maps in (4, 12, 40)]
    assert 20 < at[0] < at[1] < at[2] < 30
    assert at[1] == pytest.approx(25.0)     # 12 maps against a 12-map half-life


def test_the_shrink_works_downward_too():
    # 12 maps against a 12-map half-life is exactly half the disagreement, so
    # this lands on 15.0 rather than above it.
    assert shrink_toward_line(10, 20, 12, shrink_maps=12) == pytest.approx(15.0)
    assert 15.0 < shrink_toward_line(10, 20, 6, shrink_maps=12) < 20


def test_shrinking_narrows_the_probability():
    """The intended consequence: fewer slips clear the bar."""
    raw = outcome_probabilities(30, 7.0, 20.5)[0]
    shrunk = outcome_probabilities(shrink_toward_line(30, 20.5, 6), 7.0, 20.5)[0]
    assert raw > shrunk > 0.5


# ------------------------------------------------------ the market prior

def graded_rows(n, under, game="cs2", stat="kills", maps=2, odds_type="standard"):
    return [{"game": game, "stat": stat, "maps": maps, "odds_type": odds_type,
             "result": "under" if i < under else "over"} for i in range(n)]


def test_under_rates_are_measured_per_cell():
    rates = under_rates(graded_rows(100, 60))
    assert rates[("cs2", "kills", 2, "standard")] == (0.6, 100)


def test_pushes_are_not_counted_either_way():
    rows = graded_rows(100, 60) + [{"game": "cs2", "stat": "kills", "maps": 2,
                                    "odds_type": "standard", "result": "push"}] * 20
    assert under_rates(rows)[("cs2", "kills", 2, "standard")] == (0.6, 100)


def test_the_prior_is_keyed_on_odds_type():
    """A goblin's line is moved in your favour, so its under-rate is not a
    measurement of the standard board. On the real record CS2 goblins went under
    35.3% against standard's 54.3%; pooling them describes no bettable product."""
    rows = graded_rows(100, 60) + graded_rows(100, 20, odds_type="goblin")
    rates = under_rates(rows)
    assert rates[("cs2", "kills", 2, "standard")][0] == 0.6
    assert rates[("cs2", "kills", 2, "goblin")][0] == 0.2


def test_a_thin_cell_shrinks_almost_all_the_way_to_a_coin_flip():
    thin, _ = prior_under(under_rates(graded_rows(20, 14)), "cs2", "kills", 2)
    fat, _ = prior_under(under_rates(graded_rows(600, 420)), "cs2", "kills", 2)
    assert 0.5 < thin < 0.55        # 70% of 20 says almost nothing
    assert fat > 0.63               # 70% of 600 says a lot


def test_a_cell_with_no_record_has_no_prior():
    assert prior_under(under_rates([]), "cs2", "kills", 2) is None


def test_the_shrink_never_crosses_the_coin_flip():
    for under in (0, 5, 10, 15, 20):
        got, _ = prior_under(under_rates(graded_rows(20, under)), "cs2", "kills", 2)
        assert (got - 0.5) * ((under / 20) - 0.5) >= 0


PLAN_DATE = "2026-09-28"


def test_the_plans_quoted_figures_match_the_committed_record():
    """The build plan quotes rates as of 2026-09-28. They are not hardcoded
    anywhere -- this is the check that the file still says what it said.

    Read as of that date, not over the whole file. The record grows every
    day, so the whole-file rate drifts away from any figure quoted at a fixed
    date and the check failed for that reason alone: Valorant kills went
    under 71.6% of 109 lines by 2026-09-28 (inside the plan's range) and
    59.8% of 209 by 2026-10-02. The live rate is what the priors use; this
    only asks whether the record still supports what the plan said then.
    """
    graded = json.loads(open("props_results.json").read())["graded"]
    as_of_plan = [r for r in graded if str(r.get("match_date") or "")[:10] <= PLAN_DATE]
    rates = under_rates(as_of_plan)
    cs2_kills = rates[("cs2", "kills", 2, "standard")]
    cs2_hs = rates[("cs2", "headshots", 2, "standard")]
    val = rates[("valorant", "kills", 2, "standard")]
    assert cs2_kills[1] > 600 and 0.50 < cs2_kills[0] < 0.56
    # 54.1% of 593 when quoted; 53.9% of 597 once 91 duplicated CS2 matches
    # were removed (2026-10-03) and lines they had blocked could grade. The
    # record got more correct, not different, so the bound allows for it.
    assert cs2_hs[1] > 500 and 0.53 < cs2_hs[0] < 0.60
    assert val[1] > 100 and 0.65 < val[0] < 0.78


# --------------------------------------------------------- residual scale

def test_a_measured_window_comes_back_verbatim():
    assert residual_scale("cs2", "kills", 2) == 7.70
    assert residual_scale("cs2", "headshots", 2) == 4.93


def test_an_unmeasured_window_is_stretched_and_grows():
    two = residual_scale("valorant", "kills", 2)
    three = residual_scale("valorant", "kills", 3)
    assert three > two
    assert three == pytest.approx(two * (3 / 2) ** 0.63)


@pytest.mark.parametrize("game,stat,maps", [
    ("cs2", "first_bloods", 2), ("dota", "kills", 2), ("cs2", "kills", 0),
    ("cs2", "kills", None), ("cs2", "kills", 2.5),
])
def test_no_scale_rather_than_a_guess(game, stat, maps):
    assert residual_scale(game, stat, maps) is None


# ------------------------------------------------------ locating a player

def roster(*players):
    return {"players": [{"name": name, "hist": None} for name in players]}


def match(date, team, opponent, actual):
    return {"date": date, "teamA": team, "teamB": opponent, "score": "2-0",
            "actual": actual}


def model_with(data, **kw):
    made = EsportsModel(data, [], **kw)
    return made


def test_one_roster_one_answer():
    data = {"valorant": {"VCT Americas": {"teams": {"G2": roster("leaf")},
                                         "past_matches": []}}}
    assert model_with(data).locate("valorant", "leaf")[1] == "G2"


def test_a_player_on_no_roster_is_not_located():
    assert model_with({"valorant": {}}).locate("valorant", "nobody") is None


def test_the_same_team_in_two_regions_is_one_player_not_an_ambiguity():
    """What dropped 78 of 79 Valorant props: G2 is in VCT Americas AND at
    Champions, so the handle resolved to two rosters with the same team name and
    was thrown away as ambiguous. The region with the most matches for that team
    is where the form is."""
    data = {"valorant": {
        "VCT Americas": {"teams": {"G2": roster("leaf")},
                         "past_matches": [match("2026-09-0%d" % i, "G2", "X",
                                                {"G2": {"leaf": {"k": 20}}})
                                          for i in range(1, 6)]},
        "VCT Champions": {"teams": {"G2": roster("leaf")},
                          "past_matches": [match("2026-09-20", "G2", "Y",
                                                 {"G2": {"leaf": {"k": 20}}})]}}}
    assert model_with(data).locate("valorant", "leaf", "G2")[0] == "VCT Americas"


def test_two_genuinely_different_teams_stay_ambiguous():
    data = {"cs2": {"CS2": {"teams": {"Alpha": roster("dupe"),
                                      "Beta": roster("dupe")},
                            "past_matches": []}}}
    assert model_with(data).locate("cs2", "dupe") is None
    assert model_with(data).locate("cs2", "dupe", "Beta")[1] == "Beta"


def test_history_folds_in_the_international_event():
    """A player's recent form is split across their league and Champions, and a
    projection off one of them is a projection off half the evidence."""
    data = {"valorant": {
        "VCT Pacific": {"teams": {"PRX": roster("Jinggg")},
                        "past_matches": [match("2026-09-01", "PRX", "X", {})]},
        "VCT Champions": {"teams": {"PRX": roster("Jinggg")},
                          "past_matches": [match("2026-09-20", "PRX", "Y", {})]}}}
    matches, teams = model_with(data).history_for("valorant", "VCT Pacific")
    assert len(matches) == 2
    assert [m["date"] for m in matches] == ["2026-09-01", "2026-09-20"]
    assert "PRX" in teams


def test_a_fixture_listed_in_both_regions_is_not_counted_twice():
    shared = match("2026-09-20", "PRX", "Y", {})
    data = {"valorant": {
        "VCT Pacific": {"teams": {"PRX": roster("Jinggg")}, "past_matches": [shared]},
        "VCT Champions": {"teams": {"PRX": roster("Jinggg")},
                          "past_matches": [dict(shared)]}}}
    matches, _ = model_with(data).history_for("valorant", "VCT Pacific")
    assert len(matches) == 1


def test_a_game_with_no_international_region_is_untouched():
    data = {"cs2": {"CS2": {"teams": {"A": roster("p")},
                            "past_matches": [match("2026-09-01", "A", "B", {})]}}}
    matches, teams = model_with(data).history_for("cs2", "CS2")
    assert len(matches) == 1 and list(teams) == ["A"]


# ------------------------------------------------------------- identity

def test_a_prop_id_includes_the_start_time():
    """R4DYX had kills 16.5 on three Sangal matches on 2026-09-29. Without the
    start time all three are one id, and the builder's one-leg-one-slip
    bookkeeping is keyed on it."""
    base = {"game": "cs2", "player": "R4DYX", "stat": "kills", "maps": 1,
            "line": 16.5, "odds_type": "demon"}
    ids = {EsportsModel.prop_id({**base, "start_time": f"2026-09-29T1{h}:30:00"})
           for h in (2, 3, 4)}
    assert len(ids) == 3


def test_a_prop_id_separates_odds_types():
    base = {"game": "cs2", "player": "P", "stat": "kills", "maps": 2, "line": 20.5,
            "start_time": "x"}
    assert (EsportsModel.prop_id({**base, "odds_type": "standard"})
            != EsportsModel.prop_id({**base, "odds_type": "goblin"}))


@pytest.mark.parametrize("maps,level", [(2, "low"), (8, "medium"), (25, "high")])
def test_confidence_comes_from_the_evidence(maps, level):
    assert EsportsModel.confidence(maps) == level


# ---------------------------------------------------- against the real files

@pytest.fixture(scope="module")
def live():
    graded = json.loads(open("props_results.json").read())["graded"]
    return EsportsModel.from_files(".", graded)


def test_the_real_board_projects(live):
    board = json.loads(open("props.json").read())
    made = live.project_board(board)
    posted = sum(len(rows) for players in board["props"].values()
                 for rows in players.values())
    assert made, "nothing on the committed board projected at all"
    assert len(made) > posted * 0.5, (
        f"only {len(made)} of {posted} projected — a coverage collapse, which is "
        "how the Valorant roster bug looked")
    assert len({p.prop_id for p in made}) == len(made), "prop_ids collide"


def test_every_projection_is_a_usable_row(live):
    board = json.loads(open("props.json").read())
    for made in live.project_board(board)[:40]:
        assert 0 <= made.p_win <= 1 and 0 <= made.p_push <= 1
        assert made.p_win + made.p_push <= 1 + 1e-9
        assert made.scenarios and all(0 <= p <= 1 for p in made.scenarios.values())
        assert made.reasons and made.sport in esports.GAMES
        assert made.to_leg().model_prob == made.p_win


def test_the_projection_is_not_double_divided_by_the_window(live):
    """The shipped model already divides by the maps each entry covers. Dividing
    again halves every projection, which would show up as a maps-2 projection
    sitting absurdly below a maps-2 line."""
    board = json.loads(open("props.json").read())
    made = [p for p in live.project_board(board)
            if p.sport == "cs2" and p.stat == "kills" and p.maps == 2]
    assert made, "no CS2 maps-2 kills props on the committed board"
    ratios = []
    for projection in made:
        mean = live.project_mean("cs2", projection.player, "kills", 2,
                                 projection.team, None,
                                 str(projection.start_time)[:10])
        if mean:
            ratios.append(mean[0] / projection.line)
    assert ratios
    middle = sorted(ratios)[len(ratios) // 2]
    assert 0.7 < middle < 1.3, (
        f"median projection is {middle:.2f} of the line — a factor-of-two error "
        "is the maps 1-2 division applied twice")

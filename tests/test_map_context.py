"""The odds, veto and stage parsers, against the markup a live page really has.

The HTML in this file is not invented. It is what scripts/dev/probe_map_context.py
printed from live vlr.gg match pages on 2026-09-29, tabs and all, trimmed only
of the bookmaker logo paths' siblings. The point of pinning it verbatim is that
a layout change on vlr.gg should break a test here rather than quietly start
handing the favourite's price to the underdog.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import map_context as mc


# One bookmaker's anchor, exactly as vlr.gg emits it: the first half puts the
# team before its price, the second puts the price first.
ANCHOR = """
<a href="/rr/bet/52105" class="wf-card mod-dark match-bet-item" rel="nofollow sponsored noopener" target="_blank">
\t\t\t\t\t<div class="match-bet-item-half mod-1">
\t\t\t\t\t\t<img src="/img/pd/{book}.png" class="mod-{book}">
\t\t\t\t\t\t<div style="white-space: nowrap; display: flex; align-items: center;">
\t\t\t\t\t\t\t<span class="match-bet-item-team text-of">
\t\t\t\t\t\t\t\t<span class="match-bet-item-team-name">{home}</span>
\t\t\t\t\t\t\t\t<span class="match-bet-item-team-tag">{home_tag}</span>
\t\t\t\t\t\t\t</span>
\t\t\t\t\t\t\t<span class="match-bet-item-odds mod- mod-1">{home_price}</span>
\t\t\t\t\t\t</div>
\t\t\t\t\t</div>
\t\t\t\t\t<div class="ge-text-light match-bet-item-vs">vs</div>
\t\t\t\t\t<div class="match-bet-item-half mod-2">
\t\t\t\t\t\t<div style=" white-space: nowrap; display: flex; align-items: center;">
\t\t\t\t\t\t\t<span class="match-bet-item-odds mod- mod-2">{away_price}</span>
\t\t\t\t\t\t\t<span class="match-bet-item-team text-of">
\t\t\t\t\t\t\t\t<span class="match-bet-item-team-name">{away}</span>
\t\t\t\t\t\t\t\t<span class="match-bet-item-team-tag">{away_tag}</span>
\t\t\t\t\t\t\t</span>
\t\t\t\t\t\t</div>
\t\t\t\t\t\t<div class="ge-text-light match-bet-item-note">{note}</div>
\t\t\t\t\t</div>
\t\t\t\t</a>
"""


def anchor(book="thunderpick", home="G2 Esports", home_tag="G2", home_price="2.29",
           away="Paper Rex", away_tag="PRX", away_price="1.58", note="Pre-match"):
    return ANCHOR.format(book=book, home=home, home_tag=home_tag,
                         home_price=home_price, away=away, away_tag=away_tag,
                         away_price=away_price, note=note)


VETO_LINE = ('<div class="match-header-note">FUT ban Abyss; JDG ban Haven; '
             'FUT pick Ascent; JDG pick Summit; FUT ban Lotus; JDG ban Sunset; '
             'Split remains</div>')
SERIES = ('<div class="match-header-event-series">Group Stage: Opening (A)</div>')


# ------------------------------------------------------------------ odds

def test_each_bookmaker_is_one_row_with_both_prices():
    books = mc.parse_odds(anchor() + anchor(book="rainbet", home_price="2.28",
                                            away_price="1.60"))
    assert [b["book"] for b in books] == ["thunderpick", "rainbet"]
    assert books[0]["prices"] == {"G2 Esports": 2.29, "Paper Rex": 1.58}
    assert books[1]["prices"] == {"G2 Esports": 2.28, "Paper Rex": 1.60}


def test_the_price_follows_its_own_team_not_document_order():
    """The whole reason the parser splits on halves.

    Reading names and prices in document order across the anchor pairs G2 with
    2.29 and Paper Rex with 1.58 by luck of layout in half one, and swaps them
    in half two, where the price is emitted BEFORE the name. A parser that got
    this wrong would call the underdog the favourite on every match and the
    numbers would still look perfectly plausible.
    """
    books = mc.parse_odds(anchor(home="Underdog", home_price="4.00",
                                 away="Favourite", away_price="1.20"))
    assert books[0]["prices"]["Underdog"] == 4.00
    assert books[0]["prices"]["Favourite"] == 1.20


def test_a_half_missing_its_price_drops_the_book_rather_than_half_a_market():
    broken = anchor().replace('<span class="match-bet-item-odds mod- mod-2">1.58</span>', "")
    assert mc.parse_odds(broken) == []


def test_a_price_outside_the_plausible_range_is_a_parse_error():
    assert mc.parse_odds(anchor(home_price="0.98")) == []
    assert mc.parse_odds(anchor(home_price="410")) == []


def test_no_odds_module_is_empty_not_an_exception():
    assert mc.parse_odds("<html><body>no betting here</body></html>") == []
    assert mc.parse_odds("") == []


# ------------------------------------------------------------- consensus

def test_consensus_takes_the_median_price_per_team():
    html = (anchor(home_price="2.29", away_price="1.58")
            + anchor(book="rainbet", home_price="2.28", away_price="1.60")
            + anchor(book="ggbet", home_price="2.40", away_price="1.55"))
    got = mc.consensus(mc.parse_odds(html))
    assert got["books"] == 3
    assert got["prices"]["G2 Esports"] == 2.29
    assert got["prices"]["Paper Rex"] == 1.58


def test_implied_probabilities_sum_to_one_and_favour_the_short_price():
    got = mc.consensus(mc.parse_odds(anchor()))
    implied = got["implied"]
    assert abs(sum(implied.values()) - 1.0) < 1e-12
    assert implied["Paper Rex"] > implied["G2 Esports"]


def test_a_live_price_is_refused_because_it_has_the_first_map_in_it():
    live = anchor(note="Live")
    assert mc.parse_odds(live)          # it parses
    assert mc.consensus(mc.parse_odds(live)) == {}   # and is still refused
    assert mc.consensus(mc.parse_odds(live), pre_match_only=False)["books"] == 1


def test_devig_is_proportional_and_handles_a_fair_market():
    assert mc.devig({"a": 2.0, "b": 2.0}) == {"a": 0.5, "b": 0.5}
    assert mc.devig({}) == {}


# ------------------------------------------------------------------ veto

def test_the_veto_comes_back_in_order_with_bans_picks_and_the_decider():
    steps = mc.parse_veto(VETO_LINE)
    assert [(s["team"], s["action"], s["map"]) for s in steps] == [
        ("FUT", "ban", "Abyss"),
        ("JDG", "ban", "Haven"),
        ("FUT", "pick", "Ascent"),
        ("JDG", "pick", "Summit"),
        ("FUT", "ban", "Lotus"),
        ("JDG", "ban", "Sunset"),
        (None, "decider", "Split"),
    ]


def test_the_maps_played_are_the_picks_then_the_decider():
    assert mc.maps_from_veto(mc.parse_veto(VETO_LINE)) == ["Ascent", "Summit", "Split"]
    assert mc.maps_from_veto([]) == []


def test_a_header_note_that_is_not_a_veto_is_not_read_as_one():
    """Upcoming matches carry a note too, and it is not a veto.

    The probe found match-header-note absent on upcoming pages and a countdown
    in match-header-vs-note, but the note element is shared markup and a patch
    number or a forfeit notice lands in it. Nothing without a ban or a pick in
    it counts.
    """
    assert mc.parse_veto('<div class="match-header-note">Patch 10.0</div>') == []
    assert mc.parse_veto('<div class="match-header-note">forfeit</div>') == []


def test_the_first_note_that_is_a_veto_wins_over_an_earlier_one_that_is_not():
    html = ('<div class="match-header-note">Patch 10.0</div>' + VETO_LINE)
    assert mc.maps_from_veto(mc.parse_veto(html)) == ["Ascent", "Summit", "Split"]


# ----------------------------------------------------------------- stage

def test_the_stage_is_the_event_series_line():
    assert mc.parse_stage(SERIES) == "Group Stage: Opening (A)"
    assert mc.parse_stage("<div>nothing</div>") is None


# --------------------------------------------------------------- whole page

def test_a_finished_page_yields_odds_veto_maps_and_stage():
    html = anchor() + anchor(book="rainbet", home_price="2.28") + VETO_LINE + SERIES
    got = mc.context_from_page(html)
    assert set(got) == {"odds", "veto", "maps", "stage"}
    assert got["maps"] == ["Ascent", "Summit", "Split"]
    assert got["odds"]["books"] == 2


def test_an_upcoming_page_yields_odds_and_stage_but_no_veto():
    """Which is the finding that bounds what odds can be used for.

    The veto happens minutes before the first map, so it is on the page only
    after the fact: backtestable, not projectable. Odds are on both.
    """
    got = mc.context_from_page(anchor() + SERIES)
    assert set(got) == {"odds", "stage"}


def test_a_page_with_none_of_it_yields_an_empty_record():
    """Empty rather than a record of Nones, which a caller would average in."""
    assert mc.context_from_page("<html></html>") == {}


# ------------------------------------------------------- bo3.gg bet_updates

# Both taken verbatim from scripts/dev/probe_bo3_odds.py against live bo3.gg
# on 2026-09-29, and they are different in the way that matters: the first is
# an upcoming match's pre-match book, the second a finished match's record,
# which holds an IN-PLAY price frozen at the end.
UPCOMING = {
    "path": ("https://refpa04636.pro/L?tag=x&r=/en/line/Esports/"
             "2652798-CS-2-United-21/373804700-Wraith-PCIFIC-State"),
    "team_1": {"name": "PCIFIC", "coeff": 2.279, "active": True,
               "team_id": 6255, "max_coeff": 2.366, "aggrement_score": 0.52},
    "team_2": {"name": "STATE", "coeff": 1.57, "active": True,
               "team_id": 10493, "max_coeff": 1.67, "aggrement_score": 0.48},
    "markets_count": 16, "bet_provider_id": 39,
    "additional_markets": [
        {"coeff": 1.9, "active": True, "team_id": None,
         "bet_type": "total_maps_over_2_5", "max_coeff": 1.92},
        {"coeff": 1.8, "active": True, "team_id": None,
         "bet_type": "total_maps_under_2_5", "max_coeff": 1.83},
        {"coeff": 1.44, "active": True, "team_id": 6255,
         "bet_type": "team_1_handicap_over_1_5", "max_coeff": 1.47},
    ],
}
FINISHED = {
    "path": ("https://refpa04636.pro/L?tag=x&r=/en/live/Esports/"
             "2659308-CS-2-ESL-Challenger-League-North-America/"
             "756979267-regain-Marsborne"),
    "team_1": {"name": "regain", "coeff": 13.6, "active": False,
               "team_id": 9655, "max_coeff": 13.6, "aggrement_score": 0.33},
    "team_2": {"name": "Marsborne", "coeff": 1.016, "active": False,
               "team_id": 19187, "max_coeff": 1.157, "aggrement_score": 0.67},
    "markets_count": 2, "bet_provider_id": 39,
    "additional_markets": [
        {"coeff": 2.45, "active": True, "team_id": None,
         "bet_type": "total_maps_over_2_5", "max_coeff": 2.45},
        {"coeff": 1.51, "active": True, "team_id": None,
         "bet_type": "total_maps_under_2_5", "max_coeff": 1.53},
    ],
}


def test_an_upcoming_match_gives_the_pre_match_book():
    got = mc.from_bet_updates(UPCOMING)
    assert got["prices"] == {"PCIFIC": 2.279, "STATE": 1.57}
    assert got["implied"]["STATE"] > got["implied"]["PCIFIC"]
    assert abs(sum(got["implied"].values()) - 1.0) < 1e-12


def test_a_finished_match_is_refused_because_its_price_is_in_play():
    """The single most important test in this file.

    13.6 against 1.016 is not a forecast, it is the scoreboard. Accepting it
    would make any feature built on these odds look extraordinary in a
    backtest and do nothing live, which is the exact failure the six tests in
    can_we_beat_the_line.py exist to avoid repeating.
    """
    assert mc.is_pre_match(FINISHED) is False
    assert mc.from_bet_updates(FINISHED) == {}
    # And it is still readable when a caller knowingly asks for it.
    assert mc.from_bet_updates(FINISHED, require_pre_match=False)["prices"] == {
        "regain": 13.6, "Marsborne": 1.016}


def test_each_pre_match_signal_is_load_bearing_on_its_own():
    assert mc.is_pre_match(UPCOMING) is True
    for field, value in (("path", UPCOMING["path"].replace("/en/line/", "/en/live/")),
                         ("markets_count", 2)):
        assert mc.is_pre_match({**UPCOMING, field: value}) is False
    settled = {**UPCOMING, "team_1": {**UPCOMING["team_1"], "active": False}}
    assert mc.is_pre_match(settled) is False


def test_the_series_length_market_comes_through_devigged():
    total = mc.from_bet_updates(UPCOMING)["total_maps"]
    assert total["over_2_5"] == 1.9 and total["under_2_5"] == 1.8
    # 1/1.9 against 1/1.8, normalised: the market leans slightly to a 2-0.
    assert 0.48 < total["implied_over"] < 0.49


def test_the_affiliate_link_is_never_carried_through():
    got = mc.from_bet_updates(UPCOMING)
    assert "path" not in got
    assert "refpa04636" not in repr(got)


def test_junk_gives_nothing_rather_than_half_a_market():
    assert mc.from_bet_updates(None) == {}
    assert mc.from_bet_updates({}) == {}
    assert mc.from_bet_updates({**UPCOMING, "team_2": None}) == {}
    assert mc.from_bet_updates({**UPCOMING,
                                "team_1": {**UPCOMING["team_1"], "coeff": 0.5}}) == {}


def test_a_match_with_no_side_markets_still_gives_the_two_way():
    got = mc.from_bet_updates({**UPCOMING, "additional_markets": []})
    assert got["prices"]["PCIFIC"] == 2.279
    assert "total_maps" not in got


# ------------------------------------------- the settled layout, which leaks

# Verbatim from scripts/dev/probe_vlr_finished_odds.py against a live finished
# page on 2026-09-29. Note what is NOT here: the losing team. vlr.gg replaces
# the two-way block with a settled-bet message naming only the winner.
SETTLED = """
<a href="/rr/bet/52119" class="wf-card mod-dark match-bet-item" rel="nofollow sponsored noopener" target="_blank">
\t\t\t\t\t<div class="match-bet-item-half mod-1">
\t\t\t\t\t\t<div><img src="/img/pd/rainbet.png" class="mod-rainbet"></div>
\t\t\t\t\t</div>
\t\t\t\t\t<div class="match-bet-item-return">
\t\t\t\t\t\t<div class="match-bet-item-return-msg">
\t\t\t\t\t\t\t<span class="match-bet-item-odds">$100</span> on
\t\t\t\t\t\t\t<span class="match-bet-item-teamzzz">100 Thieves</span>
\t\t\t\t\t\t\treturned <span class="match-bet-item-odds">$140</span>
\t\t\t\t\t\t\tat pre-match odds
\t\t\t\t\t\t</div>
\t\t\t\t\t\t<div class="match-bet-item-return-short">
\t\t\t\t\t\t\t<span class="match-bet-item-odds">1.40</span>
\t\t\t\t\t\t\t<span class="match-bet-item-teamzzz">100T</span> odds pre-match
\t\t\t\t\t\t</div>
\t\t\t\t\t</div>
\t\t\t\t\t<div class="match-bet-item-half mod-2"></div>
\t\t\t\t</a>
"""


def test_a_settled_page_yields_no_odds_because_only_the_winner_is_priced():
    """The correction that cost the Valorant backfill, and it is worth the cost.

    1.40 really is the pre-match price, which is what makes this dangerous
    rather than merely useless: the page shows it for 100 Thieves because 100
    Thieves won. "Has a price" and "won" are the same statement here, so a
    win-probability feature built from a finished page would separate the data
    perfectly in sample and know nothing whatsoever in advance. Valorant
    therefore accumulates forward exactly like CS2 does.
    """
    assert mc.parse_odds(SETTLED) == []
    assert mc.context_from_page(SETTLED + VETO_LINE + SERIES).get("odds") is None


def test_the_veto_and_stage_still_come_off_a_settled_page():
    """Which is why a finished page is still worth parsing at all."""
    got = mc.context_from_page(SETTLED + VETO_LINE + SERIES)
    assert got["maps"] == ["Ascent", "Summit", "Split"]
    assert got["stage"] == "Group Stage: Opening (A)"


def test_a_settled_anchor_next_to_a_live_two_way_one_does_not_poison_it():
    books = mc.parse_odds(SETTLED + anchor())
    assert len(books) == 1
    assert books[0]["prices"] == {"G2 Esports": 2.29, "Paper Rex": 1.58}

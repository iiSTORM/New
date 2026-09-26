"""Reporting whether a provider payload carries the entry payout at all.

The Parlays tab prices every rung off PAYOUT_MULTIPLIERS, which is a published
default. It cannot be verified from anything this repo fetches: the provider
refuses server-side requests from every network, which is why props.json is
captured from a logged-in browser in the first place.

But scrape_props.py keeps nine fields per projection and discards the rest, so
if the payout IS in the capture it would never reach props.json and nobody would
know. One command on a real saved payload settles it, and this covers that
command -- including the answer nobody wants, which is the one that has to be
unambiguous.
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "dev"))

pytest.importorskip("requests")  # the inspector imports scrape_props

import inspect_props_payload as ipp


def payload(attrs_list, included=None):
    return {
        "data": [{"type": "projection", "id": str(i), "attributes": attrs,
                  "relationships": {"new_player": {"data": {"type": "new_player", "id": "1"}}}}
                 for i, attrs in enumerate(attrs_list)],
        "included": included or [],
    }


class TestItNamesEveryFieldAProjectionCarries:
    def test_it_lists_the_fields_and_a_sample(self, capsys):
        ipp.report_payout_fields(payload([
            {"stat_type": "MAPS 1-2 Kills", "line_score": 24.5, "odds_type": "standard"}]), 30)
        out = capsys.readouterr().out
        assert "stat_type" in out and "line_score" in out
        assert "MAPS 1-2 Kills" in out, "no sample value, so a field cannot be judged"

    def test_relationships_are_listed_too(self, capsys):
        ipp.report_payout_fields(payload([{"line_score": 1}]), 30)
        assert "relationships.new_player" in capsys.readouterr().out

    def test_a_payload_with_no_projections_says_so(self, capsys):
        ipp.report_payout_fields({"data": [], "included": []}, 30)
        assert "nothing to say" in capsys.readouterr().out


class TestTheAnswerIsUnambiguousEitherWay:
    def test_no_payout_field_says_the_table_must_be_entered_by_hand(self, capsys):
        ipp.report_payout_fields(payload([
            {"stat_type": "Kills", "line_score": 24.5, "start_time": "x"}]), 30)
        out = capsys.readouterr().out
        assert "No payout-shaped field" in out
        assert "entered by hand" in out, "it does not say what to do instead"

    @pytest.mark.parametrize("field", [
        "payout", "payout_multiplier", "flex_multiplier", "power_play_payout",
        "odds_type", "adjusted_odds", "priceUsd", "boost_factor", "demon_multiplier",
    ])
    def test_anything_payout_shaped_is_flagged(self, field, capsys):
        ipp.report_payout_fields(payload([{"line_score": 1, field: 3.0}]), 30)
        out = capsys.readouterr().out
        assert "PAYOUT-SHAPED" in out and field in out

    def test_it_says_what_to_do_when_one_is_found(self, capsys):
        ipp.report_payout_fields(payload([{"payout_multiplier": 3.0}]), 30)
        assert "PAYOUT_MULTIPLIERS" in capsys.readouterr().out

    @pytest.mark.parametrize("field", ["stat_type", "line_score", "start_time",
                                       "display_name", "team", "league"])
    def test_an_ordinary_field_is_not_flagged(self, field, capsys):
        ipp.report_payout_fields(payload([{field: "x"}]), 30)
        assert "No payout-shaped field" in capsys.readouterr().out, (
            f"{field} was mistaken for a price")

    def test_the_included_side_is_searched_as_well(self, capsys):
        """A league or board object sometimes carries the payout structure
        rather than the projection."""
        got = payload([{"line_score": 1}], included=[
            {"type": "league", "attributes": {"name": "CS2", "payout_table": {"2": 3}}}])
        ipp.report_payout_fields(got, 30)
        out = capsys.readouterr().out
        assert "included side" in out and "league.payout_table" in out


class TestAgainstTheCommittedFixture:
    def test_the_hand_built_fixture_carries_no_payout(self, capsys):
        """Which is the honest state of what this repo can see today. If a real
        capture ever replaces this fixture and DOES carry one, this fails and
        the table should be read rather than defaulted."""
        path = REPO_ROOT / "tests/fixtures/prizepicks_projections.json"
        if not path.exists():
            pytest.skip("no payload fixture committed")
        ipp.report_payout_fields(json.loads(path.read_text()), 30)
        assert "No payout-shaped field" in capsys.readouterr().out

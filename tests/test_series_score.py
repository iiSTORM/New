"""CS2 series scores read teamA-teamB, like every other game's."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import series_score  # noqa: E402


def test_a_backwards_score_is_turned_to_fit_the_winner():
    assert series_score.orient("0-2", "MOUZ", "Vitality", "MOUZ") == "2-0"
    assert series_score.orient("2-1", "TTG Esports", "Ducks", "Ducks") == "1-2"


def test_a_right_way_round_score_and_anything_unreadable_are_left_alone():
    assert series_score.orient("2-0", "MOUZ", "Vitality", "MOUZ") == "2-0"
    assert series_score.orient("1-1", "A", "B", "A") == "1-1"
    assert series_score.orient("", "A", "B", "A") == ""
    assert series_score.orient("0-2", "A", "B", None) == "0-2"


def test_orient_all_fixes_stored_matches_in_place():
    matches = [{"teamA": "A", "teamB": "B", "winner": "A", "score": "1-2"},
               {"teamA": "A", "teamB": "B", "winner": "B", "score": "1-2"}]
    assert series_score.orient_all(matches) == 1
    assert [m["score"] for m in matches] == ["2-1", "1-2"]


def test_every_stored_match_reads_the_same_way_as_its_winner():
    import json
    root = Path(__file__).resolve().parent.parent
    for name in ("data.json", "valorant_data.json", "dota_data.json"):
        path = root / name
        if not path.exists():
            continue
        for blob in json.load(open(path))["regions"].values():
            for m in blob.get("past_matches") or []:
                assert series_score.orient(m.get("score"), m.get("teamA"), m.get("teamB"),
                                           m.get("winner")) == m.get("score")

"""How many maps a stored match's totals cover.

Every reader of this defaulted to 2, and for CS2 that was simply wrong.
The scraper has never written maps_counted -- it writes `games` -- so a
Bo1 was read as two maps in all eleven places that asked. 50 of 699
committed CS2 matches are Bo1s, and each was compared against a two-map
projection in the backtest and folded into its players' rates at half
its real per-map value.

It presented as a level bias -- "the model runs high on CS2, +0.449
kills" -- and it is not one. The days it spikes hardest are the days
with the most Bo1s, and it spikes on kills and deaths together
(correlation 0.87 across days), which is the signature of a map-count
mismatch rather than a wrong zero. Correcting it moved CS2 kills MAE
6.819 -> 6.205 and deaths 5.455 -> 4.714, against 0.1-0.5% for every
weight ever tuned in this repo.

The helper is mirrored in src/app.jsx as mapsCountedFor(), and the two
have to agree on every input or the shipped model and its Python port
diverge -- which tests/model_parity.test.mjs would catch as 3600
disagreeing predictions, long after the cause was edited.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "dev"))

import optimize_weights as ow  # noqa: E402

maps_counted_for = ow.maps_counted_for


class TestWhatItReads:
    def test_lol_records_it_directly(self):
        """A Bo5 whose total runs through map 3."""
        assert maps_counted_for({"maps_counted": 3, "games": 2}) == 3

    def test_cs2_records_games_instead(self):
        """The whole bug. No maps_counted, and `games` is the real
        count -- 1 for a Bo1."""
        assert maps_counted_for({"games": 1}) == 1
        assert maps_counted_for({"games": 2}) == 2

    def test_maps_counted_wins_where_both_are_present(self):
        """LoL writes both, and maps_counted is the one that tracks what
        `actual` summed over."""
        assert maps_counted_for({"maps_counted": 3, "games": 2}) == 3

    def test_a_match_with_neither_still_reads_as_two(self):
        """The old default, kept for the record shape that predates both
        fields. Two is right for every game's ordinary series."""
        assert maps_counted_for({}) == 2
        assert maps_counted_for({"teamA": "A"}) == 2

    @pytest.mark.parametrize("bad", [0, -1, None, "2", "", [], {}, float("nan")])
    def test_a_value_it_cannot_use_falls_through(self, bad):
        """Zero is the dangerous one: it would divide a player's totals
        by nothing. A string is the quiet one -- it compares fine and
        multiplies wrong."""
        got = maps_counted_for({"maps_counted": bad, "games": bad})
        assert got == 2

    def test_a_bad_maps_counted_falls_through_to_games(self):
        assert maps_counted_for({"maps_counted": 0, "games": 3}) == 3

    def test_a_bool_is_not_a_map_count(self):
        """True == 1 in Python and would read as a one-map series."""
        assert maps_counted_for({"maps_counted": True, "games": 2}) == 2

    def test_none_match_is_not_fatal(self):
        """It is called inside history loops over data this app did not
        write; a missing match must not take a scrape or a render down."""
        assert maps_counted_for(None) == 2


class TestItMatchesTheShippedJs:
    """The Python port and src/app.jsx have to agree on every input.

    model_parity.test.mjs compares 3600 predictions and would catch a
    divergence, but only as a wall of disagreeing numbers. This names the
    cause directly.
    """

    CASES = [
        {"maps_counted": 3, "games": 2},
        {"games": 1},
        {"games": 2},
        {},
        {"maps_counted": 0, "games": 3},
        {"maps_counted": None, "games": None},
        {"maps_counted": "2", "games": "2"},
        {"maps_counted": -1, "games": 2},
    ]

    def test_the_two_implementations_agree(self):
        node = pytest.importorskip("subprocess")
        import json
        src = (ROOT / "src" / "app.jsx").read_text()
        start = src.index("function mapsCountedFor(match) {")
        end = src.index("\n}", start) + 2
        fn = src[start:end]
        script = (fn + "\nconst cases = " + json.dumps(self.CASES)
                  + ";\nconsole.log(JSON.stringify(cases.map(mapsCountedFor)));")
        out = node.run(["node", "-e", script], capture_output=True, text=True,
                       cwd=str(ROOT))
        assert out.returncode == 0, out.stderr
        js = json.loads(out.stdout)
        py = [maps_counted_for(c) for c in self.CASES]
        assert js == py, f"js {js} != py {py}"

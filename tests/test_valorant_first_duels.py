"""Reading the rest of a vlr.gg stat row.

FK and FD -- who won the opening duel and who lost it -- are the only
columns on that page describing HOW a player plays rather than how much
they produce. Every style proxy derivable from k/d/a was measured against
the model's errors first and came back near zero (K/D ratio 0.07 at best,
champion pool 0.02), because style is already inside a player's own
rates. These columns are not.

Every row below is real, copied from a production run's output, and the
expected values were read off the page's own checksum columns.
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
pytest.importorskip("requests")
pytest.importorskip("bs4")

import scrape_valorant as sv  # noqa: E402

# name, row text, (k, d, a), (fk, fd)
REAL_ROWS = [
    ("Neon",
     "Neon LEV 1.37 1.56 1.11 213 266 144 19 13 6 / 10 6 4 / 2 0 2 +9 +7 +2 "
     "81% 83% 78% 155 198 97 33% 40% 22% 1 1 0 1 0 1 0 +1 -1", (19, 10, 2), (1, 1)),
    ("blowz",
     "blowz LEV 1.22 1.06 1.44 220 173 284 15 7 8 / 13 7 6 / 11 5 6 +2 0 +2 "
     "86% 83% 89% 154 128 190 32% 33% 32% 2 1 1 0 0 0 +2 +1 +1", (15, 13, 11), (2, 0)),
    ("Sato",
     "Sato LEV 1.13 1.28 0.93 246 256 234 16 10 6 / 16 8 8 / 11 6 5 0 +2 -2 "
     "86% 92% 78% 172 162 186 39% 43% 33% 1 1 0 2 0 2 -1 +1 -2", (16, 16, 11), (1, 2)),
    ("spike",
     "spike LEV 1.02 0.53 1.68 247 200 310 19 8 11 / 16 9 7 / 3 2 1 +3 -1 +4 "
     "71% 58% 89% 156 130 191 34% 32% 36% 6 3 3 5 5 0 +1 -2 +3", (19, 16, 3), (6, 5)),
]

KDA = re.compile(r"(\d+)\s+\d+\s+\d+\s*/\s*(\d+)\s+\d+\s+\d+\s*/\s*(\d+)\s+\d+\s+\d+")


class TestRealRows:
    @pytest.mark.parametrize("name,row,kda,duels",
                             REAL_ROWS, ids=[r[0] for r in REAL_ROWS])
    def test_duels_are_read_correctly(self, name, row, kda, duels):
        assert sv.parse_first_duels(row) == duels

    @pytest.mark.parametrize("name,row,kda,duels",
                             REAL_ROWS, ids=[r[0] for r in REAL_ROWS])
    def test_kda_is_unaffected(self, name, row, kda, duels):
        """The reason this can be added at all: it reads a different part
        of the row and cannot disturb the part that was already working."""
        assert KDA.search(row).groups() == tuple(str(x) for x in kda)

    def test_the_two_percent_triples_do_not_confuse_it(self):
        """KAST and HS are both percent triples. Anchoring on the first
        would land on ADR and read three columns early -- which is why
        this reads from the LAST percent sign, not the first."""
        row = REAL_ROWS[3][1]
        assert row.count("%") == 6, "two triples of three, as on the real page"
        assert sv.parse_first_duels(row) == (6, 5)


class TestItRefusesRatherThanGuesses:
    def test_a_combined_shape_row_has_no_duel_columns(self):
        """China renders rows this way. There are no duels on them, and a
        number invented here would be indistinguishable from a real one."""
        assert sv.parse_first_duels("PlayerTwo PT 0.95 222 11 / 15 / 1 -4") is None

    def test_a_row_with_no_percent_at_all(self):
        assert sv.parse_first_duels("nothing numeric here") is None

    def test_a_failing_checksum_is_refused(self):
        """The +/- column is the page's own arithmetic. If fk - fd does
        not equal it, the columns have moved and the parse is wrong --
        which is the whole reason a mis-bind cannot be silent here."""
        row = REAL_ROWS[0][1].replace("1 0 1 0 +1 -1", "1 0 1 9 +9 -9")
        assert sv.parse_first_duels(row) is None

    def test_a_truncated_tail_is_refused(self):
        assert sv.parse_first_duels("x 33% 40% 22% 1 1 0") is None


class TestEveryColumn:
    """acs, adr, kast and hs sit on the same row as the duels and cost
    nothing extra to read. ADR is the one most likely to earn its place:
    damage accrues every round while kills arrive in lumps, so it is a
    lower-noise read on the same performance."""

    ROW = REAL_ROWS[0][1]           # Neon

    def parsed(self):
        return sv.parse_stat_row(self.ROW, KDA.search(self.ROW))

    def test_every_column_lands_where_it_should(self):
        got = self.parsed()
        assert got == {"acs": 213.0, "rating": 1.37, "kast": 81.0, "adr": 155.0,
                       "hs": 33.0, "fk": 1, "fd": 1}

    def test_adr_is_not_confused_with_acs(self):
        """They are both three-digit integers three columns apart, which
        is exactly the mix-up a positional parse invites."""
        got = self.parsed()
        assert got["acs"] == 213.0 and got["adr"] == 155.0

    def test_kast_is_not_confused_with_headshot_percent(self):
        """Both are percent triples; anchoring on the wrong one swaps them."""
        got = self.parsed()
        assert got["kast"] == 81.0 and got["hs"] == 33.0

    def test_both_checksums_have_to_agree(self):
        """kills-minus-deaths as well as first-kills-minus-first-deaths.
        One alone would let a shift in the earlier columns through."""
        broken = self.ROW.replace("+9 +7 +2", "+4 +7 +2")
        assert sv.parse_stat_row(broken, KDA.search(broken)) is None

    def test_a_row_without_the_kda_anchor_is_refused(self):
        assert sv.parse_stat_row("no numbers here", None) is None


class TestRates:
    """extra_rates is called, not grepped.

    An earlier version of these read the source for `return {}` and
    passed against a mutation that made that line unreachable. Source
    text is not behaviour.
    """

    FULL = {"extra_games": 4, "extra_rows": 2, "fk": 6, "fd": 2,
            "acs": 420, "adr": 310, "kast": 160, "hs": 66, "rating": 2.4}

    def test_counts_become_per_map_rates(self):
        got = sv.extra_rates(self.FULL)
        assert got["fk"] == 6 / 4 and got["fd"] == 2 / 4

    def test_averages_are_divided_by_rows_not_maps(self):
        """acs and adr are already per-round. Dividing by maps would
        halve every one of them, silently and plausibly."""
        got = sv.extra_rates(self.FULL)
        assert got["acs"] == 420 / 2 and got["adr"] == 310 / 2
        assert got["kast"] == 80.0 and got["hs"] == 33.0

    def test_a_player_with_nothing_readable_gets_no_fields(self):
        """Absent, not zero: never contesting an opening and never being
        parsed are different facts and must stay distinguishable."""
        assert sv.extra_rates({"extra_games": 0, "extra_rows": 0}) == {}
        assert sv.extra_rates({}) == {}

    def test_rows_without_maps_is_refused_rather_than_dividing_by_zero(self):
        assert sv.extra_rates({"extra_games": 0, "extra_rows": 3, "fk": 1}) == {}
        assert sv.extra_rates({"extra_games": 3, "extra_rows": 0, "fk": 1}) == {}

    def test_the_map_count_is_carried_so_thinness_is_visible(self):
        """Without it, four maps of duel data and forty look identical."""
        assert sv.extra_rates(self.FULL)["eg"] == 4

    def test_the_aggregate_counts_maps_and_rows_separately(self):
        src = (ROOT / "scripts" / "scrape_valorant.py").read_text(encoding="utf-8")
        assert 'slot["extra_games"] += m["maps_played"]' in src
        assert 'slot["extra_rows"] += kda["rows"]' in src

    def test_the_run_reports_how_much_it_read(self):
        """Defined AND called. The first version split the source on the
        definition and looked after it, which assumed the call came
        second -- it does not, and the test failed for a reason that had
        nothing to do with the code."""
        src = (ROOT / "scripts" / "scrape_valorant.py").read_text(encoding="utf-8")
        assert "def report_first_duels():" in src
        calls = [ln for ln in src.splitlines()
                 if "report_first_duels()" in ln and not ln.lstrip().startswith("def ")]
        assert calls, "report_first_duels is defined but never called"

    def test_a_silent_column_move_is_reported_loudly(self):
        """If every checksum fails the fields go absent, which is the safe
        outcome -- but silence would leave it looking like Valorant simply
        has no duel data."""
        src = (ROOT / "scripts" / "scrape_valorant.py").read_text(encoding="utf-8")
        block = src[src.index("def report_first_duels():"):]
        assert "if parsed == 0:" in block and "file=sys.stderr" in block

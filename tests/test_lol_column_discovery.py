"""The LoL column report, and the promise that it changes nothing.

gol.gg's players/list table is wider than the five columns this scraper
reads. On the Valorant side the same question -- what else is on the
page? -- turned out to be worth first kills and first deaths, the only
signal that survived an honest point-in-time test.

The labels have never been seen from this environment, so nothing is
parsed from them yet. These tests hold that line: the report is
diagnostic, once per run, and the five columns already read are
untouched by it.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "scripts" / "scrape_lcs.py"
pytestmark = pytest.mark.skipif(not SRC.exists(), reason="scraper not present")


@pytest.fixture(scope="module")
def source():
    return SRC.read_text(encoding="utf-8")


def test_columns_are_still_found_by_header_text(source):
    """Not by position. That is what makes the table safe to widen --
    gol.gg can reorder freely and the five keep resolving."""
    for label in ("Games", "Avg kills", "Avg deaths", "Avg assists", "KP%"):
        assert f'col("{label}")' in source


def test_the_report_names_what_is_not_read(source):
    """A list of every column is less useful than the difference. The
    point is to see what is being left on the table."""
    assert "[columns] not read:" in source
    assert "unread = [h for h in header_labels" in source


def test_it_reports_once_per_run(source):
    assert "_HEADER_LABELS_REPORTED = False" in source
    assert "if not _HEADER_LABELS_REPORTED and header_labels:" in source


def test_it_reports_before_any_row_is_parsed(source):
    """A table whose rows all fail is exactly when the labels matter
    most, so the report must not sit behind the parse loop."""
    report = source.index("[columns] gol.gg players/list offers")
    loop = source.index("for i, row in enumerate(rows[1:], start=1):")
    assert report < loop


def test_nothing_new_is_parsed_yet(source):
    """The line being held. The moment a sixth column is read, this test
    should be replaced by one that checks the value -- not deleted."""
    block = source[source.index("_HEADER_LABELS_REPORTED = False"):
                   source.index("for i, row in enumerate(rows[1:], start=1):")]
    for guessed in ("DPM", "CSM", "GPM", "VSPM", "FB%", "Solo kills"):
        assert guessed not in block, f"{guessed} was guessed rather than observed"


def test_the_report_goes_to_stderr_with_the_other_diagnostics(source):
    idx = source.index("[columns] gol.gg players/list offers")
    assert "file=sys.stderr" in source[idx:idx + 400]


class TestItActuallyReports:
    """Driven, not grepped.

    The tests above read the source, which is how a mutation that made a
    guard unreachable slipped past the Valorant equivalent earlier. These
    call parse_player_list against a stand-in table and read what it
    says.
    """

    TABLE = ("""<table class="table_list"><tr>
        <th>Player</th><th>Games</th><th>Win rate</th><th>KDA</th><th>Avg kills</th>
        <th>Avg deaths</th><th>Avg assists</th><th>CSM</th><th>GPM</th><th>KP%</th>
        <th>DMG%</th><th>DPM</th><th>VSPM</th><th>Avg WPM</th><th>FB %</th>
        <th>Solo kills</th></tr><tr>
        <td>Faker</td><td>30</td><td>60%</td><td>4.1</td><td>3.2</td><td>1.8</td>
        <td>6.4</td><td>9.1</td><td>420</td><td>68%</td><td>25%</td><td>520</td>
        <td>1.2</td><td>0.6</td><td>12%</td><td>4</td></tr></table>""")

    @pytest.fixture
    def scraper(self, monkeypatch):
        import io
        import types
        sys.modules.setdefault("requests", types.ModuleType("requests"))
        sys.path.insert(0, str(ROOT / "scripts"))
        pytest.importorskip("bs4")
        import scrape_lcs as sl
        monkeypatch.setattr(sl, "get", lambda url: self.TABLE)
        monkeypatch.setattr(sl, "_HEADER_LABELS_REPORTED", False)
        return sl, io

    def run(self, sl, io, tournament="FAKE 2026 Summer"):
        err = io.StringIO()
        real, sys.stderr = sys.stderr, err
        try:
            players = sl.parse_player_list(tournament)
        finally:
            sys.stderr = real
        return players, err.getvalue()

    def test_the_five_columns_still_parse(self, scraper):
        sl, io = scraper
        players, _ = self.run(sl, io)
        assert "Faker" in players

    def test_it_names_the_columns_it_does_not_read(self, scraper):
        """The useful half. A full list is noise; the difference is the
        thing worth looking at."""
        sl, io = scraper
        _, out = self.run(sl, io)
        assert "[columns] not read:" in out
        for offered in ("DPM", "CSM", "FB %", "VSPM", "Solo kills"):
            assert offered in out, f"{offered} was offered and not reported"
        # and the ones it DOES read must not be listed as unread
        unread = out.split("not read:", 1)[1]
        for read in ("Avg kills", "Avg deaths", "KP%"):
            assert read not in unread

    def test_it_reports_once_across_tournaments(self, scraper):
        """Seven regions times two tournaments would be fourteen copies
        of the same line."""
        sl, io = scraper
        _, first = self.run(sl, io, "FAKE 2026 Summer")
        _, second = self.run(sl, io, "FAKE 2026 Spring")
        assert "[columns]" in first
        assert "[columns]" not in second

    def test_a_table_with_no_header_says_nothing_rather_than_crashing(self, scraper):
        sl, io = scraper
        sl.get = lambda url: "<table class='table_list'></table>"
        players, out = self.run(sl, io)
        assert players == {} and "[columns]" not in out

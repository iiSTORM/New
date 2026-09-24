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

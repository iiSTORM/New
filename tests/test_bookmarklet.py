"""The bookmarklet page and the script it installs must be the same thing.

The page carries the script twice: once percent-encoded into the href you
install, once in plain text for you to read first. If those ever drift,
the page shows you one program and installs another -- which is the only
way a tool like this could genuinely mislead someone, so it is generated
from a single file and checked here rather than trusted.
"""
import html
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "tools" / "bookmarklet.js"
HTML = ROOT / "tools" / "bookmarklet.html"

pytestmark = pytest.mark.skipif(not JS.exists(), reason="bookmarklet not present")


@pytest.fixture(scope="module")
def page():
    return HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def source():
    return JS.read_text(encoding="utf-8")


def href_of(page):
    m = re.search(r'class="drag" href="([^"]+)"', page)
    assert m, "no bookmarklet link in the page"
    return html.unescape(m.group(1))


class TestTheLinkIsTheScript:
    def test_the_href_decodes_to_exactly_the_source(self, page, source):
        href = href_of(page)
        assert href.startswith("javascript:")
        assert unquote(href[len("javascript:"):]) == source

    def test_the_source_shown_is_the_source_installed(self, page, source):
        shown = html.unescape(re.search(r"<pre>(.*?)</pre>", page, re.S).group(1))
        assert shown == source

    def test_nothing_in_the_href_can_truncate_it(self, page):
        """A raw ?, & or # would cut the URL short or start a fragment,
        and the bookmarklet would install as a fraction of itself --
        still valid JavaScript, quite possibly, and wrong."""
        body = href_of(page)[len("javascript:"):]
        assert not any(c in body for c in '?&=# "\'<>')


class TestGeneratedFileIsCurrent:
    def test_check_mode_passes_against_the_committed_page(self):
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "build_bookmarklet.py"),
                            "--check"], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr or r.stdout

    def test_check_mode_fails_when_the_page_is_stale(self, tmp_path, monkeypatch):
        """Guards the guard: a --check that cannot fail protects nothing."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "bb", ROOT / "tools" / "build_bookmarklet.py")
        bb = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bb)
        monkeypatch.setattr(bb, "SRC", tmp_path / "bookmarklet.js")
        monkeypatch.setattr(bb, "OUT", tmp_path / "bookmarklet.html")
        bb.SRC.write_text("alert(1);", encoding="utf-8")
        assert bb.build() == 0
        assert bb.build(check=True) == 0
        bb.SRC.write_text("alert(2);", encoding="utf-8")
        assert bb.build(check=True) == 1, "a changed script must fail the check"


class TestWhatTheScriptDoes:
    def test_it_saves_under_the_name_the_refresh_script_looks_for(self, source):
        """The two halves are wired by a filename and nothing else."""
        assert 'download = "prizepicks-payload.json"' in source
        sh = (ROOT / "scripts" / "refresh_props.sh").read_text(encoding="utf-8")
        assert "prizepicks-payload.json" in sh

    def test_it_does_not_pretend_to_be_something_else(self, source):
        """No spoofed identity. It is the session's own request, made
        because someone clicked; the moment it starts dressing itself up
        it is a different tool with a different justification."""
        lowered = source.lower()
        for forbidden in ("user-agent", "useragent", "navigator.webdriver",
                          "headless", "x-forwarded"):
            assert forbidden not in lowered

    def test_it_reports_an_http_failure_rather_than_saving_the_error(self, source):
        assert "res.ok" in source and "res.status" in source

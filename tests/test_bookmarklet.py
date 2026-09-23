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
        # render() quotes the endpoint into the instructions, so a
        # source without one is not a page it can build.
        bb.SRC.write_text('const ENDPOINT = "https://example.test/a";\nalert(1);',
                          encoding="utf-8")
        assert bb.build() == 0
        assert bb.build(check=True) == 0
        bb.SRC.write_text('const ENDPOINT = "https://example.test/a";\nalert(2);',
                          encoding="utf-8")
        assert bb.build(check=True) == 1, "a changed script must fail the check"


class TestWhatTheScriptDoes:
    def test_it_saves_under_the_name_the_refresh_script_looks_for(self, source):
        """The two halves are wired by a filename and nothing else."""
        assert 'OUT_NAME = "prizepicks-payload.json"' in source
        assert "a.download = OUT_NAME" in source
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

    @staticmethod
    def code_only(source):
        """The script with its comments removed.

        Needed because the comments explain at length that this makes no
        request, and the word they use for that is "fetch()". Checking
        the raw text found the explanation and called it the thing it was
        explaining.

        Block comments, then whole-line "//" ones. Deliberately not
        stripping "//" wherever it appears: the endpoint is an https URL
        and a blunter rule would cut it in half.
        """
        no_blocks = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        return "\n".join(l for l in no_blocks.splitlines()
                          if not l.lstrip().startswith("//"))

    def test_the_comment_stripper_keeps_the_code(self, source):
        """Guards the guard: a stripper that ate everything would make
        every assertion below pass against nothing."""
        code = self.code_only(source)
        assert "prizepicks-payload.json" in code
        assert "https://api.prizepicks.com" in code, "an https URL survived intact"
        assert "makes no request" not in code, "prose should be gone"

    def test_it_asks_for_nothing(self, source):
        """The point of the rewrite. Fetching the endpoint from the app's
        page was answered 403 -- a cross-origin fetch carries an Origin
        header and goes through CORS, which is not what opening the URL
        does. Reading the document already on screen has nothing to
        refuse, and re-introducing a request would quietly bring the 403
        back."""
        code = self.code_only(source)
        for forbidden in ("fetch(", "XMLHttpRequest", "sendBeacon",
                          "credentials", "import("):
            assert forbidden not in code, f"{forbidden} is a request"

    def test_it_refuses_a_page_that_is_not_the_board(self, source):
        """Saving the wrong page's JSON under the right name sends
        scrape_props.py off to report zero matches, which reads like a
        matching bug rather than the wrong file."""
        assert "JSON.parse(text)" in source
        assert 'Array.isArray(payload && payload.data)' in source

    def test_it_tells_firefox_users_what_to_do(self, source):
        """Firefox's viewer innerText is the pretty tree, not the source,
        so it would save something that is not the payload."""
        assert "Raw Data" in source


class TestWhereTheRefreshScriptLooks:
    """A saved board has to be findable from the machine running the
    script, which is not always the machine that downloaded it.

    In a codespace or a remote VS Code window the browser saves to your
    laptop while refresh_props.sh runs in the cloud, so $HOME/Downloads
    there is a different filesystem and is simply empty. That is not a
    hypothetical -- it is how this was first reported.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def sh(cls):
        return (ROOT / "scripts" / "refresh_props.sh").read_text(encoding="utf-8")

    def test_the_repo_root_is_searched_as_well_as_downloads(self, sh):
        assert '$HOME/Downloads:$REPO' in sh, \
            "a file dragged into the editor's file explorer must be found"

    def test_the_search_list_is_split_rather_than_used_whole(self, sh):
        """A colon-joined default used as one path would match nothing
        and quietly fall through to the live fetch, which is exactly the
        403 this route exists to avoid."""
        assert 'IFS=":"' in sh

    def test_a_dropped_payload_cannot_be_committed(self):
        """The repo root being a drop location makes this the difference
        between a convenience and a board committed by a stray git add."""
        ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "prizepicks-payload.json" in ignore

    def test_the_name_it_looks_for_is_the_name_the_script_saves(self, sh, source):
        assert 'PAYLOAD_NAME="${PROPS_PAYLOAD_NAME:-prizepicks-payload.json}"' in sh
        assert 'OUT_NAME = "prizepicks-payload.json"' in source

    def test_staleness_is_bounded_by_the_window_the_app_enforces(self, sh):
        """90 minutes is not a preference. src/app.jsx greys out a line
        older than that, so a download past it is not worth committing."""
        assert 'PAYLOAD_MAX_AGE_MIN="${PROPS_PAYLOAD_MAX_AGE_MIN:-90}"' in sh
        app = (ROOT / "src" / "app.jsx").read_text(encoding="utf-8")
        assert "const PROPS_MAX_AGE_MINUTES = 90;" in app

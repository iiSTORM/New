#!/usr/bin/env python3
"""Build tools/bookmarklet.html from tools/bookmarklet.js.

Generated rather than hand-written because the page has to carry the
script twice -- once as the href you install, once as the source you read
before installing it -- and two hand-maintained copies of one thing is
the failure this repo keeps running into. Here it would be worse than
usual: the copy you audit would stop being the copy you run.

The script is percent-encoded whole, newlines and all, with no
minification. Stripping comments to shorten the URL risks changing the
code's meaning for the sake of a length nobody sees, and a `//` comment
whose newline went missing silently eats the rest of the line.

    python tools/build_bookmarklet.py
"""
import html
import sys
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
SRC = HERE / "bookmarklet.js"
OUT = HERE / "bookmarklet.html"

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Save PrizePicks Board</title>
<style>
  :root {{
    --bg: #faf9f7; --card: #ffffff; --ink: #1a1a1a; --dim: #5c5c5c;
    --line: #e2dfda; --accent: #123f2b; --accent-ink: #ffffff; --code: #f4f2ee;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg: #14161a; --card: #1c1f24; --ink: #ececec; --dim: #a0a0a0;
      --line: #2e333a; --accent: #4ade9b; --accent-ink: #0b1f16; --code: #101317;
    }}
  }}
  :root[data-theme="dark"] {{
    --bg: #14161a; --card: #1c1f24; --ink: #ececec; --dim: #a0a0a0;
    --line: #2e333a; --accent: #4ade9b; --accent-ink: #0b1f16; --code: #101317;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--ink);
    font: 16px/1.65 system-ui, -apple-system, "Segoe UI", sans-serif;
    padding: 40px 16px 80px;
  }}
  main {{ max-width: 720px; margin: 0 auto; }}
  h1 {{ font-size: 1.6rem; margin: 0 0 6px; letter-spacing: -0.02em; }}
  .sub {{ color: var(--dim); margin: 0 0 28px; }}
  .card {{
    background: var(--card); border: 1px solid var(--line);
    border-radius: 12px; padding: 22px; margin-bottom: 20px;
  }}
  .drag {{
    display: inline-block; background: var(--accent); color: var(--accent-ink);
    padding: 13px 22px; border-radius: 9px; font-weight: 650;
    text-decoration: none; cursor: grab; user-select: none;
  }}
  .drag:active {{ cursor: grabbing; }}
  ol {{ padding-left: 20px; }}
  li {{ margin: 9px 0; }}
  code {{
    background: var(--code); padding: 2px 6px; border-radius: 5px;
    font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
  }}
  pre {{
    background: var(--code); border: 1px solid var(--line); border-radius: 9px;
    padding: 16px; overflow-x: auto;
    font: 12.5px/1.6 ui-monospace, SFMono-Regular, Menlo, monospace;
  }}
  .note {{ color: var(--dim); font-size: 14px; }}
  h2 {{ font-size: 1.05rem; margin: 0 0 10px; }}
</style>
</head>
<body>
<main>
  <h1>Save PrizePicks Board</h1>
  <p class="sub">One click, in your own logged-in browser, to the file the projector reads.</p>

  <div class="card">
    <h2>Install</h2>
    <p class="note">Drag this to your bookmarks bar. Clicking it here will not work &mdash;
       a bookmarklet has to run on the PrizePicks tab, not on this page.</p>
    <p><a class="drag" href="{href}">Save PrizePicks Board</a></p>
  </div>

  <div class="card">
    <h2>Use</h2>
    <ol>
      <li>Open <code>app.prizepicks.com</code> and make sure you are logged in.</li>
      <li>Click the bookmarklet. A banner says how many projections it saved.</li>
      <li><code>prizepicks-payload.json</code> lands in your Downloads folder.</li>
      <li>Run <code>./scripts/refresh_props.sh</code>. It finds that file, checks it is
          recent, runs the matcher and commits <code>props.json</code>.</li>
    </ol>
    <p class="note">The app refuses to compute an edge against a line older than 90
       minutes, so the refresh script will not accept a stale download either &mdash;
       it tells you to click again rather than publishing lines that look fresher
       than they are.</p>
  </div>

  <div class="card">
    <h2>What it does</h2>
    <p class="note">It makes the same request the site makes, from the session you
       are already in, and saves the answer. It does not disguise itself, and it does
       nothing the page could not do on its own. The whole source is below &mdash; it is
       the same text encoded into the link above, generated from one file so the code
       you read is the code you install.</p>
    <pre>{source}</pre>
  </div>
</main>
</body>
</html>
"""


def render():
    """The page that bookmarklet.js implies, as text."""
    source = SRC.read_text(encoding="utf-8")
    # safe="" encodes the forward slash too. quote() already encodes
    # ? & = # + by default, so this is not what stops the URL being
    # truncated -- that is the default's doing, and the test asserts it
    # rather than this argument. What safe="" adds is only that no "/"
    # survives raw, which keeps the href a single opaque token with no
    # path-looking structure in it. Small, but free.
    href = "javascript:" + quote(source, safe="")
    return PAGE.format(href=html.escape(href, quote=True), source=html.escape(source))


def build(check=False):
    if not SRC.exists():
        print(f"! {SRC} is missing", file=sys.stderr)
        return 1
    want = render()
    if check:
        # Same contract as `npm run check` for index.html: a generated
        # file committed out of date is a page that shows one script and
        # installs another, which is the one way this could mislead.
        have = OUT.read_text(encoding="utf-8") if OUT.exists() else None
        if have != want:
            print(f"! {OUT.name} is out of date with {SRC.name}. "
                  f"Run: python tools/build_bookmarklet.py", file=sys.stderr)
            return 1
        print(f"{OUT.name} is up to date with {SRC.name}")
        return 0
    OUT.write_text(want, encoding="utf-8")
    try:
        where = OUT.relative_to(HERE.parent)
    except ValueError:
        where = OUT          # written somewhere outside the repo, e.g. by a test
    print(f"Wrote {where} ({len(SRC.read_text(encoding='utf-8'))} bytes of script, "
          f"{len(want)} bytes of page)")
    return 0


if __name__ == "__main__":
    sys.exit(build(check="--check" in sys.argv[1:]))

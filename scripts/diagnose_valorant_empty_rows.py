#!/usr/bin/env python3
"""
Diagnoses the real data loss in scrape_valorant.py where some matches
extract ZERO player rows.

Observed in a live run (VCT China, event 2978):
    match 701044: 30 player links found but extracted 0 rows
                  (20 unresolved, 10 skipped as 'all maps')
    match 701046: 30 links, 0 rows
    match 701043: 40 links, 0 rows
    ... 10 matches total extracted 0 player rows this run

The "skipped as 'all maps'" counts look correct (10 each = one combined
totals view). The problem is the remaining rows: on a WORKING match
(701038) 10 resolve fine, but on these the real per-map rows all land in
`unresolved`, meaning one of the DOM lookups in parse_match_players
failed -- name_div/tag_div, the ovw-row parent, or find_game_id_container.

This prints the ACTUAL raw HTML around a failing row and compares it to
a working match, because the useful detail here is exact tag/class
structure and that is precisely what gets destroyed by any
markdown-converting fetch. Guessing at markup without looking has
already cost this project a wasted implementation round once.

Usage:
    python scripts/diagnose_valorant_empty_rows.py
    python scripts/diagnose_valorant_empty_rows.py 701044 701038
"""
import sys

import re

import requests
from bs4 import BeautifulSoup

BASE = "https://www.vlr.gg"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}

# Default: a known-failing match and a known-working one, so the output
# is a direct structural comparison rather than an isolated dump.
DEFAULT_MATCHES = [("701044", "FAILING - 30 links, 0 rows extracted"),
                   ("701038", "WORKING - 10 rows extracted OK")]


def inspect(match_id, label):
    url = f"{BASE}/{match_id}"
    print("=" * 72)
    print(f"match {match_id}  [{label}]")
    print(f"  {url}")
    print("=" * 72)
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
    except Exception as e:
        print(f"  ! fetch failed: {type(e).__name__}: {e}\n")
        return
    if r.status_code != 200:
        print(f"  ! status {r.status_code}\n")
        return

    soup = BeautifulSoup(r.text, "html.parser")

    player_links = soup.find_all("a", href=lambda h: h and "/player/" in h)
    print(f"  player links found: {len(player_links)}")

    # Mirror parse_match_players' own lookups so the failure point is
    # identified precisely rather than inferred.
    # Exactly the regex scrape_valorant.py uses, copied rather than
    # imported so this stays runnable standalone.
    kda_re = re.compile(r"(\d+)\s+\d+\s+\d+\s*/\s*(\d+)\s+\d+\s+\d+\s*/\s*(\d+)\s+\d+\s+\d+")
    counts = {"no name/tag div": 0, "no ovw-row parent": 0, "no game-id container": 0,
              "resolvable": 0, "KDA REGEX FAILED": 0, "fully parseable": 0}
    first_failure = None
    regex_failures = []
    for link in player_links:
        name_div = link.find(class_="ovw-player-name")
        tag_div = link.find(class_="ovw-player-tag")
        if not name_div or not tag_div:
            counts["no name/tag div"] += 1
            if first_failure is None:
                first_failure = ("no name/tag div", link)
            continue
        row = link.find_parent("div", class_="ovw-row")
        if row is None:
            counts["no ovw-row parent"] += 1
            if first_failure is None:
                first_failure = ("no ovw-row parent", link)
            continue
        # scrape_valorant.py's find_game_id_container walks up at most 20
        # ancestors. Mirroring that cap exactly matters: if the attribute
        # is present but sits DEEPER than 20 levels, an unbounded search
        # would call it resolvable while the real scraper gives up — and
        # that difference would itself be the bug.
        depth, n, wrapper = 0, row, None
        while depth < 20:
            n = n.parent
            depth += 1
            if n is None:
                break
            if hasattr(n, "get") and n.get("data-game-id") is not None:
                wrapper = n
                break
        if wrapper is None:
            counts["no game-id container"] += 1
            # How deep would it have been with no cap? If this reports a
            # number > 20, the cap is the root cause.
            unbounded = row.find_parent(attrs={"data-game-id": True})
            if unbounded is not None:
                d, m = 0, row
                while m is not None and m is not unbounded:
                    m = m.parent
                    d += 1
                counts.setdefault("_found_beyond_cap_at_depth", d)
            if first_failure is None:
                first_failure = ("no game-id container", row)
            continue
        counts["resolvable"] += 1
        # The lookup the first version of this diagnostic MISSED. All the
        # DOM lookups above passed on both a failing and a working match
        # (30/30 resolvable each), which proved the structure is fine and
        # pointed here instead: scrape_valorant.py also requires this
        # regex to match the row's text, and counts the row as
        # `unresolved` when it doesn't.
        #
        # It expects vlr.gg's all/attack/defense triple for each stat --
        # "K K K / D D D / A A A". A row showing only combined numbers
        # (no atk/def split) will not match and is dropped silently.
        row_text = row.get_text(" ", strip=True)
        if not kda_re.search(row_text):
            counts["KDA REGEX FAILED"] += 1
            if len(regex_failures) < 4:
                regex_failures.append(row_text)
        else:
            counts["fully parseable"] += 1

    print("  breakdown of why each link resolves or not:")
    for k, v in counts.items():
        print(f"    {k:<24} {v}")

    if regex_failures:
        print(f"\n  --- RAW TEXT of rows where the KDA regex FAILED (first {len(regex_failures)}) ---")
        for i, t in enumerate(regex_failures, 1):
            print(f"    [{i}] {t[:240]}")
        print("    ^ compare against a passing row's shape to see what the real format is.")

    if first_failure:
        reason, node = first_failure
        print(f"\n  --- RAW HTML of the first link failing on '{reason}' ---")
        snippet = str(node)
        print(snippet[:1200])
        parent = node.parent
        if parent is not None:
            print(f"\n  --- its PARENT tag: <{parent.name} class={parent.get('class')}> ---")
            print(str(parent)[:900])

    # What data-game-id values exist at all? If the attribute name or
    # nesting depth changed on these pages, this is where it shows.
    wrappers = soup.find_all(attrs={"data-game-id": True})
    ids = [w.get("data-game-id") for w in wrappers]
    print(f"\n  elements carrying data-game-id: {len(wrappers)}  ids={ids[:12]}")
    if wrappers:
        w = wrappers[0]
        print(f"  first such element: <{w.name} class={w.get('class')}>")
    print()


def main():
    targets = sys.argv[1:]
    pairs = [(m, "user-specified") for m in targets] if targets else DEFAULT_MATCHES
    for match_id, label in pairs:
        inspect(match_id, label)
    print("What to look for: compare the FAILING match's breakdown against the WORKING one.")
    print("Whichever counter is non-zero on the failing match names the exact lookup that")
    print("broke, and the raw HTML beneath it shows what the real structure is now.")


if __name__ == "__main__":
    main()
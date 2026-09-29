#!/usr/bin/env python3
"""
Why does a FINISHED vlr.gg match page yield no odds?

The first scrape with scripts/map_context.py wired in put a veto and a stage
on all 254 past Valorant matches and odds on none of them, while every one of
the 8 upcoming matches got its odds. So the parser is right about the upcoming
layout and wrong about the finished one -- and probe run 1 hinted at why: the
finished pages' bet items read "$100 on FUT Esports returned $127 at pre-match
odds 1.27", which is a settled-bet display, not the two-half team-and-price
block the upcoming pages use.

The question this answers is whether a finished page carries BOTH sides or
only the winner's. Both, and it backfills. Only the winner's, and a devig is
impossible without assuming a margin, which is a different and weaker thing.
"""
import re
import sys
import urllib.request

VLR = "https://www.vlr.gg"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"}


def get(path):
    req = urllib.request.Request(f"{VLR}{path}", headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"  ! {path}: {e}", file=sys.stderr)
        return None, ""


def main():
    code, html = get("/matches/results")
    paths = re.findall(r'href="(/\d+/[a-z0-9\-]+)"', html or "")
    print(f"results page -> {code}, {len(paths)} links")
    for path in paths[:2]:
        code, page = get(path)
        print(f"\n=== {path} -> {code} ===")
        anchors = re.findall(
            r'<a\b[^>]*class="[^"]*\bmatch-bet-item\b[^"]*"[^>]*>(.*?)</a>',
            page, re.S | re.I)
        print(f"  {len(anchors)} match-bet-item anchors")
        for index, anchor in enumerate(anchors[:2]):
            print(f"\n  --- anchor {index}, {len(anchor)} chars ---")
            print("  " + anchor.strip()[:2200].replace("\n", "\n  "))
        # Counted rather than shown, so a page with the spans in a layout the
        # parser does not walk is distinguishable from one without them.
        for cls in ("match-bet-item-team-name", "match-bet-item-odds",
                    "match-bet-item-half", "match-bet-item-note"):
            print(f"  {cls}: {page.count(cls)} occurrences")


if __name__ == "__main__":
    main()

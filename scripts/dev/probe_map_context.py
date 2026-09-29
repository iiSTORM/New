#!/usr/bin/env python3
"""
Finds out what match context bo3.gg and vlr.gg actually publish, so a
scraper can be written against the real payload rather than a guess.

Four things are on the list of features the model does not have and the
market might: per-match odds, the map pool and veto, LAN-vs-online, and
tournament stage. Three of them live on pages the existing scrapers
already visit. None of them is in cs2_data.json or valorant_data.json
today, and neither file records a map name at all.

bo3.gg's /matches endpoint takes `with=` expansions. scrape_cs2.py
already asks for teams, tournament, ai_predictions, games, streams and
match_maps; a comment in it records that `bet_updates` was seen on 23 of
29 upcoming matches, which is the only evidence anywhere in the repo that
odds are reachable. That comment says nothing about the SHAPE of the
field, which is what a scraper needs.

So this prints, for a handful of real matches:
  - every top-level key on a match record, with the type and a truncated
    sample of each expansion asked for;
  - whatever sits under bet_updates / match_maps / games, fully, for one
    match, since that is the part a parser has to walk;
  - whether a vlr.gg match page carries the odds and veto markup, tested
    by candidate selector rather than assumed.

Read-only, no logged-in pages, nothing placed. Run it from Actions
(.github/workflows/probe.yml) -- the dev container's network policy
refuses both hosts.
"""
import json
import re
import sys
import urllib.request

BO3 = "https://api.bo3.gg/api/v1"
VLR = "https://www.vlr.gg"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json,text/html",
}
TRUNC = 400


def get(url, params=None):
    if params:
        from urllib.parse import urlencode
        url = f"{url}?{urlencode(params)}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, body
    except Exception as e:
        print(f"  ! GET {url} failed: {e}", file=sys.stderr)
        return None, ""


def get_json(path, params=None):
    status, body = get(f"{BO3}{path}", params)
    if status != 200:
        print(f"  ! {path} -> {status}")
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        print(f"  ! {path} -> not JSON ({e})")
        return None


def brief(value):
    text = json.dumps(value, default=str)
    return text if len(text) <= TRUNC else text[:TRUNC] + f"... (+{len(text) - TRUNC} chars)"


def describe_keys(record, label):
    print(f"\n--- {label}: {len(record)} top-level keys ---")
    for key in sorted(record):
        value = record[key]
        kind = type(value).__name__
        if isinstance(value, list):
            kind = f"list[{len(value)}]"
        elif isinstance(value, dict):
            kind = f"dict{sorted(value)[:8]}"
        print(f"  {key:<28} {kind:<40} {brief(value) if not isinstance(value, (dict, list)) else ''}")


def probe_bo3():
    print("=" * 72)
    print("bo3.gg /matches -- which expansions come back, and in what shape")
    print("=" * 72)

    # Every expansion scrape_cs2.py knows about, plus the two this probe
    # exists for. Asked for together first: if the API rejects an unknown
    # name outright we learn that immediately rather than per-field.
    wanted = "teams,tournament,games,match_maps,bet_updates,stage,ai_predictions"
    for status, label in (("finished", "recent finished matches"),
                          ("upcoming", "upcoming matches")):
        data = get_json("/matches", {
            "scope": "widget-matches",
            "page[offset]": "0",
            "page[limit]": "5",
            "sort": "-start_date" if status == "finished" else "start_date",
            "filter[matches.status][in]": status,
            "with": wanted,
        })
        rows = (data or {}).get("results") if isinstance(data, dict) else data
        if not rows:
            print(f"\n  no rows for {label}")
            continue
        print(f"\n### {label}: {len(rows)} rows")
        describe_keys(rows[0], f"{label}[0] ({rows[0].get('slug')})")

        # Coverage matters more than one sample: a field present on one
        # match and absent on the rest is not something to build on.
        for field in ("bet_updates", "match_maps", "games", "stage",
                      "tournament", "ai_predictions"):
            present = sum(1 for r in rows if r.get(field))
            print(f"  coverage {field:<16} {present}/{len(rows)}")

        for field in ("bet_updates", "match_maps"):
            sample = next((r[field] for r in rows if r.get(field)), None)
            if sample is not None:
                print(f"\n  FULL {field} for the first row that has it:")
                print("   ", json.dumps(sample, indent=2, default=str)[:3000])

        # A single match fetched on its own may carry more than the list
        # view does -- the list is a widget scope and widgets trim.
        slug = rows[0].get("slug")
        if slug:
            one = get_json(f"/matches/{slug}", {"with": wanted})
            record = (one or {}).get("results") if isinstance(one, dict) else one
            if isinstance(record, list):
                record = record[0] if record else None
            if isinstance(record, dict):
                describe_keys(record, f"/matches/{slug} (single fetch)")


def probe_vlr():
    print("\n" + "=" * 72)
    print("vlr.gg -- does a match page carry odds and the veto?")
    print("=" * 72)
    status, html = get(f"{VLR}/matches/results")
    if status != 200:
        print(f"  ! results page -> {status}")
        return
    paths = re.findall(r'href="(/\d+/[a-z0-9\-]+)"', html)
    if not paths:
        print("  ! no match links found on the results page")
        return
    print(f"  {len(paths)} match links found; probing the first 2")
    for path in paths[:2]:
        status, page = get(f"{VLR}{path}")
        print(f"\n### {path} -> {status}, {len(page)} bytes")
        if status != 200:
            continue
        # Candidate markers rather than one assumed selector: the point is
        # to learn which of these vlr.gg actually uses today.
        markers = {
            "match-bet-item": "odds block (bookmaker row)",
            "match-bet-item-odds": "odds number",
            "match-header-note": "veto / note line",
            "vm-stats-game-header": "per-map stats header",
            "map-name": "map name span",
            "match-header-vs-note": "LAN/online + stage note",
            "match-header-event-series": "event series (stage)",
        }
        for cls, what in markers.items():
            print(f"  {'YES' if cls in page else 'no ':<4} {cls:<28} {what}")
        for cls in ("match-header-note", "match-header-vs-note",
                    "match-header-event-series"):
            for match in re.finditer(
                    rf'class="[^"]*{cls}[^"]*"[^>]*>(.*?)</div>', page, re.S):
                text = re.sub(r"<[^>]+>", " ", match.group(1))
                text = " ".join(text.split())
                if text:
                    print(f"    {cls}: {text[:300]}")
                break
        for match in re.finditer(r'class="[^"]*match-bet-item[^"]*"[^>]*>(.*?)</a>',
                                 page, re.S):
            text = " ".join(re.sub(r"<[^>]+>", " ", match.group(1)).split())
            print(f"    match-bet-item: {text[:200]}")


if __name__ == "__main__":
    probe_bo3()
    probe_vlr()

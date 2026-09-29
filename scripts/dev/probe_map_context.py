#!/usr/bin/env python3
"""
Finds out what match context bo3.gg and vlr.gg actually publish, so a
scraper can be written against the real payload rather than a guess.

Four things are on the list of features the model does not have and the
market might: per-match odds, the map pool and veto, LAN-vs-online, and
tournament stage. Three of them live on pages the existing scrapers
already visit. None of them is in cs2_data.json or valorant_data.json
today, and neither file records a map name at all.

Run 1 of this probe settled the Valorant half: a vlr.gg match page
carries the odds module, the full veto line with map names and pick
order, and the event series. It also showed bo3.gg answering 422 to a
`with=` list containing every expansion at once, so the CS2 half is
re-done here one expansion at a time -- scrape_cs2.py is known to use
`teams,tournament,ai_predictions,games,streams` successfully, so at
least one of `bet_updates` and `stage` is the name it will not take.

Read-only, no logged-in pages, nothing placed. Run it from Actions
(.github/workflows/probe.yml) -- the dev container's network policy
refuses both hosts.
"""
import json
import re
import sys
import urllib.request
from urllib.parse import urlencode

BO3 = "https://api.bo3.gg/api/v1"
VLR = "https://www.vlr.gg"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json,text/html",
}

# Known-good from scrape_cs2.py, so a 422 on one of these would mean the
# API changed rather than that the name is wrong.
KNOWN_GOOD = ["teams", "tournament", "games", "match_maps", "ai_predictions",
              "streams"]
# The reason this probe exists.
CANDIDATES = ["bet_updates", "bets", "odds", "stage", "tournament_stage",
              "tournament_deep", "maps", "veto", "bans", "picks"]


def get(url, params=None):
    if params:
        url = f"{url}?{urlencode(params)}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        print(f"  ! GET {url} failed: {e}", file=sys.stderr)
        return None, ""


def bo3_matches(status, expansions, limit=5):
    params = {
        "scope": "widget-matches",
        "page[offset]": "0",
        "page[limit]": str(limit),
        "sort": "-start_date" if status == "finished" else "start_date",
        "filter[matches.status][in]": status,
    }
    if expansions:
        params["with"] = ",".join(expansions)
    code, body = get(f"{BO3}/matches", params)
    if code != 200:
        return code, None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return code, None
    rows = data.get("results") if isinstance(data, dict) else data
    return code, rows


def probe_bo3():
    print("=" * 72)
    print("bo3.gg -- which `with=` expansions the API accepts, one at a time")
    print("=" * 72)
    accepted = []
    for name in KNOWN_GOOD + CANDIDATES:
        code, rows = bo3_matches("finished", [name], limit=3)
        tag = "ok " if code == 200 else f"{code}"
        covered = ""
        if rows:
            present = sum(1 for r in rows if r.get(name) not in (None, [], {}))
            covered = f"  present on {present}/{len(rows)}"
            if present:
                accepted.append(name)
        print(f"  {tag:<5} with={name:<20}{covered}")

    print(f"\n  expansions that came back with content: {accepted or 'none'}")

    # Whatever survived, dumped in full for one match -- the shape is what
    # a parser needs, and coverage across a few rows is what decides
    # whether it is worth parsing at all.
    for name in accepted:
        if name in ("teams", "streams", "ai_predictions"):
            continue
        code, rows = bo3_matches("finished", [name], limit=10)
        sample = next((r[name] for r in (rows or []) if r.get(name)), None)
        present = sum(1 for r in (rows or []) if r.get(name) not in (None, [], {}))
        print(f"\n--- {name}: present on {present}/{len(rows or [])} finished ---")
        print(json.dumps(sample, indent=2, default=str)[:2500])

    # Odds are only useful if they exist BEFORE the match. A field that
    # only fills in afterwards backtests beautifully and is worthless.
    for name in accepted:
        code, rows = bo3_matches("upcoming", [name], limit=10)
        present = sum(1 for r in (rows or []) if r.get(name) not in (None, [], {}))
        print(f"  upcoming coverage {name:<18} {present}/{len(rows or [])}")

    # One match on its own, with everything that was accepted: the widget
    # scope trims, and a single fetch may carry more.
    code, rows = bo3_matches("finished", accepted, limit=1)
    print(f"\n  all accepted together -> HTTP {code}")
    if rows:
        print(f"  top-level keys: {sorted(rows[0])}")


MARKERS = {
    "match-bet-item": "odds block",
    "match-header-note": "veto line",
    "match-header-event-series": "stage",
    "vm-stats-game-header": "per-map header",
}


def text_of(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def probe_vlr_page(path, label):
    code, page = get(f"{VLR}{path}")
    print(f"\n### {label} {path} -> {code}, {len(page)} bytes")
    if code != 200:
        return
    for cls, what in MARKERS.items():
        print(f"  {'YES' if cls in page else 'no ':<4} {cls:<28} {what}")

    # The odds module in full: run 1 showed two bet items both naming the
    # same team, which is either two bookmakers on one side or a regex
    # that ran past its element. Printed raw so the parser can be written
    # against the real markup.
    block = re.search(r'(<div[^>]*class="[^"]*match-bet[^"]*".*?)(?=<div class="match-h)',
                      page, re.S)
    if block:
        print("  --- odds markup ---")
        print("  " + block.group(1)[:1800].replace("\n", "\n  "))

    for cls in ("match-header-note", "match-header-event-series",
                "match-header-vs-note"):
        found = re.search(rf'class="[^"]*{cls}[^"]*"[^>]*>(.*?)</div>', page, re.S)
        if found:
            print(f"  {cls}: {text_of(found.group(1))[:300]}")

    # Map names per game, which is what the veto line has to be checked
    # against and what a per-map feature would key on.
    for found in re.finditer(r'class="[^"]*map[^"]*"[^>]*>\s*<div[^>]*>(.*?)</div>',
                             page, re.S):
        got = text_of(found.group(1))
        if got and len(got) < 60:
            print(f"  map cell: {got}")


def probe_vlr():
    print("\n" + "=" * 72)
    print("vlr.gg -- the odds module, the veto, and whether they exist pre-match")
    print("=" * 72)
    for listing, label in (("/matches/results", "finished"), ("/matches", "upcoming")):
        code, html = get(f"{VLR}{listing}")
        if code != 200:
            print(f"  ! {listing} -> {code}")
            continue
        paths = re.findall(r'href="(/\d+/[a-z0-9\-]+)"', html)
        print(f"\n{label}: {len(paths)} match links")
        for path in paths[:2]:
            probe_vlr_page(path, label)


if __name__ == "__main__":
    probe_bo3()
    probe_vlr()

#!/usr/bin/env python3
"""
What does bo3.gg publish about odds, maps and stage, and in what shape?

The companion probe (probe_map_context.py) settled the Valorant half from
vlr.gg and is dominated by HTML dumps, which pushed the bo3 half out of
the job log's tail -- GitHub's job-log API returns the END of a log. So
the CS2 half lives here on its own and prints nothing but a summary.

scrape_cs2.py asks for `with=teams,tournament,ai_predictions,games,
streams` and succeeds, and a comment in it records bet_updates being seen
on 23 of 29 upcoming matches, which is the only evidence anywhere that
odds are reachable. What that field CONTAINS has never been written down.

Run from Actions (.github/workflows/probe.yml).
"""
import json
import sys
import urllib.error
import urllib.request
from urllib.parse import urlencode

BASE = "https://api.bo3.gg/api/v1"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}
KNOWN_GOOD = ["teams", "tournament", "games", "streams", "ai_predictions"]
CANDIDATES = ["match_maps", "bet_updates", "bets", "odds", "stage",
              "tournament_stage", "maps", "veto", "bans", "picks", "bo3_odds"]


def fetch(status, expansions, limit=5):
    params = {
        "scope": "widget-matches",
        "page[offset]": "0",
        "page[limit]": str(limit),
        "sort": "-start_date" if status == "finished" else "start_date",
        "filter[matches.status][in]": status,
    }
    if expansions:
        params["with"] = ",".join(expansions)
    url = f"{BASE}/matches?{urlencode(params)}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        print(f"  ! {e}", file=sys.stderr)
        return None, None
    rows = data.get("results") if isinstance(data, dict) else data
    return 200, rows


def shape(value, depth=0):
    """A type sketch, not the data -- a hundred odds rows tell you nothing
    that the shape of one does not."""
    pad = "  " * depth
    if isinstance(value, dict):
        lines = [f"{pad}{{"]
        for key in sorted(value)[:20]:
            inner = value[key]
            if isinstance(inner, (dict, list)):
                lines.append(f"{pad}  {key}:")
                lines.append(shape(inner, depth + 2))
            else:
                lines.append(f"{pad}  {key}: {type(inner).__name__} = "
                             f"{json.dumps(inner, default=str)[:80]}")
        return "\n".join(lines + [f"{pad}}}"])
    if isinstance(value, list):
        if not value:
            return f"{pad}[] (empty)"
        return f"{pad}[{len(value)} items, first:]\n" + shape(value[0], depth + 1)
    return f"{pad}{type(value).__name__} = {json.dumps(value, default=str)[:80]}"


def main():
    print("bo3.gg: which `with=` expansions are accepted, and which carry data")
    print("-" * 68)
    accepted = []
    for name in KNOWN_GOOD + CANDIDATES:
        code, rows = fetch("finished", [name], limit=5)
        if code != 200:
            print(f"  HTTP {code:<5} with={name}")
            continue
        present = sum(1 for r in (rows or []) if r.get(name) not in (None, [], {}))
        print(f"  ok        with={name:<20} content on {present}/{len(rows or [])}")
        if present:
            accepted.append(name)

    # Everything a match record carries with no expansion at all: a field
    # that is already there needs no `with=` and no second request.
    code, rows = fetch("finished", [], limit=1)
    if rows:
        print(f"\nbare match record, {len(rows[0])} keys:")
        print("  " + ", ".join(sorted(rows[0])))

    for name in accepted:
        if name in KNOWN_GOOD:
            continue
        code, rows = fetch("finished", [name], limit=10)
        sample = next((r[name] for r in (rows or []) if r.get(name)), None)
        done = sum(1 for r in (rows or []) if r.get(name) not in (None, [], {}))
        code, up = fetch("upcoming", [name], limit=10)
        soon = sum(1 for r in (up or []) if r.get(name) not in (None, [], {}))
        print(f"\n=== {name}: {done}/{len(rows or [])} finished, "
              f"{soon}/{len(up or [])} upcoming ===")
        print(shape(sample))
        # Odds that only appear once a match is over backtest beautifully
        # and are worth nothing, so the upcoming sample matters more.
        up_sample = next((r[name] for r in (up or []) if r.get(name)), None)
        if up_sample is not None and soon:
            print(f"  -- upcoming sample --")
            print(shape(up_sample))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fetches live player prop lines and matches them onto this app's players.

Writes props.json: {fetched_at, source, props: {game: {player: [...]}}, ...}.

WHY THIS IS A SEPARATE SCRIPT AND A SEPARATE SCHEDULE
-----------------------------------------------------
Everything else here regenerates twice a day, which is fine for season
stats. Lines are not season stats — they move continuously and are pulled
when news breaks. A twelve-hour-old line is not merely stale, it is
misleading in the one direction that costs money: it looks actionable. So
props run on their own cadence, carry their own fetched_at, and the app
refuses to show an edge once they age out (see PROPS_MAX_AGE_MINUTES in
the frontend).

PROVIDERS
---------
The fetch is one function returning a list of raw dicts, so a different
source is an adapter rather than a rewrite. PrizePicks is implemented
because it covers LoL, CS2 and Valorant, which licensed odds APIs largely
do not — The Odds API, for instance, has no esports player props at all.

Its projections endpoint is not a documented public API, and it REFUSES
requests from a GitHub runner: HTTP 403, datacenter IP plus a non-browser
client. That was measured, not assumed. Defeating it would mean
impersonating a browser to get past a control that exists on purpose, so
this does not try.

What does work:

  - Run this locally, from your own connection, where you are an ordinary
    logged-in customer, and commit props.json. See the README.
  - Point PROVIDERS at a source with a real server-side API. That is the
    only route that makes the hourly workflow viable, and it is why the
    fetch is a single swappable function.

If this app is ever served to other people rather than used personally,
which source the lines come from is worth more thought than it needs now.

    python scripts/scrape_props.py                  # all configured leagues
    python scripts/scrape_props.py --dry-run        # fetch and report, write nothing
    python scripts/scrape_props.py --fixture f.json # parse a saved payload offline
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from props_match import build_roster_index, match_props

OUTPUT_PATH = "props.json"

# Which app game each configured league feeds, and the roster file to match
# against. League ids are configurable rather than hardcoded guesses: they
# are provider-side identifiers that change, and a wrong one fails silently
# as "no props today", which is indistinguishable from a quiet evening.
GAMES = {
    "lol": {"data": "data.json", "prizepicks_league": "League of Legends"},
    "cs2": {"data": "cs2_data.json", "prizepicks_league": "CS2"},
    "valorant": {"data": "valorant_data.json", "prizepicks_league": "VALORANT"},
}

PRIZEPICKS_URL = "https://api.prizepicks.com/projections"
HEADERS = {
    "User-Agent": "esports-kill-projector/1.0 (personal projection tool)",
    "Accept": "application/json",
}
REQUEST_PAUSE_SECONDS = 2.0  # polite; this runs unattended on a schedule


def fetch_prizepicks_payload(session):
    """One request for every posted projection.

    Deliberately fetched once and filtered per game afterwards rather than
    once per league: the endpoint returns everything anyway, so three
    requests would be three times the load on someone else's server for
    exactly the same bytes.
    """
    params = {"per_page": 250, "single_stat": "true"}
    try:
        resp = session.get(PRIZEPICKS_URL, params=params, headers=HEADERS, timeout=25)
    except requests.RequestException as exc:
        print(f"  ! prizepicks request failed: {exc}", file=sys.stderr)
        return None
    if resp.status_code != 200:
        if resp.status_code == 403:
            print("  ! prizepicks returned HTTP 403. This is what bot protection "
                  "looks like from a datacenter IP: the same request from a "
                  "normal home connection generally succeeds. Run this script "
                  "locally and commit props.json, or configure a provider that "
                  "permits server-side access (see PROVIDERS).", file=sys.stderr)
        else:
            print(f"  ! prizepicks returned HTTP {resp.status_code}", file=sys.stderr)
        return None
    try:
        return resp.json()
    except ValueError:
        print("  ! prizepicks response was not JSON", file=sys.stderr)
        return None


def parse_prizepicks(payload, league_name):
    """Pull (player, stat label, line) out of a JSON:API payload.

    Separate from the request so it can be run against a saved fixture,
    which is the only way to check the parser without hitting the provider.
    """
    players = {}
    for item in payload.get("included") or []:
        if item.get("type") in ("new_player", "player"):
            attrs = item.get("attributes") or {}
            players[str(item.get("id"))] = {
                "name": attrs.get("display_name") or attrs.get("name"),
                "team": attrs.get("team"),
                "league": attrs.get("league"),
            }
    out = []
    for item in payload.get("data") or []:
        if item.get("type") != "projection":
            continue
        attrs = item.get("attributes") or {}
        rel = ((item.get("relationships") or {}).get("new_player") or {}).get("data") or {}
        player = players.get(str(rel.get("id"))) or {}
        league = player.get("league") or attrs.get("league")
        if league_name and league and league_name.lower() not in str(league).lower():
            continue
        out.append({
            "player_name": player.get("name"),
            "stat_label": attrs.get("stat_type"),
            "line": attrs.get("line_score"),
            "team": player.get("team"),
            "start_time": attrs.get("start_time"),
            "provider": "prizepicks",
        })
    return out


PROVIDERS = {"prizepicks": fetch_prizepicks_payload}


def load_regions(path):
    if not Path(path).exists():
        return {}
    with open(path) as f:
        return json.load(f).get("regions", {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=sorted(PROVIDERS), default="prizepicks")
    ap.add_argument("--game", choices=sorted(GAMES) + ["all"], default="all")
    ap.add_argument("--dry-run", action="store_true",
                     help="fetch and report, but do not write props.json")
    ap.add_argument("--fixture",
                     help="parse a saved provider payload instead of fetching. "
                          "Use '-' to read it from stdin, which lets you pipe a "
                          "payload saved from a browser on a connection the "
                          "provider does not block.")
    ap.add_argument("--out", default=OUTPUT_PATH,
                     help=f"where to write (default {OUTPUT_PATH})")
    args = ap.parse_args()

    games = sorted(GAMES) if args.game == "all" else [args.game]
    session = requests.Session()
    result = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": args.provider,
        "props": {},
    }
    total_matched = total_unmatched = 0

    if args.fixture:
        # "-" means stdin. The provider refuses datacenter IPs, and that
        # includes Codespaces and any other cloud shell — "run it locally"
        # means a machine on an ordinary connection, or a browser on one.
        # Piping in a payload saved from that browser is the shortest route
        # that works without asking anyone to defeat a bot check.
        if args.fixture == "-":
            payload = json.load(sys.stdin)
        else:
            with open(args.fixture) as f:
                payload = json.load(f)
    else:
        payload = PROVIDERS[args.provider](session)
        time.sleep(REQUEST_PAUSE_SECONDS)
    if payload is None:
        print("Provider returned nothing usable — leaving the existing "
              "props.json in place rather than overwriting it with an empty "
              "one, for the same reason scrape_career.py refuses to.",
              file=sys.stderr)
        return 1

    for game in games:
        cfg = GAMES[game]
        regions = load_regions(cfg["data"])
        if not regions:
            print(f"{game}: no roster data at {cfg['data']} — skipped", file=sys.stderr)
            continue
        index, ambiguous = build_roster_index(regions)
        if ambiguous:
            print(f"{game}: {len(ambiguous)} handle(s) on more than one roster, "
                  f"excluded from matching: {ambiguous}", file=sys.stderr)

        raw = parse_prizepicks(payload, cfg["prizepicks_league"]) if payload else []

        matched, unmatched = match_props(raw, index)
        total_matched += len(matched)
        total_unmatched += len(unmatched)

        by_player = {}
        for prop in matched:
            by_player.setdefault(prop["player"], []).append(prop)
        result["props"][game] = by_player

        print(f"{game}: {len(raw)} raw prop(s) -> {len(matched)} matched across "
              f"{len(by_player)} player(s), {len(unmatched)} unmatched")
        # Unmatched props are printed, not swallowed. A rising unmatched
        # count is how a provider renaming its stat labels shows up, and it
        # would otherwise look exactly like a quiet slate.
        reasons = {}
        for prop in unmatched:
            reasons[prop["reason"]] = reasons.get(prop["reason"], 0) + 1
        for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {count:4d}  {reason}")

    if total_matched == 0:
        print("\n! No props matched for any game. Either there is genuinely "
              "nothing posted right now, or the provider changed shape — "
              "check the unmatched reasons above before assuming the former.",
              file=sys.stderr)

    if args.dry_run:
        print("\n--dry-run: not writing props.json")
        return 0

    with open(args.out, "w") as f:
        json.dump(result, f, separators=(",", ":"))
    print(f"\nWrote {args.out}: {total_matched} prop(s), "
          f"{total_unmatched} unmatched, fetched_at {result['fetched_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

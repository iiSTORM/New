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

A Codespace or any other hosted shell is a datacenter too, and gets the
same 403. What does work:

  - Save the payload from a browser on an ordinary connection and pipe it
    in with --fixture -, then commit props.json. No install, nothing to
    defeat, and it runs through the parser and matching below.
  - Run this from a machine at home, where you are an ordinary logged-in
    customer, on a timer. See the README.
  - Point PROVIDERS at a source with a real server-side API. That is the
    only route that makes the hourly workflow viable, and it is why the
    fetch is a single swappable function.

If this app is ever served to other people rather than used personally,
which source the lines come from is worth more thought than it needs now.

    python scripts/scrape_props.py                  # all configured leagues
    python scripts/scrape_props.py --dry-run        # fetch and report, write nothing
    python scripts/scrape_props.py --fixture f.json # parse a saved payload offline
    cat f.json | python scripts/scrape_props.py --fixture -

To check the whole pipeline without a provider, run it against the saved
payload in tests/fixtures/ — see "Checking it worked" in the README.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    # Only the fetching needs it. The paste-a-payload route is advertised as
    # needing no install, and a stock system python does not ship requests,
    # so importing it at the top would break that route with a traceback
    # before it ever looked at the file -- on exactly the machine where it
    # is the only route that works.
    requests = None

from props_match import build_roster_index, match_props

OUTPUT_PATH = "props.json"

# Which app game each configured league feeds, and the roster file to match
# against. League ids are configurable rather than hardcoded guesses: they
# are provider-side identifiers that change, and a wrong one fails silently
# as "no props today", which is indistinguishable from a quiet evening.
# Matched EXACTLY, not as a substring. The provider posts season-long and
# part-period variants of a league under suffixed names -- NBASZN, NFL1H,
# WNBA1Q all appear in a real payload -- and those are different products:
# a season total or a first-half line compared against this app's
# per-series projection is not a slightly-off edge, it is a meaningless
# one. A substring rule would swallow a future LoLSZN without a word, so
# an unrecognised spelling is left to report itself as "0 raw" instead,
# which scripts/dev/inspect_props_payload.py then names.
#
# Both spellings of each are accepted because the provider has used the
# long form and currently uses the short one.
GAMES = {
    "lol": {"data": "data.json", "leagues": {"LoL", "League of Legends"}},
    "cs2": {"data": "cs2_data.json", "leagues": {"CS2"}},
    "valorant": {"data": "valorant_data.json", "leagues": {"VAL", "VALORANT"}},
}

PRIZEPICKS_URL = "https://api.prizepicks.com/projections"
HEADERS = {
    "User-Agent": "esports-kill-projector/1.0 (personal projection tool)",
    "Accept": "application/json",
}
REQUEST_PAUSE_SECONDS = 2.0  # polite; this runs unattended on a schedule

# Mirrors PROPS_MAX_AGE_MINUTES in src/app.jsx, which is where it is
# enforced. Only used here to warn that lines are already past it.
MAX_AGE_MINUTES = 90


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
            # Says "a machine at home" rather than "locally" on purpose:
            # a Codespace or cloud shell feels local and is a datacenter,
            # so it gets this same 403 and the advice reads as wrong.
            print("  ! prizepicks returned HTTP 403. This is what bot protection "
                  "looks like from a datacenter IP: the same request from a "
                  "normal home connection generally succeeds. A Codespace or "
                  "cloud shell is a datacenter too and will land right back "
                  "here.\n"
                  "    Shortest way through: open the endpoint in a browser on "
                  "a home connection, save the JSON, and pipe it in —\n"
                  "      python scripts/scrape_props.py --fixture - --out props.json\n"
                  "    Or run this from a machine at home, or configure a "
                  "provider that permits server-side access (see PROVIDERS).",
                  file=sys.stderr)
        else:
            print(f"  ! prizepicks returned HTTP {resp.status_code}", file=sys.stderr)
        return None
    try:
        return resp.json()
    except ValueError:
        print("  ! prizepicks response was not JSON", file=sys.stderr)
        return None


def parse_prizepicks(payload, leagues):
    """Pull (player, stat label, line) out of a JSON:API payload.

    `leagues` is the set of provider league names that feed one game, and
    a projection is kept only when its league is one of them exactly. See
    GAMES for why exactly rather than loosely. Pass a falsy value to keep
    everything, which is only useful for inspecting a payload.

    Separate from the request so it can be run against a saved fixture,
    which is the only way to check the parser without hitting the provider.
    """
    if isinstance(leagues, str):
        leagues = {leagues}
    wanted = {str(name).strip().lower() for name in (leagues or ())}
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
        if wanted and str(league or "").strip().lower() not in wanted:
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


def existing_prop_count(path):
    """How many props the file we are about to replace already holds.

    Used to tell "nothing is posted right now" apart from "we are about to
    wipe good lines", which are the same zero at the point of writing and
    very different afterwards.
    """
    try:
        with open(path) as f:
            existing = json.load(f)
        return sum(len(props) for game in (existing.get("props") or {}).values()
                   for props in game.values())
    except (OSError, ValueError, AttributeError, TypeError):
        # Unreadable or not the shape we write is the same answer as absent:
        # there is nothing here worth protecting, because the frontend cannot
        # read it either. Refusing to write over it would strand the file.
        return 0


def read_payload(source):
    """Load a saved provider payload, or explain what arrived instead.

    This is the route the README sends people to, and the two ways it goes
    wrong both used to surface as a raw traceback: a paste that did not land
    (an empty file) and a browser that saved the rendered page instead of the
    raw response (HTML). A JSONDecodeError names a line and column in a file
    the reader cannot see, which tells them nothing about either.
    """
    try:
        if source == "-":
            raw, shown = sys.stdin.read(), "stdin"
        else:
            with open(source) as f:
                raw, shown = f.read(), source
    except FileNotFoundError:
        print(f"! No such file: {source}\n"
              "    If you are in a Codespace or a remote VS Code window, the "
              "browser saved that file to your own disk, not to this one. "
              "Drag it into the file explorer, or paste it into a new file "
              "there.", file=sys.stderr)
        return None
    except OSError as exc:
        print(f"! Could not read {source}: {exc}", file=sys.stderr)
        return None

    if not raw.strip():
        print(f"! {shown} is empty ({len(raw)} byte(s)) — nothing arrived.\n"
              "    A terminal paste that large often does not register. "
              "Paste into a new file in your editor and save it, then point "
              "--fixture at that.", file=sys.stderr)
        return None

    if raw.lstrip()[:1] == "<":
        print(f"! {shown} looks like HTML, not JSON ({len(raw)} bytes, starts "
              f"with {raw.lstrip()[:40]!r}).\n"
              "    Saving the page from a browser can store the rendered view "
              "rather than the response. Select the raw JSON and copy it, or "
              "save from the raw view. A block or login page also lands here.",
              file=sys.stderr)
        return None

    try:
        return json.loads(raw)
    except ValueError as exc:
        # Showing the tail as well as the head is the whole diagnosis for
        # the common case: a payload this size is copied, not typed, and a
        # copy that dropped its end stops mid-token rather than at "}".
        print(f"! {shown} is not valid JSON ({len(raw)} bytes): {exc}\n"
              f"    It starts with {raw[:60]!r}\n"
              f"    It ends with   {raw[-60:]!r}\n"
              "    Ending mid-token rather than on a closing brace means the "
              "copy was cut short. Selecting a rendered JSON view often "
              "copies only what is drawn — save the response to a file and "
              "move the file across instead of pasting it.",
              file=sys.stderr)
        return None


def payload_fetched_at(source):
    """When these lines actually came off the provider, as best as known.

    For a live fetch that is now. For a saved payload it is NOT: the
    browser pulled those lines, then the file was saved, moved between
    machines, and run some time later. Stamping "now" on it would tell the
    frontend a two-hour-old line is seconds old, and the frontend would
    then compute an edge against it rather than greying it out -- which is
    the one failure this whole design is arranged to prevent.

    A saved file's mtime is close to when it was written, i.e. when it was
    fetched. Where it is wrong it is usually too OLD (a copy preserving the
    original timestamp), and old is the safe direction: the line shows as
    stale rather than falsely fresh.
    """
    if source and source != "-":
        try:
            return datetime.fromtimestamp(os.path.getmtime(source), timezone.utc)
        except OSError:
            pass
    return datetime.now(timezone.utc)


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
    ap.add_argument("--allow-empty", action="store_true",
                     help="write a props file with nothing in it even when one "
                          "with lines already exists (see the refusal below)")
    args = ap.parse_args()

    games = sorted(GAMES) if args.game == "all" else [args.game]
    fetched_at = payload_fetched_at(args.fixture)
    age_minutes = (datetime.now(timezone.utc) - fetched_at).total_seconds() / 60
    result = {
        "fetched_at": fetched_at.isoformat(),
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
        payload = read_payload(args.fixture)
        if payload is None:
            return 1
    else:
        if requests is None:
            print("Fetching needs the requests package (pip install requests). "
                  "Parsing a saved payload does not — see --fixture.",
                  file=sys.stderr)
            return 1
        payload = PROVIDERS[args.provider](requests.Session())
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

        raw = parse_prizepicks(payload, cfg["leagues"]) if payload else []

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

    if age_minutes > MAX_AGE_MINUTES:
        print(f"\n! These lines are already {age_minutes:.0f} minutes old, past "
              f"the {MAX_AGE_MINUTES}-minute window the app computes an edge "
              f"inside.\n"
              "    The timestamp comes from when the payload was saved, not "
              "when this ran, so the app will show them greyed out with their "
              "age rather than as an edge. Fetch a fresh payload.",
              file=sys.stderr)

    # Printed before the --dry-run exit below, because reporting this is
    # most of what a dry run is for: the smoke check in CI runs one, and a
    # silent zero there would read as a clean bill of health.
    if total_matched == 0:
        print("\n! No props matched for any game. Either there is genuinely "
              "nothing posted right now, or the provider changed shape — "
              "check the unmatched reasons above before assuming the former.",
              file=sys.stderr)

    if args.dry_run:
        print("\n--dry-run: not writing props.json")
        return 0

    if total_matched == 0:
        # The payload-was-unusable path above already declines to overwrite a
        # good file. This is the same failure arriving one step later: a
        # payload that parses fine but matches nothing is what a renamed stat
        # label or a restructured response looks like, and writing it out
        # would delete real lines on the strength of a guess that tonight is
        # simply quiet. Keeping them costs nothing — the frontend stops
        # computing an edge against a line older than 90 minutes and shows
        # its age instead — so the stale file is a visibly stale file, not a
        # confident wrong number.
        already = existing_prop_count(args.out)
        if already and not args.allow_empty:
            print(f"Refusing to overwrite {args.out} ({already} prop(s)) with "
                  f"an empty one. Pass --allow-empty if the slate really is "
                  f"bare.", file=sys.stderr)
            return 1

    with open(args.out, "w") as f:
        json.dump(result, f, separators=(",", ":"))
    print(f"\nWrote {args.out}: {total_matched} prop(s), "
          f"{total_unmatched} unmatched, fetched_at {result['fetched_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

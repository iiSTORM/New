#!/usr/bin/env python3
"""Dota 2 pro matches from OpenDota, in the shape the app already reads.

OpenDota's API is free and needs no key (about 60 requests a minute). This
writes dota_data.json with one region, "Dota 2", built exactly like
cs2_data.json: teams with rosters and per-game rates, and past_matches as
SERIES, each with a two-map `actual` total and a per-map `per_game`
breakdown, so every window a line can name is gradeable.

What it carries over from the other three games, on purpose:

  - Series, not games. OpenDota lists games; a line is posted on a series
    ("Maps 1-2 Kills"), so games are grouped by series_id and ordered by
    start time. `actual` sums maps 1-2 (CS2's COLLAPSED window), and
    `games` says how many maps that total covers (a Bo1 covers one).
  - A clock on every series. Teams play twice in a day at Dota events too,
    and a date alone left 75 League lines ungraded until results carried
    their start time.
  - Only finished series. A series still being played would be stored as a
    1-0 "result" and grade a maps 1-2 line against one map.
  - Teams by id, named by their latest name. Rebrands and spelling drift
    split a team's history in two in CS2 until names were reconciled.
  - Short sides refused. A game with fewer than five players on a side is
    a broken box score; storing it would project off four players.
  - Positions 1-5, from the lane each player was in and their farm, so the
    Record tab can split Dota by position the way it splits League by lane.
  - Incremental. Games already stored are never fetched again, so each run
    costs only the new games; the first runs backfill the window.

Tier 1 and 2 only: OpenDota's league tiers "premium" and "professional".

    python scripts/scrape_dota.py
    python scripts/scrape_dota.py --days 45 --max-fetch 300
"""
import argparse
import collections
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    import requests
except ImportError:  # the tests exercise the pure functions without it
    requests = None

API = "https://api.opendota.com/api"
HEADERS = {"User-Agent": "esports-projections/1.0 (+https://github.com/iiSTORM/New)"}
OUT = "dota_data.json"
REGION = "Dota 2"
TIERS = {"premium", "professional"}
SIDE_SIZE = 5
COLLAPSED_WINDOW_MAPS = 2       # `actual` covers maps 1-2, as in CS2
MAX_MAPS_STORED = 5
MATCHES_KEPT_PER_TEAM = 12      # series per team kept across runs
REQUEST_GAP_SECONDS = 1.1       # under the free tier's 60 a minute
BEST_OF = {0: 1, 1: 3, 2: 5}    # OpenDota series_type
COLOR_PALETTE = ["#c95050", "#4a90d9", "#50b080", "#d9a441", "#9b6bd9", "#d96b9b",
                 "#41b3d9", "#a0c050", "#d97b41", "#7b8cd9", "#41d9a4", "#d9d041"]
REQUESTS = {"n": 0}


# ------------------------------------------------------------------ fetching

def get(path, params=None, retries=3):
    """One API call, politely spaced, retried on 429 and 5xx."""
    for attempt in range(retries):
        time.sleep(REQUEST_GAP_SECONDS)
        REQUESTS["n"] += 1
        try:
            r = requests.get(f"{API}{path}", params=params, headers=HEADERS, timeout=30)
        except requests.exceptions.RequestException as e:
            print(f"  ! {path}: {e}", file=sys.stderr)
            continue
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                return None
        if r.status_code in (429, 500, 502, 503, 504):
            wait = 10 * (attempt + 1)
            print(f"  ! {path}: HTTP {r.status_code}, waiting {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
        print(f"  ! {path}: HTTP {r.status_code}", file=sys.stderr)
        return None
    return None


def tracked_league_ids():
    leagues = get("/leagues") or []
    return {lg["leagueid"]: lg.get("name") for lg in leagues
            if isinstance(lg, dict) and lg.get("tier") in TIERS}


def recent_pro_games(since_ts, leagues, max_pages=60):
    """Summaries of tier 1-2 pro games newer than `since_ts`, newest first."""
    out, before = [], None
    for _ in range(max_pages):
        page = get("/proMatches", {"less_than_match_id": before} if before else None) or []
        if not page:
            break
        for g in page:
            if g.get("leagueid") in leagues:
                out.append(g)
        before = min(g["match_id"] for g in page)
        if min(g.get("start_time") or 0 for g in page) < since_ts:
            break
    return [g for g in out if (g.get("start_time") or 0) >= since_ts]


# --------------------------------------------------------- pure: box scores

def assign_positions(players):
    """{account_id: 1..5} for one side of one game.

    From the lane first: one mid is position 2; of the two safe-lane
    players the one with more last hits is 1 and the other 5; of the two
    off-lane players the farmer is 3 and the other 4. When the lanes do not
    come out as 1 mid, 2 safe and 2 off (a trilane, a jungler, a missing
    field) every player is ranked by farm instead, which orders a normal
    draft 1-2-3-4-5 almost always."""
    by_lane = collections.defaultdict(list)
    for p in players:
        by_lane[p.get("lane_role")].append(p)
    farm = lambda p: (p.get("last_hits") or 0, p.get("gold_per_min") or 0)
    if len(by_lane.get(2, [])) == 1 and len(by_lane.get(1, [])) == 2 and len(by_lane.get(3, [])) == 2:
        safe = sorted(by_lane[1], key=farm, reverse=True)
        off = sorted(by_lane[3], key=farm, reverse=True)
        return {by_lane[2][0]["account_id"]: 2, safe[0]["account_id"]: 1, safe[1]["account_id"]: 5,
                off[0]["account_id"]: 3, off[1]["account_id"]: 4}
    ranked = sorted(players, key=farm, reverse=True)
    return {p["account_id"]: i + 1 for i, p in enumerate(ranked)}


def player_name(p, pro_names):
    return (p.get("name") or pro_names.get(p.get("account_id")) or p.get("personaname") or "").strip()


def game_box(detail, pro_names, team_names):
    """(team -> player -> row, radiant team, dire team) for one game, or None
    when either side is not a known team or is a player short."""
    sides = {True: (detail.get("radiant_team") or {}), False: (detail.get("dire_team") or {})}
    names = {}
    for is_radiant, t in sides.items():
        tid = t.get("team_id") or detail.get("radiant_team_id" if is_radiant else "dire_team_id")
        if not tid:
            return None
        names[is_radiant] = team_names.get(tid) or t.get("name") or f"team {tid}"
    box = {names[True]: {}, names[False]: {}}
    for is_radiant in (True, False):
        side = [p for p in detail.get("players") or [] if bool(p.get("isRadiant")) == is_radiant]
        if len(side) != SIDE_SIZE or any(p.get("account_id") is None for p in side):
            return None
        team_kills = sum(p.get("kills") or 0 for p in side)
        pos = assign_positions(side)
        for p in side:
            name = player_name(p, pro_names)
            if not name:
                return None
            box[names[is_radiant]][name] = {
                "k": p.get("kills") or 0, "d": p.get("deaths") or 0, "a": p.get("assists") or 0,
                "lh": p.get("last_hits") or 0, "gpm": p.get("gold_per_min") or 0,
                "pos": pos[p["account_id"]], "id": p["account_id"],
                "team_total_k": team_kills,
            }
    return box, names[True], names[False]


def series_key(game):
    sid = game.get("series_id")
    return f"s{sid}" if sid else f"m{game['match_id']}"


def is_finished(games, best_of, now_ts):
    """Whether a series is over: a side has won enough games, or nothing has
    happened in it for twelve hours (an abandoned or misrecorded series)."""
    need = best_of // 2 + 1
    wins = collections.Counter(g["winner"] for g in games if g.get("winner"))
    if wins and max(wins.values()) >= need:
        return True
    return now_ts - max(g["start_time"] for g in games) > 12 * 3600


def build_series(key, games, best_of, league):
    """One past_matches record from a series' games (each carrying `box`,
    `start_time`, `radiant`, `dire`, `winner`, `match_id`), in map order."""
    games = sorted(games, key=lambda g: g["start_time"])[:MAX_MAPS_STORED]
    team_a, team_b = games[0]["radiant"], games[0]["dire"]
    per_game, totals = [], {team_a: {}, team_b: {}}
    for i, g in enumerate(games):
        this_map = {}
        for team, rows in g["box"].items():
            this_map[team] = {name: {f: row[f] for f in ("k", "d", "a", "lh", "gpm", "pos")}
                              for name, row in rows.items()}
            if i >= COLLAPSED_WINDOW_MAPS or team not in totals:
                continue
            for name, row in rows.items():
                slot = totals[team].setdefault(name, {"k": 0, "d": 0, "a": 0, "lh": 0, "gpm": 0,
                                                      "kp_numerator": 0, "kp_denominator": 0})
                for f in ("k", "d", "a", "lh", "gpm"):
                    slot[f] += row[f]
                slot["kp_numerator"] += row["k"] + row["a"]
                slot["kp_denominator"] += row["team_total_k"]
        per_game.append(this_map)
    wins = collections.Counter(g["winner"] for g in games if g.get("winner"))
    first = datetime.fromtimestamp(games[0]["start_time"], tz=timezone.utc)
    winner = wins.most_common(1)[0][0] if wins else None
    return {
        "match_id": f"dota-{key}",
        "week": league, "tournament": league,
        "date": first.strftime("%Y-%m-%d"), "start_time": first.isoformat(),
        "patch": None, "teamA": team_a, "teamB": team_b,
        "winner": winner, "score": f"{wins.get(team_a, 0)}-{wins.get(team_b, 0)}",
        "best_of": best_of, "actual": totals,
        "games": min(len(games), COLLAPSED_WINDOW_MAPS), "per_game": per_game,
        # OpenDota game ids, map by map: what is already stored, so a later
        # run never fetches a game twice. Beside per_game rather than inside
        # it, where a non-team key would read as a third side.
        "game_ids": [g["match_id"] for g in games],
    }


def season_rates(past_matches, team, player):
    """Per-game rates over every series on record for one player."""
    g = k = d = a = lh = gpm = num = den = 0
    for m in past_matches:
        row = (m.get("actual") or {}).get(team, {}).get(player)
        if not row:
            continue
        n = m.get("games") or 1
        g += n
        k += row["k"]; d += row["d"]; a += row["a"]
        lh += row.get("lh", 0); gpm += row.get("gpm", 0)
        num += row.get("kp_numerator", 0); den += row.get("kp_denominator", 0)
    if not g:
        return None
    return {"g": g, "k": k / g, "d": d / g, "a": a / g, "lh": lh / g, "gpm": gpm / g,
            "kp": (num / den * 100) if den else 0}


def main_position(past_matches, team, player, last=10):
    """The position a player played most over their last `last` maps."""
    seen = []
    for m in sorted(past_matches, key=lambda m: m.get("start_time") or m.get("date") or "", reverse=True):
        for game in m.get("per_game") or []:
            row = (game.get(team) or {}).get(player)
            if row and row.get("pos"):
                seen.append(row["pos"])
        if len(seen) >= last:
            break
    if not seen:
        return None
    return f"POS{collections.Counter(seen[:last]).most_common(1)[0][0]}"


def build_teams(past_matches, previous_teams=None):
    """Rosters: each team's players from its three most recent series, with
    rates over everything on record and their usual position."""
    colors = {t: v.get("color") for t, v in (previous_teams or {}).items()}
    recent = collections.defaultdict(list)
    for m in sorted(past_matches, key=lambda m: m.get("start_time") or "", reverse=True):
        for team in (m["teamA"], m["teamB"]):
            if len(recent[team]) < 3:
                recent[team].append(m)
    teams = {}
    for i, (team, ms) in enumerate(sorted(recent.items())):
        names = []
        for m in ms:
            for p in (m.get("actual") or {}).get(team, {}):
                if p not in names:
                    names.append(p)
        players = []
        for p in names:
            cur = season_rates(past_matches, team, p)
            if cur:
                players.append({"name": p, "role": main_position(past_matches, team, p),
                                "cur": cur, "hist": None})
        teams[team] = {"color": colors.get(team) or COLOR_PALETTE[i % len(COLOR_PALETTE)],
                       "players": players}
    return teams


def merge_past_matches(previous, fresh, per_team=MATCHES_KEPT_PER_TEAM):
    """Last run's series and this run's, newest first, a fresh copy winning;
    each team keeps its most recent `per_team`, and a series survives while
    either side still wants it (CS2's rule)."""
    by_key = {}
    for m in list(previous or []) + list(fresh or []):
        if m and m.get("teamA") and m.get("teamB"):
            by_key[m["match_id"]] = m
    ordered = sorted(by_key.values(), key=lambda m: m.get("start_time") or "", reverse=True)
    kept, seen = [], collections.Counter()
    for m in ordered:
        sides = (m["teamA"], m["teamB"])
        if any(seen[t] < per_team for t in sides):
            kept.append(m)
            seen.update(sides)
    return kept


def stored_game_ids(past_matches):
    return {gid for m in past_matches for gid in m.get("game_ids") or []}


# ---------------------------------------------------------------------- run

def load_previous(path=OUT):
    try:
        with open(path) as f:
            region = (json.load(f).get("regions") or {}).get(REGION) or {}
        return region.get("past_matches") or [], region.get("teams") or {}
    except (OSError, ValueError):
        return [], {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60, help="how far back to look for series")
    ap.add_argument("--max-fetch", type=int, default=400,
                    help="new games fetched per run; the rest come next run")
    args = ap.parse_args()
    if requests is None:
        print("! requests is not installed", file=sys.stderr)
        return 1
    now = datetime.now(timezone.utc)
    previous, previous_teams = load_previous()
    have = stored_game_ids(previous)

    leagues = tracked_league_ids()
    print(f"{len(leagues)} tier 1-2 leagues")
    if not leagues:
        print("! no leagues came back; keeping the previous file", file=sys.stderr)
        return 1
    summaries = recent_pro_games((now - timedelta(days=args.days)).timestamp(), leagues)
    print(f"{len(summaries)} tier 1-2 games in the last {args.days} days; {len(have)} already stored")

    # Whole series only: every game of a series is needed to build it, so a
    # series is fetched as a unit, newest first, until the budget runs out.
    by_series = collections.defaultdict(list)
    for g in summaries:
        by_series[series_key(g)].append(g)
    stored_series = {m["match_id"] for m in previous}
    pro = get("/proPlayers") or []
    pro_names = {p["account_id"]: p["name"] for p in pro if p.get("account_id") and p.get("name")}
    team_names = {}

    fresh, fetched, skipped = [], 0, collections.Counter()
    for key, games in sorted(by_series.items(), key=lambda kv: -max(g["start_time"] for g in kv[1])):
        best_of = BEST_OF.get(games[0].get("series_type"), 1)
        quick = [{"winner": (g.get("radiant_name") if g.get("radiant_win") else g.get("dire_name")),
                  "start_time": g["start_time"]} for g in games]
        if not is_finished(quick, best_of, now.timestamp()):
            skipped["still being played"] += 1
            continue
        if f"dota-{key}" in stored_series and all(g["match_id"] in have for g in games):
            continue
        if fetched + len(games) > args.max_fetch:
            skipped["over this run's fetch budget"] += 1
            continue
        built = []
        for g in games:
            detail = get(f"/matches/{g['match_id']}")
            fetched += 1
            if not detail:
                break
            for side in ("radiant_team", "dire_team"):
                t = detail.get(side) or {}
                if t.get("team_id") and t.get("name"):
                    team_names[t["team_id"]] = t["name"]
            got = game_box(detail, pro_names, team_names)
            if not got:
                break
            box, radiant, dire = got
            built.append({"match_id": g["match_id"], "start_time": detail.get("start_time") or g["start_time"],
                          "box": box, "radiant": radiant, "dire": dire,
                          "winner": radiant if detail.get("radiant_win") else dire})
        if len(built) != len(games):
            skipped["a game's box score was incomplete"] += 1
            continue
        fresh.append(build_series(key, built, best_of, games[0].get("league_name")))

    past = merge_past_matches(previous, fresh)
    teams = build_teams(past, previous_teams)
    payload = {"teams": teams, "past_matches": past, "upcoming_matches": [],
               "refreshed_at": now.isoformat()}
    with open(OUT, "w") as f:
        json.dump({"generated_at": now.isoformat(), "regions": {REGION: payload}}, f, separators=(",", ":"))
    print(f"wrote {OUT}: {len(teams)} teams, {len(past)} series "
          f"({len(fresh)} new or refreshed, {fetched} games fetched, {REQUESTS['n']} requests)")
    for why, n in skipped.items():
        print(f"  {n} series skipped: {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Simulate every upcoming fixture and write simulations.json for the app.

Each fixture the data files list as upcoming is played RUNS times by
propedge/series_sim.py, as of now, with each team's current roster, its
format, the bookmaker's price where one was captured (CS2) and Elo where
not, and the event-pace and class-gap factors the projections use. What is
written is the summary -- series scores, a breakdown of each map, every
player's distribution per stat and map window -- not the runs themselves.

Every posted line for a simulated player is attached with two
probabilities side by side: the engine's own P(over), read off its
distribution, and the current model's (the slate tool's). The engine's is
not the one to bet on: scripts/dev/simulation_backtest.py found it worse on
single legs and the projection-anchored version only tying the model. The
backtest's verdict is carried in the file so the app can say so beside the
numbers.

Model outputs only: no stake, no bankroll, nothing from the private store.

    python scripts/publish_simulations.py
    python scripts/publish_simulations.py --runs 4000 --games lol
"""
import argparse
import collections
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "dev"))

from propedge import esports                         # noqa: E402
from propedge import series_sim as sim               # noqa: E402

OUT = "simulations.json"
DEFAULT_BEST_OF = {"cs2": 3, "valorant": 3, "dota2": 3, "lol": 3}
#: Fixtures that started longer ago than this are left out.
STARTED_GRACE = timedelta(hours=3)
STAT_OF = {"k": "kills", "d": "deaths", "a": "assists", "hs": "headshots"}


def _when(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None


def fixtures(lib, now):
    """[(region, fixture dict)] for upcoming fixtures, deduplicated."""
    seen, out = set(), []
    for region, blob in lib.regions.items():
        for f in blob.get("upcoming_matches") or []:
            a, b = f.get("teamA"), f.get("teamB")
            when = _when(f.get("start_time") or f.get("date"))
            if not a or not b or not when or when < now - STARTED_GRACE:
                continue
            if "tbd" in (sim.norm(a), sim.norm(b)):
                continue
            key = (when.date().isoformat(), frozenset((sim.norm(a), sim.norm(b))))
            if key in seen:
                continue
            seen.add(key)
            out.append((region, f, when))
    out.sort(key=lambda x: x[2])
    return out


def roster(lib, region, team, date):
    blob = lib.regions.get(region) or {}
    info = (blob.get("teams") or {}).get(team)
    if not info:
        for other in lib.regions.values():
            info = (other.get("teams") or {}).get(team)
            if info:
                break
    names = [p.get("name") for p in (info or {}).get("players") or [] if p.get("name")]
    if len(names) >= 5:
        return [(sim.norm(n), n) for n in names[:5]]
    return lib.roster(sim.norm(team), date)


def best_of(game, lib, region, fixture):
    if isinstance(fixture.get("best_of"), int):
        return fixture["best_of"]
    if game == "lol":
        recent = (lib.regions.get(region) or {}).get("past_matches") or []
        formats = collections.Counter(sim.best_of_for(m) for m in recent[-20:])
        if formats:
            return formats.most_common(1)[0][0]
    return DEFAULT_BEST_OF[game]


def lines_for(props, game, player):
    rows = ((props or {}).get("props") or {}).get(game, {}).get(player) or []
    return [r for r in rows if r.get("map") in (None, 1)]


def attach_lines(entry, game, props, model, team, opponent, date):
    """Posted lines for this player, with the engine's and the model's P(over)."""
    out = []
    for r in lines_for(props, game, entry["player"]):
        stat = sim.STAT_NAMES.get(r.get("stat"))
        window = str(r.get("maps"))
        dist = (entry["stats"].get(stat) or {}).get(window)
        if not dist:
            continue
        engine = sim.p_over(dist["hist"], r["line"])
        decided = engine[0] + engine[2] if engine else 0
        got = {"stat": r["stat"], "maps": r["maps"], "line": r["line"],
               "odds_type": r.get("odds_type") or "standard",
               "engine_over": engine[0] / decided if decided else None,
               "window_played": dist["played"]}
        mean = model.project_mean(game, entry["player"], r["stat"], r["maps"], team, opponent, date)
        if mean and mean[1] >= esports.MIN_MAPS:
            priced = model.probabilities(game, r["stat"], r["maps"], mean[0], r["line"], mean[1],
                                         got["odds_type"])
            if priced:
                over, _, under, _ = priced
                got["model_over"] = over / (over + under) if over + under else None
                got["projection"] = mean[0]
        out.append(got)
    return out


def projections(entry, game, model, team, opponent, date, stats, windows):
    out = {}
    for s in stats:
        for w in windows:
            mean = model.project_mean(game, entry["player"], STAT_OF[s], w, team, opponent, date)
            if mean and mean[1] >= esports.MIN_MAPS:
                out.setdefault(s, {})[str(w)] = round(mean[0], 2)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=sim.DEFAULT_RUNS)
    ap.add_argument("--games", nargs="+", default=list(sim.SOURCES))
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--now", help="ISO time to simulate as of (default: now)")
    args = ap.parse_args(argv)
    now = _when(args.now) if args.now else datetime.now(timezone.utc)
    props = json.load(open("props.json")) if os.path.exists("props.json") else None
    graded = []
    if os.path.exists("props_results.json"):
        graded = json.load(open("props_results.json")).get("graded") or []
    model = esports.EsportsModel.from_files(".", graded)
    verdict = None
    if os.path.exists(os.path.join(HERE, "dev", "simulation_backtest.json")):
        verdict = json.load(open(os.path.join(HERE, "dev", "simulation_backtest.json")))
    out = []
    skipped = collections.Counter()
    for game in args.games:
        lib = sim.load_library(game)
        if lib is None:
            continue
        for n, (region, f, when) in enumerate(fixtures(lib, now)):
            team_a, team_b = f["teamA"], f["teamB"]
            date = when.date().isoformat()
            bo = best_of(game, lib, region, f)
            fa, fb = sim.kill_factors(lib.regions, team_a, team_b)
            prepared = sim.prepare(lib, team_a, team_b, date, bo,
                                   roster(lib, region, team_a, date),
                                   roster(lib, region, team_b, date),
                                   odds=f.get("odds"), kill_factor_a=fa, kill_factor_b=fb)
            if prepared is None:
                skipped[f"{game}: not enough history"] += 1
                continue
            got = sim.summarise(lib, prepared, args.runs, seed=n + 1)
            got["teams"] = [team_a, team_b]
            got["region"] = region
            got["start"] = when.isoformat()
            got["block"] = f.get("block")
            for entry in got["players"]:
                team = team_a if entry["side"] == 0 else team_b
                opp = team_b if entry["side"] == 0 else team_a
                entry["team"] = team
                entry["projection"] = projections(entry, game, model, team, opp, date,
                                                  lib.stats, [w for w in (1, 2, 3) if w <= bo])
                entry["lines"] = attach_lines(entry, game, props, model, team, opp, date)
            for story in got["storylines"]:
                story["mvp_team"] = team_a if story["mvp_team"] == got["teams"][0] else story["mvp_team"]
            out.append(got)
            print(f"  {game} {team_a} v {team_b} (Bo{bo}, {date}): {team_a} "
                  f"{got['series']['a_win']:.0%}, map win {got['p_map']:.0%} from {got['p_map_source']}")
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "runs": args.runs,
               "backtest": verdict, "fixtures": out}
    with open(args.out, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
        f.write("\n")
    print(f"wrote {args.out}: {len(out)} fixtures"
          + (f"; skipped {dict(skipped)}" if skipped else "")
          + f", {os.path.getsize(args.out) / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Point-in-time correlation of each new column with the model's errors.

The companion to test_new_signals.py, and the one that decides. That
script hands each field the season aggregate, which contains the matches
being scored; this one rebuilds every figure from matches strictly
before each match.

Run both. A field that looks strong on the first and collapses here was
measuring itself -- which is what adr (+0.21 -> +0.02), acs (+0.21 ->
+0.02) and rating (+0.19 -> -0.02) all did. Only fk and fd survived.

The leaky screen put adr at +0.21 and acs at +0.21. Those aggregates
contained the matches being scored. Recomputed here from matches
strictly before each one -- if the signal survives, it is real and worth
wiring in properly; if it collapses, the screen was measuring itself.
"""
import statistics, sys
sys.path.insert(0, "scripts/dev")
import optimize_weights as ow

import argparse
_ap = argparse.ArgumentParser()
_ap.add_argument("--game", default="valorant", choices=["valorant", "cs2", "lol"])
_args = _ap.parse_args()
GAME = _args.game
data = ow.load_region_data({"valorant": "valorant_data.json",
                            "cs2": "cs2_data.json", "lol": "data.json"}[GAME])
FIELDS = ("adr", "acs", "kast", "rating", "fk", "fd", "hs",
          "clutch", "tk", "td", "dmg")

def corr(xs, ys):
    if len(xs) < 50: return None
    mx,my=statistics.mean(xs),statistics.mean(ys)
    sx,sy=statistics.pstdev(xs),statistics.pstdev(ys)
    if not sx or not sy: return None
    return sum((a-mx)*(b-my) for a,b in zip(xs,ys))/len(xs)/(sx*sy)

def pit(past, team, player, field, cutoff):
    """Flat per-map mean of `field` before cutoff, and how many maps."""
    tot = maps = 0.0
    rows = 0
    for m in past:
        if not m.get("date") or m["date"] >= cutoff: continue
        for side in ("teamA","teamB"):
            if m[side] != team: continue
            row = (m["actual"].get(team) or {}).get(player)
            if not row or field not in row: continue
            tot += row[field]; maps += m.get("games",2); rows += 1
    if not rows: return None
    # counts per map; per-round and per-cent figures averaged over rows
    return tot/maps if field in ("fk","fd") else tot/rows

for stat in ("kills","deaths"):
    W = ow.SHIPPED_WEIGHTS[GAME][stat]
    key = ow.STAT_TYPES[stat]["key"]
    acc = {f: ([], []) for f in FIELDS}
    n = 0
    for rd in data.values():
        teams, past = rd.get("teams",{}), rd.get("past_matches",[])
        if not teams or not past: continue
        for m in past:
            cutoff = m["date"]
            for side in ("teamA","teamB"):
                team=m[side]; opp=m["teamB"] if side=="teamA" else m["teamA"]
                if team not in teams: continue
                for p in teams[team]["players"]:
                    a = ow.get_actual_stat(m, team, p["name"], key)
                    if a is None or a=="unavailable": continue
                    pred, prior = ow.project_point_in_time(past, teams, p, team, opp,
                        ow.maps_counted_for(m), W, cutoff, stat, m.get("patch"))
                    if pred is None or prior < 4: continue
                    r = a - pred; n += 1
                    for f in FIELDS:
                        v = pit(past, team, p["name"], f, cutoff)
                        if v is not None:
                            acc[f][0].append(v); acc[f][1].append(r)
    print(f"\n{GAME}/{stat}  n={n}")
    print(f"  {'field':8} {'n':>6} {'point-in-time':>14}"
          + (f" {'leaky screen':>14}" if GAME == "valorant" else ""))
    leak = {} if GAME != "valorant" else {"kills": {"adr":0.2062,"acs":0.2122,"kast":0.0733,"rating":0.1891,
                      "fk":0.1356,"fd":0.0729,"hs":0.0143},
            "deaths": {"adr":-0.0423,"acs":-0.0454,"kast":-0.0939,"rating":-0.0935,
                       "fk":-0.0053,"fd":0.0392,"hs":-0.0088}}[stat]
    leak = leak if isinstance(leak, dict) else {}
    for f in FIELDS:
        xs, ys = acc[f]
        c = corr(xs, ys)
        if c is None:
            print(f"  {f:8} {len(xs):>6} {'(thin)':>14}")
        elif f in leak:
            print(f"  {f:8} {len(xs):>6} {c:>14.4f} {leak[f]:>14.4f}")
        else:
            print(f"  {f:8} {len(xs):>6} {c:>14.4f}")

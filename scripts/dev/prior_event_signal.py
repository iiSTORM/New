#!/usr/bin/env python3
"""Does the PRIOR EVENT's style profile predict this event's residuals?

The cleanest version of the style question, and the last one worth
asking. The hypothesis all along was that how a player plays -- early to
a fight or not, contesting openings or trading off them -- carries
across competitions better than raw production does, and would therefore
fix an international event that raw production cannot.

Every earlier attempt had a leakage problem to argue about. This one
does not: `hist` is a DIFFERENT TOURNAMENT from the matches being
scored, so the separation is structural rather than reconstructed. No
point-in-time rebuild, nothing to get wrong.

RESULT, on 1,488 rows with the full extra-stat set on both tiers:

                kills    deaths   assists
  hist.fk      +0.0612  -0.0365  -0.0209
  hist.fd      +0.0555  -0.0316  -0.0088
  hist.acs     +0.0521  -0.0197  -0.0172
  hist.adr     +0.0421  -0.0083  -0.0171
  hist.kast    -0.0372  +0.0006  -0.0541
  hist.hs      -0.0213  -0.0017  -0.0799
  hist.rating  -0.0082  -0.0005  -0.0194

Nothing reaches 0.08. Opening duels are again the best of them, which is
consistent with every earlier run, and again far too weak to fit.

The control is the part worth keeping. hist.k -- the prior event's own
KILL RATE, the most obviously predictive number on the table -- scores
+0.0172 against kill residuals. It is not that the residuals are hard to
explain; it is that the model already contains this tier (Valorant
carries history at 0.7), so what is left over is close to orthogonal to
everything in it. A column that beats hist.k on residuals is not thereby
useful, and fk beating it by four hundredths is not a signal.

LoL, on 6,403 rows and the thirteen columns just read off gol.gg's
players/list table, says the same thing harder. NOTHING reaches 0.034,
and most sit under 0.02:

                kills    deaths   assists
  hist.fb_pct  -0.0121  +0.0178  +0.0088
  hist.dpm     -0.0033  +0.0205  -0.0162
  hist.gd15    -0.0119  -0.0002  +0.0060
  hist.csm     +0.0115  +0.0257  -0.0210
  hist.gold%   +0.0008  +0.0328  -0.0186
  hist.solo_k  +0.0110  +0.0020  +0.0070
  hist.k       +0.0028  (control)

"FB %" is the column this whole line of work was aimed at -- the LoL
first-blood share, the direct analogue of the Valorant opening duels --
and it is worth nothing on any of the three stats. The control is again
the reading: the prior split's own kill rate scores +0.0028 against kill
residuals, because LoL already sits at roughly 100% of its
predictability ceiling. There is nothing left in this table to find.

Taken with the earlier rounds -- K/D ratio 0.07, assist share 0.04,
champion pool 0.02, adr 0.2062 leaky collapsing to 0.0192 point-in-time,
and a duel term that cost 0.15% MAE over 4 of 6 folds -- the style
hypothesis is answered, in both games, by the cleanest test available to
either. Style is inside a player's own rates, and adding another view of
it adds noise.

The columns are still worth having parsed: they cost no extra request,
they are what made this test possible, and a future question about a
DIFFERENT quantity gets to ask it against real data rather than plan
another scrape.

What moves this model is evidence DEPTH, which is measured elsewhere and
is not subtle: LoL at a median of 14 maps per player sits at ~100% of
its predictability ceiling, Valorant at 8 sits at 62%, CS2 at 2 sits at
39%.

Usage:
    python scripts/dev/prior_event_signal.py [--game valorant]
"""
import argparse
import statistics
import sys

sys.path.insert(0, "scripts/dev")
import optimize_weights as ow

# Valorant's set. LoL's columns are different and are discovered from
# the data instead -- gol.gg's table was only just read, and hard-coding
# a list here would quietly stop testing a column the day it is added.
FIELDS = ("fk", "fd", "adr", "acs", "kast", "hs", "rating", "kp", "k", "d", "a")
MIN_ROWS = 50


def fields_present(data, limit=40):
    """Every numeric key any player's hist tier carries, most common
    first, so a newly parsed column is measured without being listed."""
    seen = {}
    for rd in data.values():
        for team in (rd.get("teams") or {}).values():
            for player in team.get("players") or []:
                for key, value in (player.get("hist") or {}).items():
                    if isinstance(value, (int, float)) and key != "g":
                        seen[key] = seen.get(key, 0) + 1
    return [k for k, _ in sorted(seen.items(), key=lambda kv: -kv[1])][:limit]


def corr(xs, ys):
    if len(xs) < MIN_ROWS:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
    if not sx or not sy:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / len(xs) / (sx * sy)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="valorant", choices=["valorant", "cs2", "lol"])
    args = ap.parse_args()
    path = {"valorant": "valorant_data.json", "cs2": "cs2_data.json",
            "lol": "data.json"}[args.game]
    data = ow.load_region_data(path)
    fields = fields_present(data) or list(FIELDS)

    for stat in ("kills", "deaths", "assists"):
        weights = ow.SHIPPED_WEIGHTS[args.game][stat]
        key = ow.STAT_TYPES[stat]["key"]
        acc = {f: ([], []) for f in fields}
        n = 0
        for rd in data.values():
            teams, scored = rd.get("teams", {}), rd.get("past_matches", [])
            if not teams or not scored:
                continue
            past = ow.history_pool(rd)
            for match in scored:
                for side in ("teamA", "teamB"):
                    team = match[side]
                    opp = match["teamB"] if side == "teamA" else match["teamA"]
                    if team not in teams:
                        continue
                    for player in teams[team]["players"]:
                        hist = player.get("hist")
                        if not hist:
                            continue
                        actual = ow.get_actual_stat(match, team, player["name"], key)
                        if actual is None or actual == "unavailable":
                            continue
                        predicted, prior = ow.project_point_in_time(
                            past, teams, player, team, opp,
                            match.get("maps_counted", 2), weights,
                            match["date"], stat, match.get("patch"))
                        # prior < 4 is a cold start, not a test of the model
                        if predicted is None or prior < 4:
                            continue
                        residual = actual - predicted
                        n += 1
                        for field in fields:
                            if field in hist:
                                acc[field][0].append(hist[field])
                                acc[field][1].append(residual)
        print(f"\n{args.game}/{stat}  n={n}")
        for field in fields:
            xs, ys = acc[field]
            c = corr(xs, ys)
            shown = "(thin)" if c is None else f"{c:+.4f}"
            note = "   <- the control" if field == "k" and stat == "kills" else ""
            print(f"  hist.{field:8} {len(xs):>6}  {shown}{note}")


if __name__ == "__main__":
    main()

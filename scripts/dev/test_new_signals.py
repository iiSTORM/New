#!/usr/bin/env python3
"""Do acs, adr, kast or the opening duels predict better than kills do?

ANSWERED, and the answer is mostly no. Recorded here because the
measurement was nearly believed:

  field    leaky screen   point-in-time
  adr           +0.2062         +0.0192
  acs           +0.2122         +0.0229
  rating        +0.1891         -0.0174
  kast          +0.0733         -0.0349
  fk            +0.1356         +0.0718
  fd            +0.0729         +0.0723

Everything but the opening duels was measuring itself. Season aggregates
contain the matches being scored, so a player who happened to have a big
season looked like a player the model was underrating. On that screen
ADR alone "beat" the whole model, 5.60 against 5.91. Recomputed from
matches strictly before each one, ADR alone loses on 0 of 6 folds and is
5.95% worse.

fk and fd are the two that survive, which is what the hypothesis
predicted: opening duels describe HOW a player plays, and that does not
collapse the way a production average does. Both correlate POSITIVELY
with the kills residual, which is the tell -- nobody is both good and
bad at openings, so what they share is contesting them at all.

Fitted as a model term walk-forward, though, it is worth -0.15% on MAE
at 4 of 6 folds. That is the same size as the opponent-weight candidate
already rejected on this game (5/6 folds, -0.08%), and it is held to the
same standard: not shipped.

The columns are still captured, for two reasons. They cost nothing --
they were already on a page being fetched -- and the question the
hypothesis was really about is transfer to an international field, which
needs VCT Champions results that did not exist when this was run.

These arrived together off one vlr.gg row. The question for each is the
same and it is not "does it correlate with kills" -- of course it does --
but "given what the model already knows, does it know MORE?"

So each is scored two ways:

  1. correlation with what the model currently gets wrong. Nothing there
     means the signal is already arriving by another route.
  2. as a REPLACEMENT basis for the player's rate. ADR is the live
     candidate: damage accrues every round while kills come in lumps, so
     a rate built on damage may be a steadier estimate of the same
     player, converted back to kills by the league's damage-per-kill.

Point-in-time throughout: a match is described only by what preceded it.
Run once a scrape carrying the new fields has landed.
"""
import argparse
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import optimize_weights as ow

FIELDS = ("adr", "acs", "kast", "fk", "fd", "hs", "rating")


def corr(xs, ys):
    if len(xs) < 50:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
    if not sx or not sy:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / len(xs) / (sx * sy)


def collect(data, stat):
    """(date, prediction, actual, {field: season value}) per scoreable row.

    The season figures are NOT point-in-time -- they are what the scraper
    aggregated over the whole event. That is leakage, and it is on
    purpose: if a field cannot beat the model even with the season handed
    to it, it will not beat it point-in-time either. A field that DOES
    show something here has earned a proper point-in-time trial, not a
    place in the model.
    """
    cfg = ow.STAT_TYPES[stat]
    rows = []
    for rd in data.values():
        teams, past = rd.get("teams", {}), rd.get("past_matches", [])
        if not teams or not past:
            continue
        for m in past:
            for side in ("teamA", "teamB"):
                team = m[side]
                opp = m["teamB"] if side == "teamA" else m["teamA"]
                if team not in teams:
                    continue
                for p in teams[team]["players"]:
                    actual = ow.get_actual_stat(m, team, p["name"], cfg["key"])
                    if actual is None or actual == "unavailable":
                        continue
                    pred, prior = ow.project_point_in_time(
                        past, teams, p, team, opp, m.get("maps_counted", 2),
                        ow.SHIPPED_WEIGHTS["valorant"][stat], m["date"], stat,
                        m.get("patch"))
                    if pred is None or prior < 4:
                        continue
                    cur = p.get("cur") or {}
                    if not any(f in cur for f in FIELDS):
                        continue
                    rows.append((m["date"], pred, actual,
                                 {f: cur[f] for f in FIELDS if f in cur},
                                 cur.get("k")))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="valorant_data.json")
    args = ap.parse_args()
    data = ow.load_region_data(args.data)

    for stat in ("kills", "deaths", "assists"):
        rows = collect(data, stat)
        if len(rows) < 200:
            print(f"\nvalorant/{stat}: {len(rows)} rows carrying the new fields — "
                  f"re-run once a scrape with them has landed")
            continue
        resid = [a - p for _, p, a, _, _ in rows]
        print(f"\nvalorant/{stat}  n={len(rows)}  "
              f"MAE {statistics.mean(abs(r) for r in resid):.4f}")
        print(f"  {'field':8} {'coverage':>9} {'corr with residual':>20}")
        for f in FIELDS:
            have = [(x[3][f], r) for x, r in zip(rows, resid) if f in x[3]]
            if len(have) < 50:
                print(f"  {f:8} {len(have):9} {'(too thin)':>20}")
                continue
            c = corr([v for v, _ in have], [r for _, r in have])
            flag = "  <- worth a proper trial" if c is not None and abs(c) > 0.08 else ""
            print(f"  {f:8} {len(have):9} {c:+20.4f}{flag}")

        # ADR as a replacement basis, scaled to kills by the league ratio.
    # NOTE: this figure is leaky and was wrong. See the header.
        pairs = [(x[3]["adr"], x[4]) for x in rows if "adr" in x[3] and x[4]]
        if stat == "kills" and len(pairs) >= 100:
            per_kill = statistics.mean(a / k for a, k in pairs if k)
            alt = [(x[3]["adr"] / per_kill) * 2 for x in rows if "adr" in x[3]]
            act = [x[2] for x in rows if "adr" in x[3]]
            cur = [x[1] for x in rows if "adr" in x[3]]
            print(f"\n  ADR as the rate basis instead of kills "
                  f"({per_kill:.1f} damage per kill):")
            print(f"    model as shipped : MAE {statistics.mean(abs(p-a) for p,a in zip(cur,act)):.4f}")
            print(f"    from ADR alone   : MAE {statistics.mean(abs(p-a) for p,a in zip(alt,act)):.4f}")
            print("    (ADR alone is not a model -- it has no opponent, share or"
                  " shrink tier.\n     It beating the model would be a finding; losing"
                  " narrowly still means\n     it is worth blending in.)")


if __name__ == "__main__":
    sys.exit(main())

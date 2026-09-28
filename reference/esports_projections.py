"""
Esports kill / headshot projections from the iiSTORM/New data files (written 9/28/26 in chat).
Usage:  python esports_projections.py <data_dir>
<data_dir> must hold cs2_data.json, valorant_data.json, props.json, props_results.json.

Key facts baked in:
- cs2_data.json `actual` for a Bo3 = MAPS 1-2 totals only -> divide by 2 (Bo1 -> 1).
- valorant_data.json `actual` carries `rows` = maps played in that series.
- props_results.json shows PrizePicks esports lines run high; bias() returns the graded under rate per window.
"""
import json, sys, os, numpy as np, pandas as pd
D = sys.argv[1] if len(sys.argv) > 1 else '.'
load = lambda f: json.load(open(os.path.join(D, f)))

def bias():
    r = load('props_results.json'); g = pd.DataFrame(r['graded'])
    g = g[g.odds_type == 'standard']
    out = g[g.result != 'push'].groupby(['game', 'maps', 'stat']).result.agg(n='size', under_rate=lambda s: (s == 'under').mean())
    return out.reset_index()

def cs2_projection(team, player, last_n=12):
    """per-player maps 1-2 kills & headshots (2-map totals) from recent series"""
    C = load('cs2_data.json')['regions']['CS2']['past_matches']
    ms = sorted([m for m in C if team in m.get('actual', {}) and m.get('score')], key=lambda m: m['date'])[-last_n:]
    ks, hs, w = [], [], []
    for m in ms:
        a = {k.lower(): v for k, v in m['actual'][team].items()}
        if player.lower() not in a: continue
        n = 1 if sum(int(x) for x in m['score'].split('-')) == 1 else 2     # maps 1-2 totals
        ks.append(a[player.lower()]['k'] / n); hs.append((a[player.lower()].get('hs') or 0) / n); w.append(n)
    if not ks: return None
    return dict(maps=sum(w), kills2=2 * np.average(ks, weights=w), hs2=2 * np.average(hs, weights=w))

def val_projection(team, player, league_region, last_n=12):
    V = load('valorant_data.json')['regions']
    rows = []
    for reg in (league_region, 'VCT Champions'):
        for m in V.get(reg, {}).get('past_matches', []):
            s = m.get('actual', {}).get(team, {})
            s = {k.lower(): v for k, v in s.items()}.get(player.lower())
            if s and s.get('rows'): rows.append((m['date'], reg, s['k'] / s['rows'], s['rows']))
    rows = sorted(rows)[-last_n:]
    if not rows: return None
    kpm = np.array([r[2] for r in rows]); w = np.array([r[3] for r in rows])
    return dict(series=len(rows), kills2=2 * np.average(kpm, weights=w))

def p_under(proj, line, sd, shrink=0.5, prior_under=0.5):
    """normal approx; edge shrunk toward the line; blended with the graded under-rate prior"""
    from math import erf, sqrt
    edge = (line - proj) * shrink
    p = 0.5 * (1 + erf(edge / (sd * sqrt(2))))
    return 0.5 * p + 0.5 * prior_under if prior_under != 0.5 else p

if __name__ == '__main__':
    print(bias().to_string(index=False))
    print('djay CS2:', cs2_projection('LAG', 'djay'))
    print('Jinggg VAL:', val_projection('Paper Rex', 'Jinggg', 'VCT Pacific'))
    # 2-map SDs used on 9/28: CS2 kills ~7, CS2 headshots ~4.5, Valorant kills ~7.5

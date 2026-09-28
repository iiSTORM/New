import os
import pickle, numpy as np, itertools, pandas as pd
u,m=pickle.load(open('sims_v2.pkl','rb')); W=0.6
LEGS={'Raymond O2.5 recs':("Kalif Raymond","Recs",2.5,'O'),
      'Loveland U33.5 rec yds':("Colston Loveland","Rec Yards",33.5,'U'),
      'Burden U37.5 rec yds':("Luther Burden III","Rec Yards",37.5,'U'),
      'Swift O14.5 rush atts':("D'Andre Swift","Rush Atts",14.5,'O'),
      'Swift U13.5 rec yds':("D'Andre Swift","Rec Yards",13.5,'U'),
      'Lemon U27.5 rec yds':("Makai Lemon","Rec Yards",27.5,'U'),
      'Odunze U4.5 targets':("Rome Odunze","Rec Targets",4.5,'U')}
def hit(o,leg):
    p,s,l,side=LEGS[leg]; x=o[p][s]; return (x<l) if side=='U' else (x>l)
rows=[]
for k in (1,2,3):
    for c in itertools.combinations(LEGS,k):
        if len({LEGS[x][0] for x in c})<k: continue
        pu=np.all([hit(u,x) for x in c],0).mean(); pm=np.all([hit(m,x) for x in c],0).mean()
        pb=W*pm+(1-W)*pu; worst=min(pu,pm)
        pay={1:1,2:3.0,3:6.0}[k]
        rows.append(dict(slip=' + '.join(c),legs=k,p_usage=pu,p_market=pm,p_blend=pb,p_worst=worst,
                         ev_blend=pb*pay if k>1 else np.nan, ev_worst=worst*pay if k>1 else np.nan,
                         ev5_blend=pb*5 if k==3 else np.nan, ev5_worst=worst*5 if k==3 else np.nan))
R=pd.DataFrame(rows); pd.set_option('display.width',260); pd.set_option('display.max_colwidth',80)
print(R[R.legs==1].sort_values('p_blend',ascending=False).round(3).to_string(index=False))
for k in (2,3):
    print(); print(R[R.legs==k].sort_values('ev_worst',ascending=False).head(8).round(3).to_string(index=False))
R.to_pickle('parlays_v2.pkl'); R.round(3).to_csv('./mnf_parlay_ev_v2.csv',index=False)

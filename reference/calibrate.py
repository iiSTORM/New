"""Tune usage/efficiency so the model's 50/50 points match sportsbook lines (vig-free)."""
import copy, numpy as np, pickle, json
from model_v2 import BASE, simulate
# (player, stat, book line)  - books: DK/FD/ESPN/BetMGM via published guides, Sep 25-28 2026
MARKET=[("DeVonta Smith","Rec Yards",72.5),("DeVonta Smith","Recs",5.5),
        ("Dontayvion Wicks","Rec Yards",41.5),("Dontayvion Wicks","Recs",3.5),
        ("Makai Lemon","Rec Yards",23.5),("Makai Lemon","Recs",2.5),
        ("Saquon Barkley","Rec Yards",13.5),("Saquon Barkley","Recs",2.5),
        ("Luther Burden III","Rec Yards",32.5),("Luther Burden III","Recs",3.5),
        ("Colston Loveland","Rec Yards",31.5),("Colston Loveland","Recs",3.5),
        ("Rome Odunze","Rec Yards",25.5),("Rome Odunze","Recs",2.5),
        ("Kalif Raymond","Rec Yards",24.5),("Kalif Raymond","Recs",3.5),
        ("D'Andre Swift","Rec Yards",10.5),
        ("Saquon Barkley","Rush Yards",71.5),("D'Andre Swift","Rush Yards",60.5),("Kyle Monangai","Rush Yards",39.5),
        ("Jalen Hurts","Pass Yards",216.5),("Jalen Hurts","Rush Yards",25.5)]
def team_of(p,P):
    for t,T in P['team'].items():
        if p in T['targets'] or p in T['rushers'] or p==T['qb']: return t
def p_over(o,p,s,l): x=o[p][s]; return (x>l).mean()+0.5*(x==l).mean()
P=copy.deepcopy(BASE)
for it in range(14):
    o=simulate(P,60_000,seed=it)
    err=[]
    for p,s,l in MARKET:
        po=p_over(o,p,s,l); d=0.5-po; err.append(abs(d))
        t=team_of(p,P); T=P['team'][t]
        step=0.9 if it<8 else 0.5
        if s=='Recs': T['targets'][p]*=np.exp(step*d*1.6)
        elif s=='Rec Yards': P['ypr'][p]*=np.exp(step*d*1.2)
        elif s=='Rush Yards' and p==T['qb'] and p=='Jalen Hurts' : T['qb_runs']=(T['qb_runs'][0],T['qb_runs'][1]*np.exp(step*d*0.6))
        elif s=='Rush Yards': P['ypc'][p]*=np.exp(step*d*0.5); T['rushers'][p]*=np.exp(step*d*0.8)
        elif s=='Pass Yards': T['qb_comp']=float(np.clip(T['qb_comp']+step*d*0.08,0.5,0.75))
    print(it,'mean |P(over)-0.5| =',round(np.mean(err),3),' max =',round(max(err),3))
pickle.dump(P,open('P_market.pkl','wb'))
print(json.dumps({t:{k:round(v,3) for k,v in P['team'][t]['targets'].items()} for t in P['team']},indent=0))

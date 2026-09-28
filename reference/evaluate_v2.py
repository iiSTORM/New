import os
import pickle, numpy as np, pandas as pd, itertools
from model_v2 import BASE, simulate
Pm=pickle.load(open('P_market.pkl','rb'))
usage=simulate(BASE,200_000,seed=11); market=simulate(Pm,200_000,seed=12)
pickle.dump((usage,market),open('sims_v2.pkl','wb'))
df=pd.read_pickle(os.environ.get('PP_PKL','pp.pkl'))
t=df[(df.start.dt.strftime('%m-%d')=='09-28')&(df.league=='NFL')&(df.odds=='standard')]
W_MKT=0.6
rows=[]
for _,r in t.iterrows():
    if r.player not in usage or r.stat not in usage[r.player]: continue
    d={}
    for nm,o in (('usage',usage),('market',market)):
        x=o[r.player][r.stat]; ov=(x>r.line).mean(); pu=(x==r.line).mean()
        d[nm]=(ov,pu,1-ov-pu)
    ov=W_MKT*d['market'][0]+(1-W_MKT)*d['usage'][0]; pu=W_MKT*d['market'][1]+(1-W_MKT)*d['usage'][1]; un=1-ov-pu
    side='More' if ov>un else 'Less'
    g=lambda tup,s: (tup[0] if s=='More' else tup[2])/(1-tup[1])
    rows.append(dict(player=r.player,stat=r.stat,line=r.line,side=side,
        usage=g(d['usage'],side),market=g(d['market'],side),blend=(max(ov,un))/(1-pu),push=pu))
R=pd.DataFrame(rows).sort_values('blend',ascending=False)
R['agree']=np.where((R.usage>0.5)&(R.market>0.5),'yes','no')
pd.set_option('display.width',200)
print((R.round(3)).head(25).to_string(index=False))
R.to_pickle('results_v2.pkl')

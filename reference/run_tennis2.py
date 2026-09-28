import os
import pandas as pd, numpy as np, pickle
from scipy.optimize import minimize
from scipy.interpolate import RegularGridInterpolator as RGI
D=pickle.load(open('tennis_grid.pkl','rb')); G=D['G']
I={k:[RGI((G,G),D['tabs'][k][:,:,t],bounds_error=False,fill_value=None) for t in range(40)] for k in D['tabs']}
W=RGI((G,G),D['win'],bounds_error=False,fill_value=None)
qx,qw=np.polynomial.hermite_e.hermegauss(5); qw=qw/qw.sum()
SG,SL=0.045,0.02          # match-to-match form noise on gap and on serve level
def P_ge(kind,t,a,g):
    pts=[];w=[]
    for x1,w1 in zip(qx,qw):
        for x2,w2 in zip(qx,qw):
            gg=g+SG*x1; aa=a+SL*x2; pts.append((np.clip(aa+gg/2,0.42,0.78),np.clip(aa-gg/2,0.42,0.78))); w.append(w1*w2)
    pts=np.array(pts); w=np.array(w)
    if kind=='win': return float((W(pts)*w).sum())
    t=int(t); 
    if t<=0: return 1.0
    if t>=40: return 0.0
    return float((I[kind][t](pts)*w).sum())
def p_over(kind,line,a,g):   # handles .5 and whole lines; returns (over,push)
    if float(line).is_integer():
        over=P_ge(kind,line+1,a,g); push=P_ge(kind,line,a,g)-over; return over,push
    return P_ge(kind,np.ceil(line),a,g),0.0
BAND={'ATP':(0.60,0.69),'WTA':(0.52,0.61)}
df=pd.read_pickle(os.environ.get('PP_PKL','pp.pkl'))
t=df[(df.start>'2026-09-28 15:00-05:00')&(df.start<'2026-09-29 11:00-05:00')&(df.league=='TENNIS')&(df.odds=='standard')].copy()
t['opp']=t.desc.str.replace(' 1st Set','',regex=False); t['key']=t.apply(lambda r:' vs '.join(sorted([r.player,r.opp])),axis=1)
from run_tennis_atp import ATP, MARKET_WIN
KIND={'Total Games':'total','1st Set Total Games':'s1t'}
rows=[]
for key,g in t.groupby('key'):
    A,B=key.split(' vs '); tour='ATP' if A in ATP else 'WTA'; lo,hi=BAND[tour]
    anchors=[]
    for _,r in g[g.stat=='Total Games'].iterrows(): anchors.append(('total',r.line,0.5))
    for _,r in g[g.stat=='Total Games Won'].iterrows(): anchors.append(('gwA' if r.player==A else 'gwB',r.line,0.5))
    mw=MARKET_WIN.get((A,B)) or (1-MARKET_WIN[(B,A)] if (B,A) in MARKET_WIN else None)
    if mw: anchors=[x for x in anchors if not x[0].startswith('gw')]+[('win',None,mw)]
    spread=any(x[0] in('gwA','gwB','win') for x in anchors)
    if not spread: continue                                    # can't pin who's favoured -> skip match
    def loss(v):
        a,gp=v; pen=0 if lo<=a<=hi else 50*(min(abs(a-lo),abs(a-hi)))**2
        s=0
        for kind,line,tgt in anchors:
            if kind=='win': s+=(P_ge('win',0,a,gp)-tgt)**2
            else: o,pu=p_over(kind,line,a,gp); s+=(o+0.5*pu-tgt)**2
        return s+pen
    r=min((minimize(loss,[x0,g0],method='Nelder-Mead',options=dict(maxiter=300)) for x0 in ((lo+hi)/2,) for g0 in (-0.1,0,0.1)),key=lambda z:z.fun)
    a,gp=r.x; resid=r.fun
    for _,row in g.iterrows():
        who='A' if row.player==A else 'B'
        kind={'Total Games':'total','1st Set Total Games':'s1t','Total Games Won':'gw'+who,'1st Set Total Games Won':'s1'+who.lower()}.get(row.stat)
        if kind is None: continue
        o,pu=p_over(kind,row.line,a,gp); u=1-o-pu
        is_anchor=any(k==kind and l==row.line for k,l,_ in anchors)
        side='More' if o>u else 'Less'
        rows.append(dict(start=row.start.strftime('%d %H:%M'),match=key,tour=tour,player=row.player,stat=row.stat,line=row.line,side=side,
                         p=round(max(o,u)/(1-pu),3),push=round(pu,3),anchor=is_anchor,fit_resid=round(resid,4),
                         pA=round(a+gp/2,3),pB=round(a-gp/2,3),favA=round(P_ge('win',0,a,gp),3),market=bool(mw)))
R=pd.DataFrame(rows); R.to_pickle('tennis_results2.pkl')
pd.set_option('display.width',230); pd.set_option('display.max_rows',100)
print(R[~R.anchor].sort_values('p',ascending=False).to_string(index=False))
print(R[R.anchor][['match','stat','line','side','p','fit_resid']].to_string(index=False))

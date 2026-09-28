"""
MNF model v2 - Eagles @ Bears.  Function-based so it can be auto-calibrated to sportsbook lines.
simulate(P, n, seed) -> {player: {stat: np.array}}
"""
import numpy as np, copy

BASE = dict(
 spread_bears=-3.5, margin_sd=13.0, conc=35, keenum_pulled=0.08,
 exit_p={'DeVonta Smith':0.08,'Saquon Barkley':0.08,'Kyle Monangai':0.08,"D'Andre Swift":0.04},
 team={
  'CHI': dict(plays=60, plays_sd=6, pass_rate=0.47, script_k=0.006, qb='Case Keenum', qb_comp=0.625,
              sack_scr=0.08, qb_runs=(1.5,3.0),
              rushers={"D'Andre Swift":0.57,'Kyle Monangai':0.37,'other':0.06},
              # 2026 wks1-2 targets blended with 2025 roles (Raymond 14, Burden 12, Odunze 7, Swift 7, Loveland 5, Mon ~4, Kmet ~3)
              targets={'Kalif Raymond':0.18,'Luther Burden III':0.18,'Rome Odunze':0.15,"D'Andre Swift":0.12,
                       'Colston Loveland':0.12,'Cole Kmet':0.065,'Kyle Monangai':0.055,'other':0.13}),
  'PHI': dict(plays=63, plays_sd=6, pass_rate=0.51, script_k=0.006, qb='Jalen Hurts', qb_comp=0.665,
              sack_scr=0.07, qb_runs=(4.3,4.8),
              rushers={'Saquon Barkley':0.74,'Tank Bigsby':0.16,'Will Shipley':0.06,'other':0.04},
              targets={'DeVonta Smith':0.29,'Dontayvion Wicks':0.17,'Makai Lemon':0.13,'Saquon Barkley':0.08,
                       'Johnny Mundt':0.07,'Zach Ertz':0.07,'other':0.19})},
 ypc={"D'Andre Swift":4.3,'Kyle Monangai':4.5,'Saquon Barkley':4.4,'Tank Bigsby':4.0,'Will Shipley':4.0,'other':3.8},
 catch={'Kalif Raymond':0.85,'Luther Burden III':0.70,'Rome Odunze':0.60,"D'Andre Swift":0.80,'Colston Loveland':0.66,
        'Cole Kmet':0.72,'Kyle Monangai':0.80,'DeVonta Smith':0.69,'Dontayvion Wicks':0.60,'Makai Lemon':0.66,
        'Saquon Barkley':0.76,'Johnny Mundt':0.70,'Zach Ertz':0.68,'other':0.65},
 ypr={'Kalif Raymond':8.8,'Luther Burden III':10.5,'Rome Odunze':14.2,"D'Andre Swift":6.8,'Colston Loveland':9.5,
      'Cole Kmet':9.0,'Kyle Monangai':7.5,'DeVonta Smith':13.6,'Dontayvion Wicks':15.5,'Makai Lemon':12.5,
      'Saquon Barkley':7.0,'Johnny Mundt':8.0,'Zach Ertz':9.0,'other':10.0},
)

def simulate(P, n=100_000, seed=1):
    rng=np.random.default_rng(seed)
    out={}
    def add(p,k,v): out.setdefault(p,{}); out[p][k]=out[p].get(k,0)+v
    def sum_draws(c,f):
        c=np.maximum(np.asarray(c).astype(int),0); mx=c.max()
        if mx==0: return np.zeros(n)
        d=f((n,mx)); return (d*(np.arange(mx)[None,:]<c[:,None])).sum(1)
    def carry(ypc):
        b=ypc-1.44
        def f(sh):
            x=rng.normal(b,3.2,sh); t=rng.random(sh)<0.12
            return np.where(t,x+rng.exponential(12,sh),np.maximum(x,-4))
        return f
    def recy(m,cv=0.85):
        s2=np.log(1+cv**2); mu=np.log(m)-s2/2
        return lambda sh: rng.lognormal(mu,np.sqrt(s2),sh)
    M=rng.normal(P['spread_bears'],P['margin_sd'],n)
    for team,T in P['team'].items():
        mg=M if team=='CHI' else -M
        plays=np.round(rng.normal(T['plays'],T['plays_sd'],n)+0.08*mg).clip(40,85)
        pr=np.clip(T['pass_rate']-T['script_k']*mg+rng.normal(0,0.05,n),0.3,0.8)
        db=rng.binomial(plays.astype(int),pr); runs=plays-db
        att=rng.binomial(db,1-T['sack_scr'])
        qbr=np.minimum(rng.poisson(T['qb_runs'][0],n)+(db-att)//2,runs); rbr=runs-qbr
        comp=np.clip(rng.normal(T['qb_comp'],0.06,n),0.35,0.9)
        qf=np.ones(n)
        if team=='CHI':
            pull=rng.random(n)<P['keenum_pulled']; qf=np.where(pull,rng.uniform(0.3,0.8,n),1.0)
        add(T['qb'],'Pass Attempts',np.round(att*qf)); add(T['qb'],'Rush Atts',np.round(qbr*qf))
        add(T['qb'],'Rush Yards',sum_draws(np.round(qbr*qf),carry(T['qb_runs'][1])))
        allp=set(T['rushers'])|set(T['targets'])
        fr={}
        for p in allp:
            e=rng.random(n)<P['exit_p'].get(p,0); fr[p]=np.where(e,rng.uniform(0.1,0.9,n),1.0)
        for kind,dct in (('run',T['rushers']),('tgt',T['targets'])):
            names=list(dct); w=np.array([dct[x] for x in names]); w=w/w.sum()
            sh=rng.dirichlet(w*P['conc'],n)*np.stack([fr[x] for x in names],1); sh/=sh.sum(1,keepdims=True)
            tot=rbr if kind=='run' else att
            cnt=[rng.binomial(tot.astype(int),sh[:,i]) for i in range(len(names))]
            if kind=='run':
                for x,c in zip(names,cnt):
                    if x=='other': continue
                    add(x,'Rush Atts',c); add(x,'Rush Yards',sum_draws(c,carry(P['ypc'][x])))
            else:
                py=np.zeros(n); cp=np.zeros(n)
                for x,c in zip(names,cnt):
                    cr=np.clip(P['catch'][x]+(comp-T['qb_comp']),0.2,0.97)
                    r=rng.binomial(c,cr); y=sum_draws(r,recy(P['ypr'][x])); py+=y; cp+=r
                    if x=='other': continue
                    add(x,'Rec Targets',c); add(x,'Recs',r); add(x,'Rec Yards',y)
                add(T['qb'],'Pass Yards',np.round(py*qf)); add(T['qb'],'Pass Comp',np.round(cp*qf))
    # victory-formation kneels count as QB rush attempts (about -1 yd each)
    for qb,win in (('Jalen Hurts',M<0),('Case Keenum',M>0)):
        k=np.where(win&(rng.random(n)<0.85),rng.integers(1,4,n),0)
        out[qb]['Rush Atts']=out[qb]['Rush Atts']+k; out[qb]['Rush Yards']=out[qb]['Rush Yards']-k
    for p in out:
        if 'Rush Yards' in out[p] or 'Rec Yards' in out[p]:
            out[p]['Rush+Rec Yds']=out[p].get('Rush Yards',0)+out[p].get('Rec Yards',0)
    out['_margin']={'m':M}
    return out

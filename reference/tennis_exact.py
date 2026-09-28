"""Exact Markov-chain distributions for a best-of-3 tennis match (tiebreak at 6-6).
match_dist(pA,pB) -> dict {(gamesA,gamesB,setsA,setsB,set1A,set1B): prob}  (averaged over who serves first)."""
import numpy as np
from functools import lru_cache
from scipy.optimize import minimize

def hold(p):
    q=1-p; return p**4*(1+4*q+10*q*q)+20*p**3*q**3*(p*p/(p*p+q*q))

def tb_win(pA,pB,a_first):
    # P(A wins tiebreak) - DP over points; server pattern A,B,B,A,A,... if A serves first
    N=30; P=np.zeros((N+1,N+1)); P[0,0]=1.0; win=0.0
    for s in range(0,2*N):
        for a in range(max(0,s-N),min(s,N)+1):
            b=s-a
            if a>N or b>N: continue
            pr=P[a,b]
            if pr==0: continue
            if (a>=7 or b>=7) and abs(a-b)>=2: continue
            k=a+b; srvA=((k+1)//2)%2==0
            if not a_first: srvA=not srvA
            pw=pA if srvA else 1-pB
            if a+1<=N:
                if a+1>=7 and a+1-b>=2: win+=pr*pw
                else: P[a+1,b]+=pr*pw
            if b+1<=N:
                if not(b+1>=7 and b+1-a>=2): P[a,b+1]+=pr*(1-pw)
    return win

def set_dist(pA,pB,a_first):
    """returns dict {(ga,gb, a_serves_next_set_first): prob}"""
    hA,hB=hold(pA),hold(pB); tbA=tb_win(pA,pB,a_first)
    D={}; P={(0,0):1.0}
    for _ in range(13):
        NP={}
        for (a,b),pr in P.items():
            k=a+b; srvA=(k%2==0)==a_first
            if a==6 and b==6:
                # tiebreak: counts as one game; next set first server = player who received first in TB
                nxt=not a_first if True else a_first
                D[(7,6,(k+1)%2==0)]=D.get((7,6,(k+1)%2==0),0)+pr*tbA
                D[(6,7,(k+1)%2==0)]=D.get((6,7,(k+1)%2==0),0)+pr*(1-tbA)
                continue
            pw=hA if srvA else 1-hB
            for (na,nb,pp) in ((a+1,b,pw),(a,b+1,1-pw)):
                fin=((na>=6 or nb>=6) and abs(na-nb)>=2) or na==7 or nb==7
                if fin:
                    nk=na+nb; a_next=((nk%2==0)==a_first)   # who serves next game = first of next set
                    key=(na,nb,a_next); D[key]=D.get(key,0)+pr*pp
                else: NP[(na,nb)]=NP.get((na,nb),0)+pr*pp
        P=NP
        if not P: break
    return D

def match_dist(pA,pB):
    out={}
    for a_first,w0 in ((True,0.5),(False,0.5)):
        S1=set_dist(pA,pB,a_first)
        for (g1a,g1b,af2),p1 in S1.items():
            s1a=int(g1a>g1b)
            S2=set_dist(pA,pB,af2)
            for (g2a,g2b,af3),p2 in S2.items():
                s2a=int(g2a>g2b)
                if s1a==s2a:
                    k=(g1a+g2a,g1b+g2b,2*s1a,2*(1-s1a),g1a,g1b); out[k]=out.get(k,0)+w0*p1*p2
                else:
                    S3=set_dist(pA,pB,af3)
                    for (g3a,g3b,_),p3 in S3.items():
                        s3a=int(g3a>g3b)
                        k=(g1a+g2a+g3a,g1b+g2b+g3b,1+s3a,1+(1-s3a),g1a,g1b); out[k]=out.get(k,0)+w0*p1*p2*p3
    return out

def stats(pA,pB):
    d=match_dist(pA,pB); keys=np.array(list(d.keys())); pr=np.array(list(d.values())); pr/=pr.sum()
    return keys,pr

def p_over(keys,pr,col,line,who=None):
    if col=='total': x=keys[:,0]+keys[:,1]
    elif col=='gw': x=keys[:,0] if who==0 else keys[:,1]
    elif col=='s1total': x=keys[:,4]+keys[:,5]
    elif col=='s1gw': x=keys[:,4] if who==0 else keys[:,5]
    elif col=='win': x=(keys[:,2]==2) if who==0 else (keys[:,3]==2); return (pr*x).sum()
    return (pr*(x>line)).sum()+0.5*(pr*(x==line)).sum()

def fit(anchors,tour):
    """anchors: list of (kind, who, line, target_prob). Returns pA,pB."""
    lo,hi=(0.50,0.80) if tour=='ATP' else (0.42,0.72)
    def loss(v):
        a,g=v; pA,pB=a+g/2,a-g/2
        if not(lo<=pA<=hi and lo<=pB<=hi): return 10
        k,p=stats(pA,pB)
        return sum((p_over(k,p,kind,line,who)-tgt)**2 for kind,who,line,tgt in anchors)
    best=None
    for a0 in ((0.62,0.66) if tour=='ATP' else (0.54,0.58)):
        for g0 in (-0.08,0.0,0.08):
            r=minimize(loss,[a0,g0],method='Nelder-Mead',options=dict(xatol=1e-4,fatol=1e-7,maxiter=200))
            if best is None or r.fun<best.fun: best=r
    a,g=best.x; return a+g/2,a-g/2,best.fun

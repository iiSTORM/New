"""
Tennis prop engine (best-of-3, standard tiebreak at 6-6), point-level Monte Carlo.
Inputs per match: pA, pB = prob. each player wins a point on own serve; ace/DF rates per service point.
calibrate(): solves pA,pB so the model matches two anchors (e.g. PrizePicks/book total games + one player's games won,
or a moneyline win prob + total). Outputs every PrizePicks tennis stat.
PrizePicks tennis fantasy (assumed): match played 10, game won +1, game lost -1, set won +3, set lost -3, ace +0.5, DF -0.5.
"""
import numpy as np
TOUR={'ATP':dict(ace=0.075,df=0.030,avg=0.64),'WTA':dict(ace=0.035,df=0.045,avg=0.56)}

def sim_match(pA,pB,n=30000,seed=0,ace=(0.075,0.075),df=(0.03,0.03)):
    rng=np.random.default_rng(seed)
    g=np.zeros((n,2),int); sets=np.zeros((n,2),int); aces=np.zeros((n,2)); dfs=np.zeros((n,2))
    breaks=np.zeros((n,2)); s1=None
    server=rng.integers(0,2,n)                    # who serves first
    done=np.zeros(n,bool)
    p=np.array([pA,pB]); A=np.array(ace); D=np.array(df)
    def play_game(srv,active):
        pts=np.zeros((n,2),int); fin=~active.copy(); win=np.full(n,-1)
        ps=p[srv]
        while not fin.all():
            live=~fin
            r=rng.random(n)
            wS=r<ps
            a=rng.random(n)<A[srv]/ps*1.0; d=rng.random(n)<D[srv]/(1-ps)
            idx=np.arange(n)
            aces[idx[live&wS&a],srv[live&wS&a]]+=1; dfs[idx[live&~wS&d],srv[live&~wS&d]]+=1
            pts[idx[live&wS],srv[live&wS]]+=1; pts[idx[live&~wS],1-srv[live&~wS]]+=1
            ps_=pts[idx,srv]; pr_=pts[idx,1-srv]
            sw=live&(ps_>=4)&(ps_-pr_>=2); rw=live&(pr_>=4)&(pr_-ps_>=2)
            win[sw]=srv[sw]; win[rw]=1-srv[rw]; fin|=sw|rw
        return win
    def play_tb(first,active):
        pts=np.zeros((n,2),int); fin=~active.copy(); win=np.full(n,-1); k=np.zeros(n,int)
        while not fin.all():
            live=~fin
            srv=np.where(((k+1)//2)%2==0,first,1-first)
            ps=p[srv]; wS=rng.random(n)<ps; idx=np.arange(n)
            pts[idx[live&wS],srv[live&wS]]+=1; pts[idx[live&~wS],1-srv[live&~wS]]+=1
            a_,b_=pts[:,0],pts[:,1]
            w0=live&(a_>=7)&(a_-b_>=2); w1=live&(b_>=7)&(b_-a_>=2)
            win[w0]=0; win[w1]=1; fin|=w0|w1; k+=live
        return win
    set_no=0
    while not done.all():
        sg=np.zeros((n,2),int); sdone=done.copy()
        while not sdone.all():
            act=~sdone
            tb=act&(sg[:,0]==6)&(sg[:,1]==6)
            if tb.any():
                w=play_tb(server,tb); idx=np.where(tb)[0]
                sg[idx,w[idx]]+=1; g[idx,w[idx]]+=1; server[idx]=1-server[idx]
            reg=act&~tb
            if reg.any():
                w=play_game(server,reg); idx=np.where(reg)[0]
                brk=w[idx]!=server[idx]; breaks[idx[brk],w[idx][brk]]+=1
                sg[idx,w[idx]]+=1; g[idx,w[idx]]+=1; server[idx]=1-server[idx]
            a_,b_=sg[:,0],sg[:,1]
            fin=((a_>=6)|(b_>=6))&(np.abs(a_-b_)>=2) | (a_==7)|(b_==7)
            sdone|=fin
        if set_no==0: s1=sg.copy()
        sw=np.where(sg[:,0]>sg[:,1],0,1); live=~done
        sets[np.where(live)[0],sw[live]]+=1
        done|=(sets.max(1)>=2); set_no+=1
    fant=10+g-g[:,::-1]+3*(sets-sets[:,::-1])+aces-dfs
    out={}
    for i in (0,1):
        out[i]={'Total Games':g.sum(1),'Total Games Won':g[:,i],'1st Set Total Games':s1.sum(1),
                '1st Set Total Games Won':s1[:,i],'Fantasy Score':fant[:,i],'Double Faults':dfs[:,i],
                'Break Points Won':breaks[:,i],'Aces':aces[:,i],'win':(sets[:,i]==2)}
    return out

def med_over(x,line): return (x>line).mean()+0.5*(x==line).mean()

def calibrate(tour,anchors,n=3000,iters=12):
    """anchors: list of (who, stat, line) where who in {0,1,'match'}; solves avg serve level & gap so each anchor is 50/50."""
    T=TOUR[tour]; avg=T['avg']; gap=0.0
    for it in range(iters):
        pA,pB=avg+gap/2,avg-gap/2
        o=sim_match(pA,pB,n,seed=it,ace=(T['ace'],)*2,df=(T['df'],)*2)
        for who,stat,line in anchors:
            x=o[0 if who=='match' else who][stat]; e=0.5-med_over(x,line)
            if stat=='Total Games' or stat=='1st Set Total Games': avg=np.clip(avg+0.06*e,0.45,0.85)
            elif stat=='Total Games Won': gap=np.clip(gap+(0.10*e if who==0 else -0.10*e),-0.35,0.35)
            elif stat=='win': gap=np.clip(gap+(0.12*e if who==0 else -0.12*e),-0.35,0.35)
    return avg+gap/2,avg-gap/2

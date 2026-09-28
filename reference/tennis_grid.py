import numpy as np, pickle, time
from tennis_exact import stats
G=np.round(np.arange(0.42,0.785,0.01),2); L=np.arange(0,40)   # thresholds 0..39 -> P(x>=t)
tabs={k:np.zeros((len(G),len(G),len(L))) for k in ('total','gwA','gwB','s1t','s1a','s1b')}
win=np.zeros((len(G),len(G)))
t=time.time()
for i,pA in enumerate(G):
    for j,pB in enumerate(G):
        k,p=stats(pA,pB)
        cols={'total':k[:,0]+k[:,1],'gwA':k[:,0],'gwB':k[:,1],'s1t':k[:,4]+k[:,5],'s1a':k[:,4],'s1b':k[:,5]}
        for nm,x in cols.items():
            h=np.bincount(x.astype(int),weights=p,minlength=41)[:41]
            tabs[nm][i,j]=1-np.concatenate([[0],np.cumsum(h)])[:40]   # P(x>=t)
        win[i,j]=p[k[:,2]==2].sum()
pickle.dump(dict(G=G,tabs=tabs,win=win),open('tennis_grid.pkl','wb')); print('done',round(time.time()-t))

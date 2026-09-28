"""
Fractional Kelly for PrizePicks slips with push / refund outcomes (written 9/28/26 in chat).
outcomes = list of (probability, gross_return_multiple) for ONE slip, e.g. a 2-pick power 3x with a
possible push that refunds:  [(0.39, 3.0), (0.25, 1.0), (0.36, 0.0)].
For several simultaneous slips pass joint outcomes: list of (prob, [return_slip1, return_slip2, ...]).
"""
import numpy as np
from scipy.optimize import minimize

def kelly(outcomes, fraction=0.5, cap=0.10):
    p = np.array([o[0] for o in outcomes]); r = np.array([o[1] for o in outcomes])
    g = lambda f: -np.sum(p * np.log(np.maximum(1 - f[0] + f[0] * r, 1e-12)))
    f = minimize(g, [0.05], bounds=[(0, 0.99)]).x[0]
    return min(f * fraction, cap)

def kelly_joint(outcomes, fraction=0.5, cap_each=0.10, cap_total=0.25):
    k = len(outcomes[0][1]); p = np.array([o[0] for o in outcomes]); R = np.array([o[1] for o in outcomes])
    g = lambda f: -np.sum(p * np.log(np.maximum(1 - f.sum() + R @ f, 1e-12)))
    f = minimize(g, np.full(k, 0.03), bounds=[(0, 0.9)] * k, constraints={'type': 'ineq', 'fun': lambda f: 0.95 - f.sum()}).x
    f = np.minimum(f * fraction, cap_each)
    return f * min(1, cap_total / max(f.sum(), 1e-12))

def stake(bankroll, frac, minimum=1.0):
    s = round(bankroll * frac, 2)
    return 0.0 if s < minimum * 0.5 else max(s, minimum)   # below half the minimum -> skip

if __name__ == '__main__':
    f = kelly([(0.39, 3.0), (0.25, 1.0), (0.36, 0.0)], fraction=0.5)
    print('break-point slip: bet %.1f%% of bankroll -> $%.2f of $13.19' % (100 * f, stake(13.19, f)))

# reference/ — models built in chat on 9/28/26

Hand this folder to Claude Code with the PropEdge prompt. These are working prototypes, not production code:
each was built for one night's slate and must be generalized (see "Known limits").

| Folder | File | What it does |
|---|---|---|
| prizepicks | load_prizepicks.py | Flattens a saved PrizePicks API payload into pp.pkl (one row per projection: player, team, league, stat, line, odds_type, start). `python load_prizepicks.py payload.json` |
| nfl | model_v2.py | Monte Carlo game sim: game script from spread, pass/run split, Dirichlet target/carry shares, per-play yards, kneels, early exits, Keenum-pull risk |
| nfl | calibrate.py | Tunes shares/efficiency until the model's 50/50 points match sportsbook lines (the MARKET list) |
| nfl | evaluate_v2.py | Scores every PrizePicks line with the usage model and the market-calibrated model; blend = 60% market / 40% usage |
| nfl | parlays_v2.py | Joint hit rates and EV for 1-3 leg slips straight from the simulations (correlation included) |
| tennis | tennis_exact.py | Exact Markov chain for best-of-3 (tiebreak at 6-6) from serve-point win % |
| tennis | tennis_grid.py | Precomputes probabilities over a serve-level grid -> tennis_grid.pkl (~80 s, run once) |
| tennis | run_tennis2.py | Fits each match to its anchors (total games + games won, or moneyline) with form noise; prices 1st-set and games props |
| tennis | tennis_model.py | Point-level simulator with player ace/DF rates -> fantasy score, double faults, break points won |
| tennis | run_tennis_atp.py | ATP player list + moneyline anchors used by run_tennis2.py |
| esports | esports_projections.py | CS2/Valorant per-player projections from the iiSTORM/New data files + graded under-rate priors |
| sizing | kelly.py | Fractional Kelly on a slip's outcome table (win/refund/loss), joint Kelly for simultaneous slips, caps, $1 minimum |

## Run order (NFL example)
1. `python prizepicks/load_prizepicks.py payload.json` -> pp.pkl
2. `cd nfl && python calibrate.py && PP_PKL=../pp.pkl python evaluate_v2.py && PP_PKL=../pp.pkl python parlays_v2.py`

## Known limits (fix while generalizing)
- nfl/model_v2.py BASE parameters and calibrate.py MARKET are hand-set for Eagles @ Bears, 9/28/26. They must come from weekly usage data (nflverse) and an odds API per game.
- In the market model, players without a sportsbook line get squeezed by calibration; trust only the usage number for them.
- Tennis iid point model overstates holds for big servers; check break props against real breaks per match.
- Tennis fantasy scoring (fixed in tennis_model.py): played 10, game +1/-1, set +3/-3, ace +1, double fault -1.
- cs2_data.json Bo3 totals are maps 1-2 only (handled in esports_projections.py).
- 3-pick power paid 6x on a real 9/28 slip; always read the multiplier off the slip.

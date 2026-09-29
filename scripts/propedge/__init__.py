"""PropEdge: a private PrizePicks tracker, bankroll ledger and slip pricer.

All six phases are here: the rules, the ledger, the sizing, a private store,
the analytics that decide how the sizing behaves, the row every model emits,
the builder that turns those rows into a ranked slate, and three models that
produce them -- esports from the scrapers' own data, NFL from a game simulation
calibrated to the book, tennis from an exact Markov chain fitted to it. The
models and the slip builder arrive in later phases and sit on top of the same
outcome-table machinery, which is why `sizing.outcome_table` prices a slip from
per-leg probabilities rather than from a single number.

Nothing in this package places a bet or talks to PrizePicks. It records what
was placed by hand and works out what it should have cost.
"""
from .analytics import (calibration, calibration_error, closing_lines, report,
                        roi, sizing_policy, wilson)
from .builder import build_slate, explain, render, search, take_disjoint
from .nfl import GameSetup, TeamSetup, calibrate, simulate
from .tennis import match_distribution, simulate_points
from .model import Projection, Simulations, price
from .money import dollars, fmt, multiply, to_cents
from .payouts import DEFAULT_TABLE, PayoutTable
from .slips import Leg, Slip, settle
from .ledger import Ledger, LedgerError
from .sizing import Outcome, kelly_fraction, outcome_table, recommend
from .store import PrivacyError, Tracker

__all__ = ["calibration", "calibration_error", "closing_lines", "report",
           "roi", "sizing_policy", "wilson",
           "build_slate", "explain", "render", "search", "take_disjoint",
           "GameSetup", "TeamSetup", "simulate", "calibrate",
           "match_distribution", "simulate_points",
           "Projection", "Simulations", "price",
           "to_cents", "dollars", "fmt", "multiply", "PayoutTable",
           "DEFAULT_TABLE", "Leg", "Slip", "settle", "Ledger", "LedgerError",
           "Outcome", "outcome_table", "kelly_fraction", "recommend", "Tracker",
           "PrivacyError"]

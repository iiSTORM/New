"""PropEdge: a private PrizePicks tracker, bankroll ledger and slip pricer.

Phases 1 and 2 are here: the rules, the ledger, the sizing, a private store,
and the analytics that decide how the sizing behaves. The
models and the slip builder arrive in later phases and sit on top of the same
outcome-table machinery, which is why `sizing.outcome_table` prices a slip from
per-leg probabilities rather than from a single number.

Nothing in this package places a bet or talks to PrizePicks. It records what
was placed by hand and works out what it should have cost.
"""
from .analytics import (calibration, calibration_error, closing_lines, report,
                        roi, sizing_policy, wilson)
from .money import dollars, fmt, multiply, to_cents
from .payouts import DEFAULT_TABLE, PayoutTable
from .slips import Leg, Slip, settle
from .ledger import Ledger, LedgerError
from .sizing import Outcome, kelly_fraction, outcome_table, recommend
from .store import PrivacyError, Tracker

__all__ = ["calibration", "calibration_error", "closing_lines", "report",
           "roi", "sizing_policy", "wilson",
           "to_cents", "dollars", "fmt", "multiply", "PayoutTable",
           "DEFAULT_TABLE", "Leg", "Slip", "settle", "Ledger", "LedgerError",
           "Outcome", "outcome_table", "kelly_fraction", "recommend", "Tracker",
           "PrivacyError"]

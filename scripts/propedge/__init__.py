"""PropEdge: a private PrizePicks tracker, bankroll ledger and slip pricer.

Phase 1 is here: the rules, the ledger, the sizing and a private store. The
models and the slip builder arrive in later phases and sit on top of the same
outcome-table machinery, which is why `sizing.outcome_table` prices a slip from
per-leg probabilities rather than from a single number.

Nothing in this package places a bet or talks to PrizePicks. It records what
was placed by hand and works out what it should have cost.
"""
from .money import dollars, fmt, multiply, to_cents
from .payouts import DEFAULT_TABLE, PayoutTable
from .slips import Leg, Slip, settle
from .ledger import Ledger, LedgerError
from .sizing import Outcome, kelly_fraction, outcome_table, recommend
from .store import PrivacyError, Tracker

__all__ = ["to_cents", "dollars", "fmt", "multiply", "PayoutTable",
           "DEFAULT_TABLE", "Leg", "Slip", "settle", "Ledger", "LedgerError",
           "Outcome", "outcome_table", "kelly_fraction", "recommend", "Tracker",
           "PrivacyError"]

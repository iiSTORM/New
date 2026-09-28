"""Where the bets live: a private JSON file, deliberately not in this repo.

This repository is PUBLIC and serves its data files straight off
raw.githubusercontent.com. A bankroll and a bet history do not belong in it,
and "remember not to commit it" is not a control. So the store refuses to
write anywhere inside a git work tree unless it is told to in as many words.

Default location: $PROPEDGE_DATA, else ~/.propedge/store.json -- outside any
checkout, so there is nothing for `git add -A` to pick up.

For a shared store (a phone and a laptop seeing the same balance), this file
is the seam: it is the only place that knows how bytes are read and written, so
a Supabase or private-repo backend replaces this and nothing above it changes.
"""
import json
import os
import tempfile
from pathlib import Path

from .ledger import Ledger, PAYOUT, REFUND, STAKE
from .payouts import PayoutTable
from .slips import Slip, settle

VERSION = 1
ENV_PATH = "PROPEDGE_DATA"
ENV_ALLOW_REPO = "PROPEDGE_ALLOW_REPO_PATH"


class PrivacyError(Exception):
    """A write that would put personal data somewhere public."""


def default_path():
    return Path(os.environ.get(ENV_PATH) or Path.home() / ".propedge" / "store.json")


def in_git_worktree(path):
    """The nearest .git at or above `path`, or None.

    Resolved first, so a symlink pointing into a checkout cannot get around it,
    and `path` ITSELF is checked before its parents: Path.parents does not
    include the path, so walking only the parents of the repo root misses a
    store written straight into the repo root -- which is the likeliest place
    for one to end up, and the one the guard exists for.
    """
    here = Path(path).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


class Tracker:
    """Slips, the ledger and the payout table, loaded from and saved to one file."""

    def __init__(self, path=None, slips=None, ledger=None, table=None):
        self.path = Path(path) if path else default_path()
        self.slips = list(slips or [])
        self.ledger = ledger or Ledger()
        self.table = table or PayoutTable()

    # ------------------------------------------------------------ persistence

    @classmethod
    def load(cls, path=None):
        path = Path(path) if path else default_path()
        if not path.exists():
            return cls(path)
        blob = json.loads(path.read_text(encoding="utf-8"))
        if blob.get("version") != VERSION:
            raise ValueError(f"{path} is version {blob.get('version')}, this code "
                             f"reads version {VERSION}")
        return cls(path,
                   slips=[Slip.from_json(s) for s in blob.get("slips", [])],
                   ledger=Ledger(blob.get("ledger", [])),
                   table=PayoutTable.from_json(blob.get("payout_table")))

    def save(self):
        repo = in_git_worktree(self.path.parent if self.path.parent.exists()
                               else self.path)
        if repo and not os.environ.get(ENV_ALLOW_REPO):
            raise PrivacyError(
                f"{self.path} is inside the git work tree at {repo}. This repo is "
                f"public — a bet history committed here is published. Set "
                f"{ENV_PATH} to a path outside any checkout (the default, "
                f"~/.propedge/store.json, is), or set {ENV_ALLOW_REPO}=1 if the "
                f"checkout really is private.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        blob = {"version": VERSION, "slips": [s.as_json() for s in self.slips],
                "ledger": self.ledger.as_json(),
                "payout_table": self.table.as_json()}
        # Written to a neighbouring temp file and renamed, so an interrupted
        # write cannot leave a truncated ledger where the real one was.
        handle, tmp = tempfile.mkstemp(dir=str(self.path.parent),
                                      prefix=".store-", suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as f:
                json.dump(blob, f, indent=1, sort_keys=True)
                f.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return self.path

    # ----------------------------------------------------------------- slips

    def find(self, slip_id):
        for slip in self.slips:
            if slip.id == slip_id or slip.id.endswith(slip_id):
                return slip
        raise KeyError(f"no slip matching {slip_id!r}")

    def place(self, slip, force=False):
        """Record a placed slip and debit the stake.

        `force` records a slip that breaks a PrizePicks rule. It exists because
        this is a LOG: if the app somehow accepted an entry, the tracker has to
        be able to represent it, or the balance goes wrong to protect a rule
        that was already broken.
        """
        problems = slip.problems()
        if problems and not force:
            raise ValueError("this slip breaks PrizePicks rules: "
                             + "; ".join(problems))
        self.slips.append(slip)
        self.ledger.add(STAKE, slip.stake_cents, slip.id,
                        note=f"{slip.mode} {slip.n_legs}-pick", at=slip.placed_at or None)
        return slip

    def settle(self, slip_id, actual_payout=None):
        """Grade a slip from its legs and credit whatever it pays."""
        slip = self.find(slip_id)
        got = settle(slip, self.table, actual_payout)
        slip.status = got.status
        slip.payout_cents = got.payout_cents
        if got.payout_cents:
            kind = REFUND if got.status == "refunded" else PAYOUT
            self.ledger.add(kind, got.payout_cents, slip.id,
                            note="; ".join(got.reasons)[:200])
        return got

    # ---------------------------------------------------------------- reading

    def balance(self):
        return self.ledger.balance()

    def pending(self):
        return [s for s in self.slips if not s.is_settled]

    def reused_players(self, day=None):
        """Players appearing on more than one slip, which is the mistake that
        cost two slips at once on 2026-09-28: one goalkeeper leg was on both, so
        one number busted both entries.

        Not an error -- it is legal on PrizePicks and the tracker only records
        what happened -- but it is the thing to notice, so it is reported.
        """
        seen = {}
        for slip in self.slips:
            if day and not str(slip.placed_at).startswith(str(day)):
                continue
            for player in {leg.player_key for leg in slip.legs}:
                seen.setdefault(player, []).append(slip.id)
        return {player: ids for player, ids in seen.items() if len(ids) > 1}

    def exposure(self):
        """Cents currently at risk on unsettled slips."""
        return sum(s.stake_cents for s in self.pending())

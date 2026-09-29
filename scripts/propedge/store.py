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
import re
import subprocess
import tempfile
from pathlib import Path

from .ledger import Ledger, PAYOUT, REFUND, STAKE
from .payouts import PayoutTable
from .slips import Slip, settle

VERSION = 1
ENV_PATH = "PROPEDGE_DATA"
ENV_ALLOW_REPO = "PROPEDGE_ALLOW_REPO_PATH"
ENV_SYNC = "PROPEDGE_SYNC"

#: Values of ENV_ALLOW_REPO that mean "yes" rather than naming a repository.
#: Accepted only for a checkout with NO remote, which cannot publish anything.
_FLAGS = {"1", "true", "yes", "on"}


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


def _git_dir(repo):
    """The real .git directory for a checkout.

    `.git` is a FILE in a linked worktree or a submodule, holding
    "gitdir: <path>". Reading the config out of it blindly would find nothing
    and silently conclude the checkout has no remote, which is the permissive
    answer -- so the indirection is followed rather than ignored.
    """
    dot = Path(repo) / ".git"
    if dot.is_dir():
        return dot
    try:
        pointer = dot.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not pointer.startswith("gitdir:"):
        return None
    target = Path(pointer.split(":", 1)[1].strip())
    if not target.is_absolute():
        target = (Path(repo) / target).resolve()
    return target if target.exists() else None


def remote_of(repo):
    """origin's URL for a checkout, or None if it has none.

    Read out of .git/config rather than shelled out to `git remote`, so the
    guard does not depend on a git binary being on PATH and cannot be slowed
    or hung by one.
    """
    git_dir = _git_dir(repo)
    if not git_dir:
        return None
    try:
        config = (git_dir / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    found = re.search(r'\[remote\s+"origin"\]((?:[^\[]|\n)*)', config)
    if not found:
        return None
    url = re.search(r"^\s*url\s*=\s*(\S+)", found.group(1), re.M)
    return url.group(1) if url else None


def canonical_remote(url):
    """A remote URL reduced to host/owner/repo, for comparing two spellings.

    git@github.com:iiSTORM/propedge.git, ssh://git@github.com/iiSTORM/propedge
    and https://github.com/iiSTORM/propedge are one repository written three
    ways, and a guard that only matched the exact string would send someone
    looking for a typo instead of telling them the truth.
    """
    if not url:
        return ""
    text = str(url).strip().rstrip("/")
    text = re.sub(r"\.git$", "", text)
    text = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", text)   # scheme
    text = re.sub(r"^[^/@]+@", "", text)                       # user@
    text = text.replace(":", "/", 1) if "/" not in text.split(":")[0] else text
    return re.sub(r"/+", "/", text).lower()


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
        if repo:
            allow = (os.environ.get(ENV_ALLOW_REPO) or "").strip()
            if not allow:
                raise PrivacyError(
                    f"{self.path} is inside the git work tree at {repo}. That "
                    f"repo may be public — a bet history committed there is "
                    f"published. Set {ENV_PATH} to a path outside any checkout "
                    f"(the default, ~/.propedge/store.json, is), or set "
                    f"{ENV_ALLOW_REPO} to the remote URL of the private "
                    f"repository this store belongs in.")
            # A bare "1" permits ANY checkout, which is fine only where there
            # is nothing to publish to. Once a checkout has a remote the
            # override has to NAME it: otherwise one exported variable, set
            # months ago for the private repo, silently blesses the public one
            # the day a path is mistyped. Naming it turns that into an error.
            remote = remote_of(repo)
            if remote and allow.lower() in _FLAGS:
                raise PrivacyError(
                    f"{self.path} is inside a checkout of {remote}, and "
                    f"{ENV_ALLOW_REPO} is set to {allow!r}. A bare flag is not "
                    f"enough for a checkout that has a remote — it would bless "
                    f"every repository on this machine. Set {ENV_ALLOW_REPO} to "
                    f"that remote's URL so the override names the one "
                    f"repository it permits.")
            if remote and canonical_remote(allow) != canonical_remote(remote):
                raise PrivacyError(
                    f"{self.path} is inside a checkout of {remote}, but "
                    f"{ENV_ALLOW_REPO} names {allow}. Refusing to write a bet "
                    f"history into a repository the override does not name.")
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


def sync(path, message, remote="origin"):
    """Commit and push the store, so a private repo is the copy of record.

    Off unless PROPEDGE_SYNC is set, because a git push is a network call with
    a credential behind it and a tracker must keep working on a train. When it
    is off, or when anything here fails, the store on disk is already written
    and correct -- this only decides whether the remote has caught up -- so
    nothing raises. It returns a line to print instead, and the caller says so.

    Only the store file is staged, by name. Never `git add -A`: the checkout is
    the user's and whatever else is sitting in it is not this tool's to commit.
    """
    if not os.environ.get(ENV_SYNC):
        return None
    path = Path(path).resolve()
    repo = in_git_worktree(path.parent if path.parent.exists() else path)
    if not repo:
        return f"not syncing: {path} is not inside a checkout"

    def git(*args):
        return subprocess.run(("git", "-C", str(repo), *args), capture_output=True,
                              text=True, timeout=120)

    try:
        relative = path.relative_to(Path(repo).resolve())
        staged = git("add", "--", str(relative))
        if staged.returncode != 0:
            return f"not syncing: git add failed — {staged.stderr.strip()}"
        if git("diff", "--quiet", "--cached", "--", str(relative)).returncode == 0:
            return "nothing to sync: the stored file is unchanged"
        done = git("commit", "-m", message, "--", str(relative))
        if done.returncode != 0:
            return f"not syncing: git commit failed — {done.stderr.strip()}"
        pushed = git("push", remote, "HEAD")
        if pushed.returncode != 0:
            return (f"committed locally but NOT pushed — {pushed.stderr.strip()}. "
                    f"Run `git -C {repo} push` when you have a connection.")
        return f"synced to {remote}"
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return f"not syncing: {e.__class__.__name__}: {e}"

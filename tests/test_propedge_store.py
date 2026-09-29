"""The private store, and the guard that keeps bets out of a public repo.

iiSTORM/New is public and serves its data files off raw.githubusercontent.com.
A bet history in it is published, and "remember not to commit it" is not a
control -- `git add -A` does not ask. So the store refuses to write anywhere
inside a git work tree unless it is told, in an environment variable, that the
checkout is private.

The amounts here are invented. The real seed's arithmetic is asserted in the
private seed script, not in this public file, for the same reason.
"""
import json
import os

import subprocess

import pytest

from propedge.ledger import DEPOSIT
from propedge.money import to_cents
from propedge.slips import Leg, Slip
from propedge.store import (ENV_ALLOW_REPO, ENV_SYNC, PrivacyError, Tracker,
                            canonical_remote, in_git_worktree, remote_of, sync)


def leg(player, team, line=15.5, side="over", **kw):
    return Leg(sport="cs2", player=player, team=team, stat="kills", line=line,
               side=side, **kw)


def two_legs():
    return [leg("P", "Alpha"), leg("Q", "Beta", side="under")]


# ------------------------------------------------------------------- privacy

def test_a_store_inside_a_git_worktree_is_refused(tmp_path):
    (tmp_path / ".git").mkdir()
    checkout = tmp_path / "repo_subdir"
    checkout.mkdir()
    tracker = Tracker(checkout / "store.json")
    tracker.ledger.add(DEPOSIT, 2500)
    with pytest.raises(PrivacyError, match="public"):
        tracker.save()
    assert not (checkout / "store.json").exists()


def test_a_flag_overrides_the_guard_only_where_there_is_no_remote(tmp_path, monkeypatch):
    """A checkout with no remote has nowhere to publish to, so a bare yes is
    enough. That is the only case it is enough for."""
    (tmp_path / ".git").mkdir()
    monkeypatch.setenv(ENV_ALLOW_REPO, "1")
    tracker = Tracker(tmp_path / "store.json")
    tracker.ledger.add(DEPOSIT, 2500)
    assert tracker.save().exists()


def make_checkout(root, remote):
    """A directory that looks like a checkout of `remote` to the guard."""
    git = root / ".git"
    git.mkdir()
    (git / "config").write_text(
        '[core]\n\trepositoryformatversion = 0\n'
        f'[remote "origin"]\n\turl = {remote}\n'
        '\tfetch = +refs/heads/*:refs/remotes/origin/*\n', encoding="utf-8")
    return root


PRIVATE = "https://github.com/iiSTORM/propedge"
PUBLIC = "https://github.com/iiSTORM/New"


def test_a_bare_flag_is_refused_once_the_checkout_has_a_remote(tmp_path, monkeypatch):
    """The hole worth closing.

    One PROPEDGE_ALLOW_REPO_PATH=1 exported months ago for the private repo
    blesses every checkout on the machine, including the public one, the day a
    path is mistyped. Requiring the override to NAME a repository turns that
    from a silent publish into an error.
    """
    make_checkout(tmp_path, PUBLIC)
    monkeypatch.setenv(ENV_ALLOW_REPO, "1")
    tracker = Tracker(tmp_path / "store.json")
    tracker.ledger.add(DEPOSIT, 2500)
    with pytest.raises(PrivacyError, match="bare flag"):
        tracker.save()
    assert not (tmp_path / "store.json").exists()


def test_the_override_must_name_the_checkout_it_is_used_in(tmp_path, monkeypatch):
    make_checkout(tmp_path, PUBLIC)
    monkeypatch.setenv(ENV_ALLOW_REPO, PRIVATE)
    tracker = Tracker(tmp_path / "store.json")
    tracker.ledger.add(DEPOSIT, 2500)
    with pytest.raises(PrivacyError, match="does not name"):
        tracker.save()
    assert not (tmp_path / "store.json").exists()


def test_naming_the_checkout_lets_it_through(tmp_path, monkeypatch):
    make_checkout(tmp_path, PRIVATE)
    monkeypatch.setenv(ENV_ALLOW_REPO, PRIVATE)
    tracker = Tracker(tmp_path / "store.json")
    tracker.ledger.add(DEPOSIT, 2500)
    assert tracker.save().exists()


@pytest.mark.parametrize("spelling", [
    "git@github.com:iiSTORM/propedge.git",
    "https://github.com/iiSTORM/propedge",
    "https://github.com/iiSTORM/propedge.git",
    "ssh://git@github.com/iiSTORM/propedge",
    "https://github.com/iistorm/propedge/",
])
def test_the_same_repository_written_any_way_matches(tmp_path, monkeypatch, spelling):
    """Otherwise the guard sends you hunting for a typo instead of the truth."""
    make_checkout(tmp_path, "git@github.com:iiSTORM/propedge.git")
    monkeypatch.setenv(ENV_ALLOW_REPO, spelling)
    tracker = Tracker(tmp_path / "store.json")
    tracker.ledger.add(DEPOSIT, 2500)
    assert tracker.save().exists()


def test_two_different_repositories_never_collapse_together():
    assert canonical_remote(PRIVATE) != canonical_remote(PUBLIC)
    assert canonical_remote("git@github.com:a/b.git") == canonical_remote(
        "https://github.com/A/B")


def test_a_worktree_pointer_file_is_followed_rather_than_ignored(tmp_path):
    """.git is a FILE in a linked worktree. Failing to follow it would read no
    config, find no remote, and take the permissive branch."""
    real = tmp_path / "real"
    real.mkdir()
    make_checkout(real, PUBLIC)
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / ".git").write_text(f"gitdir: {real / '.git'}\n", encoding="utf-8")
    assert remote_of(linked) == PUBLIC


def test_a_checkout_with_no_config_reads_as_having_no_remote(tmp_path):
    (tmp_path / ".git").mkdir()
    assert remote_of(tmp_path) is None


def test_a_path_outside_any_checkout_saves(tmp_path):
    tracker = Tracker(tmp_path / "nested" / "store.json")
    tracker.ledger.add(DEPOSIT, 2500)
    assert tracker.save().exists()


def test_a_store_in_the_repo_ROOT_is_refused(tmp_path):
    """The case the first version of the guard missed, and the likeliest one.

    in_git_worktree walked Path.parents, which does not include the path
    itself -- so a store written to <repo>/bets.json looked clean, because the
    only directory holding .git was the one never checked. The bug let a real
    ledger land next to the committed data files.
    """
    (tmp_path / ".git").mkdir()
    tracker = Tracker(tmp_path / "bets.json")
    tracker.ledger.add(DEPOSIT, 2500)
    with pytest.raises(PrivacyError, match="public"):
        tracker.save()
    assert not (tmp_path / "bets.json").exists()


def test_this_repo_is_detected_as_a_worktree():
    """The guard has to fire for the repo the code lives in, or it is theatre."""
    assert in_git_worktree(__file__) is not None
    assert in_git_worktree("scripts/propedge/store.py") is not None
    # and for the repo root itself, not only for files inside it
    assert in_git_worktree(".") is not None


# --------------------------------------------------------------- persistence

def test_a_tracker_round_trips_through_the_file(tmp_path):
    tracker = Tracker(tmp_path / "store.json")
    tracker.ledger.add(DEPOSIT, 2500, note="starting bankroll")
    slip = Slip(mode="power", stake_cents=to_cents("1.50"), multiplier="3",
                legs=two_legs(), placed_at="2026-09-28T22:00:00+00:00")
    tracker.place(slip)
    tracker.save()

    back = Tracker.load(tmp_path / "store.json")
    assert back.balance() == tracker.balance() == 2350
    assert [s.id for s in back.slips] == [slip.id]
    assert back.slips[0].multiplier == slip.multiplier
    assert back.slips[0].legs[1].side == "under"


def test_a_future_version_is_refused_rather_than_misread(tmp_path):
    path = tmp_path / "store.json"
    path.write_text(json.dumps({"version": 99, "slips": [], "ledger": []}))
    with pytest.raises(ValueError, match="version"):
        Tracker.load(path)


def test_a_missing_file_is_an_empty_tracker(tmp_path):
    tracker = Tracker.load(tmp_path / "nothing.json")
    assert tracker.balance() == 0 and tracker.slips == []


def test_saving_leaves_no_temp_files_behind(tmp_path):
    tracker = Tracker(tmp_path / "store.json")
    tracker.ledger.add(DEPOSIT, 2500)
    tracker.save()
    tracker.save()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["store.json"]


# -------------------------------------------------------------- placing slips

def test_placing_debits_the_stake(tmp_path):
    tracker = Tracker(tmp_path / "store.json")
    tracker.ledger.add(DEPOSIT, 2500)
    tracker.place(Slip(mode="power", stake_cents=100, multiplier="3", legs=two_legs()))
    assert tracker.balance() == 2400
    assert tracker.exposure() == 100


def test_a_slip_breaking_a_rule_is_refused_but_can_be_forced(tmp_path):
    tracker = Tracker(tmp_path / "store.json")
    one_team = [leg("P", "Alpha"), leg("Q", "Alpha")]
    bad = Slip(mode="power", stake_cents=100, legs=one_team)
    with pytest.raises(ValueError, match="2 different teams"):
        tracker.place(bad)
    assert tracker.balance() == 0
    tracker.place(bad, force=True)
    assert tracker.balance() == -100


def test_settling_credits_once_and_only_once(tmp_path):
    tracker = Tracker(tmp_path / "store.json")
    tracker.ledger.add(DEPOSIT, 2500)
    slip = Slip(mode="power", stake_cents=100, multiplier="3", legs=two_legs())
    tracker.place(slip)
    for l in slip.legs:
        l.result = "won"
    got = tracker.settle(slip.id)
    assert (got.payout_cents, tracker.balance()) == (300, 2700)
    from propedge.ledger import LedgerError
    with pytest.raises(LedgerError, match="already settled"):
        tracker.settle(slip.id)
    assert tracker.balance() == 2700


def test_a_refund_returns_the_stake_and_nothing_more(tmp_path):
    tracker = Tracker(tmp_path / "store.json")
    tracker.ledger.add(DEPOSIT, 2500)
    slip = Slip(mode="power", stake_cents=150, multiplier="3",
                legs=[leg("P", "Alpha", line=15, result="push"),
                      leg("Q", "Beta", result="won")])
    tracker.place(slip)
    got = tracker.settle(slip.id)
    assert (got.status, tracker.balance()) == ("refunded", 2500)


def test_a_slip_found_by_the_tail_of_its_id(tmp_path):
    tracker = Tracker(tmp_path / "store.json")
    slip = Slip(mode="power", stake_cents=100, multiplier="3", legs=two_legs())
    tracker.place(slip)
    assert tracker.find(slip.id[-6:]).id == slip.id
    with pytest.raises(KeyError):
        tracker.find("nope")


def test_a_player_on_two_slips_the_same_night_is_reported(tmp_path):
    """The 2026-09-28 lesson: one leg on two slips busted both at once."""
    tracker = Tracker(tmp_path / "store.json")
    shared = dict(placed_at="2026-09-28T22:00:00+00:00")
    tracker.place(Slip(mode="power", stake_cents=150, multiplier="3", **shared,
                       legs=[leg("Keeper", "Alpha"), leg("Q", "Beta")]))
    tracker.place(Slip(mode="power", stake_cents=250, multiplier="6", **shared,
                       legs=[leg("Keeper", "Alpha"), leg("R", "Gamma"),
                             leg("S", "Delta")]))
    reused = tracker.reused_players("2026-09-28")
    assert list(reused) == ["cs2:keeper"]
    assert len(reused["cs2:keeper"]) == 2
    assert tracker.reused_players("2026-09-27") == {}


# ---------------------------------------------- a night, end to end, on paper

def test_a_whole_night_adds_up(tmp_path):
    """Two losses and two pending slips, with invented amounts: the shape of a
    night, so the ledger arithmetic is covered end to end."""
    tracker = Tracker(tmp_path / "store.json")
    tracker.ledger.add(DEPOSIT, to_cents("25.00"), note="starting bankroll")
    stakes = ["2.00", "3.00", "1.00", "1.00"]
    slips = []
    for i, stake in enumerate(stakes):
        slip = Slip(mode="power", stake_cents=to_cents(stake), multiplier="3",
                    legs=[leg(f"P{i}", "Alpha"), leg(f"Q{i}", "Beta")],
                    placed_at="2026-09-28T22:00:00+00:00")
        tracker.place(slip)
        slips.append(slip)
    assert tracker.balance() == to_cents("18.00")

    for slip in slips[:2]:                      # the two that lost
        slip.legs[0].result, slip.legs[1].result = "won", "lost"
        tracker.settle(slip.id, actual_payout="0")
    assert tracker.balance() == to_cents("18.00")     # a loss pays nothing
    assert tracker.exposure() == to_cents("2.00")     # the two still pending
    assert len(tracker.pending()) == 2
    tracker.save()
    assert Tracker.load(tmp_path / "store.json").balance() == to_cents("18.00")


# ---------------------------------------------------------------------- sync

def test_sync_is_off_unless_asked_for(tmp_path, monkeypatch):
    """A tracker has to keep working on a train."""
    monkeypatch.delenv(ENV_SYNC, raising=False)
    assert sync(tmp_path / "store.json", "test") is None


def test_sync_outside_a_checkout_says_so_rather_than_failing(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_SYNC, "1")
    assert "not inside a checkout" in sync(tmp_path / "store.json", "test")


def test_sync_commits_and_reports_when_there_is_nothing_to_commit(tmp_path, monkeypatch):
    """The store is written on every command; most of them change it, and the
    ones that do not should say so instead of making an empty commit."""
    monkeypatch.setenv(ENV_SYNC, "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    store = repo / "store.json"
    store.write_text("{}", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "store.json"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "first"], check=True)

    assert "nothing to sync" in sync(store, "no change")

    store.write_text('{"ledger": []}', encoding="utf-8")
    note = sync(store, "a change")
    # No remote is configured, so the push fails and the message says the
    # commit survived -- which is the outcome that matters.
    assert "NOT pushed" in note
    log = subprocess.run(["git", "-C", str(repo), "log", "--oneline", "-1"],
                         capture_output=True, text=True)
    assert "a change" in log.stdout


def test_sync_stages_only_the_store(tmp_path, monkeypatch):
    """The checkout is the user's. Whatever else is sitting in it is not this
    tool's to commit, and `git add -A` would take all of it."""
    monkeypatch.setenv(ENV_SYNC, "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "store.json").write_text("{}", encoding="utf-8")
    (repo / "private_notes.txt").write_text("not yours", encoding="utf-8")
    sync(repo / "store.json", "first")
    tracked = subprocess.run(["git", "-C", str(repo), "ls-files"],
                             capture_output=True, text=True).stdout.split()
    assert tracked == ["store.json"]

#!/usr/bin/env python3
"""
An accumulating record of pre-match odds, because CS2's cannot be backfilled.

scripts/map_context.py explains the asymmetry: vlr.gg keeps the pre-match
prices on a finished match's page, so Valorant backfills itself the next time
the scraper walks the pages it already walks. bo3.gg does not. Once a CS2 match
is finished its `bet_updates` field holds the LAST price seen, and that is an
in-play price -- 13.6 against 1.016 on a real sample, which is the scoreboard
rather than a forecast.

So a CS2 pre-match price exists for exactly as long as the match is upcoming,
and if nobody writes it down while the scraper is passing, it is gone. This is
that file. It accumulates: a run adds what it saw and never drops what it did
not, because a match this run cannot see is one whose odds only this file has.

Consequences worth being honest about:
  * A backtest on CS2 odds can only cover matches captured since this shipped.
    There is no way to recover the ones before it, and no amount of scraping
    changes that.
  * Both the first and the last pre-match sighting are kept. The last is nearer
    kickoff and is the sharper forecast; the first is further out and is what a
    board posted early would have been priced against. Which one answers a
    question depends on the question.
"""
import json
import os

PATH = "odds_history.json"
#: Guards the file against unbounded growth. At two runs a day and a few dozen
#: upcoming CS2 matches per run this is years of history, and a file that has
#: drifted past it is one to look at rather than one to keep writing to.
MAX_MATCHES = 200_000


def load(path=PATH):
    """The stored captures, or an empty store when there is no file yet."""
    try:
        with open(path) as handle:
            stored = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    captured = stored.get("captured") if isinstance(stored, dict) else None
    return captured if isinstance(captured, dict) else {}


def generated_at(path=PATH):
    """When the stored file was last written, or None if there is no file.

    Read separately from load() so a merge can carry the stamp forward. The
    merge step in the workflow runs between a scraper writing the file and git
    committing it, and without this the committed copy loses the only
    provenance it has.
    """
    try:
        with open(path) as handle:
            stored = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return stored.get("generated_at") if isinstance(stored, dict) else None


def record(store, key, odds, when, **fields):
    """Add one sighting, keeping the first and the last.

    Returns True when the store changed, so a caller can report how much of a
    run was new rather than printing a total that never moves.
    """
    if not key or not odds:
        return False
    entry = store.get(key)
    if entry is None:
        store[key] = {"first_seen": when, "last_seen": when,
                      "first": odds, "last": odds, **fields}
        return True
    # Later is the only direction this moves. A run that somehow replays an
    # older capture must not overwrite a sighting nearer to kickoff.
    if str(when) < str(entry.get("last_seen") or ""):
        return False
    changed = entry.get("last") != odds or entry.get("last_seen") != when
    entry["last_seen"] = when
    entry["last"] = odds
    for name, value in fields.items():
        if value is not None:
            entry[name] = value
    return changed


def save(store, path=PATH, generated_at=None):
    """Write the store, refusing to shrink a file that already exists.

    The refusal is the point. Every other JSON file in this repository is
    wholesale-regenerated each run and "whoever pushes last wins with their
    snapshot" is correct for them. It is exactly wrong here: this file is the
    only copy of a price that no longer exists anywhere, so a run that saw
    fewer matches than the last one must not be allowed to publish its smaller
    view. Callers merge into what load() gave them, and a shrink means that
    merge did not happen.
    """
    if len(store) > MAX_MATCHES:
        raise ValueError(f"{len(store)} captures exceeds MAX_MATCHES "
                         f"({MAX_MATCHES}) — look at the file rather than "
                         "writing it")
    existing = load(path)
    if len(store) < len(existing):
        raise ValueError(f"refusing to write {len(store)} captures over "
                         f"{len(existing)} already stored — this file "
                         "accumulates and is never regenerated")
    payload = {"generated_at": generated_at, "captured": store}
    tmp = f"{path}.tmp"
    with open(tmp, "w") as handle:
        json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, path)
    return len(store)

#!/usr/bin/env python3
"""Union of two career files, keeping the better record per player.

Written after a run undid a better one. Two scrapes overlapped: run 146
started from a 263-record cache, spent forty minutes, and committed
1,246 players with game history. Run 147 had already checked out before
that commit landed, so its base was the SAME stale 263 -- it spent its
budget, reached 871, and its snapshot replaced the 1,246 wholesale.
Nothing was broken; the later finisher simply knew less.

  cs2_career_data.json   263 -> 1,246 (run 146)  ->  871 (run 147)

The commit step's "whoever pushes last wins with their snapshot" rule is
right for cs2_data.json, which every run regenerates in full. It is
wrong for a cache that ACCUMULATES: there, later is not better, more is.

So the snapshot is merged into whatever is already committed rather than
replacing it. A record with game history beats one without; between two
that both have history, the longer one wins, since MATCHES_PER_PLAYER is
a rolling window and a short list means a run that got cut off rather
than a player who stopped playing.

Usage:
    python scripts/merge_career_files.py MINE BASE OUT
    git show origin/main:cs2_career_data.json > /tmp/base.json
"""
import json
import sys


def games_in(record):
    return len((record or {}).get("games") or [])


def better(a, b):
    """The record to keep. `a` wins ties, so pass this run's first."""
    if b is None:
        return a
    if a is None:
        return b
    return a if games_in(a) >= games_in(b) else b


def merge(mine, base):
    """Union of both, keeping the richer record for every player."""
    out = dict(base or {})
    for name, record in (mine or {}).items():
        out[name] = better(record, out.get(name))
    return out


def load(path):
    try:
        with open(path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        print(f"  {path}: unreadable ({e.__class__.__name__}) — treated as empty",
              file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def main():
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    mine_path, base_path, out_path = sys.argv[1:4]
    mine, base = load(mine_path), load(base_path)
    merged = merge(mine, base)
    with open(out_path, "w") as f:
        json.dump(merged, f, separators=(",", ":"), default=str)

    def with_games(d):
        return sum(1 for v in d.values() if (v or {}).get("games"))

    print(f"career merge: this run {len(mine)} records / {with_games(mine)} with games, "
          f"committed {len(base)} / {with_games(base)} -> "
          f"{len(merged)} / {with_games(merged)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

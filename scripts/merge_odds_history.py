#!/usr/bin/env python3
"""
Merge two odds_history.json files, keeping every capture from both.

    python scripts/merge_odds_history.py <mine> <theirs> <out>

The scrape workflow pushes with "whoever pushes last wins with their snapshot",
which is right for every other file it writes: they are wholesale-regenerated
each run, so a content-level merge of two near-total rewrites was never going
to mean anything. It is exactly wrong for this one. A capture is a price
bo3.gg has already stopped serving, so a run that resets to origin and
re-applies its own snapshot would silently delete whatever another run
captured in the meantime -- and nothing would look broken, because a file full
of real prices is indistinguishable from a file full of real prices with some
missing.

Same shape as merge_career_files.py, and for the same reason: the file
accumulates, so later is not better, more is.
"""
import json
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])

import odds_store


def merge(mine, theirs):
    """Union of two stores, keeping the earliest first sighting and the latest
    last sighting for any match both of them saw."""
    # A non-dict entry is a corrupt row, not a capture, and copying it would
    # propagate the corruption into the merged file both sides then keep.
    out = {key: dict(value) for key, value in theirs.items()
           if isinstance(value, dict)}
    for key, entry in mine.items():
        if not isinstance(entry, dict):
            continue
        kept = out.get(key)
        if not isinstance(kept, dict):
            out[key] = dict(entry)
            continue
        merged = dict(kept)
        if str(entry.get("first_seen") or "") < str(kept.get("first_seen") or "~"):
            merged["first_seen"] = entry.get("first_seen")
            merged["first"] = entry.get("first")
        if str(entry.get("last_seen") or "") > str(kept.get("last_seen") or ""):
            merged["last_seen"] = entry.get("last_seen")
            merged["last"] = entry.get("last")
        for name, value in entry.items():
            if name not in ("first", "last", "first_seen", "last_seen") \
                    and merged.get(name) is None and value is not None:
                merged[name] = value
        out[key] = merged
    return out


def main(argv):
    if len(argv) != 4:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    mine, theirs, out = argv[1], argv[2], argv[3]
    merged = merge(odds_store.load(mine), odds_store.load(theirs))
    # The later of the two stamps, so the committed copy keeps the provenance
    # of the run that produced it rather than losing it in the merge.
    stamps = [s for s in (odds_store.generated_at(mine),
                          odds_store.generated_at(theirs)) if s]
    # save() refuses to shrink, and the destination here is a scratch path
    # rather than either input, so it is written directly.
    payload = {"generated_at": max(stamps) if stamps else None,
               "captured": merged}
    with open(out, "w") as handle:
        json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
    print(f"merged {len(odds_store.load(mine))} + {len(odds_store.load(theirs))} "
          f"-> {len(merged)} captures")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

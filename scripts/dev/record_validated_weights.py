#!/usr/bin/env python3
"""Records a weight set as validated, after you have validated it.

scripts/dev/validated_weights.json holds, per game and stat, the FULL weight
set as it stood when it was last measured. tests/test_weight_provenance.py
fails while the shipped weights differ from a recorded set, which is the point:
a weight measured against one tier configuration is invalidated when another
tier changes underneath it, and this project has been bitten by that three
times (CS2's shrink meaning two different things depending on whether the
career tier was on; a "+149.84%" that was a locked-out tier; a Valorant kp
weight fitted around a parameter written as a literal 0).

So the record has to be easy to keep true, or it rots and the test that
depends on it gets deleted. This reads the live weights out of src/app.jsx and
writes the stanza for you -- it never invents a measurement, which is why
--note is required and why it refuses a note that says nothing.

    python scripts/dev/record_validated_weights.py --game cs2 --stat kills \
        --note "career 1.0 and shrink 8.0 measured together over 8 folds: ..."

    python scripts/dev/record_validated_weights.py --check   # what has drifted
"""
import argparse
import json
import os
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import diagnose_calibration as dc

RECORD_PATH = os.path.join(HERE, "validated_weights.json")
DEFAULT_COMMAND = "python scripts/dev/optimize_weights.py --game {game} --validate"
MIN_NOTE = 30


def load():
    with open(RECORD_PATH) as f:
        return json.load(f)


def save(record):
    with open(RECORD_PATH, "w") as f:
        json.dump(record, f, indent=2)
        f.write("\n")


def drifted(shipped, record):
    """[(game, stat, weight, shipped value, recorded value)] for every
    difference, in a stable order."""
    out = []
    for game in sorted(shipped):
        for stat in sorted(shipped[game]):
            live = shipped[game][stat]
            held = (record.get(game, {}).get(stat) or {}).get("weights")
            if held is None:
                out.append((game, stat, "*", "shipped", "not recorded at all"))
                continue
            for name in sorted(set(live) | set(held)):
                if live.get(name) != held.get(name):
                    out.append((game, stat, name, live.get(name), held.get(name)))
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game")
    parser.add_argument("--stat")
    parser.add_argument("--note", help="what the measurement bought, and over what")
    parser.add_argument("--command", help=f"default: {DEFAULT_COMMAND}")
    parser.add_argument("--check", action="store_true",
                        help="report what has drifted and change nothing")
    args = parser.parse_args(argv)

    shipped = dc.shipped_weights()
    record = load()

    if args.check or not (args.game or args.stat or args.note):
        rows = drifted(shipped, record)
        if not rows:
            print("Every shipped weight set is one that was validated.")
            return 0
        print(f"{len(rows)} difference(s) between the shipped weights and the "
              f"validated record:")
        for game, stat, name, live, held in rows:
            print(f"  {game}/{stat}.{name}: ships {live!r}, validated at {held!r}")
        print("\nRe-run the validation, then record it with --game/--stat/--note.")
        return 1

    if not (args.game and args.stat and args.note):
        parser.error("--game, --stat and --note are all required to record a set")
    if args.game not in shipped or args.stat not in shipped[args.game]:
        parser.error(f"the app ships no weights for {args.game}/{args.stat}")
    if len(args.note.strip()) < MIN_NOTE:
        # A set recorded without what it bought is a pin with no reason to
        # keep it, and the test enforces the same floor.
        parser.error(f"--note is {len(args.note.strip())} characters; say what the "
                     f"measurement bought and over what (at least {MIN_NOTE})")

    before = (record.get(args.game, {}).get(args.stat) or {}).get("weights")
    entry = {
        "weights": shipped[args.game][args.stat],
        "measured": args.note.strip(),
        "validated": date.today().isoformat(),
        "command": args.command or DEFAULT_COMMAND.format(game=args.game),
    }
    record.setdefault(args.game, {})[args.stat] = entry
    save(record)

    print(f"Recorded {args.game}/{args.stat} as validated {entry['validated']}:")
    for name in sorted(entry["weights"]):
        was = (before or {}).get(name)
        now = entry["weights"][name]
        mark = "" if before is None or was == now else f"   (was {was!r})"
        print(f"  {name:16s} {now!r}{mark}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

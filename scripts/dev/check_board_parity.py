#!/usr/bin/env python3
"""Python half of tests/board_parity.test.mjs.

Reads the handoff the JS test wrote — the cases it ran and the answers it
got — recomputes each case with scripts/board_fixtures.py, and fails on any
disagreement.

The cases live in the JS test, not here. Duplicating them would let the two
lists drift apart and then the check would be comparing two different
questions, which is the failure mode a parity test exists to prevent.

    python3 scripts/dev/check_board_parity.py /tmp/.../js.json
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import board_fixtures as bf


def upcoming_of(regions):
    return {key: (data.get("upcoming_matches") or [])
            for key, data in regions.items()}


def run_case(case, now, root):
    """The augmented fixture list this case produces, per region."""
    if case.get("dataFile"):
        with open(os.path.join(root, case["dataFile"])) as f:
            regions = json.load(f).get("regions") or {}
    else:
        regions = json.loads(json.dumps(case.get("regions") or {}))
    if case.get("propsFile"):
        with open(os.path.join(root, case["propsFile"])) as f:
            props = json.load(f)
    else:
        props = case.get("props") or {}

    slots_by_region, _ = bf.board_slots(
        props.get("props") or {}, case["game"], regions, now,
        fetched_at=props.get("fetched_at"))
    bf.augment_regions(regions, slots_by_region)
    return upcoming_of(regions)


def differences(expected, got):
    """Every region where the two lists disagree, as (region, why) pairs."""
    out = []
    for key in sorted(set(expected) | set(got)):
        mine, theirs = got.get(key), expected.get(key)
        if mine is None or theirs is None:
            out.append((key, f"present on one side only (js={theirs is not None}, py={mine is not None})"))
            continue
        if len(mine) != len(theirs):
            out.append((key, f"{len(theirs)} fixture(s) in JS, {len(mine)} in Python"))
            continue
        for i, (a, b) in enumerate(zip(theirs, mine)):
            if a != b:
                out.append((key, f"fixture {i}: JS {a} vs Python {b}"))
    return out


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1]) as f:
        handoff = json.load(f)
    root = handoff.get("root") or os.getcwd()
    now = bf.parse_stamp(handoff["now"])
    if now is None:
        print(f"! unreadable 'now' in the handoff: {handoff.get('now')!r}", file=sys.stderr)
        return 1

    failures = 0
    for case in handoff["cases"]:
        got = run_case(case, now, root)
        diffs = differences(case["result"], got)
        total = sum(len(v) for v in got.values())
        if diffs:
            failures += 1
            print(f"  {case['name']:28s} MISMATCH")
            for region_key, why in diffs[:6]:
                print(f"      {region_key}: {why}")
            if len(diffs) > 6:
                print(f"      ... (+{len(diffs) - 6} more)")
        else:
            print(f"  {case['name']:28s} {total:4d} fixture(s)  OK")

    print()
    if failures:
        print(f"{failures} of {len(handoff['cases'])} case(s) disagree — the JS port in "
              f"src/app.jsx and scripts/board_fixtures.py have drifted.")
        return 1
    print(f"{len(handoff['cases'])} case(s) compared across both ports. "
          f"The JS port and board_fixtures.py agree on every fixture.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

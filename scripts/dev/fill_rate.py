#!/usr/bin/env python3
"""What fraction of posted lines can we actually grade, and why not?

The record is only worth what it covers. This says where every posted
line stands, splitting the ungraded ones into the part more scraping
fixes and the part it never will.

Usage:
    python scripts/dev/fill_rate.py
"""
import collections
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..") )
import score_props as sp

FILES = {"cs2": "cs2_data.json", "valorant": "valorant_data.json", "lol": "data.json"}
# Refusals no amount of re-scraping will clear.
STRUCTURAL = ("cover maps 1-2 only", "player missing from the box score",
              "line is not a number", "unreadable start time")


def main():
    rows = []
    with open("props_history.jsonl") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    uniq = {}
    for r in rows:
        uniq[(r.get("game"), r.get("player"), r.get("stat"),
              r.get("start_time"), r.get("line"), r.get("maps"))] = r

    data = {g: json.load(open(f)) for g, f in FILES.items() if os.path.exists(f)}
    graded, refused = sp.grade(list(uniq.values()), data)

    today = datetime.datetime.now(datetime.timezone.utc).date()
    future = sum(1 for r in uniq.values()
                 if (sp.utc_date(r.get("start_time")) or today) > today)
    total = len(uniq)
    settleable = total - future
    structural = sum(n for reason, n in refused.items()
                     if any(k in reason for k in STRUCTURAL))
    recoverable = settleable - len(graded) - structural

    print(f"posted lines on record      {total}")
    print(f"  fixture not played yet    {future}")
    print(f"  settleable now            {settleable}")
    print(f"    graded                  {len(graded):5d}  "
          f"{100 * len(graded) / settleable:.1f}% of settleable")
    print(f"    ungradeable by nature   {structural:5d}  "
          f"{100 * structural / settleable:.1f}%")
    print(f"    still recoverable       {recoverable:5d}  "
          f"{100 * recoverable / settleable:.1f}%   <- what another scrape buys")
    reachable = len(graded) + recoverable
    print(f"\n  ceiling for this history  {reachable}/{settleable} "
          f"({100 * reachable / settleable:.1f}%)  -- 100% is not the target")
    print("\n  refusals:")
    for reason, n in refused.most_common():
        tag = "structural" if any(k in reason for k in STRUCTURAL) else "recoverable"
        print(f"    {n:5d}  [{tag}] {reason}")


if __name__ == "__main__":
    main()

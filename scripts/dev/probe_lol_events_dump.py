#!/usr/bin/env python3
"""Print, as one JSON line, what scrape_schedule.scrape_international writes
for the events on now -- so merge.py and the app's event views can be
exercised locally against the real structure without a scrape run.

Read-only. Run it from Actions (.github/workflows/probe.yml).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import scrape_schedule as ss

upcoming, events = ss.scrape_international(ss.get_all_leagues())
print("EVENTS_JSON " + json.dumps({"regions": upcoming, "events": events}, separators=(",", ":")))

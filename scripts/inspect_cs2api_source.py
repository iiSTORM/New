#!/usr/bin/env python3
"""
Inspects the actually-installed cs2api package's own source code to find
the real URL construction for get_team_matches() and
get_team_upcoming_matches() -- rather than guessing at endpoints or
searching GitHub, this reads the exact code already running in this
environment. get_team_matches() is confirmed broken when CALLED (an
AttributeError from early in this investigation), but the bug is likely
in how it PARSES the response, not necessarily that the URL it hits is
wrong -- if we can see the URL, we can call it directly with our own
aiohttp code and skip the wrapper's broken parsing entirely, the same
approach that already worked for the CS2 per-map stats endpoints.

Run in the Codespace:
    python scripts/inspect_cs2api_source.py
"""
import inspect
import re

from cs2api import CS2


def show_method_source(cls, method_name):
    print("=" * 60)
    print(f"cs2api.CS2.{method_name}")
    print("=" * 60)
    try:
        method = getattr(cls, method_name)
        source = inspect.getsource(method)
        print(source)
    except AttributeError:
        print(f"  ! CS2 has no method called {method_name!r}")
    except (TypeError, OSError) as e:
        print(f"  ! could not get source: {e}")
    print()


def find_url_patterns(cls, method_name):
    """Pulls out anything that looks like a URL path or f-string being
    built, as a quick summary in case the full source is long."""
    try:
        source = inspect.getsource(getattr(cls, method_name))
    except Exception:
        return
    patterns = re.findall(r'["\'][^"\']*(?:teams?|matches?)[^"\']*["\']', source, re.IGNORECASE)
    if patterns:
        print(f"  [quick scan] string literals mentioning team/match in {method_name}:")
        for p in set(patterns):
            print(f"    {p}")
    print()


if __name__ == "__main__":
    print(f"cs2api module location: {CS2.__module__}\n")

    for method_name in [
        "get_team_matches",
        "get_team_upcoming_matches",
        "search_teams",
        "finished",
    ]:
        show_method_source(CS2, method_name)
        find_url_patterns(CS2, method_name)

    # Also dump the base URL / any shared request-building method, since
    # individual endpoint methods might just pass a path suffix to a
    # shared helper rather than building the full URL inline.
    print("=" * 60)
    print("Looking for a shared base URL or request-helper method...")
    print("=" * 60)
    for name in dir(CS2):
        if name.startswith("_"):
            continue
        if name in ("get_team_matches", "get_team_upcoming_matches", "search_teams", "finished"):
            continue
        attr = getattr(CS2, name)
        if callable(attr) and any(k in name.lower() for k in ("request", "get", "fetch", "base", "url")):
            print(f"  candidate helper: {name}")
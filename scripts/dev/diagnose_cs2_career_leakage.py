#!/usr/bin/env python3
"""
Checks whether CS2's career: 1.0 finding reflects genuine predictive
power or data leakage -- career data was fetched as "most recent N
matches as of scrape time", with no awareness of the historical cutoff
dates the backtest evaluates against. If a player's career data
substantially overlaps in time with the same past_matches being
backtested, career_rate could be partly built from the same games (or
games very close to) whatever it's being scored on predicting --
inflating its apparent value in a way that wouldn't hold up on a
genuinely future, unplayed match.

Concretely: for each player with both career data and real backtest
history, compares the DATE RANGE of their career_data.json games against
the date range of their past_matches appearances in cs2_data.json. Heavy
overlap is the smoking gun.

Usage:
    python scripts/diagnose_cs2_career_leakage.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

CS2_DATA_PATH = "cs2_data.json"
CAREER_DATA_PATH = "cs2_career_data.json"


def parse_date(s):
    """Normalizes to a naive datetime regardless of whether the source
    string had an explicit timezone offset or not -- a real crash showed
    cs2_data.json's generated_at has one ("+00:00") while past_matches'
    own date strings apparently don't, and Python refuses to subtract a
    naive datetime from an aware one. Stripping tzinfo after parsing
    keeps every date directly comparable; exact timezone doesn't matter
    for a ~200-day-scale overlap check like this one."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.replace(tzinfo=None)
    except (ValueError, AttributeError):
        return None


def main():
    if not Path(CS2_DATA_PATH).exists() or not Path(CAREER_DATA_PATH).exists():
        print(f"! Need both {CS2_DATA_PATH} and {CAREER_DATA_PATH}", file=sys.stderr)
        sys.exit(1)

    with open(CS2_DATA_PATH) as f:
        cs2_data = json.load(f)
    with open(CAREER_DATA_PATH) as f:
        career_data = json.load(f)

    # Real dates aren't stored per-game in cs2_career_data.json's output
    # (only the decayed aggregate is), so this check uses what IS
    # available: the overall date range of past_matches in cs2_data.json
    # (the backtest window) versus when cs2_career_data.json was
    # generated relative to that. If career fetching happened at/after
    # the END of the backtest window, and career's own 60-day half-life
    # reaches back INTO or PAST the backtest window's start, overlap is
    # essentially guaranteed by construction, not just possible.
    past_matches = cs2_data["regions"]["CS2"]["past_matches"]
    match_dates = [parse_date(m.get("date")) for m in past_matches]
    match_dates = [d for d in match_dates if d]
    if not match_dates:
        print("! No usable match dates found in cs2_data.json")
        return

    earliest = min(match_dates)
    latest = max(match_dates)
    backtest_span_days = (latest - earliest).days
    print(f"Backtest window (past_matches in {CS2_DATA_PATH}): {earliest.date()} to {latest.date()} "
          f"({backtest_span_days} days, {len(match_dates)} matches)")

    generated_at = parse_date(cs2_data.get("generated_at"))
    print(f"cs2_data.json generated_at: {generated_at}")

    DAY_HALF_LIFE = 60  # matches the constant in scrape_cs2_career.py
    # A weight of 0.5^(days_ago/60) is still meaningfully non-negligible
    # (>10%) out to about 200 days -- past that it's rounding-error
    # small. If the backtest window's EARLIEST match is within ~200 days
    # of when career data was fetched, essentially every match in the
    # backtest falls within "career"'s real, non-negligible reach.
    meaningful_reach_days = 200
    if generated_at and (generated_at - earliest).days < meaningful_reach_days:
        print(f"\n>>> LEAKAGE LIKELY: the backtest window's earliest match "
              f"({earliest.date()}) is only {(generated_at - earliest).days} days before "
              f"career data was generated ({generated_at.date() if generated_at else '?'}) -- "
              f"well within the ~{meaningful_reach_days}-day range where the 60-day half-life "
              f"still gives real, non-negligible weight. Career data fetched 'as of now' almost "
              f"certainly overlaps meaningfully with the SAME games (or games very close in time) "
              f"the backtest is scoring predictions against.")
    else:
        print(f"\n>>> Backtest window appears to sit meaningfully outside career data's "
              f"non-negligible reach — leakage less likely to be the dominant explanation.")

    # Secondary check: how many total games did career fetching pull per
    # player vs. how many games that same player has in the backtest
    # window -- if career's game count is similar to or smaller than the
    # backtest's own per-player game count, it's very likely almost
    # entirely THE SAME underlying games, not a genuinely broader
    # history.
    player_backtest_games = {}
    for m in past_matches:
        for side in ("teamA", "teamB"):
            team = m[side]
            for player_name in (m.get("actual") or {}).get(team, {}):
                player_backtest_games[player_name] = player_backtest_games.get(player_name, 0) + m.get("games", 2)

    print(f"\nPer-player game count comparison (career fetch vs. backtest window) -- "
          f"sample of 10:")
    sample = list(career_data.items())[:10]
    for name, record in sample:
        career_g = (record.get("career") or {}).get("g", 0)
        backtest_g = player_backtest_games.get(name, 0)
        print(f"  {name}: career fetched {career_g} games, backtest window has {backtest_g} games "
              f"for this player {'(career <= backtest window size -- almost certainly the same games)' if career_g <= backtest_g and career_g > 0 else ''}")


if __name__ == "__main__":
    main()
"""Fill in `actual` for esports legs from the scrapers' own graded record.

props_results.json is already produced by score_props.py for every PrizePicks
esports line the scrapers saw, with the number the player actually put up. A
leg on this tracker that names the same player, stat, window and line is the
same prop, so it can be graded without typing anything.

It refuses on ambiguity rather than guessing. The same player can have the same
line on the same stat on two different days, and grading a Tuesday slip off
Wednesday's result is worse than leaving it pending: it is wrong, it looks
graded, and it moves the bankroll.
"""
import json
from pathlib import Path

ESPORTS = ("cs2", "lol", "valorant")


def load_graded(path="props_results.json"):
    blob = json.loads(Path(path).read_text(encoding="utf-8"))
    return blob.get("graded") or []


def _key(game, player, stat, maps, line):
    return (str(game).lower(), str(player).strip().lower(), str(stat).lower(),
            None if maps is None else int(maps), float(line))


def index_graded(rows):
    out = {}
    for row in rows:
        try:
            key = _key(row.get("game"), row.get("player"), row.get("stat"),
                       row.get("maps"), row.get("line"))
        except (TypeError, ValueError):
            continue
        out.setdefault(key, []).append(row)
    return out


def candidates_for(leg, index, on_date=None):
    """Graded rows that are this leg, narrowed by date when one is known."""
    if leg.sport not in ESPORTS or leg.actual is not None:
        return []
    rows = index.get(_key(leg.sport, leg.player, leg.stat, leg.maps, leg.line), [])
    if on_date:
        day = str(on_date)[:10]
        narrowed = [r for r in rows if str(r.get("match_date") or "")[:10] == day
                    or str(r.get("start_time") or "")[:10] == day]
        if narrowed:
            return narrowed
    return rows


def autograde(tracker, results_path="props_results.json"):
    """Grade what can be graded. Returns (graded, skipped) as report lines."""
    index = index_graded(load_graded(results_path))
    graded, skipped = [], []
    for slip in tracker.pending():
        for leg in slip.legs:
            if leg.result != "pending":
                continue
            if leg.sport not in ESPORTS:
                skipped.append(f"{slip.id[:12]} {leg.player} {leg.stat}: "
                               f"{leg.sport} is not auto-graded — use `grade`")
                continue
            rows = candidates_for(leg, index, on_date=slip.placed_at)
            if not rows:
                skipped.append(f"{slip.id[:12]} {leg.player} {leg.side} {leg.line} "
                               f"{leg.stat}: not in the graded record yet")
                continue
            actuals = {float(r["actual"]) for r in rows if r.get("actual") is not None}
            if len(actuals) != 1:
                skipped.append(f"{slip.id[:12]} {leg.player} {leg.stat}: "
                               f"{len(rows)} graded rows disagree ({sorted(actuals)}) "
                               "— grade this one by hand")
                continue
            leg.grade(actuals.pop())
            graded.append(f"{slip.id[:12]} {leg.player} {leg.side} {leg.line} "
                          f"{leg.stat}: actual {leg.actual:g} → {leg.result}")
    return graded, skipped

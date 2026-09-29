"""Was the model honest, did the money go up, and did the line move your way.

Three questions, three answers, and the first one decides how the stakes are
sized. Kelly is only as good as the probabilities fed into it: staking half
Kelly on a number that claims 65% and realises 52% is not half Kelly, it is
roughly double it, and the overbetting compounds. So calibration is not a chart
to admire -- when it is off by more than five points over enough legs,
`sizing_policy` takes Kelly away and hands back flat units, with the reason
attached so the banner can say why.

WHAT IS AND IS NOT MEASURABLE YET, stated here rather than discovered later:

  * Calibration needs `model_prob` on a leg. Nothing fills it in yet -- the
    models arrive in phases 3 and 4 -- so today this reports zero legs and no
    verdict. That is the correct answer, not a broken function.
  * CLV is measurable more often than it first looks. props_history.jsonl does
    not hold one row per observation -- scrape_props.archive_props keys on the
    LINE and appends only what is new -- so a prop with a single row is one
    whose line never moved while the board was being watched, which is a real
    measurement of zero movement rather than a missing one. 153 props did move.
    What is genuinely unknowable is whether the board was looked at again
    between placing a slip and lock: a capture where nothing anywhere changed
    leaves no trace in the file at all. So captures are inferred from the
    distinct timestamps that DID leave rows, which across a board of hundreds
    of props is a close proxy and is stated rather than assumed.
  * ROI works now, on whatever has settled.
"""
import collections
import json
import math
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path

from .money import fmt
#: What replaces Kelly when the probabilities cannot be trusted; defined where
#: the staking lives, so there is one copy of it.
from .sizing import FLAT_UNIT
from .slips import LOST, WON

#: Probability bands for the calibration table. Five-point bands up to 70%,
#: then wider, because a model that claims 85% will not produce many legs and
#: narrow bands there would be all noise.
BUCKETS = ((0.0, 0.50), (0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70),
           (0.70, 0.80), (0.80, 1.01))
#: The plan's rule: below this many graded legs, calibration cannot be judged.
MIN_LEGS_FOR_VERDICT = 50
#: Off by more than this and Kelly is withdrawn.
MAX_CALIBRATION_ERROR = 0.05


def wilson(wins, n, z=1.96):
    """A confidence interval that behaves at the edges.

    Not wins/n +- z*sqrt(p(1-p)/n): that interval has zero width at 0 wins of 8,
    which would read as certainty from the thinnest possible evidence.
    """
    if n <= 0:
        return (0.0, 1.0)
    p = wins / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, centre - spread), min(1.0, centre + spread))


# ============================================================
# Calibration
# ============================================================

@dataclass
class Band:
    lo: float
    hi: float
    n: int
    claimed: float          # mean model probability in the band
    realised: float         # fraction that actually won
    interval: tuple
    matches: int            # distinct matches the legs came from

    @property
    def error(self):
        return self.realised - self.claimed

    @property
    def straddles_its_claim(self):
        return self.interval[0] <= self.claimed <= self.interval[1]


def graded_legs(slips):
    """Legs that were played, decided, and carry a probability to judge.

    Pushes, DNPs and unknowns are excluded: a leg that was removed was never a
    prediction that resolved, and counting it either way would be wrong.
    """
    out = []
    for slip in slips:
        for leg in slip.legs:
            if leg.result in (WON, LOST) and isinstance(leg.model_prob, (int, float)):
                out.append((slip, leg))
    return out


def _match_key(slip, leg):
    """What counts as one match, for counting independent observations.

    Ten legs off one map are not ten observations -- they share the map's pace.
    Legs do not carry a match id, so this is the best available proxy: the two
    teams, unordered, on the day the slip was placed.
    """
    return (leg.sport, str(slip.placed_at)[:10],
            tuple(sorted((leg.team_key, (leg.opponent or "?").strip().lower()))))


def calibration(slips, buckets=BUCKETS):
    """One row per probability band: what was claimed, what landed."""
    rows = graded_legs(slips)
    out = []
    for lo, hi in buckets:
        band = [(slip, leg) for slip, leg in rows if lo <= leg.model_prob < hi]
        if not band:
            continue
        wins = sum(1 for _, leg in band if leg.result == WON)
        out.append(Band(
            lo=lo, hi=hi, n=len(band),
            claimed=sum(leg.model_prob for _, leg in band) / len(band),
            realised=wins / len(band),
            interval=wilson(wins, len(band)),
            matches=len({_match_key(slip, leg) for slip, leg in band})))
    return out


def calibration_error(slips, min_legs=MIN_LEGS_FOR_VERDICT):
    """(error, legs, matches): mean |claimed - realised|, weighted by band size.

    `error` is None below `min_legs`, because the rule this feeds is a rule
    about evidence and a two-leg band is not evidence.
    """
    rows = graded_legs(slips)
    bands = calibration(slips)
    matches = len({_match_key(slip, leg) for slip, leg in rows})
    if len(rows) < min_legs or not bands:
        return (None, len(rows), matches)
    weighted = sum(band.n * abs(band.error) for band in bands)
    return (weighted / sum(band.n for band in bands), len(rows), matches)


def sizing_policy(slips, min_legs=MIN_LEGS_FOR_VERDICT,
                  max_error=MAX_CALIBRATION_ERROR):
    """"kelly" or "flat", and why -- the rule that decides how stakes are set.

    Kelly is a function of the probabilities. If they are wrong by five points
    the stake is wrong by roughly a factor of two in the same direction, and it
    compounds. Flat units do not care whether 60% means 60%.
    """
    error, legs, matches = calibration_error(slips, min_legs)
    if error is None:
        return ("kelly", f"{legs} graded leg(s) carry a model probability; "
                         f"{min_legs} is the floor for judging calibration, so "
                         f"Kelly stands by default — untested, not verified")
    if error > max_error:
        return ("flat", f"calibration is off by {error:.1%} over {legs} graded "
                        f"leg(s) across {matches} match(es), past the "
                        f"{max_error:.0%} limit — stakes drop to flat "
                        f"{FLAT_UNIT:.0%} units until it comes back")
    return ("kelly", f"calibration is within {error:.1%} over {legs} graded "
                     f"leg(s) across {matches} match(es)")


# ============================================================
# ROI
# ============================================================

@dataclass
class Split:
    label: str
    slips: int
    staked_cents: int
    returned_cents: int

    @property
    def net_cents(self):
        return self.returned_cents - self.staked_cents

    @property
    def roi(self):
        return None if not self.staked_cents else self.net_cents / self.staked_cents


#: How a slip or leg is bucketed. Each takes (slip, leg) and returns a label,
#: or None to leave that leg out.
KEYS = {
    "sport": lambda slip, leg: leg.sport,
    "stat": lambda slip, leg: f"{leg.sport} {leg.stat}",
    "size": lambda slip, leg: f"{slip.n_legs}-pick {slip.mode}",
    "odds_type": lambda slip, leg: leg.odds_type or "standard",
    "side": lambda slip, leg: leg.side,
}


def roi(slips, key="sport"):
    """Staked against returned, split by `key`.

    A slip's stake is attributed to EVERY bucket its legs touch, not divided
    between them. A 3-pick with two CS2 legs and one LoL leg is a slip that
    both sports could have busted, so it counts whole against both, and the
    splits deliberately do not sum to the total. Dividing a stake three ways
    would suggest each leg risked a third of it, which is the opposite of how a
    parlay fails.
    """
    if key not in KEYS:
        raise ValueError(f"key must be one of {sorted(KEYS)}, not {key!r}")
    label_of = KEYS[key]
    buckets = {}
    for slip in slips:
        if not slip.is_settled or slip.payout_cents is None:
            continue
        for label in {label_of(slip, leg) for leg in slip.legs}:
            if label is None:
                continue
            split = buckets.setdefault(label, Split(label, 0, 0, 0))
            split.slips += 1
            split.staked_cents += slip.stake_cents
            split.returned_cents += slip.payout_cents
    return sorted(buckets.values(), key=lambda s: -s.staked_cents)


def overall(slips):
    settled = [s for s in slips if s.is_settled and s.payout_cents is not None]
    return Split("all settled", len(settled),
                 sum(s.stake_cents for s in settled),
                 sum(s.payout_cents for s in settled))


# ============================================================
# Closing line value
# ============================================================

@dataclass
class Movement:
    player: str
    stat: str
    placed_line: float
    closing_line: float
    side: str
    captures_after_placing: int

    @property
    def moved(self):
        return round(self.closing_line - self.placed_line, 4)

    @property
    def toward_us(self):
        """Did the line move so our side became HARDER to reach?

        That is the good direction: the number we took is better than the one
        available at lock. An over that closes higher, or an under that closes
        lower, is value captured. A line that did not move is neither.
        """
        if not self.moved:
            return None
        return self.moved > 0 if self.side == "over" else self.moved < 0


def parse_ts(text):
    """An ISO timestamp as an aware datetime, or None.

    Three different clocks meet here -- the history is UTC, a board start_time
    carries the venue's offset, and placed_at is local -- so they are compared
    as instants and never as strings.
    """
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


#: How close a leg's recorded start time has to be to a board posting's for
#: them to be the same match. The same window propsFor uses in the app, and for
#: the same reason: a posted start time is approximate and can drift by an hour.
MATCH_WINDOW = timedelta(hours=2)


def board_history(path="props_history.jsonl"):
    """(by_match, captures).

    `by_match` is keyed by (game, player, stat, maps, start_time) -- the START
    TIME MATTERS. Without it a player's kills line in Tuesday's match and
    Friday's collapse into one series, and the "closing line" for a Tuesday bet
    could be a number posted for Friday. That is not a slightly wrong CLV
    figure, it is a number about a different game.

    Each group holds one row per DISTINCT LINE, oldest first, because that is
    what scrape_props.archive_props records: it keys on the line and appends
    only what is new. A group with one row is a line that never moved.

    `captures` is every timestamp that produced a row, which is the best
    available record of when the board was looked at -- a capture in which no
    line anywhere changed leaves nothing behind. Across a board of hundreds of
    props that is rare, and it is the one assumption CLV rests on.
    """
    by_match, captures = collections.defaultdict(list), set()
    source = Path(path)
    if not source.exists():
        return by_match, []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        seen_at = parse_ts(row.get("observed_at"))
        if seen_at is None or not isinstance(row.get("line"), (int, float)):
            continue
        captures.add(seen_at)
        key = (str(row.get("game", "")).lower(), str(row.get("player", "")).lower(),
               str(row.get("stat", "")).lower(), row.get("maps"),
               str(row.get("start_time") or ""))
        by_match[key].append({"at": seen_at, "line": float(row["line"]),
                              "start": parse_ts(row.get("start_time"))})
    for rows in by_match.values():
        rows.sort(key=lambda r: r["at"])
    return by_match, sorted(captures)


def resolve_prop(leg, placed, by_match):
    """(rows, problem): the board postings that are THIS leg's prop.

    Keyed on the player, never on a pair of teams, and narrowed to one match --
    by the leg's own start time where it has one, otherwise by taking the next
    match after the slip was placed. Two candidates that both fit is reported as
    ambiguous rather than resolved by picking one: a coin flip between two
    matches produces a number that looks like a measurement.
    """
    prefix = (leg.sport, leg.player.strip().lower(), leg.stat.lower(), leg.maps)
    groups = [(key, rows) for key, rows in by_match.items() if key[:4] == prefix]
    if not groups:
        return None, "never seen on a board we captured"

    stated = parse_ts(leg.start_time)
    if stated is not None:
        near = [rows for key, rows in groups
                if rows[0]["start"] and abs(rows[0]["start"] - stated) <= MATCH_WINDOW]
        if len(near) == 1:
            return near[0], None
        if len(near) > 1:
            return None, f"{len(near)} matches within the window of its start time"
        return None, "no posting near the start time on file for it"

    upcoming = sorted((rows for key, rows in groups
                       if rows[0]["start"] and placed and rows[0]["start"] >= placed),
                      key=lambda rows: rows[0]["start"])
    if not upcoming:
        return None, "no posting for a match after the slip was placed"
    if len(upcoming) > 1 and (upcoming[1][0]["start"] - upcoming[0][0]["start"]
                              <= MATCH_WINDOW):
        return None, "two matches start within the window, so which one is unclear"
    return upcoming[0], None


def closing_lines(slips, path="props_history.jsonl"):
    """(movements, coverage): how each leg's line moved by lock, where knowable.

    A leg is measurable when the board was captured at least once between
    placing the slip and lock. The line in force at lock is then the last
    recorded line at or before it -- and if that row predates the slip, the line
    simply held, which is a measurement of zero movement and not a gap.
    """
    by_match, captures = board_history(path)
    movements, legs_seen, unmeasured = [], 0, collections.Counter()
    for slip in slips:
        placed = parse_ts(slip.placed_at)
        for leg in slip.legs:
            legs_seen += 1
            rows, problem = resolve_prop(leg, placed, by_match)
            if problem:
                unmeasured[problem] += 1
                continue
            lock = parse_ts(leg.start_time) or rows[0]["start"]
            if lock is None or placed is None:
                unmeasured["no lock time or no placement time on file"] += 1
                continue
            watched = [c for c in captures if placed < c <= lock]
            if not watched:
                unmeasured["the board was not captured again before lock"] += 1
                continue
            at_lock = [r for r in rows if r["at"] <= lock]
            if not at_lock:
                unmeasured["every recorded line postdates lock"] += 1
                continue
            placed_line = float(leg.line_at_placement if leg.line_at_placement
                                is not None else leg.line)
            movements.append(Movement(leg.player, leg.stat, placed_line,
                                      at_lock[-1]["line"], leg.side, len(watched)))
    return movements, {"legs": legs_seen, "measured": len(movements),
                       "captures": len(captures), "unmeasured": dict(unmeasured)}


# ============================================================
# The report
# ============================================================

def report(tracker, history_path="props_history.jsonl"):
    """Everything above, as lines of text. The CLI prints them."""
    out = []
    total = overall(tracker.slips)
    balance = tracker.balance()
    out.append(f"BANKROLL {fmt(balance)}   "
               f"{len(tracker.pending())} pending, {fmt(tracker.exposure())} at risk")
    if total.staked_cents:
        out.append(f"  settled: staked {fmt(total.staked_cents)}, returned "
                   f"{fmt(total.returned_cents)}, net {fmt(total.net_cents)} "
                   f"({total.roi:+.1%}) over {total.slips} slip(s)")
    else:
        out.append("  nothing has settled yet")

    series = tracker.ledger.history()
    if len(series) > 1:
        peak = max(running for _, running in series)
        out.append(f"  peak {fmt(peak)}, now {fmt(balance)}"
                   + (f", {fmt(peak - balance)} off the high" if peak > balance else ""))

    out.append("")
    out.append("CALIBRATION — do the model's probabilities mean anything")
    bands = calibration(tracker.slips)
    if not bands:
        error, legs, _ = calibration_error(tracker.slips)
        out.append(f"  no graded leg carries a model probability yet ({legs} found). "
                   "Nothing fills model_prob until the models land, so there is "
                   "nothing to check.")
    else:
        out.append(f"  {'band':>10} {'n':>4} {'matches':>8} {'claimed':>8} "
                   f"{'realised':>9}  {'95%':>13}")
        for band in bands:
            out.append(f"  {band.lo:.0%}-{band.hi:.0%}".ljust(13)
                       + f"{band.n:>4} {band.matches:>8} {band.claimed:>8.1%} "
                       f"{band.realised:>9.1%}  "
                       f"{band.interval[0]:.0%}-{band.interval[1]:.0%}".rjust(14)
                       + ("" if band.straddles_its_claim else "  <- off"))
        out.append("  legs on one match are not independent, so these intervals "
                   "are optimistic")

    policy, why = sizing_policy(tracker.slips)
    out.append("")
    out.append(f"SIZING: {policy.upper()}")
    out.append(f"  {why}")

    for key in ("sport", "size", "stat", "odds_type"):
        splits = roi(tracker.slips, key)
        if not splits:
            continue
        out.append("")
        out.append(f"ROI BY {key.upper().replace('_', ' ')}"
                   + ("   (a slip counts whole against every bucket it touches, "
                      "so these do not sum)" if key != "size" else ""))
        for split in splits:
            out.append(f"  {split.label:<28} {split.slips:>3} slip(s)  "
                       f"staked {fmt(split.staked_cents):>8}  "
                       f"net {fmt(split.net_cents):>8}"
                       + (f"  {split.roi:+.0%}" if split.roi is not None else ""))

    movements, coverage = closing_lines(tracker.slips, history_path)
    out.append("")
    out.append(f"CLOSING LINE VALUE   ({coverage['captures']} board capture(s) on file)")
    if not movements:
        out.append(f"  none of {coverage['legs']} leg(s) can be measured:")
        for why, n in sorted(coverage["unmeasured"].items(), key=lambda kv: -kv[1]):
            out.append(f"    {n} — {why}")
        out.append("  a leg is only measurable if the board was captured again "
                   "between placing and lock; capturing it by hand once a night "
                   "is what leaves this blank")
    else:
        better = sum(1 for m in movements if m.toward_us is True)
        worse = sum(1 for m in movements if m.toward_us is False)
        held = sum(1 for m in movements if m.toward_us is None)
        out.append(f"  {coverage['measured']} of {coverage['legs']} leg(s) "
                   f"measurable: {better} moved your way, {worse} against you, "
                   f"{held} held")
        for move in movements:
            verdict = ("held" if move.toward_us is None
                       else "your way" if move.toward_us else "against you")
            out.append(f"  {move.player:<20} {move.stat:<18} "
                       f"{move.placed_line:g} -> {move.closing_line:g}  {verdict}"
                       f"   ({move.captures_after_placing} capture(s) before lock)")
        for why, n in sorted(coverage["unmeasured"].items(), key=lambda kv: -kv[1]):
            out.append(f"  unmeasured: {n} — {why}")
    return out

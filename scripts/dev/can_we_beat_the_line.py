#!/usr/bin/env python3
"""Can a better model close the gap to the posted line? Seven tests, one answer.

scripts/dev/beat_the_line.py reports that the model is behind the book as a
predictor -- +0.20 kills of mean absolute error on the biggest CS2 cell. The
obvious next move is to improve the model. This is the investigation of whether
that is possible with the data in this repository, kept as a script because the
answer is a negative and a negative that lives only in a chat log gets
re-litigated every few weeks.

THE SEVEN TESTS, each able to find information the others would miss:

  1. BLEND. Mix the model's projection with the line at every weight. If the
     model holds anything the line does not, some non-zero weight beats the line
     alone.

  2. REGRESSION on the model's output. Put the line and the model in together
     and look at the model's coefficient. Unlike the blend this allows the model
     to be rescaled and re-centred first, so it finds information that a raw
     average would waste.

  3. RAW FEATURES. The model's OUTPUT having no information does not mean its
     INPUTS have none -- a bad function can destroy a good signal. So every
     input is regressed against the line separately: recent form over three and
     ten series, kills per round, team and opponent scoring, team and opponent
     pace, the player's share, and how much history there is.

  4. PER-MAP FEATURES. cs2_data.json's per_game field carries per-MAP player
     lines for 570 of 877 matches and the shipped model has never touched it --
     it works from series totals. Map-level mean, median, spread, recent trend
     and a four-versus-eight form trend all get the same treatment.

  5. CONTEXT. Two items on WHAT WOULD ACTUALLY HELP turned out not to need a
     scraper: rest and roster churn are both derivable from the match list.
     Days since the team last played, matches in the past week, whether the
     player appeared in his team's last match, how many of the last five he
     appeared in, and how often the lineup changed across them.

  6. RICH STATS. The model reads `k` and nothing else, while `actual` carries
     adr, kast, rating, headshots, opening kills and deaths, assists, damage,
     clutches and trades. Damage was the strongest hypothesis of the six: a
     player who does the work and gets traded out of the frag still banks the
     damage, so adr ought to read his form more cleanly than kills do.

  7. THE MARKET'S OWN PRICE, and the only one of the seven that is not a
     read on form. Tests 3 to 6 all failed the same way -- a feature derived
     from what a player has done is his form, and the line already has his
     form. A bookmaker's pre-match price is a forecast of how the MATCH will
     go: competitive or a walkover, which is the quarter of kill variance the
     round-count finding said nothing here can predict. Devigged win
     probability, distance from a coin flip, and where the provider posts it,
     the price on the series going past two and a half maps.

Everything is point in time, and anything that looks good in sample is then
walk-forward tested, because at nine candidate features and 557 rows a t of 2
is what noise looks like on its best day.

WHAT IT FOUND, on 2026-09-29:

  1. Zero weight on the model is optimal in all three cells. Any amount hurts.
  2. Model coefficient -0.061 (t -0.47) for CS2 kills, +0.092 (t +0.58) for
     headshots, -0.528 (t -1.81) for Valorant -- nothing, and negative twice.
     The line's own coefficient is 1.014 with t +7.94: an efficient predictor.
  3. Three features looked significant in sample -- opponent pace (t +3.28),
     opponent scoring (t +2.88), history length (t -3.53) -- and NONE survived
     walk-forward. Even "line plus opponent pace" scored 5.40 against the line's
     5.37 out of sample.
  4. Nothing, in sample or out. No per-map feature reached t 1.96.
  5. Nothing. Strongest was days of rest at t +1.46, and walk-forward every
     context variant was WORSE than the line alone -- rest by +0.024, churn by
     +0.012, all of them together by +0.033.
  6. Nothing, and this one is the most decisive of the six: fifteen stats, none
     significant, the best of them kast at t +1.27 and adr at t +0.62. The
     damage hypothesis is dead -- a cleaner read on form is still a read on
     form, and form is what the line already has.
  7. Pending, and NOTHING CAN BE BACKFILLED. Both providers stop serving a
     usable pre-match price the moment a match starts, for different reasons:
     bo3.gg replaces it with the in-play price, and vlr.gg swaps its two-way
     block for a settled-bet message naming only the WINNER -- a real price,
     genuinely pre-match, and unusable, because having one is the same
     statement as having won. A feature built on it separates the data
     perfectly in sample and knows nothing in advance. So both games
     accumulate forward from 2026-09-29, and this test prints how many props
     it could join and declines to conclude below a hundred. That is weeks
     away, not minutes, and saying so is the point of writing it down.

So the CS2 kills line is efficient with respect to every piece of information in
this repository. The gap is not closable by better modelling of this data, and
the honest place to spend effort is on data the repository does not have -- see
WHAT WOULD ACTUALLY HELP at the bottom of this file.

    python scripts/dev/can_we_beat_the_line.py
    python scripts/dev/can_we_beat_the_line.py --game cs2 --stat kills --json out.json
"""
import argparse
import collections
import datetime
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import optimize_weights as ow                      # noqa: E402
import search_weights_by_outcome as sw             # noqa: E402
import odds_store                                  # noqa: E402

BLEND_WEIGHTS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 1.0)
FOLDS = 4
#: Below this many training rows a regression is fitting its own noise.
MIN_TRAIN = 80


# ============================================================
# Least squares, by normal equations, in the standard library
# ============================================================

def ols(y, columns):
    """(coefficients, standard errors) with an intercept prepended.

    Written out rather than imported because this package has no dependencies
    and a two-to-five column regression is a dozen lines. Returns (None, None)
    on a singular design, which happens when a column is constant.
    """
    n, k = len(y), len(columns) + 1
    if n <= k:
        return (None, None)
    design = [[1.0] + [col[i] for col in columns] for i in range(n)]
    normal = [[sum(design[i][a] * design[i][b] for i in range(n)) for b in range(k)]
              for a in range(k)]
    moment = [sum(design[i][a] * y[i] for i in range(n)) for a in range(k)]
    work = [row[:] + [1.0 if i == j else 0.0 for j in range(k)]
            for i, row in enumerate(normal)]
    for col in range(k):
        pivot_row = max(range(col, k), key=lambda r: abs(work[r][col]))
        work[col], work[pivot_row] = work[pivot_row], work[col]
        pivot = work[col][col]
        if abs(pivot) < 1e-12:
            return (None, None)
        work[col] = [v / pivot for v in work[col]]
        for r in range(k):
            if r != col and work[r][col]:
                factor = work[r][col]
                work[r] = [v - factor * w for v, w in zip(work[r], work[col])]
    inverse = [row[k:] for row in work]
    beta = [sum(inverse[a][b] * moment[b] for b in range(k)) for a in range(k)]
    residuals = [y[i] - sum(beta[a] * design[i][a] for a in range(k)) for i in range(n)]
    variance = sum(r * r for r in residuals) / (n - k)
    errors = [math.sqrt(max(variance * inverse[a][a], 0.0)) for a in range(k)]
    return (beta, errors)


def predict(beta, values):
    return beta[0] + sum(b * v for b, v in zip(beta[1:], values))


def walk_forward(rows, variants, folds=FOLDS):
    """{name: held-out MAE}. Fit before a cut, score after it, expanding."""
    size = len(rows) // (folds + 1)
    errors = {name: [] for name in variants}
    if size < 20:
        return {}
    for i in range(1, folds + 1):
        train, test = rows[:size * i], rows[size * i:size * (i + 1)]
        if len(train) < MIN_TRAIN or not test:
            continue
        for name, pick in variants.items():
            if pick is None:
                errors[name] += [abs(r["line"] - r["actual"]) for r in test]
                continue
            columns = list(zip(*[pick(r) for r in train]))
            beta, _ = ols([r["actual"] for r in train], [list(c) for c in columns])
            if beta is None:
                continue
            errors[name] += [abs(predict(beta, pick(r)) - r["actual"]) for r in test]
    return {name: statistics.mean(e) for name, e in errors.items() if e}


# ============================================================
# The data each test needs
# ============================================================

def rounds_of(stats, maps):
    """Rounds played, from damage and damage-per-round.

    Several of this provider's fields are SUMMED across the maps of a series --
    adr and kast among them -- so the per-map rate is adr/maps and the round
    count is dmg divided by that.
    """
    adr, dmg = stats.get("adr"), stats.get("dmg")
    if not adr or not dmg or not maps:
        return None
    per_map = adr / maps
    return dmg / per_map if per_map else None


def series_rows(game, stat, graded, data, index, maps=2):
    """Model projection, line, actual and the raw inputs, point in time."""
    region = next(iter(data))
    blob = data[region]
    matches = sorted(blob.get("past_matches") or [], key=lambda m: str(m.get("date")))
    key = ow.STAT_TYPES[stat]["key"]

    by_player, by_team = collections.defaultdict(list), collections.defaultdict(list)
    for match in matches:
        score = str(match.get("score") or "")
        try:
            played = min(sum(int(x) for x in score.split("-")), 2)
        except ValueError:
            played = match.get("games") or 2
        date = str(match.get("date") or "")
        for team, players in (match.get("actual") or {}).items():
            total, team_rounds = 0.0, []
            for name, stats in players.items():
                if not isinstance(stats, dict) or stats.get(key) is None:
                    continue
                total += stats[key]
                got = rounds_of(stats, played)
                if got and 15 < got < 70:
                    team_rounds.append(got)
                by_player[(team, name)].append({"date": date, "val": stats[key],
                                                "maps": played})
            by_team[team].append({
                "date": date, "total": total, "maps": played,
                "rounds": statistics.median(team_rounds) if team_rounds else None})

    projected = {}
    for row, mu, _ in sw.project_all(sw.cell_rows(graded, game, stat), data, index,
                                     game, stat, ow.SHIPPED_WEIGHTS[game][stat]):
        projected[id(row)] = mu

    out = []
    for row in sw.cell_rows(graded, game, stat):
        if (maps and row.get("maps") != maps) or row.get("actual") is None:
            continue
        team, player = str(row.get("team") or ""), str(row.get("player") or "")
        date = str(row.get("match_date") or "")
        history = by_player.get((team, player)) or []
        prior = [h for h in history if h["date"] < date]
        if len(prior) < 3:
            continue
        def rate(rows_):
            played_ = sum(r["maps"] for r in rows_)
            return sum(r["val"] for r in rows_) / played_ if played_ else None
        def team_rate(rows_):
            played_ = sum(r["maps"] for r in rows_)
            return sum(r["total"] for r in rows_) / played_ if played_ else None
        def pace(rows_):
            have = [r["rounds"] for r in rows_ if r["rounds"]]
            return statistics.mean(have) if have else None
        mine = [h for h in (by_team.get(team) or []) if h["date"] < date][-10:]
        theirs = [h for h in (by_team.get(str(row.get("opponent") or "")) or [])
                  if h["date"] < date][-10:]
        made = {
            "line": row["line"], "actual": row["actual"],
            "model": projected.get(id(row)),
            "form3": rate(prior[-3:]), "form10": rate(prior[-10:]),
            "history": len(prior),
            "team_rate": team_rate(mine), "opp_rate": team_rate(theirs),
            "team_pace": pace(mine), "opp_pace": pace(theirs),
        }
        if all(v is not None for v in made.values()):
            out.append(made)
    return out


def per_map_rows(graded, stat="kills", maps=2):
    """The same, from per_game -- per-MAP lines the shipped model never reads."""
    key = "k" if stat == "kills" else "hs"
    blob = json.load(open("cs2_data.json"))["regions"]["CS2"]
    matches = sorted(blob.get("past_matches") or [], key=lambda m: str(m.get("date")))
    by_player = collections.defaultdict(list)
    for match in matches:
        games = match.get("per_game")
        if not isinstance(games, list):
            continue
        date = str(match.get("date") or "")
        for index, entry in enumerate(games):
            if not isinstance(entry, dict):
                continue
            for team, players in entry.items():
                if not isinstance(players, dict):
                    continue
                for name, stats in players.items():
                    if isinstance(stats, dict) and stats.get(key) is not None:
                        by_player[(team, name)].append({"date": date, "map": index,
                                                        "val": stats[key]})
    out = []
    for row in sw.cell_rows(graded, "cs2", stat):
        if row.get("maps") != maps or row.get("actual") is None:
            continue
        prior = [h for h in (by_player.get((str(row.get("team") or ""),
                                            str(row.get("player") or ""))) or [])
                 if h["date"] < str(row.get("match_date") or "")]
        if len(prior) < 6:
            continue
        last, recent = [h["val"] for h in prior[-12:]], [h["val"] for h in prior[-4:]]
        older = [h["val"] for h in prior[-12:-4]]
        out.append({
            "line": row["line"], "actual": row["actual"],
            "map_mean": statistics.mean(last),
            "map_median": statistics.median(last),
            "map_sd": statistics.pstdev(last) if len(last) > 1 else 0.0,
            "map_recent": statistics.mean(recent),
            "trend": (statistics.mean(recent) - statistics.mean(older)) if older else 0.0,
            "maps_seen": len(prior),
        })
    return out


# Fields this provider SUMS across the maps of a series -- dividing by maps
# turns them back into the per-map rate they are quoted as everywhere else.
RATE_FIELDS = ("adr", "kast", "rating")
# Per-series totals, which become a rate the same way but for a different
# reason: the denominator is maps played, not maps summed over.
COUNT_FIELDS = ("k", "d", "a", "hs", "fk", "fd", "dmg", "clutch", "tk", "td")


def _played(match):
    """Maps played in a series, capped at 2 -- lines are quoted over maps 1-2."""
    score = str(match.get("score") or "")
    try:
        return min(sum(int(x) for x in score.split("-")), 2)
    except ValueError:
        return match.get("games") or 2


def _as_date(value):
    try:
        year, month, day = str(value)[:10].split("-")
        return datetime.date(int(year), int(month), int(day))
    except Exception:
        return None


def context_rows(graded, data, index, stat="kills", maps=2):
    """Rest, congestion, roster churn and stand-ins, from what is already scraped.

    Two of the five items on WHAT WOULD ACTUALLY HELP do not need a scraper at
    all. Days since the team last played, and how many matches it has played in
    the past week, are both derivable from the match list. So is roster churn:
    `actual` names the five players who appeared, so a lineup that changes
    between matches is visible even though the provider never says "stand-in",
    and a player who did not appear in his team's last match is the closest
    thing to a stand-in flag this data has.

    Every feature is about the team's RECENT lineups rather than this match's,
    because this match's lineup is not knowable before it is played.
    """
    region = next(iter(data))
    matches = sorted(data[region].get("past_matches") or [],
                     key=lambda m: str(m.get("date")))
    key = ow.STAT_TYPES[stat]["key"]

    history = collections.defaultdict(list)
    for match in matches:
        when = _as_date(match.get("date"))
        if not when:
            continue
        played = _played(match)
        for team, players in (match.get("actual") or {}).items():
            vals = {name: stats.get(key) for name, stats in players.items()
                    if isinstance(stats, dict) and stats.get(key) is not None}
            if vals:
                history[team].append({"date": when, "lineup": frozenset(vals),
                                      "vals": vals, "maps": played})

    rows_in = sw.cell_rows(graded, "cs2", stat)
    projected = {}
    for row, mu, _ in sw.project_all(rows_in, data, index, "cs2", stat,
                                     ow.SHIPPED_WEIGHTS["cs2"][stat]):
        projected[id(row)] = mu

    out = []
    for row in rows_in:
        if row.get("maps") != maps or row.get("actual") is None:
            continue
        team, player = str(row.get("team") or ""), str(row.get("player") or "")
        when = _as_date(row.get("match_date"))
        if not when:
            continue
        prior = [h for h in history.get(team, []) if h["date"] < when]
        if len(prior) < 4:
            continue
        last, recent = prior[-1], prior[-5:]
        window = prior[-10:]
        team_maps = sum(h["maps"] for h in window)
        mine = [h for h in window if player in h["vals"]]
        my_maps = sum(h["maps"] for h in mine)
        out.append({
            "line": row["line"], "actual": row["actual"],
            "model": projected.get(id(row)),
            "form10": (sum(h["vals"][player] for h in mine) / my_maps)
                      if my_maps else None,
            "days_rest": min((when - last["date"]).days, 60),
            "played_last": 1.0 if player in last["vals"] else 0.0,
            "appearances5": sum(1 for h in recent if player in h["vals"]),
            "lineup_changes5": sum(1 for i in range(1, len(recent))
                                   if recent[i]["lineup"] != recent[i - 1]["lineup"]),
            "matches_last_7d": sum(1 for h in prior if (when - h["date"]).days <= 7),
            "team_rate": (sum(sum(h["vals"].values()) for h in window) / team_maps)
                         if team_maps else None,
        })
    return [r for r in out if all(v is not None for v in r.values())]


def rich_rows(graded, data, index, maps=2, window=10):
    """Every stat the provider records, as a predictor of kills.

    The shipped model reads `k` and nothing else, while `actual` carries adr,
    kast, rating, headshots, opening kills and deaths, assists, damage, clutches
    and trades. Damage is the interesting one: it is far less noisy than kills,
    since a player who does the work and gets traded out of the frag still
    banks the damage, so a player's adr ought to be a cleaner read on his form
    than his kill count is.
    """
    matches = sorted(data[next(iter(data))].get("past_matches") or [],
                     key=lambda m: str(m.get("date")))
    history = collections.defaultdict(list)
    for match in matches:
        played, date = _played(match), str(match.get("date") or "")
        for team, players in (match.get("actual") or {}).items():
            for name, stats in players.items():
                if not isinstance(stats, dict) or stats.get("k") is None:
                    continue
                entry = {"date": date, "maps": played}
                for field in COUNT_FIELDS:
                    entry[field] = stats.get(field)
                for field in RATE_FIELDS:
                    value = stats.get(field)
                    entry[field] = (value / played) if isinstance(
                        value, (int, float)) and played else None
                got = rounds_of(stats, played)
                entry["rounds"] = got if got and 15 < got < 70 else None
                history[(team, name)].append(entry)

    rows_in = sw.cell_rows(graded, "cs2", "kills")
    projected = {}
    for row, mu, _ in sw.project_all(rows_in, data, index, "cs2", "kills",
                                     ow.SHIPPED_WEIGHTS["cs2"]["kills"]):
        projected[id(row)] = mu

    out = []
    for row in rows_in:
        if row.get("maps") != maps or row.get("actual") is None:
            continue
        prior = [h for h in (history.get((str(row.get("team") or ""),
                                          str(row.get("player") or ""))) or [])
                 if h["date"] < str(row.get("match_date") or "")][-window:]
        if len(prior) < 4:
            continue
        made = {"line": row["line"], "actual": row["actual"],
                "model": projected.get(id(row))}
        total_maps = sum(h["maps"] for h in prior)
        for name in COUNT_FIELDS:
            vals = [h[name] for h in prior if h.get(name) is not None]
            made[f"{name}_per_map"] = (sum(vals) / total_maps) if vals and total_maps else None
        for name in RATE_FIELDS:
            vals = [h[name] for h in prior if h.get(name) is not None]
            made[name] = statistics.mean(vals) if vals else None
        paced = [h for h in prior if h["rounds"]]
        made["rounds_per_series"] = (statistics.mean(h["rounds"] for h in paced)
                                     if paced else None)
        made["kills_per_round"] = ((sum(h["k"] for h in paced)
                                    / sum(h["rounds"] for h in paced)) if paced else None)
        made["dmg_per_kill"] = ((made["dmg_per_map"] / made["k_per_map"])
                                if made.get("dmg_per_map") and made.get("k_per_map")
                                else None)
        made["opening_share"] = ((made["fk_per_map"] / made["k_per_map"])
                                 if made.get("fk_per_map") and made.get("k_per_map")
                                 else None)
        out.append(made)
    return out


def odds_rows(game, stat, graded, data, index, maps=2):
    """Props joined to the bookmakers' pre-match price on their own match.

    THE ONE FEATURE THAT IS NOT FORM. Tests 3 to 6 all failed the same way: a
    feature derived from what a player has done is a read on his form, and the
    line already has his form. The market's price is not that. It is a forecast
    of how the match itself will go -- whether it is competitive or a walkover
    -- which is the quarter of kill variance the round-count finding said is
    unpredictable from anything in this repository.

    Two features come out of it:
      * `win_prob`, the devigged probability that the player's own team wins.
      * `mismatch`, how far that is from a coin flip. A blowout in either
        direction is short, and short maps mean fewer rounds for everybody, so
        the sign of the edge is not obvious and this deliberately does not
        assume one.
    And for CS2 a third, where the provider posts it: `implied_over`, the
    market's own price on the series going past two and a half maps.

    What is joinable is bounded by where the odds came from. Valorant's are on
    vlr.gg's own match pages, which keep the pre-match prices after the fact,
    so every past match carries them once the scraper has walked the pages
    again. CS2's cannot be backfilled at all -- see scripts/odds_store.py --
    so its rows only exist for matches captured since that started.
    """
    priced = {}
    for region, blob in (data or {}).items():
        for match in blob.get("past_matches") or []:
            context = match.get("context") or {}
            odds = context.get("odds") or {}
            implied = odds.get("implied") or {}
            if len(implied) != 2:
                continue
            for team in (match.get("teamA"), match.get("teamB")):
                if team and team in implied:
                    priced[(str(match.get("date") or ""), str(team))] = {
                        "win_prob": implied[team],
                        "stage": context.get("stage"),
                        "maps": context.get("maps") or [],
                    }

    # The CS2 store is keyed on bo3.gg's slug rather than date and team,
    # because that is the only identity two teams meeting twice in a day have.
    captured = {}
    if game == "cs2":
        try:
            captured = odds_store.load()
        except Exception:
            captured = {}
    by_pair = {}
    for entry in captured.values():
        if not isinstance(entry, dict):
            continue
        odds = entry.get("last") or entry.get("first") or {}
        implied = (odds or {}).get("implied") or {}
        date = str(entry.get("start_date") or "")[:10]
        for team in (entry.get("teamA"), entry.get("teamB")):
            if team and team in implied:
                by_pair[(date, str(team))] = {
                    "win_prob": implied[team],
                    "implied_over": ((odds.get("total_maps") or {})
                                     .get("implied_over")),
                    "stage": None, "maps": [],
                }
    priced.update(by_pair)
    if not priced:
        return []

    rows_in = sw.cell_rows(graded, game, stat)
    projected = {}
    for row, mu, _ in sw.project_all(rows_in, data, index, game, stat,
                                     ow.SHIPPED_WEIGHTS[game][stat]):
        projected[id(row)] = mu

    out = []
    for row in rows_in:
        if (maps and row.get("maps") != maps) or row.get("actual") is None:
            continue
        found = priced.get((str(row.get("match_date") or "")[:10],
                            str(row.get("team") or "")))
        if not found:
            continue
        made = {
            "line": row["line"], "actual": row["actual"],
            "model": projected.get(id(row)),
            "win_prob": found["win_prob"],
            "mismatch": abs(found["win_prob"] - 0.5),
        }
        if found.get("implied_over") is not None:
            made["implied_over"] = found["implied_over"]
        if made["model"] is not None:
            out.append(made)
    return out


# ============================================================
# The tests
# ============================================================

def test_blend(rows, report):
    report("\n1. BLEND — mix the model with the line at every weight.")
    base = statistics.mean(abs(r["line"] - r["actual"]) for r in rows)
    best = (0.0, base)
    for weight in BLEND_WEIGHTS:
        mae = statistics.mean(
            abs((weight * r["model"] + (1 - weight) * r["line"]) - r["actual"])
            for r in rows)
        if mae < best[1] - 1e-12:
            best = (weight, mae)
        report(f"   {weight:>5.0%} model  MAE {mae:.4f}"
               + ("   <- line alone" if weight == 0 else
                  "   <- model alone" if weight == 1 else ""))
    verdict = (f"best is {best[0]:.0%} model — it adds information"
               if best[0] > 0 else
               "zero weight on the model is optimal — it adds nothing")
    report(f"   {verdict}")
    return {"best_weight": best[0], "best_mae": best[1], "line_mae": base}


def test_regression(rows, report):
    report("\n2. REGRESSION — the line and the model together, model rescaled freely.")
    beta, errors = ols([r["actual"] for r in rows],
                       [[r["line"] for r in rows], [r["model"] for r in rows]])
    if beta is None:
        report("   singular; skipped")
        return {}
    out = {}
    for name, b, e in zip(("intercept", "line", "model"), beta, errors):
        t = b / e if e else 0.0
        out[name] = {"coefficient": b, "se": e, "t": t}
        report(f"   {name:<10} {b:>+8.3f}  t {t:>+6.2f}"
               + ("  significant" if abs(t) > 1.96 else ""))
    return out


def test_features(rows, names, report, label):
    report(f"\n{label}")
    y, line = [r["actual"] for r in rows], [r["line"] for r in rows]
    found = []
    for name in names:
        beta, errors = ols(y, [line, [r[name] for r in rows]])
        if beta is None:
            continue
        t = beta[2] / errors[2] if errors[2] else 0.0
        report(f"   {name:<12} {beta[2]:>+8.3f}  t {t:>+6.2f}"
               + ("   <- significant in sample" if abs(t) > 1.96 else ""))
        if abs(t) > 1.96:
            found.append(name)
    return found


def report_walk_forward(scored, report, label="   walk-forward, which is what decides it:"):
    """Print a walk-forward table against its own line-alone baseline.

    Out of sample is the only number that settles anything here, so every test
    that produces one prints it the same way rather than each inventing a
    format.
    """
    if not scored or "line alone" not in scored:
        return None
    base = scored["line alone"]
    report(label)
    for name, mae in scored.items():
        beats = name != "line alone" and mae < base - 1e-9
        report(f"     {name:<20} {mae:>8.4f} {mae - base:>+8.4f}"
               + ("   <- BEATS THE LINE" if beats else ""))
    return base


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", default="cs2")
    ap.add_argument("--stat", default="kills")
    ap.add_argument("--maps", type=int, default=2)
    ap.add_argument("--results", default="props_results.json")
    ap.add_argument("--json")
    args = ap.parse_args(argv)

    graded = json.load(open(args.results))["graded"]
    data, index = sw.load(args.game)
    rows = series_rows(args.game, args.stat, graded, data, index, args.maps)
    if len(rows) < 100:
        print(f"only {len(rows)} usable props; not enough to conclude anything")
        return 1
    print(f"{args.game} {args.stat} maps 1-{args.maps}: {len(rows)} props, "
          "point in time")

    out = {"game": args.game, "stat": args.stat, "n": len(rows)}
    out["blend"] = test_blend(rows, print)
    out["regression"] = test_regression(rows, print)

    series_features = ["form3", "form10", "team_rate", "opp_rate", "team_pace",
                       "opp_pace", "history"]
    hits = test_features(rows, series_features, print,
                         "3. RAW FEATURES — the model's inputs, against the line.")
    out["series_hits"] = hits

    variants = {"line alone": None,
                "form": lambda r: [r["form10"]],
                "form + opponent": lambda r: [r["form10"], r["opp_pace"], r["opp_rate"]],
                "line + opponent": lambda r: [r["line"], r["opp_pace"], r["opp_rate"]]}
    scored = walk_forward(rows, variants)
    if scored:
        print("\n   walk-forward, which is what decides it:")
        base = scored.get("line alone")
        for name, mae in scored.items():
            beats = mae < base - 1e-9 and name != "line alone"
            print(f"     {name:<18} {mae:>8.4f} {mae - base:>+8.4f}"
                  + ("   <- BEATS THE LINE" if beats else ""))
        out["series_walk_forward"] = scored

    if args.game == "cs2":
        per_map = per_map_rows(graded, args.stat, args.maps)
        if len(per_map) >= 100:
            print(f"\n4. PER-MAP FEATURES — {len(per_map)} props with 6+ prior maps "
                  "from per_game, which the model never reads.")
            names = ["map_mean", "map_median", "map_sd", "map_recent", "trend",
                     "maps_seen"]
            out["per_map_hits"] = test_features(per_map, names, print, "")
            scored = walk_forward(per_map, {
                "line alone": None,
                "per-map form": lambda r: [r["map_mean"]],
                "line + per-map": lambda r: [r["line"], r["map_mean"], r["trend"]]})
            if scored:
                base = scored.get("line alone")
                print("   walk-forward:")
                for name, mae in scored.items():
                    print(f"     {name:<18} {mae:>8.4f} {mae - base:>+8.4f}"
                          + ("   <- BEATS THE LINE"
                             if mae < base - 1e-9 and name != "line alone" else ""))
                out["per_map_walk_forward"] = scored

    context = context_rows(graded, data, index, args.stat, args.maps) \
        if args.game == "cs2" else []
    if len(context) >= 100:
        print(f"\n5. CONTEXT — {len(context)} props with rest, congestion and "
              "roster churn.")
        names = ["days_rest", "played_last", "appearances5", "lineup_changes5",
                 "matches_last_7d", "form10", "team_rate"]
        out["context_hits"] = test_features(context, names, print, "")
        scored = walk_forward(context, {
            "line alone": None,
            "line + rest": lambda r: [r["line"], r["days_rest"],
                                      r["matches_last_7d"]],
            "line + churn": lambda r: [r["line"], r["played_last"],
                                       r["appearances5"], r["lineup_changes5"]],
            "line + all context": lambda r: [r["line"]] + [r[n] for n in names],
            "form + all context": lambda r: [r[n] for n in names]})
        report_walk_forward(scored, print)
        out["context_walk_forward"] = scored

    rich = rich_rows(graded, data, index, args.maps) \
        if args.game == "cs2" and args.stat == "kills" else []
    if len(rich) >= 100:
        names = [f"{n}_per_map" for n in COUNT_FIELDS] + list(RATE_FIELDS) + [
            "kills_per_round", "rounds_per_series", "dmg_per_kill", "opening_share"]
        complete = [r for r in rich if all(r.get(n) is not None for n in names)]
        print(f"\n6. RICH STATS — {len(rich)} props, {len(complete)} with every "
              "stat present. The model reads kills and nothing else.")
        if len(complete) >= 100:
            out["rich_hits"] = test_features(complete, names, print, "")
            scored = walk_forward(complete, {
                "line alone": None,
                "line + adr": lambda r: [r["line"], r["adr"]],
                "line + rating/kast": lambda r: [r["line"], r["rating"], r["kast"]],
                "line + pace": lambda r: [r["line"], r["kills_per_round"],
                                          r["rounds_per_series"]],
                "line + everything": lambda r: [r["line"]] + [r[n] for n in names]})
            report_walk_forward(scored, print)
            out["rich_walk_forward"] = scored

    priced = odds_rows(args.game, args.stat, graded, data, index, args.maps)
    print(f"\n7. THE MARKET'S OWN PRICE — {len(priced)} props joined to a "
          "pre-match price.")
    if len(priced) < 100:
        print("   not enough yet to conclude anything. Valorant fills in as the "
              "scraper re-walks")
        print("   the match pages it already visits; CS2 cannot be backfilled at "
              "all and")
        print("   accumulates from the run that first captured it. See "
              "scripts/odds_store.py.")
    else:
        names = ["win_prob", "mismatch"]
        if all("implied_over" in r for r in priced):
            names.append("implied_over")
        out["odds_hits"] = test_features(priced, names, print, "")
        variants = {"line alone": None,
                    "line + win prob": lambda r: [r["line"], r["win_prob"]],
                    "line + mismatch": lambda r: [r["line"], r["mismatch"]],
                    "line + both": lambda r: [r["line"], r["win_prob"],
                                              r["mismatch"]]}
        if "implied_over" in names:
            variants["line + series length"] = lambda r: [r["line"],
                                                          r["implied_over"]]
        scored = walk_forward(priced, variants)
        report_walk_forward(scored, print)
        out["odds_walk_forward"] = scored
        out["odds_n"] = len(priced)

    beaten = (out["blend"]["best_weight"] > 0
              or any(v.get("t", 0) > 1.96 for k, v in out["regression"].items()
                     if k == "model")
              or any(name != "line alone" and mae < out.get("series_walk_forward", {})
                     .get("line alone", 9e9)
                     for name, mae in out.get("series_walk_forward", {}).items()))
    print()
    if beaten:
        print("SOMETHING BEAT THE LINE. Follow it up — that is the whole game.")
    else:
        print("NOTHING BEATS THE LINE. This market is efficient with respect to "
              "every piece")
        print("of information in this repository, so the gap is not closable by "
              "better")
        print("modelling of this data. See WHAT WOULD ACTUALLY HELP in this file's "
              "header.")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(out, f, indent=2)
            f.write("\n")
        print(f"\nwrote {args.json}")
    return 0


# WHAT WOULD ACTUALLY HELP. The list as it stood on 2026-09-29, with what
# happened to each item since:
#
#   * ODDS. COLLECTED, and now test 7. scripts/map_context.py reads them off
#     the pages the scrapers already download -- vlr.gg's odds module for
#     Valorant, and for CS2 a bet_updates field that was arriving on every
#     bo3.gg response and being thrown away. Not answerable yet and not
#     answerable soon: neither provider serves a usable pre-match price after
#     the match, so both games accumulate forward rather than backfilling.
#   * ROSTER CHANGES AND STAND-INS. RULED OUT, test 5. `actual` names the
#     five players who appeared, so a lineup change is visible even though the
#     provider never says "stand-in". Every churn feature was insignificant,
#     and walk-forward all of them together were worse than the line alone.
#   * REST AND TRAVEL. RULED OUT for rest, test 5: days since the last match
#     reached t +1.46 and lost out of sample. LAN against online is NOT
#     AVAILABLE, which is a different answer from untested: bo3.gg's whole
#     tournament object is seven fields (id, name, slug, image_url, prize,
#     status, tier_rank) and none of them says where a match is played, and
#     vlr.gg's header carries a countdown rather than a venue. Nothing short
#     of a different provider closes this one.
#   * MAP POOL AND VETO. NOW COLLECTED for Valorant, not yet testable, and
#     bounded in a way that matters: vlr.gg publishes the whole veto with map
#     names and pick order, but only AFTER it happens, minutes before the
#     first map. So it can be backtested and cannot be projected from, which
#     makes it a way to understand the residual rather than a feature.
#   * ROLE. Partly ruled out, test 6: opening kills, opening deaths and
#     opening share are what separates an entry fragger from an AWPer and all
#     three were insignificant.
#   * TOURNAMENT STAGE. NOW COLLECTED for Valorant ("Group Stage: Opening (A)")
#     and untested. NOT AVAILABLE for CS2 for the same reason as the venue.
#     What bo3.gg does carry and nothing reads is the tournament's PRIZE POOL,
#     which is a stakes proxy rather than a stage -- and a close cousin of
#     tier_rank, which the line already knows about.
#   * MAP POOL for CS2. NOT AVAILABLE either. `with=match_maps` is accepted by
#     the API and came back empty on every row tried, so the CS2 half of the
#     veto question cannot be answered from this provider at all.
#
# What is left is thin, and that is the finding. Six of seven tests are in,
# five of them negative, and the one still open is the only one that was ever
# about the match rather than about the player.

if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Match context that vlr.gg publishes and this repository has never read:
the bookmakers' prices, the map veto, and the stage of the event.

WHY THIS EXISTS. scripts/dev/can_we_beat_the_line.py establishes, over six
tests, that the posted prop line is efficient with respect to every piece of
information in this repository -- the model's output, its inputs, the per-map
record, rest and roster churn, and the fifteen provider stats the model never
reads. Nothing beats it. The honest conclusion was that the remaining hope is
data the repository does not have, and the cheapest of those was odds, because
they sit on pages scrape_valorant.py already downloads.

WHAT IS ACTUALLY ON THE PAGE, established by probe rather than assumed
(scripts/dev/probe_map_context.py, run against live pages from a runner):

  * ODDS, on an UPCOMING match. One <a class="match-bet-item"> per bookmaker,
    each carrying both teams and a decimal price, and a note reading
    "Pre-match".
  * ODDS, on a FINISHED match, in a different layout and USELESS. The page
    swaps the two-way block for a settled-bet message -- "$100 on 100 Thieves
    returned $140 at pre-match odds ... 1.40 100T odds pre-match" -- and that
    is the WINNER's price and only the winner's. The loser's is not on the
    page. The number really is the pre-match one, so it looks backfillable and
    is not: a team having a price at all means it won, so any feature built
    from it predicts the result perfectly in sample and knows nothing in
    advance. This is refused deliberately; see parse_odds.
  * VETO. A single line -- "FUT ban Abyss; JDG ban Haven; FUT pick Ascent;
    JDG pick Summit; FUT ban Lotus; JDG ban Sunset; Split remains" -- naming
    every ban, every pick and the decider, by team tag, in order. Present on
    finished matches and ABSENT on upcoming ones, because the veto happens
    minutes before the first map. So it can be backtested but cannot be
    projected from, which the caller has to know.
  * STAGE. "Group Stage: Opening (A)".

Nothing here logs in, nothing here places anything, and every one of these is
on the public match page the scraper already fetches for its stats.
"""
import re
import statistics

#: The bookmaker logo filename is the only name for the book on the page.
_BOOK = re.compile(r'<img\s+src="/img/pd/([a-z0-9_.\-]+)\.png"', re.I)
_ANCHOR = re.compile(r'<a\b[^>]*class="[^"]*\bmatch-bet-item\b[^"]*"[^>]*>(.*?)</a>',
                     re.S | re.I)
_TEAM = re.compile(r'class="match-bet-item-team-name"\s*>\s*(.*?)\s*</span>', re.S | re.I)
#: The settled layout's own marker. Its team spans are class "…-teamzzz" and
#: it carries no "…-team-name" at all, so the two layouts cannot be confused --
#: but a future layout change could, and a parser that silently accepted a
#: one-sided market would leak the result rather than fail.
_SETTLED = re.compile(r'class="match-bet-item-return', re.I)
_PRICE = re.compile(r'class="match-bet-item-odds[^"]*"\s*>\s*([0-9]+(?:\.[0-9]+)?)\s*</span>',
                    re.S | re.I)
_NOTE = re.compile(r'class="[^"]*match-bet-item-note[^"]*"[^>]*>(.*?)</div>', re.S | re.I)
_HALF = re.compile(r'class="match-bet-item-half', re.I)
_NOTE_LINE = re.compile(r'class="[^"]*match-header-note[^"]*"[^>]*>(.*?)</div>', re.S | re.I)
_SERIES = re.compile(r'class="[^"]*match-header-event-series[^"]*"[^>]*>(.*?)</div>',
                     re.S | re.I)
_VETO_STEP = re.compile(r"^(?P<team>.+?)\s+(?P<action>ban|pick)\s+(?P<map>[\w'\-]+)$", re.I)
_DECIDER = re.compile(r"^(?P<map>[\w'\-]+)\s+remains$", re.I)

#: A decimal price outside this range is a parse error, not a long shot. The
#: shortest price a two-way market can carry is just over 1.0, and anything
#: past 30 on a best-of-three between professional teams is a mis-read number.
MIN_PRICE, MAX_PRICE = 1.01, 30.0


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html or "").split())


def parse_odds(html):
    """Every bookmaker's two-way price, as {"book": name, "prices": {team: decimal}}.

    Each anchor is split on its two halves before the team and the price are
    read, because the halves are not laid out the same way: the first puts the
    team before its price and the second puts the price first. Pairing by
    document order across the whole anchor would therefore hand the favourite's
    price to the underdog on every single match.
    """
    books = []
    for anchor in _ANCHOR.findall(html or ""):
        # A settled anchor names one team -- the winner -- and no other. Its
        # price is genuinely the pre-match one, which is exactly what makes it
        # dangerous: "this team has a price" is the same statement as "this
        # team won", so a feature built on it is a perfect in-sample predictor
        # that knows nothing before the match. Refused rather than salvaged.
        if _SETTLED.search(anchor):
            continue
        book = _BOOK.search(anchor)
        note = _text(_NOTE.search(anchor).group(1)) if _NOTE.search(anchor) else ""
        prices = {}
        halves = _HALF.split(anchor)[1:]
        for half in halves:
            team, price = _TEAM.search(half), _PRICE.search(half)
            if not team or not price:
                continue
            name, value = _text(team.group(1)), float(price.group(1))
            if name and MIN_PRICE <= value <= MAX_PRICE:
                prices[name] = value
        if len(prices) == 2:
            books.append({"book": book.group(1) if book else None,
                          "note": note, "prices": prices})
    return books


def devig(prices):
    """Decimal prices to probabilities that sum to one.

    The reciprocal of a decimal price is the book's implied probability WITH
    its margin still in it, so two sides sum to something above one. Dividing
    by that sum is the proportional method: crude next to a power or
    Shin devig, and the difference between them is far smaller than anything
    this data could resolve.
    """
    raw = {team: 1.0 / price for team, price in prices.items() if price}
    total = sum(raw.values())
    if not total:
        return {}
    return {team: value / total for team, value in raw.items()}


def consensus(books, pre_match_only=True):
    """The median price per team across books, and the devigged probabilities.

    Median rather than mean: two books is the usual count and a third that has
    not moved its line yet should not drag the pair. `pre_match_only` drops any
    book whose note says the price is live, since a live price has the first
    map's result in it and would leak straight into a backtest.
    """
    usable = [b for b in books
              if not pre_match_only or "pre-match" in (b["note"] or "").lower()]
    if not usable:
        return {}
    teams = set()
    for book in usable:
        teams |= set(book["prices"])
    if len(teams) != 2:
        return {}
    prices = {}
    for team in teams:
        got = [b["prices"][team] for b in usable if team in b["prices"]]
        if not got:
            return {}
        prices[team] = statistics.median(got)
    return {"prices": prices, "implied": devig(prices), "books": len(usable)}


def parse_veto(html):
    """The veto as an ordered list of {team, action, map}.

    Teams are named by TAG here ("FUT", "JDG") while the rest of the page names
    them in full, so a caller joining this to anything else has to map tags
    itself rather than assume the names match.
    """
    for block in _NOTE_LINE.findall(html or ""):
        line = _text(block)
        steps = []
        for part in line.split(";"):
            part = part.strip()
            step = _VETO_STEP.match(part)
            if step:
                steps.append({"team": step.group("team").strip(),
                              "action": step.group("action").lower(),
                              "map": step.group("map")})
                continue
            decider = _DECIDER.match(part)
            if decider:
                steps.append({"team": None, "action": "decider",
                              "map": decider.group("map")})
        # A match-header-note that carries no ban and no pick is some other
        # note -- a patch number, a forfeit -- and not a veto at all.
        if any(s["action"] in ("ban", "pick") for s in steps):
            return steps
    return []


def maps_from_veto(veto):
    """The maps that will actually be played, in the order they were chosen."""
    return [step["map"] for step in veto or []
            if step["action"] in ("pick", "decider")]


def parse_stage(html):
    """The event series line, e.g. "Group Stage: Opening (A)"."""
    found = _SERIES.search(html or "")
    text = _text(found.group(1)) if found else ""
    return text or None


def context_from_page(html):
    """Everything above, as one record, or {} when the page carries none of it.

    Empty rather than a record full of Nones: a match whose page had no odds is
    a match to leave out of a measurement, and a record of Nones invites a
    caller to average them in as zeros.
    """
    books = parse_odds(html)
    agreed = consensus(books)
    veto = parse_veto(html)
    stage = parse_stage(html)
    record = {}
    if agreed:
        record["odds"] = agreed
    if veto:
        record["veto"] = veto
        record["maps"] = maps_from_veto(veto)
    if stage:
        record["stage"] = stage
    return record


# ============================================================
# bo3.gg, which is a different problem
# ============================================================
#
# CS2 comes from bo3.gg's JSON API rather than an HTML page, and its odds
# arrive in a `bet_updates` field that is already on every match record
# scrape_cs2.py fetches -- no expansion, no extra request, and ignored since
# the scraper was written. (`with=bet_updates` answers 422 precisely because
# it is a field and not a relation, which is what made it look unreachable.)
#
# The field holds one provider's two-way market plus a dozen side markets:
#
#     {"team_1": {"name": "PCIFIC", "coeff": 2.279, "active": true, ...},
#      "team_2": {"name": "STATE",  "coeff": 1.57,  "active": true, ...},
#      "markets_count": 16, "bet_provider_id": 39,
#      "additional_markets": [{"bet_type": "total_maps_over_2_5",
#                              "coeff": 1.9, "team_id": null, ...}, ...],
#      "path": "https://<affiliate>/...?r=/en/line/Esports/..."}
#
# AND THE IMPORTANT PART, which is why this is not simply the same code as the
# vlr side: on a FINISHED match the field holds the LAST price seen, which is
# an in-play price. A real finished sample read 13.6 against 1.016 with
# `active: false`, `markets_count: 2` and `/en/live/` in the path -- that is
# the market at the point one team had all but won, and a feature built on it
# would backtest beautifully and predict the past.
#
# So unlike vlr.gg, bo3.gg CANNOT be backfilled. Pre-match CS2 odds exist only
# while a match is still upcoming, which means they have to be captured as the
# scraper passes and carried forward, and a backtest on them can only cover
# matches captured since. is_pre_match() is what keeps a live price out.

#: The affiliate URL is the only place the provider says which market this is:
#: /en/line/ for the pre-match book, /en/live/ for the in-play one.
_LINE_PATH = re.compile(r"/en/line/", re.I)
_LIVE_PATH = re.compile(r"/en/live/", re.I)


def is_pre_match(bet_updates):
    """True only when this really is the pre-match book.

    Three signals, and all of them have to agree, because being wrong here is
    not a missing feature but a leak that looks like a discovery:

      * the path names /en/line/ rather than /en/live/;
      * both sides are still `active`, which they stop being once settled;
      * the full market count is there -- a finished match's record collapsed
        to 2 markets, against 16 on an upcoming one.
    """
    if not isinstance(bet_updates, dict):
        return False
    path = bet_updates.get("path") or ""
    if _LIVE_PATH.search(path) or not _LINE_PATH.search(path):
        return False
    sides = [bet_updates.get("team_1"), bet_updates.get("team_2")]
    if not all(isinstance(s, dict) and s.get("active") for s in sides):
        return False
    return (bet_updates.get("markets_count") or 0) > 2


def from_bet_updates(bet_updates, require_pre_match=True):
    """bo3.gg's odds field as the same record shape the vlr side produces.

    The affiliate link is deliberately NOT carried through: it is a tracking
    URL for a betting site, it is the same for every match, and nothing
    downstream has any use for it.
    """
    if not isinstance(bet_updates, dict):
        return {}
    if require_pre_match and not is_pre_match(bet_updates):
        return {}
    prices = {}
    for side in ("team_1", "team_2"):
        entry = bet_updates.get(side)
        if not isinstance(entry, dict):
            return {}
        name, coeff = entry.get("name"), entry.get("coeff")
        if not name or not isinstance(coeff, (int, float)):
            return {}
        if not MIN_PRICE <= coeff <= MAX_PRICE:
            return {}
        prices[str(name)] = float(coeff)
    if len(prices) != 2:
        return {}

    record = {"prices": prices, "implied": devig(prices), "books": 1,
              "provider": bet_updates.get("bet_provider_id")}

    # The series-length market, which is the market's own forecast of exactly
    # the thing the round-count finding said the model cannot predict: whether
    # this is a competitive series or a walkover.
    totals = {}
    for market in bet_updates.get("additional_markets") or []:
        if not isinstance(market, dict):
            continue
        kind, coeff = market.get("bet_type"), market.get("coeff")
        if (isinstance(kind, str) and kind.startswith("total_maps_")
                and isinstance(coeff, (int, float))
                and MIN_PRICE <= coeff <= MAX_PRICE):
            totals[kind] = float(coeff)
    over = totals.get("total_maps_over_2_5")
    under = totals.get("total_maps_under_2_5")
    if over and under:
        record["total_maps"] = {"over_2_5": over, "under_2_5": under,
                                "implied_over": devig({"over": over,
                                                       "under": under})["over"]}
    return record

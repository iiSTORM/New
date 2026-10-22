#!/usr/bin/env python3
"""News for every game the app tracks, in one file, graded by rule.

Pulls free RSS feeds (no keys, no logins), keeps the items that name a team
or player this app tracks, and grades each one with a fixed set of rules:
a stand-in, a benching, an illness or a visa problem is a negative for the
team it names; a return from absence is a positive; a signing is noted but
not graded, since a new player has no record with the team yet.

WHY RULES AND NOT A MODEL. Every grade has to be traceable: each item
carries the rule names that fired, so a reader can see why it was called
negative and anyone can check whether the rule was right. Nothing here is
paid for and nothing runs anywhere but the scheduled workflow.

The grades are not used by the projections. They are collected so that,
once enough have built up, whether a graded item predicts a miss can be
measured the same way every other signal in this repo was -- out of
sample, against the posted line -- before anything acts on it.

    python scripts/scrape_news.py                 # writes news.json
    python scripts/scrape_news.py --out /tmp/x.json
"""
import argparse
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

try:
    import requests
except ImportError:  # the tests exercise the parsing and grading without it
    requests = None

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; esports-news-digest/1.0)"}
KEEP_DAYS = 21
MAX_ITEMS = 400

GOOGLE = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="
# Dot Esports and Sheep Esports answer 403 to the GitHub runner, so they are
# not here. Google News search is asked for recent items only ("when:7d"):
# without it, most of what comes back is weeks old and ages straight out.
FEEDS = [
    # (game, source label, url)
    ("lol", "Google News", GOOGLE + "%22League+of+Legends%22+esports+when:7d"),
    ("lol", "Google News", GOOGLE + "LCK+OR+LPL+OR+LEC+OR+LCS+roster+when:14d"),
    ("lol", "Google News", GOOGLE + "%22Worlds+2026%22+League+of+Legends+when:7d"),
    ("valorant", "Google News", GOOGLE + "VCT+Valorant+when:7d"),
    ("valorant", "Google News", GOOGLE + "Valorant+esports+roster+when:14d"),
    ("cs2", "HLTV", "https://www.hltv.org/rss/news"),
    ("cs2", "Google News", GOOGLE + "%22Counter-Strike%22+esports+when:7d"),
    ("cs2", "Google News", GOOGLE + "CS2+roster+when:14d"),
    ("dota2", "Google News", GOOGLE + "%22Dota+2%22+esports+when:7d"),
    ("dota2", "Google News", GOOGLE + "Dota+2+roster+OR+stand-in+when:14d"),
]

DATA_FILES = {"lol": "data.json", "valorant": "valorant_data.json", "cs2": "cs2_data.json",
              "dota2": "dota_data.json"}

# ---------------------------------------------------------------- the rules
#
# (name, pattern, team effect, what it means). Matched case-insensitively
# against the title and summary. The effect is for the team the item names:
# negative where the lineup that produced the team's record will not be the
# one that plays. Ordered so the reader sees the strongest reason first.
RULES = [
    ("stand-in", r"\bstand[- ]?ins?\b|\bsubstitut\w*|\bfill(?:s|ing)? in for\b|\bin place of\b", -2,
     "a stand-in plays; the team's record was built without them"),
    ("illness/injury", r"\binjur\w*|\bill(?:ness)?\b|\bsick\b|\bhospital\w*|\bsurgery\b|\bhealth (?:issue|reason|problem)s?\b|\bwrist\b", -2,
     "a player is unwell or hurt"),
    ("visa", r"\bvisas?\b", -2, "a player may not be able to travel or play"),
    ("suspension", r"\bsuspen\w*|\bbanned\b|\bdisqualif\w*|\bpenalt(?:y|ies)\b", -2,
     "a player or team is penalised"),
    ("benched", r"\bbench(?:ed|es|ing)?\b|\binactive\b|\bmoved to (?:the )?reserves?\b", -1,
     "a regular is out of the lineup"),
    ("departure", r"\bpart(?:s|ed)? ways\b|\bleaves? the (?:team|roster|org\w*)\b|\bleft the team\b|\breleas(?:ed|es) (?:from|by)\b|\bhas been released\b|\bdeparts?\b|\bdeparture\b|\bretire\w*|\bstep(?:s|ped)? down\b|\bexits? (?:the|from) (?:team|roster|org\w*)\b|\bfree agen\w*|\bdropped\b", -1,
     "a player or coach leaves"),
    # A return FROM an absence, not an organisation "returning to CS2" or a
    # coach "returning to" a club in another role.
    ("return", r"\breturns? (?:from|after)\b|\breturning from\b|\bback in the (?:lineup|starting)\b|\brecover(?:ed|s) from\b|\bcleared to play\b|\breinstat\w*", +1,
     "a regular is back"),
    ("signing", r"\bsigns?\b|\bsigned\b|\bsigning\b|\bjoins?\b|\badds?\b|\bintroduc(?:es|ed|ing)\b|\bunveil\w*|\bacquir\w*|\bpromot\w*|\bannounce[sd]? .{0,30}roster\b", 0,
     "a new player joins; no record with the team yet"),
    ("extension", r"\bextension\b|\bextends?\b|\bre-?signs?\b", 0, "a contract is extended"),
    ("coach", r"\b(?:head )?coach\b", 0, "a coaching change"),
    ("patch", r"\bpatch \d+(?:\.\d+)*\b|\bmeta\b|\bnerf\w*|\bbuff\w*", 0, "a game patch or meta change"),
]
COMPILED = [(n, re.compile(p, re.IGNORECASE), e, why) for n, p, e, why in RULES]

# Handles that are also ordinary words: matched only when their team is
# named in the same item, or "Light" would tag every article about lighting.
COMMON_WORDS = set("""
ace ale bin bo can cold deft do east echo fade faker fly frost gate go hope
ice is jack keen kai kiin light max meteor mist neo nova one peak pure ray
river rookie ruler score shadow sky smash snow star storm sun take tarzan
tiger top vin viper zeus zest zeka jojo bean bear boo cat dog king lord
next basic alpha blood blaze atom bang asap angel brave chef clear crazy
dream flash fresh ghost hero honest jump kid lucky magic mad nice night
pace rush silent smile sonic spirit swift tank true wild zero carry
support jungle mid bot
""".split())
# An all-lowercase handle right after one of these is an ordinary word in a
# sentence ("the next", "a basic"), not a player.
_ARTICLE_BEFORE = re.compile(r"\b(?:the|a|an|of|to|in|for|and|is|be|this|that|its|their|his|her|our|"
                             r"your|on|at|with|by|from|as|or|but|very|most|more|so)\s+$", re.IGNORECASE)


def parse_date(text):
    if not text:
        return None
    text = text.strip()
    try:
        d = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def strip_html(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_feed(xml_text, game, source):
    """RSS 2.0 or Atom -> [item]. Bad XML gives [] rather than an exception:
    one broken feed must not cost the others."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    atom = "{http://www.w3.org/2005/Atom}"
    out = []
    entries = root.findall(".//item") or root.findall(f".//{atom}entry")
    for e in entries:
        def text(*tags):
            for t in tags:
                node = e.find(t)
                if node is not None:
                    if node.text and node.text.strip():
                        return node.text
                    if node.get("href"):
                        return node.get("href")
            return ""
        title = strip_html(text("title", f"{atom}title"))
        link = text("link", f"{atom}link").strip()
        when = parse_date(text("pubDate", f"{atom}published", f"{atom}updated"))
        summary = strip_html(text("description", f"{atom}summary", f"{atom}content"))[:600]
        if not title or not link:
            continue
        # Google News appends " - Publisher" to every title.
        publisher = source
        if source == "Google News" and " - " in title:
            title, publisher = title.rsplit(" - ", 1)
        out.append({"game": game, "source": publisher, "title": title, "link": link,
                    "published": when.isoformat() if when else None, "summary": summary})
    return out


def load_entities(paths=DATA_FILES):
    """{game: {"teams": {name: [aliases]}, "players": {name: team}}} from the
    committed data files: only what this app tracks can be named."""
    out = {}
    for game, path in paths.items():
        try:
            with open(path) as f:
                regions = json.load(f).get("regions", {})
        except (OSError, ValueError):
            continue
        teams, players = {}, {}
        for rd in regions.values():
            for team, info in (rd.get("teams") or {}).items():
                teams.setdefault(team, team_aliases(team))
                for p in info.get("players") or []:
                    if p.get("name"):
                        players.setdefault(p["name"], team)
        out[game] = {"teams": teams, "players": players}
    return out


def team_aliases(name):
    """The name, and the name without a trailing "Esports"/"Gaming" and the like."""
    names = {name}
    short = re.sub(r"\s+(?:Esports|E-Sports|Gaming|Team|Club|Esport)$", "", name, flags=re.IGNORECASE)
    if len(short) >= 3:
        names.add(short)
    if name.lower().startswith("team ") and len(name) > 8:
        names.add(name[5:])
    return sorted(names, key=len, reverse=True)


def mentions(text, game_entities):
    """(teams, players) named in `text`, word-bounded and case-sensitive for
    handles, so "Kiin" matches and "kiin" in a URL slug does not."""
    teams = []
    for team, aliases in game_entities["teams"].items():
        for a in aliases:
            if re.search(r"(?<![\w])" + re.escape(a) + r"(?![\w])", text, re.IGNORECASE if len(a) > 4 else 0):
                teams.append(team)
                break
    # A handle that is also a team's name ("Heroic", "HEROIC") is the team
    # when shouted, not the player.
    team_words = {a.lower() for aliases in game_entities["teams"].values() for a in aliases}
    players = []
    for name, team in game_entities["players"].items():
        if len(name) < 3:
            continue
        found = [m for m in re.finditer(r"(?<![\w])" + re.escape(name) + r"(?![\w])", text)
                 if not (name.islower() and _ARTICLE_BEFORE.search(text[:m.start()]))]
        if (not found and len(name) >= 4 and name.lower() not in COMMON_WORDS
                and name.lower() not in team_words):
            # Headlines often shout a handle ("TES ZUIAN misses Worlds").
            found = list(re.finditer(r"(?<![\w])" + re.escape(name.upper()) + r"(?![\w])", text))
        if not found:
            continue
        if name.lower() in COMMON_WORDS and team not in teams:
            continue
        players.append({"name": name, "team": team})
    for p in players:
        if p["team"] not in teams:
            teams.append(p["team"])
    return teams, players


def grade(text):
    """(score, [rule names], [reasons]) for one item's text."""
    fired = [(n, e, why) for n, rx, e, why in COMPILED if rx.search(text)]
    # A return outweighs the absence it ends ("returns from illness" is good news).
    names = {n for n, _, _ in fired}
    if "return" in names:
        fired = [f for f in fired if f[0] not in ("illness/injury", "benched", "visa")]
    score = sum(e for _, e, _ in fired)
    return score, [n for n, _, _ in fired], [why for _, _, why in fired]


def label(score):
    return "negative" if score < 0 else "positive" if score > 0 else "neutral"


# Listing pages and live-score stubs that name two teams and say nothing.
NOISE = re.compile(r"live score|match stream|live broadcast|match result|match stats|\bvs\b.*\d{1,2}\.\d{1,2}\.\d{2}",
                   re.IGNORECASE)


def build(items, entities, now, dropped=None):
    """Graded items naming a tracked team, newest first. `dropped`, when
    given, counts what was left out and why, for the run log."""
    cutoff = now - timedelta(days=KEEP_DAYS)
    seen, out = set(), []
    dropped = dropped if dropped is not None else {}
    for it in items:
        when = parse_date(it.get("published")) if it.get("published") else None
        if when and when < cutoff:
            dropped["older than %d days" % KEEP_DAYS] = dropped.get("older than %d days" % KEEP_DAYS, 0) + 1
            continue
        if NOISE.search(it["title"]):
            dropped["live-score or stream page"] = dropped.get("live-score or stream page", 0) + 1
            continue
        key = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:90]
        if key in seen:
            dropped["duplicate"] = dropped.get("duplicate", 0) + 1
            continue
        ents = entities.get(it["game"])
        if not ents:
            continue
        text = f"{it['title']}. {it.get('summary', '')}"
        teams, players = mentions(text, ents)
        if not teams:
            dropped["names no tracked team or player"] = dropped.get("names no tracked team or player", 0) + 1
            continue
        seen.add(key)
        score, rules, reasons = grade(text)
        out.append({**it, "teams": teams, "players": players, "score": score,
                    "grade": label(score), "rules": rules, "reasons": reasons})
    out.sort(key=lambda x: x.get("published") or "", reverse=True)
    return out[:MAX_ITEMS]


def team_signals(items, now, days=7):
    """{game: {team: {"score": n, "items": k, "rules": [...]}}} over the last
    `days` days: the per-team summary a fixture card can show.
    One story is usually several articles (six outlets ran ZUIAN's visa
    denial), so each KIND of news counts once per team: the score is the sum
    of the distinct rules that fired, not of the items.
    """
    cutoff = now - timedelta(days=days)
    effect = {n: e for n, _, e, _ in COMPILED}
    out = {}
    for it in items:
        when = parse_date(it.get("published")) if it.get("published") else None
        if not when or when < cutoff or not it["score"]:
            continue
        for team in it["teams"]:
            s = out.setdefault(it["game"], {}).setdefault(team, {"score": 0, "items": 0, "rules": []})
            s["items"] += 1
            for r in it["rules"]:
                if r not in s["rules"]:
                    s["rules"].append(r)
                    s["score"] += effect.get(r, 0)
    return out


def fetch_all():
    items, counts = [], []
    for game, source, url in FEEDS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            got = parse_feed(r.text, game, source)
            counts.append((game, source, len(got), None))
            items.extend(got)
        except Exception as e:  # noqa: BLE001 -- one feed's failure is logged, never fatal
            counts.append((game, source, 0, str(e)[:120]))
    return items, counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="news.json")
    args = ap.parse_args()
    if requests is None:
        print("! requests is not installed", file=sys.stderr)
        return 1
    now = datetime.now(timezone.utc)
    raw, counts = fetch_all()
    for game, source, n, err in counts:
        print(f"  {game:9} {source:14} {n:4} item(s)" + (f"  ! {err}" if err else ""))
    try:
        with open(args.out) as f:
            previous = json.load(f).get("items", [])
    except (OSError, ValueError):
        previous = []
    entities = load_entities()
    # Fresh items first, so a re-fetched story keeps its newest wording;
    # earlier ones are kept until they age out, since feeds only carry the
    # last few dozen stories.
    dropped = {}
    items = build(raw + previous, entities, now, dropped)
    print("  left out: " + (", ".join(f"{n} {why}" for why, n in sorted(dropped.items())) or "nothing"))
    if not raw and previous:
        print("! every feed failed; keeping the previous file's items")
    payload = {
        "generated_at": now.isoformat(),
        "rules": [{"name": n, "effect": e, "meaning": why} for n, _, e, why in RULES],
        "feeds": [{"game": g, "source": s, "items": n, "error": err} for g, s, n, err in counts],
        "items": items,
        "signals": team_signals(items, now),
    }
    with open(args.out, "w") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False)
    graded = sum(1 for i in items if i["score"])
    print(f"wrote {args.out}: {len(items)} item(s) naming a tracked team or player, {graded} graded +/-")
    return 0


if __name__ == "__main__":
    sys.exit(main())

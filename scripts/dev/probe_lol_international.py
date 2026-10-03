#!/usr/bin/env python3
"""
What do the three LoL sources publish about this autumn's international
events -- Demacia Cup 2026 and Worlds 2026 -- before anything is built on
them?

The scraper reads gol.gg (stats, results) and the LoL Esports API
(schedule). Neither has been pointed at an international event yet, and
playoffs_and_international_roadmap.md says to look before building: the
tournament names, the stage and round labels, and whether group and
bracket structure is published as data or has to be inferred.

Three questions, one per source:
  1. LoL Esports API: which leagues exist for these events, what
     tournaments they hold, and what getStandings returns -- stages,
     sections (groups), rankings, and per-match links in a bracket.
  2. gol.gg: the exact tournament names, and whether their match lists
     and player lists exist yet.
  3. Leaguepedia: the event pages, as a cross-check on names and dates.

Read-only. Run it from Actions (.github/workflows/probe.yml) -- the dev
container's network policy refuses all three hosts.
"""
import json
import re
import sys
import urllib.parse
import urllib.request

API = "https://esports-api.lolesports.com/persisted/gw"
API_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"   # public, as in scrape_schedule.py
GOL = "https://gol.gg"
WIKI = "https://lol.fandom.com/api.php"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
WANT = re.compile(r"world|demacia|msi|first stand|international|esports world cup", re.I)


def fetch(url, headers=None, data=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def api(path, **params):
    q = urllib.parse.urlencode({"hl": "en-US", **params})
    _, body = fetch(f"{API}/{path}?{q}", headers={"x-api-key": API_KEY})
    return json.loads(body)["data"]


def section(title):
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def short(obj, n=1500):
    text = json.dumps(obj, ensure_ascii=False)
    return text if len(text) <= n else text[:n] + f"... [{len(text)} chars]"


def lolesports():
    section("1. LoL Esports API")
    leagues = api("getLeagues")["leagues"]
    print(f"{len(leagues)} leagues. All of them, so nothing is filtered out by a guess:")
    for l in leagues:
        print(f"  {l['id']:>20}  {l.get('slug', ''):<28} {l['name']:<36} {l.get('region', '')}")
    picks = [l for l in leagues if WANT.search(l["name"]) or WANT.search(l.get("slug", ""))]
    for league in picks:
        print(f"\n--- {league['name']} ({league['slug']}, id {league['id']})")
        try:
            tours = api("getTournamentsForLeague", leagueId=league["id"])["leagues"][0]["tournaments"]
        except Exception as e:
            print(f"  getTournamentsForLeague failed: {e}")
            continue
        recent = [t for t in tours if str(t.get("startDate", "")) >= "2025-06"]
        for t in tours[:3] + [t for t in recent if t not in tours[:3]]:
            print(f"  tournament {t['id']} {t.get('slug')} {t.get('startDate')} -> {t.get('endDate')}")
        for t in recent:
            standings(t)
        try:
            events = api("getSchedule", leagueId=league["id"])["schedule"]["events"]
        except Exception as e:
            print(f"  getSchedule failed: {e}")
            continue
        events = [e for e in events if str(e.get("startTime", "")) >= "2026-01"]
        print(f"  schedule: {len(events)} events in 2026")
        blocks = {}
        for e in events:
            blocks.setdefault(e.get("blockName"), []).append(e)
        for block, evs in blocks.items():
            print(f"    block {block!r}: {len(evs)} events, "
                  f"{evs[0].get('startTime')} .. {evs[-1].get('startTime')}, "
                  f"states {sorted({x.get('state') for x in evs})}")
        for e in events[:3]:
            print("    sample event:", short(e, 900))


def standings(tournament):
    print(f"\n  getStandings for {tournament.get('slug')}:")
    try:
        data = api("getStandings", tournamentId=tournament["id"])
    except Exception as e:
        print(f"    failed: {e}")
        return
    for st in data.get("standings", []):
        for stage in st.get("stages", []):
            print(f"    stage {stage.get('name')!r} type={stage.get('type')!r} slug={stage.get('slug')!r} "
                  f"sections={len(stage.get('sections', []))}")
            for sec in stage.get("sections", [])[:8]:
                matches = sec.get("matches", [])
                ranks = sec.get("rankings", [])
                print(f"      section {sec.get('name')!r}: {len(matches)} matches, {len(ranks)} ranking rows, "
                      f"columns={sec.get('columns') and len(sec['columns'])}")
                if ranks:
                    print("        ranking sample:", short(ranks[:2], 700))
                if matches:
                    print("        match sample:", short(matches[0], 900))
                cols = sec.get("columns") or []
                for c in cols[:4]:
                    cells = c.get("cells", [])
                    print(f"        column with {len(cells)} cells; first:",
                          short(cells[0] if cells else None, 700))


def golgg():
    section("2. gol.gg")
    # The tournament list page is JS-rendered; its data comes from this
    # endpoint (season S16 = 2026).
    for season in ("S16", "S15"):
        try:
            status, body = fetch(f"{GOL}/tournament/ajax.trlist.php",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "X-Requested-With": "XMLHttpRequest",
                                          "Referer": f"{GOL}/tournament/list/"},
                                 data=urllib.parse.urlencode({"season": season}).encode())
            print(f"ajax.trlist season={season}: {status}, {len(body)} chars")
            try:
                rows = json.loads(body)
                names = [r.get("trname") or r.get("name") or str(r) for r in rows]
                hits = [n for n in names if WANT.search(str(n))]
                print(f"  {len(rows)} tournaments; international-looking: {hits}")
                if rows:
                    print("  row sample:", short(rows[0], 500))
            except ValueError:
                print("  not JSON:", body[:600])
        except Exception as e:
            print(f"ajax.trlist season={season} failed: {e}")

    candidates = ["Demacia Cup 2026", "Demacia Cup 2025", "Demacia Cup 2024",
                  "Worlds 2026", "Worlds Main Event 2026", "Worlds Play-In 2026", "Worlds Swiss Stage 2026",
                  "World Championship 2026", "Worlds Main Event 2025", "Worlds Play-In 2025",
                  "Worlds Swiss Stage 2025", "Worlds 2025"]
    for name in candidates:
        url = f"{GOL}/tournament/tournament-matchlist/{urllib.parse.quote(name)}/"
        try:
            status, body = fetch(url)
        except Exception as e:
            print(f"  matchlist {name!r}: {e}")
            continue
        rows = re.findall(r"<tr", body)
        heads = re.findall(r"<th[^>]*>(.*?)</th>", body, re.S)
        labels = sorted(set(re.findall(r"<td[^>]*>\s*([A-Z][A-Za-z0-9 \-]{2,30})\s*</td>", body)))[:30]
        print(f"  matchlist {name!r}: {status}, {len(rows)} <tr>, header={[re.sub('<.*?>', '', h).strip() for h in heads]}")
        if len(rows) > 1:
            print(f"    td labels seen: {labels}")
            first = re.search(r"<tr.*?</tr>.*?(<tr.*?</tr>)", body, re.S)
            if first:
                print("    first data row:", re.sub(r"\s+", " ", re.sub("<.*?>", " | ", first.group(1)))[:400])


def leaguepedia():
    section("3. Leaguepedia")
    q = {"action": "cargoquery", "format": "json", "limit": "60",
         "tables": "Tournaments", "fields": "Name,OverviewPage,DateStart,Date,League,Region,EventType",
         "where": "(Name LIKE '%Demacia%' OR Name LIKE '%World%') AND DateStart >= '2025-06-01'",
         "order_by": "DateStart"}
    try:
        _, body = fetch(f"{WIKI}?{urllib.parse.urlencode(q)}")
        rows = [r["title"] for r in json.loads(body).get("cargoquery", [])]
        for r in rows:
            print("  ", r)
        pages = sorted({r.get("OverviewPage") for r in rows if r.get("OverviewPage")})
    except Exception as e:
        print(f"  tournaments query failed: {e}")
        return
    for page in pages:
        if "2026" not in page:
            continue
        q = {"action": "cargoquery", "format": "json", "limit": "200",
             "tables": "MatchSchedule", "fields": "Team1,Team2,DateTime_UTC,Tab,Round,BestOf,Winner,Team1Score,Team2Score,Phase,N_MatchInPage",
             "where": f"OverviewPage='{page}'", "order_by": "DateTime_UTC"}
        try:
            _, body = fetch(f"{WIKI}?{urllib.parse.urlencode(q)}")
            rows = [r["title"] for r in json.loads(body).get("cargoquery", [])]
        except Exception as e:
            print(f"  {page}: schedule query failed: {e}")
            continue
        tabs = {}
        for r in rows:
            tabs.setdefault((r.get("Tab"), r.get("Round"), r.get("Phase")), 0)
            tabs[(r.get("Tab"), r.get("Round"), r.get("Phase"))] += 1
        print(f"\n  {page}: {len(rows)} scheduled matches")
        for k, n in tabs.items():
            print(f"    tab/round/phase {k}: {n}")
        for r in rows[:6]:
            print("    ", r)
        q = {"action": "cargoquery", "format": "json", "limit": "100",
             "tables": "TournamentGroups", "fields": "GroupName,Team,GroupN,PageAndTeam",
             "where": f"OverviewPage='{page}'"}
        try:
            _, body = fetch(f"{WIKI}?{urllib.parse.urlencode(q)}")
            rows = [r["title"] for r in json.loads(body).get("cargoquery", [])]
            print(f"    groups: {len(rows)} rows", rows[:20])
        except Exception as e:
            print(f"    groups query failed: {e}")


if __name__ == "__main__":
    for step in (lolesports, golgg, leaguepedia):
        try:
            step()
        except Exception as e:
            print(f"\n{step.__name__} failed: {e.__class__.__name__}: {e}", file=sys.stderr)
    sys.stdout.flush()

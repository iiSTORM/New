#!/usr/bin/env python3
"""
One line per match for an international event, from the LoL Esports API,
so the stage, bracket and Swiss structure can be read off real data.

probe_lol_international.py found the events and their stages; this prints
everything the group and bracket views would be built from: per match its
stage, section, state, teams with codes, series result, best-of, record,
and previousMatchIds (the only bracket link the API offers). A completed
event (Worlds 2025, MSI 2026) shows what a finished bracket looks like; the
live ones (Demacia Cup 2026, Worlds 2026) show what the views get today.

Read-only. Run it from Actions (.github/workflows/probe.yml).
"""
import json
import urllib.parse
import urllib.request

API = "https://esports-api.lolesports.com/persisted/gw"
API_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
EVENTS = [("demacia_cup", "117126995932274206", "117133773242499009"),
          ("worlds", "98767975604431411", "115660540725177488"),
          ("worlds", "98767975604431411", "113475452383887518"),
          ("msi", "98767991325878492", "115570934354631452")]


def api(path, **params):
    q = urllib.parse.urlencode({"hl": "en-US", **params})
    req = urllib.request.Request(f"{API}/{path}?{q}", headers={"x-api-key": API_KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())["data"]


def team(t):
    res = t.get("result") or {}
    rec = t.get("record") or {}
    out = t.get("code") or t.get("name")
    if res.get("outcome") or res.get("gameWins"):
        out += f"[{res.get('outcome') or '-'} {res.get('gameWins')}]"
    if rec:
        out += f"({rec.get('wins')}-{rec.get('losses')})"
    return out


def main():
    for slug, league_id, tournament_id in EVENTS:
        print(f"\n{'=' * 70}\n{slug} tournament {tournament_id}\n{'=' * 70}")
        names = {}
        try:
            data = api("getStandings", tournamentId=tournament_id)
        except Exception as e:
            print(f"getStandings failed: {e}")
            data = {"standings": []}
        for st in data.get("standings", []):
            for stage in st.get("stages", []):
                for sec in stage.get("sections", []):
                    print(f"\n[{stage.get('name')} / {sec.get('name')}] "
                          f"{len(sec.get('matches', []))} matches, rankings={len(sec.get('rankings') or [])}")
                    for r in sec.get("rankings") or []:
                        print(f"   rank {r.get('ordinal')}: "
                              + ", ".join(f"{t.get('code')}({(t.get('record') or {}).get('wins')}-"
                                          f"{(t.get('record') or {}).get('losses')})" for t in r.get("teams", [])))
                    for m in sec.get("matches", []):
                        for t in m.get("teams", []):
                            names[t.get("code")] = t.get("name")
                        print(f"   {m['id']} {m.get('state'):<10} "
                              f"{' vs '.join(team(t) for t in m.get('teams', []))} "
                              f"prev={m.get('previousMatchIds')} flags={m.get('flags')}")
        try:
            sched = api("getSchedule", leagueId=league_id)["schedule"]
        except Exception as e:
            print(f"getSchedule failed: {e}")
            continue
        events = [e for e in sched.get("events", []) if (e.get("match") or {}).get("id")]
        print(f"\nschedule pages: {sched.get('pages')}")
        print(f"schedule: {len(events)} match events (all years on this page)")
        for e in events:
            if e.get("startTime", "")[:4] < ("2026" if "2026" in str(tournament_id) or slug == "demacia_cup" else "2025"):
                pass
            m = e["match"]
            print(f"   {e.get('startTime')} {e.get('state'):<10} {e.get('blockName')!r:<18} "
                  f"{m['id']} {m.get('strategy')} "
                  f"{' vs '.join(team(t) for t in m.get('teams', []))}")
            for t in m.get("teams", []):
                names[t.get("code")] = t.get("name")
        print("\nteam codes -> names:", json.dumps(names, ensure_ascii=False))


if __name__ == "__main__":
    main()

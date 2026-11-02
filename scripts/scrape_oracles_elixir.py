#!/usr/bin/env python3
"""Oracle's Elixir LoL game data -> oracles_elixir.csv.gz, for the leagues we track.

Oracle's Elixir publishes one CSV per year of every pro LoL game: twelve rows
a game (ten players, two teams) with what gol.gg does not carry -- gold, XP
and CS differences at 10/15/20/25 minutes, damage share, vision, first
objectives, game length. Item 11 of the 2026-11-01 upgrade list. Nothing the
app loads reads it; it is here so those columns can be tested as model inputs
(scripts/dev/oracles_elixir_probe.py), and so they are already on file if one
of them turns out to help.

WHERE THE FILES ARE. The site's download links point into one public Google
Drive folder, and the current year's file is replaced daily, so a file id
written down here would go stale (the old S3 bucket the site used to serve
from no longer exists). The folder is listed through Drive's embedded folder
view, which needs no API key, and each year's file is found by its name
("2026_LoL_esports_match_data_from_OraclesElixir.csv"). KNOWN_IDS is only the
fallback for a run where the listing fails.

WHAT IS KEPT. Rows from LEAGUES only (the leagues and international events the
app tracks), and COLUMNS only -- the full file is ~100 MB a year and most of
it is leagues no line is ever posted for. The output is gzipped CSV in Oracle's
Elixir's own column names, so its documentation still describes it.

    python scripts/scrape_oracles_elixir.py               # this year and last
    python scripts/scrape_oracles_elixir.py --years 2026
"""
import argparse
import csv
import gzip
import io
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

import requests

FOLDER_ID = "1gLSw0RLjBbtaNy0dgnGQDAZOHIgCe-HH"
#: Used only when the folder listing fails. 2025's from a public pipeline
#: that hardcodes it (github.com/HerrKurz/Esports_Data_Pipeline, config.py).
KNOWN_IDS = {"2025": "1v6LRphp2kYciU4SXp0PCjEMuev1bDejc"}
OUT = "oracles_elixir.csv.gz"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

#: Oracle's Elixir's league codes for what the app tracks. WLDs is Worlds,
#: FST First Stand; the rest read as themselves.
LEAGUES = {"LCK", "LPL", "LEC", "LCS", "LCP", "CBLOL", "TCL", "WLDs", "MSI", "FST", "EWC",
           "LTA N", "LTA S", "LTA", "DCGI", "Demacia Cup"}
COLUMNS = [
    "gameid", "datacompleteness", "league", "year", "split", "playoffs", "date", "game",
    "patch", "participantid", "side", "position", "playername", "playerid", "teamname",
    "teamid", "champion", "gamelength", "result", "kills", "deaths", "assists",
    "teamkills", "teamdeaths", "doublekills", "triplekills", "firstblood",
    "firstbloodkill", "firstbloodassist", "firstbloodvictim", "team kpm", "ckpm",
    "firstdragon", "dragons", "firstherald", "firsttower", "towers", "firstbaron", "barons",
    "inhibitors", "damagetochampions", "dpm", "damageshare", "damagetakenperminute",
    "wardsplaced", "wpm", "wardskilled", "visionscore", "vspm", "totalgold", "earnedgold",
    "earned gpm", "earnedgoldshare", "goldspent", "total cs", "minionkills", "monsterkills",
    "cspm", "goldat10", "xpat10", "csat10", "golddiffat10", "xpdiffat10", "csdiffat10",
    "killsat10", "assistsat10", "deathsat10", "goldat15", "xpat15", "csat15",
    "golddiffat15", "xpdiffat15", "csdiffat15", "killsat15", "assistsat15", "deathsat15",
    "golddiffat20", "xpdiffat20", "csdiffat20", "killsat20", "deathsat20",
    "golddiffat25", "xpdiffat25", "killsat25", "deathsat25",
]
FILE_NAME = re.compile(r"^(\d{4})_LoL_esports_match_data_from_OraclesElixir\.csv$", re.I)


def parse_folder(html):
    """{year: file id} from Drive's embedded folder view. Pure, for the tests.

    Each entry is a div with id "entry-<file id>" holding a "flip-entry-title"
    div with the file name; entries are split on the former and the title read
    from what follows, so attribute order inside the tag does not matter.
    """
    out = {}
    for chunk in re.split(r'id="entry-', html or "")[1:]:
        file_id = re.match(r"[A-Za-z0-9_-]+", chunk)
        title = re.search(r'flip-entry-title"?>\s*([^<]+?)\s*<', chunk)
        if not file_id or not title:
            continue
        found = FILE_NAME.match(title.group(1).strip())
        if found:
            out[found.group(1)] = file_id.group(0)
    return out


def list_folder(session):
    try:
        r = session.get(f"https://drive.google.com/embeddedfolderview?id={FOLDER_ID}",
                        headers=HEADERS, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"  ! folder listing failed: {e}", file=sys.stderr)
        return {}
    got = parse_folder(r.text)
    if not got:
        print("  ! folder listing parsed to nothing -- Drive's page may have changed",
              file=sys.stderr)
    return got


def warning_form(html):
    """(action url, params) from Drive's "can't scan this file for viruses" page,
    or None. Pure, for the tests.

    Large files are not served straight away: Drive returns a page whose form
    carries a one-time `uuid` with the confirm, and the file comes back only
    when that form is submitted. `confirm=t` alone stopped being enough."""
    form = re.search(r'<form[^>]*id="download-form"[^>]*action="([^"]+)"(.*?)</form>',
                     html or "", re.S) or re.search(
        r'<form[^>]*action="([^"]*download[^"]*)"(.*?)</form>', html or "", re.S)
    if not form:
        return None
    params = {}
    for tag in re.findall(r"<input[^>]*>", form.group(2)):
        name = re.search(r'name="([^"]+)"', tag)
        value = re.search(r'value="([^"]*)"', tag)
        if name:
            params[name.group(1)] = value.group(1) if value else ""
    return form.group(1).replace("&amp;", "&"), params


def _stream(session, url, path, params=None):
    """Write the response to `path`; returns its first bytes."""
    with session.get(url, params=params, headers=HEADERS, stream=True, timeout=180) as r:
        r.raise_for_status()
        first = b""
        with open(path, "wb") as f:
            for block in r.iter_content(1 << 20):
                if not first:
                    first = block[:4096]
                f.write(block)
    return first


def _is_page(first):
    return first.lstrip().lower().startswith((b"<!doctype", b"<html"))


def download(session, file_id, path):
    """Stream one Drive file to `path`, getting past the virus-scan page."""
    urls = [f"https://drive.usercontent.google.com/download?id={file_id}&export=download",
            f"https://drive.google.com/uc?export=download&id={file_id}"]
    for url in urls:
        try:
            first = _stream(session, url, path)
            if _is_page(first):
                with open(path, encoding="utf-8", errors="replace") as f:
                    page = f.read()
                form = warning_form(page)
                if not form:
                    text = re.sub(r"<[^>]+>", " ", page)
                    print(f"  ! {url} returned a page with no download form: "
                          f"{' '.join(text.split())[:300]}", file=sys.stderr)
                    continue
                action, params = form
                first = _stream(session, action, path, params)
                if _is_page(first):
                    text = re.sub(r"<[^>]+>", " ", first.decode("utf-8", "replace"))
                    print(f"  ! confirming the download still returned a page: "
                          f"{' '.join(text.split())[:300]}", file=sys.stderr)
                    continue
            return True
        except requests.RequestException as e:
            print(f"  ! {url}: {e}", file=sys.stderr)
    return False


def filter_rows(lines, leagues=LEAGUES, columns=COLUMNS):
    """(header, rows) keeping tracked leagues and the named columns. Pure."""
    reader = csv.DictReader(lines)
    have = [c for c in columns if c in (reader.fieldnames or [])]
    rows = [[row.get(c, "") for c in have] for row in reader if row.get("league") in leagues]
    return have, rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    year = datetime.now(timezone.utc).year
    ap.add_argument("--years", nargs="+", default=[str(year - 1), str(year)])
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args(argv)
    session = requests.Session()
    ids = {**KNOWN_IDS, **list_folder(session)}
    print(f"files found: { {y: ids[y] for y in sorted(ids)} }")
    header, kept, done = None, [], []
    for y in args.years:
        file_id = ids.get(y)
        if not file_id:
            print(f"  ! no file for {y}", file=sys.stderr)
            continue
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, f"{y}.csv")
            if not download(session, file_id, path):
                continue
            size = os.path.getsize(path)
            with open(path, newline="", encoding="utf-8-sig") as f:
                cols, rows = filter_rows(f)
        if header is None:
            header = cols
        elif cols != header:                   # a year with fewer columns: align by name
            index = {c: i for i, c in enumerate(cols)}
            rows = [[r[index[c]] if c in index else "" for c in header] for r in rows]
        games = len({r[0] for r in rows})
        print(f"  {y}: {size / 1e6:.0f} MB downloaded, kept {len(rows)} rows, {games} games")
        kept += rows
        done.append(y)
    if not kept:
        print("nothing downloaded; leaving the existing file alone")
        return 1
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(sorted(kept, key=lambda r: (r[header.index("date")] if "date" in header else "", r[0])))
    with gzip.open(args.out, "wt", encoding="utf-8", compresslevel=9) as f:
        f.write(buf.getvalue())
    print(f"wrote {args.out}: {len(kept)} rows from {done}, "
          f"{os.path.getsize(args.out) / 1e6:.1f} MB")
    return 0 if len(done) == len(args.years) else 1


if __name__ == "__main__":
    sys.exit(main())

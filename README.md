# Esports Kill Projector

A static single-page app (`index.html`) that projects player kill totals for
League of Legends, Counter-Strike 2 and Valorant, backed by JSON data files
that a scheduled GitHub Actions workflow regenerates twice a day.

There is no build step and no server. `index.html` is the whole frontend; it
fetches its data at runtime from `raw.githubusercontent.com` on `main`, and
falls back to a snapshot bundled inside the page if a fetch fails.

## Layout

```
index.html                  the entire frontend
.github/workflows/scrape.yml the twice-daily (09:00 / 21:00 UTC) update job
scripts/                    production scrapers — run by the workflow
scripts/dev/                investigation tooling — never run by the workflow
playoffs_and_international_roadmap.md  design notes
```

## Data flow

Three jobs run in parallel on separate runners, one per game. They write
different files, so they don't depend on each other.

**LoL** (`scrape-lol`)

| Step | Script | Reads | Writes |
| --- | --- | --- | --- |
| 1 | `scrape_lcs.py` | gol.gg | `data.json` |
| 2 | `scrape_schedule.py` | LoL Esports API | `schedule.json` |
| 3 | `scrape_career.py` | gol.gg (multi-season, cached) | `career_data.json` |
| 4 | `aggregate_champion_stats.py` | `data.json` | `champion_stats.json` |
| 5 | `merge.py` | `data.json`, `schedule.json`, `career_data.json` | `data.json` |

**Valorant** (`scrape-valorant`): `scrape_valorant.py` → `valorant_data.json`

**CS2** (`scrape-cs2`): `scrape_cs2_career.py` → `cs2_career_data.json`, then
`scrape_cs2.py` → `cs2_data.json`. The career script runs *first*
deliberately: the two are circularly dependent (career reads the roster out
of `cs2_data.json`, while `scrape_cs2.py` folds the career file into its
output), so one of them has to be a run stale. A stale roster is much less
harmful than stale career data.

Regions currently covered: LCS, LEC, LCK, LPL, LCP, CBLOL, TCL (LoL);
VCT Americas / EMEA / Pacific / China; CS2 (single pool).

### Which files are committed

`data.json`, `career_data.json`, `champion_stats.json`, `valorant_data.json`,
`cs2_data.json` and `cs2_career_data.json` are committed by the workflow and
served to the frontend — they are generated artifacts that must stay in git.

`schedule.json` is an intermediate — `merge.py` folds it into `data.json` —
but it is also the fallback `merge.py` reads when `scrape_schedule.py` fails,
so the workflow commits it too. `merge.py` ignores it once it is more than
`MAX_SCHEDULE_AGE_DAYS` (3) old and publishes no upcoming matches instead:
every fixture in an old schedule has already been played, and listing those
as upcoming is worse than listing none.

All generated files are written minified (`separators=(",", ":")`), which
is purely a serialization choice — `indent=2` was about two thirds of the
bytes. `data.json` is 2.4MB rather than 7.5MB as a result. GitHub already
serves these gzipped, so the saving on the wire is more modest
(~535KB -> ~340KB); the bigger wins are the browser parsing a third as much
text and each run committing a third as many bytes. Don't expect these files
to be readable in a diff — they never were meaningfully, since every run
replaces them wholesale.

Every data file is *wholesale regenerated* each run, never incrementally
edited. That's why each commit step, on a push rejection, resets to the
latest remote and re-applies its own fresh snapshot instead of merging —
a line-level merge of two full-file rewrites is meaningless and has in the
past left real conflict markers committed to the data.

### Failure policy

Each job validates its output with `scripts/check_data.py` *before* the commit
step, so a bad snapshot never reaches `main`. The check fails the run if the
data is stale (the scrape didn't actually write it), structurally empty, or
if player/match counts collapsed versus the previously committed copy — the
last one catches a scraper that "succeeds" but silently parses nothing after
a source site changes its markup.

```bash
python scripts/check_data.py lol        # or: valorant, cs2
python scripts/check_data.py lol --skip-freshness   # checking data you didn't just scrape
```

The primary scrape of each game is a hard failure. Only the auxiliary LoL
steps (schedule, career, champion stats) and the CS2 career step remain
`continue-on-error`, because the model handles those being stale by design —
when one of them fails the run still goes green, but a `::warning::`
annotation is attached to it rather than the failure passing unnoticed.

## Running locally

```bash
pip install requests beautifulsoup4   # LoL + Valorant
pip install cs2api aiohttp            # CS2

python scripts/scrape_lcs.py          # scripts expect the repo root as cwd
python scripts/merge.py
```

`merge.py` and `aggregate_champion_stats.py` need no network — they only read
files already in the repo, which makes them the easiest things to test.

Open `index.html` in a browser to view the app. Note that it loads data from
`main` on GitHub, not from your local files.

## scripts/dev/

One-off probes, diagnostics, parameter sweeps and the weight-tuning harness
(`optimize_weights.py`). Nothing here is on the critical path; several write
`.txt` output captures into the repo root, which `.gitignore` excludes. They
expect to be run from the repo root:

```bash
python scripts/dev/optimize_weights.py
```

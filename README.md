# Esports Kill Projector

A static single-page app (`index.html`) that projects player kill totals for
League of Legends, Counter-Strike 2 and Valorant, backed by JSON data files
that a scheduled GitHub Actions workflow regenerates twice a day.

There is no build step and no server. `index.html` is the whole frontend; it
fetches its data at runtime from `raw.githubusercontent.com` on `main`, and
falls back to a snapshot bundled inside the page if a fetch fails.

## Layout

```
src/app.jsx                    the frontend source — edit this
src/index.template.html        the HTML shell around it
build/build-frontend.js        compiles src/ into index.html
index.html                     GENERATED — do not edit by hand
.github/workflows/scrape.yml   the twice-daily (09:00 / 21:00 UTC) update job
.github/workflows/tests.yml    pytest + the index.html staleness check
scripts/                       production scrapers — run by the workflow
scripts/check_data.py          pre-commit validation of a scraped file
scripts/dev/                   investigation tooling — never run by the workflow
tests/                         unit tests (no network, stdlib only)
playoffs_and_international_roadmap.md  design notes
```

## Prop lines

`scripts/scrape_props.py` fetches live player prop lines and matches them onto
rostered players, writing `props.json`. The app shows each player's line beside
the projection, with the difference as an edge.

Lines move continuously and get pulled when news breaks, so an old line is not
merely stale — it is misleading in the expensive direction, because it still
looks actionable. The frontend refuses to compute an edge against a line older
than `PROPS_MAX_AGE_MINUTES` (90) and shows its age instead.

### Where it can run

**The endpoint refuses this script from anywhere.** It answers HTTP 403, and
the reason is the client rather than the network. That took two measurements
and the first was misread: a GitHub runner gets 403, which looked like a
datacenter-IP block. A Windows machine on a home connection gets the same 403
from the script while its own browser, same machine, minutes apart, pulls the
full 42MB payload. What differs is the client — a non-browser User-Agent, a
non-browser TLS fingerprint, and none of the session cookies a browser picks
up from the site.

So moving the script to a different network does not help, and this README
said otherwise for a while. Getting it to pass would mean dressing a script up
as a browser to defeat a control that exists deliberately, so the code does
not attempt it, and `.github/workflows/props.yml` ships with its schedule
commented out rather than failing every hour.

That leaves one route that works today, and one that would also work
unattended if it were ever configured:

**1. From a browser, pasted in.** The only route that works today, and it
needs nothing
installed — parsing a payload does not import `requests`, so a stock system
python is enough. Open the projections endpoint in a normal browser tab on
your home connection:

```
https://api.prizepicks.com/projections?per_page=250&single_stat=true
```

**Use the filtered URLs.** Unfiltered, that endpoint returns every sport it
posts — 42MB, of which these three games are about 350 rows, roughly one
percent. `league_id` cuts each game to a few hundred KB: quick to save, small
enough that copy-paste stops silently truncating, and far less to ask of
someone else's server. Bookmark these three:

```
LoL       https://api.prizepicks.com/projections?league_id=121&per_page=250&single_stat=true
CS2       https://api.prizepicks.com/projections?league_id=265&per_page=250&single_stat=true
Valorant  https://api.prizepicks.com/projections?league_id=159&per_page=250&single_stat=true
```

The ids are the provider's own and can change; if one goes stale that game
simply stops appearing, and `scripts/dev/inspect_props_payload.py` prints the
current ids out of any saved payload.

Save each (⌘S / Ctrl-S), then hand all three to one run — the freshness stamp
takes the oldest of them, so a set is only as fresh as its stalest file:

```bash
python scripts/scrape_props.py --fixture lol.json cs2.json val.json --out props.json
```

Do not paste that JSON anywhere else — it only ever needs to travel from the
browser to this script:

> **The saved payload does not belong in git.** It is ~2MB, it is an input
> rather than an output, and reloading one URL regenerates it — only the
> `props.json` built from it is committed. Common names for it are in
> `.gitignore`, so a warning about adding a large file means the drop
> worked; the file is on disk and `--fixture` will read it. It can live
> anywhere, including outside the repo.
>
> **Working in a Codespace or a remote VS Code window?** This route still
> works there — parsing a saved payload touches no network, so the 403 above
> does not apply. But the browser saved that file to *your* disk, and the
> Codespace has its own filesystem in the cloud, so `--fixture saved.json`
> will report `FileNotFoundError` until the file is in the Codespace. Drag it
> into the VS Code file explorer, or make a new file there and paste into it.


```bash
# one file, or several — several is the normal case with the URLs above
python scripts/scrape_props.py --fixture lol.json cs2.json val.json --out props.json

# or a single payload straight off the clipboard
Get-Clipboard | python scripts/scrape_props.py --fixture - --out props.json  # Windows
pbpaste | python scripts/scrape_props.py --fixture - --out props.json        # macOS
xclip -o -sel clip | python scripts/scrape_props.py --fixture - --out props.json  # Linux

git add props.json && git commit -m "Update prop lines" && git push
```

### Automatic refresh

**Not possible with the PrizePicks adapter.** The scheduled job exists —
`scripts/refresh_props.ps1` for Windows Task Scheduler and
`scripts/refresh_props.sh` for launchd and cron: they run the fetch on a
timer, commit `props.json` only when the lines have moved, and push. Against
this provider every run ends in the 403 above, on any machine, because the
block follows the client rather than the network. They are kept because
nothing in them is provider-specific: point `PROVIDERS` at a source with a
real server-side API and they work unchanged, as does the hourly workflow.

Until then the refresh is the paste route above, by hand, and a set of lines
is good for 90 minutes.

`scripts/check_windows_setup.ps1` reports whether a Windows machine has what
the scheduled job needs — git, python, `requests`, and a `git push` that will
not prompt. That last one is the failure that hides: it works when you run it
by hand and silently never pushes again under Task Scheduler, which has no
console to type a password into.

**2. A provider with a real server-side API** — a keyed odds service that
permits datacenter traffic. This is the only route that makes the hosted
workflow viable, and it is why the fetch is a single swappable function in
`PROVIDERS`. Restore the cron in `props.yml` once one is configured.

Three things must line up before a line can be compared with a projection, and
`scripts/props_match.py` refuses rather than guesses on any of them:

- **player** — handles differ between the stats source and the sportsbook.
  Names are folded for case, accents and punctuation, but digits are kept:
  `sh1ro` and `shiro` are not assumed to be the same person.
- **stat** — `MAPS 1-2 Kills` is this app's `kills`. Unrecognised stats are
  reported, not mapped to the nearest guess.
- **map window** — the one that silently ruins everything. A line for maps 1-2
  is only comparable with a projection over maps 1-2, so a prop is shown
  against a projection only when the windows match; `Kills (Combo)`, which
  doesn't state a window, is refused outright.

Nothing is dropped quietly — every unmatched prop is counted by reason, because
a rising unmatched count is how a provider renaming its labels shows up, and it
would otherwise look identical to a quiet slate.

```bash
python scripts/scrape_props.py --dry-run          # fetch and report, write nothing
python scripts/scrape_props.py --fixture f.json   # parse a saved payload offline
python scripts/scrape_props.py --fixture -        # ...or piped in from stdin
```

### The refresh, start to finish

Lines are good for 90 minutes. To replace them:

1. Open the three bookmarks above and save each one (Ctrl-S) as `lol.json`,
   `cs2.json`, `val.json`.
2. Get them next to the repo. In a Codespace or a remote VS Code window that
   means dragging them into the file explorer — the browser saved them to
   your own disk, which is not the one the command runs on.
3. From the repo, on `main`:

```bash
git pull
python scripts/scrape_props.py --fixture lol.json cs2.json val.json --out props.json
git add props.json props_history.jsonl && git commit -m "Update prop lines" && git push
```

4. Hard-refresh the app (Ctrl-Shift-R). Lines appear on the **Edges** tab,
   ranked, and beside each player on an expanded match card.

Read the funnel before step 3's commit — it is printed whether or not you
asked for a dry run, and `0 matched` for a game is worth understanding
before pushing rather than after. The payload filenames are in
`.gitignore`, so only the two files below are ever committed.

**Commit `props_history.jsonl` too.** `props.json` holds only what is live,
and each refresh overwrites it. The history is append-only: every board ever
posted, one JSON object per line, with a line that *moved* recorded as a new
observation rather than replacing the old one. It is the only record of what
was offered, and it is the input to the one question that decides whether
these projections are worth anything — not "how close is the projection to
the result", which the app already backtests, but **"when the projection
disagreed with the line, which one was right"**. Every refresh taken before
this file existed is evidence that cannot be recovered.

### Grading the lines

```bash
python scripts/score_props.py                    # report
python scripts/score_props.py --json graded.json # and the rows
```

Reads `props_history.jsonl`, finds the completed match each posted line
belonged to, and resolves what the player actually did over **exactly that
line's map window** — summing LoL's `per_game` for the maps the line names,
and using CS2's and Valorant's series total, which covers precisely maps 1-2
because their scrapers collect two maps and no more. A line over any other
window in those two games is refused rather than graded against the wrong
maps, which would manufacture a losing record out of nothing.

Refusals are counted by reason, and early on almost all of them will be *no
completed match on that date* — the matches simply have not been played yet.
Rates are withheld below 30 graded lines, where a rate is noise wearing a
decimal point.

**What this measures, and what it does not.** It measures the market: how
often a posted line landed over, and by how much. A mean margin near zero is
a market doing its job. It says nothing yet about whether these projections
*beat* that market, which needs the projection as it stood when the line was
posted — the next piece of work. The app's existing accuracy figures measure
the projection against the result, which is a different and much easier
question than measuring it against a price.

### Checking it worked

The provider is unreachable from CI and from any hosted shell, so the pipeline
is verified against a saved payload instead. `tests/fixtures/` holds a trimmed
one shaped exactly like the real response, and running it needs no network:

```bash
python scripts/scrape_props.py --fixture tests/fixtures/prizepicks_projections.json --dry-run
```

A healthy run prints a funnel per game — raw props in, matched out, and a
counted reason for every one that did not match:

```
lol: 5 raw prop(s) -> 2 matched across 2 player(s), 3 unmatched
       1  unrecognised stat
       1  player not on any roster
       1  line is not a number
```

That is the same output to read after pasting in a real payload, and the three
numbers fail in distinguishable ways:

| What you see | What it means |
| --- | --- |
| `0 raw` for every game | The response is not the shape the parser knows — a changed payload, or the wrong page saved. |
| `0 raw` for *one* game | That game's league is posted under a name `GAMES` does not accept. League names are matched exactly, on purpose, so a season-long `LoLSZN` cannot be mistaken for `LoL`. Run `scripts/dev/inspect_props_payload.py` on the payload: it lists every league present and names the configured one that found nothing. |
| `raw > 0`, `0 matched` | Parsing works, matching does not. The reasons underneath name which of the three — player, stat, map window — is off. |
| `player not on any roster`, a handful | Normal. The provider posts players from leagues this app does not track. |
| `player not on any roster`, nearly all | Two very different things, and the funnel cannot tell them apart. Either a roster file scraped badly, or the slate is a league this repo does not track — LoL covers LCS, LEC, LCK, LPL, LCP, CBLOL and TCL, so a board of European regional teams (LFL, Superliga, Prime League) matches nothing and correctly shows no lines. Run the inspector with `--names`: you will either recognise the players immediately or not. |
| `map window not stated` | Lines posted as `Kills (Combo)`. Refused on purpose — see the map window note above. |

**The map window comes off the line itself.** A fixture is a Bo1, Bo3 or
Bo5 and the provider posts `Map 1`, `Maps 1-2` or `Maps 1-3` to match — a
real payload has LoL at Maps 1-3 while CS2 and Valorant are at Maps 1-2, on
the same day. So the projection compared with a line is computed over that
line's own maps rather than over the games-in-series control, and each
readout names the window it used. The control still sets the standalone
projection for players with no line posted.

Two other things are resolved per line, because each one silently produces a
wrong edge rather than a missing one:

- **which match** — a player can hold lines in two fixtures on one day, so
  the line nearest that fixture's start time wins and anything more than six
  hours away is treated as a different match, not this one.
- **which line** — the provider posts alternate lines at other payouts
  beside the market one. A real payload has three kills lines for one LoL
  player in one match: 10.5, 8.5 and 6.5. Against a projection of 9 those
  disagree about the sign of the edge, so `odds_type` picks the market line
  where the provider states it, and where it does not the readout shows the
  line with a count and no edge rather than guessing.

When a game reports nothing, `--names` is the fastest way to find out why:

```bash
python scripts/dev/inspect_props_payload.py lol.json cs2.json val.json --names 12
```

It prints the leagues present, the stat labels bucketed by where each lands
in the funnel, the provider's league ids, and who is actually on the board.

Once `props.json` is written, the committed file is checked by the test suite
like every other served file, so `pytest` catches a hand-made one with the
wrong shape before the page quietly shows no lines:

```bash
python -m pytest tests/test_scrape_props.py tests/test_data_contract.py -q
```

In the app itself, a working line shows up beside the projection with the edge
under it. If the line renders greyed out with `Nm old` instead, the pipeline
worked and the file is simply older than the 90-minute window — fetch again.

**An empty result never overwrites a good `props.json`.** If a run matches
nothing while the existing file holds lines, it refuses and exits non-zero
rather than deleting them, because a payload that parses but matches nothing is
what a renamed stat label looks like, and it is indistinguishable at that
moment from a genuinely quiet evening. Stale lines are visibly stale in the UI;
deleted ones are just gone. Pass `--allow-empty` when the slate really is bare.

The provider is one function returning raw dicts, so swapping source is an
adapter rather than a rewrite. PrizePicks is implemented because it covers LoL,
CS2 and Valorant, which licensed odds APIs largely do not. Its projections
endpoint is undocumented and carries no stability guarantee, which is what
`PROVIDERS` exists to make replaceable.

## The frontend

`index.html` is a single self-contained page: React from a CDN, everything
else inline. There is no server and no framework beyond React.

It is **generated**. Edit `src/app.jsx` (and `src/index.template.html` for
the surrounding shell), then:

```bash
npm install     # once
npm run build   # regenerates index.html
npm run check   # verifies index.html matches src/ — this is what CI runs
```

The JSX used to ship uncompiled, with `@babel/standalone` compiling it in
the browser on every visit. That was a 3MB download plus a parse of ~3,800
lines before anything could render — about 3.4MB and ~900ms to first
content, against ~310KB and ~400ms now. Babel runs at build time instead.

The compiled output is inlined into `index.html` rather than emitted as a
separate `app.js` on purpose: `raw.githubusercontent.com` serves everything
as `text/plain` with `X-Content-Type-Options: nosniff`, so a sibling script
file would be refused by the browser. Keeping the page self-contained means
it works however it is opened. The compiled JS is about the same size as
the JSX it replaces, so inlining costs nothing.

CI fails if `index.html` does not match `src/`. A stale `index.html` would
ship an app that disagrees with its own source, and the diff would be
invisible in review because it would simply be missing.

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

## Working on this repo

Run the tests before pushing — and rebuild the frontend if you touched
`src/`:

```bash
pip install pytest
python -m pytest tests/ -v
npm run build          # only if src/ changed
```

They cover the merge step, the data check and the shape of the committed
data files. Nothing in them touches the network, so they run in under a
second. The scrapers themselves are not unit-tested — they are network-bound
end to end — so CI additionally byte-compiles every script to catch syntax
errors in them.

Commit messages are worth a sentence explaining *why*, since the history is
the only record of decisions here. A fair number of existing commits on
`main` say `123`, `update` or `asdf`, which makes the reasoning behind past
changes unrecoverable — several of the more surprising choices in this repo
(the reset-and-reapply push loop, the CS2 career step ordering) had to be
reconstructed from code comments rather than history. Those commits are left
as they are: `main` is pushed to by the scrape bot twice a day, and rewriting
published history would break every existing clone and checkout for no
functional gain.

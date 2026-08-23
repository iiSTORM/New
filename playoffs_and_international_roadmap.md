# Playoffs & International Events — Readiness Guide

A planning reference, not a build spec. Each section lays out what's already
solid, what's genuinely uncertain, and the concrete options for closing the
gap — so decisions can get made deliberately rather than discovered mid-build.

---

## 1. Playoffs — format differences from regular season

### 1a. Already handled, confirmed working
- **Variable series length (Bo3/Bo5)**: the match-list score regex
  (`^\d+\s*-\s*\d+$`) and the "games 1+2" convention both work regardless
  of how long a series actually runs. No changes needed.
- **The extra "Patch" column on playoff pages**: discovered and fixed
  earlier — playoff match-list pages have a column regular-season pages
  don't, and the content-signature parser (identify cells by what they
  *look like*, not position) already handles this correctly for both
  layouts.
- **Round label normalization**: `normalize_week_label()` already
  title-cases unrecognized labels (`"PLAYOFFS"` → `"Playoffs"`,
  `"FINALS"` → `"Finals"`). Untested against the full range of real
  round names a bracket produces — see 1b.

### 1b. Genuinely untested / uncertain
- **Full round-name coverage**: real brackets produce labels like
  "Quarterfinals", "Upper Bracket Round 1", "Lower Bracket Final",
  "Play-In", "Group Stage" — `normalize_week_label()`'s fallback
  (title-case whatever's there) probably produces reasonable output for
  all of these, but hasn't been verified against a real playoff page's
  actual label set for each region. Cheap to check: fetch one region's
  current playoff match list and look at the raw label strings.
- **Standings during playoffs**: a flat win/loss table (what the new
  Standings tab shows) stops being the right presentation once a region
  enters an elimination bracket — bracket position ("won upper semifinal,
  advances to upper final") doesn't reduce to a W-L record the same way.
  Two honest options:
  - Leave Standings as-is; it naturally becomes less useful once playoffs
    start and that's an acceptable tradeoff (nobody checks standings
    during playoffs anyway, they check the bracket).
  - Add a genuine bracket view (see 1c) that supersedes standings once a
    region's data shows playoff-labeled matches.

### 1c. Bracket visualization (not yet built, real design work)
This is a different UI paradigm from anything in the app so far — a
tree/graph layout, not a card list or table. Two sub-problems:
- **Inferring bracket structure**: gol.gg's data doesn't hand us "this is
  upper bracket round 1, feeds into upper semifinal" as structured
  fields — that'd need to be reconstructed from round labels + chronological
  order + which teams meet when. Doable, but needs a real look at what
  playoff match-list pages actually expose per region (single vs. double
  elimination varies by region and sometimes by split).
- **Rendering**: an actual bracket tree (columns of rounds, connecting
  lines between advancing teams) is a genuinely different component,
  not a reskin of `FutureMatchCard`. Worth scoping as its own design pass
  once the data side is confirmed, not bolted on quickly.

**Recommendation**: before building anything here, do a quick reconnaissance
pass — fetch one real playoff bracket page per region (LCS/LEC/LCK/LPL) and
look at what labels/structure actually exist. That determines whether this
is a one-region problem or needs to handle four different bracket
conventions.

---

## 2. International events (MSI, Worlds, VCT Champions/Masters)

### 2a. The core architectural question
Right now, "region" is the fundamental unit — LCS region has LCS teams,
period. An international event pulls specific *qualifying* teams from
every region into one event. That doesn't fit the current data model
cleanly. Two real approaches:

**Option A — treat it as a new region, scraped fresh.**
gol.gg (and presumably VLR.gg for VCT) almost certainly has "MSI 2026" /
"Worlds 2026" as its own standalone tournament page, the same way "LCS
2026 Spring" and "LCS 2026 Summer" are separate pages today. If so, the
existing `REGIONS` dict pattern (a region key mapping to `{current,
historical}` tournament names) could support an `"International"` region
pointing at that tournament directly — minimal new scraper code, reuses
everything.

The wrinkle: what does "historical" mean for a one-off international
event? Last year's Worlds isn't a meaningful comparison for this year's
form. The more correct comparison is each team's *own home-region* recent
performance (their Summer split) — but that means merging data from two
different tournament scrapes (their home region's regular season +
the international bracket) rather than the simple two-tournament-per-region
model everything else uses. Real design decision, not a quick fix.

**Option B — merge qualifying teams from already-scraped regional data.**
Since LCS/LEC/LCK/LPL are already being scraped continuously, and the
qualifying teams for MSI/Worlds are a small known subset, it might be
possible to construct the international event's roster/history entirely
from data already sitting in `data.json`, without a new scraper target at
all — using each team's actual home-region "cur" stats as the international
event's "hist" baseline. This sidesteps the historical-comparison problem
in 2a-Option-A directly, at the cost of needing new merge logic rather
than a new scrape target.

**Recommendation**: Option B is probably the better fit specifically
*because* it solves the historical-baseline problem for free — worth
prototyping once an actual event is imminent, rather than committing to
one approach in the abstract months ahead of the first real international
event.

### 2b. Team identity across contexts
A team playing at Worlds needs to resolve to the *same* team entity it is
in domestic play — both for display (color/roster continuity) and for the
Elo power-ranking system to correctly carry a team's rating across
domestic and international matches. `merge.py`'s `TEAM_NAME_MAP` already
handles this class of problem (sponsor-name variants across splits) — the
same mechanism should extend cleanly to whatever naming gol.gg uses on
international tournament pages, but that needs verifying against real
data once available, not assumed.

### 2c. Format is a hybrid, not pure bracket or pure standings
International events commonly run Play-In → Group Stage (round robin,
unlike domestic single round robin) → Bracket. That means the presentation
needs to shift *within* a single event as it progresses — group stage
wants a standings-style table (which the new Standings tab already
provides, generically, no extra work), bracket stage wants the not-yet-built
bracket view from 1c. Worth noting these aren't separate problems — the
same bracket-visualization work from playoffs directly serves this too.

### 2d. Same considerations apply to Valorant
VCT has Masters events (Masters Bangkok, Masters Toronto, etc.) and VCT
Champions as the season finale, structurally parallel to LoL's MSI/Worlds.
Whatever gets designed for LoL's international events should be built
generically enough to cover VCT's equivalent without a second design pass
— the `GAMES` config object already isolates per-game specifics, so this
should mostly fall out naturally rather than needing duplicated work.

---

## 3. UI: how an international/playoff region should surface

- An international event only exists for a few weeks a year, unlike the
  four persistent regions. It shouldn't permanently occupy a 5th region
  tab slot that's empty 48 weeks a year.
- The existing "no data available for this region" empty state already
  handles a region with nothing loaded — an international event tab could
  reuse that exact pattern (present in the region list, but showing a
  graceful "no event currently running" message) rather than needing new
  UI machinery.
- Once an event starts producing real data, it'd behave like any other
  region — Future/Past Results/Standings all already work generically
  against whatever `teams`/`past_matches` gets handed to them, per-game,
  per-region. The main net-new work is the bracket view (1c) and the
  data-merging question (2a) — the display layer mostly already exists.

---

## 4. Suggested sequencing

Roughly in order of "clarifies the most uncertainty for the least effort":

1. **Reconnaissance**: fetch one real current playoff bracket page per
   region and one real past international event's tournament page (gol.gg
   and VLR.gg both), just to look at actual structure/labels — no code,
   just confirms or corrects the assumptions above.
2. **Bracket visualization** (1c) — the single piece of net-new UI that
   both playoffs and international events need, so it's the highest-leverage
   thing to build once the data shape from step 1 is confirmed.
3. **International team-merging logic** (2a Option B) — once an actual
   event is close enough to test against real qualifying teams.
4. **Full international region wiring** (2b, 2c, 3) — mostly assembly at
   that point, since Standings/Future/Past Results already work generically.

Nothing here needs to happen before it's needed — steps 2-4 are naturally
paced by the actual esports calendar (next playoffs, next MSI/Worlds),
not by an arbitrary internal deadline.

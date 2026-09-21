/* Which posted line gets compared with which projection.
 *
 * This is the one piece of money-relevant logic that lives in the frontend
 * rather than in Python, and every rule in it exists because getting it
 * wrong produces a confident WRONG edge rather than a missing one:
 *
 *   the window — a fixture is Bo1, Bo3 or Bo5 and the provider posts
 *                Map 1, Maps 1-2 or Maps 1-3 to match. The line states it,
 *                so the projection is computed over the same maps instead
 *                of the line being hidden unless a hand-set control agrees.
 *   the match  — a player can hold lines in two matches on one day.
 *   the line   — alternate payout lines sit beside the market line, and
 *                they disagree by enough to flip the sign of an edge.
 *
 * Run: node tests/prop_selection.test.mjs
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// Evaluate the shipped source rather than a copy of it, so this cannot
// drift from what the app actually runs.
const src = fs.readFileSync(path.join(root, "src/app.jsx"), "utf8");
const start = src.indexOf("const PROP_MATCH_WINDOW_HOURS");
const end = src.indexOf("function propsAgeMinutes");
if (start < 0 || end < 0) {
  console.error("Could not find propFor in src/app.jsx — has it been renamed?");
  process.exit(1);
}
const slice = src.slice(start, end);
const propFor = new Function(slice + "\nreturn propFor;")();
const projectionOverWindow = new Function(slice + "\nreturn projectionOverWindow;")();

let pass = 0, fail = 0;

/* Assertions that reach into a result — `propFor(...).line` — throw when
   the function correctly returns null, and an uncaught throw aborts the
   whole run: every later check silently never happens, and the summary
   line is the one from a previous run still on screen. A regression that
   crashes must read as a failure, not as an absence. */
function lazily(fn) {
  try {
    return fn();
  } catch (err) {
    return `threw: ${err.message}`;
  }
}

function check(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (ok) { pass++; } else {
    fail++;
    console.error(`FAIL  ${label}\n        got  ${JSON.stringify(got)}\n        want ${JSON.stringify(want)}`);
  }
}

const AT = "2026-09-20T15:00:00Z";
function props(list) {
  return { fetched_at: AT, source: "test", props: { lol: { Faker: list } } };
}
function line(over, opts = {}) {
  return { player: "Faker", stat: "kills", maps: over, line: opts.line ?? 4.5,
           start_time: opts.at ?? AT, odds_type: opts.odds ?? null };
}

// The window is read off the line, whatever the fixture is.
for (const maps of [1, 2, 3]) {
  const got = propFor(props([line(maps)]), "lol", "Faker", "kills", AT);
  check(`a maps-1-${maps} line keeps its own window`, got && got.maps, maps);
}

// Two matches on one day: the nearer start time is this fixture's.
const twoMatches = props([
  line(2, { line: 25.5, at: "2026-09-20T08:00:00Z" }),
  line(2, { line: 24.5, at: "2026-09-20T14:00:00Z" }),
]);
check("the early match gets the early line",
      lazily(() => propFor(twoMatches, "lol", "Faker", "kills", "2026-09-20T08:00:00Z").line), 25.5);
check("the late match gets the late line",
      lazily(() => propFor(twoMatches, "lol", "Faker", "kills", "2026-09-20T14:00:00Z").line), 24.5);
check("a fixture with no line of its own gets none, not someone else's",
      propFor(twoMatches, "lol", "Faker", "kills", "2026-09-24T08:00:00Z"), null);
// The window is 2 hours, measured: across a real board 24 of 25 legitimate
// pairings were under an hour apart, and the one thing a 6-hour window added
// was a 10:00 line attaching to a 16:00 fixture six hours away.
// 11:00 sits three hours from both the 08:00 and the 14:00 line. (12:00
// would be two hours from the 14:00 one and should still match — which is
// what this test asserted on its first draft, wrongly.)
check("a fixture three hours from every line of its own gets none",
      propFor(twoMatches, "lol", "Faker", "kills", "2026-09-20T11:00:00Z"), null);
check("a fixture an hour off is still this one",
      propFor(props([line(2, { line: 25.5, at: "2026-09-20T08:00:00Z" })]),
              "lol", "Faker", "kills", "2026-09-20T09:00:00Z").line, 25.5);

// Not every source states a kickoff time. gol.gg and bo3.gg give a full
// timestamp; vlr.gg gives a bare date. Champions fixtures arrived as
// "2026-09-25", which reads as midnight UTC, and every line posted for
// 09:00 sat nine hours out — so a two-hour window rejected all 78 of them.
const dayOnly = props([line(2, { line: 33.0, at: "2026-09-25T05:00:00.000-04:00" })]);
check("a dated fixture matches a line posted that day",
      lazily(() => propFor(dayOnly, "lol", "Faker", "kills", "2026-09-25").line), 33.0);
check("nine hours apart is fine when the fixture states no time",
      propFor(dayOnly, "lol", "Faker", "kills", "2026-09-25") !== null, true);
check("but a different day is still a different match",
      propFor(dayOnly, "lol", "Faker", "kills", "2026-09-26"), null);
check("a line late in the evening lands on its UTC day",
      propFor(props([line(2, { at: "2026-09-25T20:00:00.000+00:00" })]),
              "lol", "Faker", "kills", "2026-09-25") !== null, true);
// A timestamped fixture keeps the tight window: the information is there,
// so the day would be needlessly loose.
check("a fixture that states a time still uses the window",
      propFor(props([line(2, { at: "2026-09-25T20:00:00.000+00:00" })]),
              "lol", "Faker", "kills", "2026-09-25T09:00:00+00:00"), null);

// Alternate payout lines.
const withOdds = props([
  line(2, { line: 10.5, odds: "demon" }),
  line(2, { line: 8.5, odds: "standard" }),
  line(2, { line: 6.5, odds: "goblin" }),
]);
const market = propFor(withOdds, "lol", "Faker", "kills", AT);
check("the market line wins when it is named", market.line, 8.5);
check("and is not reported as ambiguous", market.lineCount, 1);

const noOdds = props([line(2, { line: 10.5 }), line(2, { line: 8.5 }), line(2, { line: 6.5 })]);
check("without odds_type the ambiguity is reported, not resolved",
      lazily(() => propFor(noOdds, "lol", "Faker", "kills", AT).lineCount), 3);
check("identical lines are not ambiguous",
      propFor(props([line(2, { line: 8.5 }), line(2, { line: 8.5 })]),
              "lol", "Faker", "kills", AT).lineCount, 1);

// Nothing to show.
check("unknown player", propFor(props([line(2)]), "lol", "Nobody", "kills", AT), null);
check("unknown stat", propFor(props([line(2)]), "lol", "Faker", "deaths", AT), null);
check("unknown game", propFor(props([line(2)]), "cs2", "Faker", "kills", AT), null);
check("no props at all", propFor(null, "lol", "Faker", "kills", AT), null);
check("no match date still resolves a line",
      lazily(() => propFor(props([line(2)]), "lol", "Faker", "kills", null).maps), 2);

// The projection put beside a line has to cover the line's maps, not the
// selector's. This is the step that decides whether an edge is real: the
// same 4.6 per-map rate is 9.2 against a Maps 1-2 line and 13.8 against a
// Maps 1-3 one, and using the wrong one moves the edge by a whole map.
const breakdown = { perGame: 4.6, total: 9.2 };
check("a maps-1-2 line is compared over 2 maps",
      projectionOverWindow(breakdown, { maps: 2 }), 9.2);
check("a maps-1-3 line is compared over 3 maps",
      projectionOverWindow(breakdown, { maps: 3 }).toFixed(2), "13.80");
check("a map-1 line is compared over 1 map",
      projectionOverWindow(breakdown, { maps: 1 }), 4.6);
check("no prop, no projection", projectionOverWindow(breakdown, null), null);
check("no breakdown, no projection", projectionOverWindow(null, { maps: 2 }), null);
check("a nonsense window is refused rather than multiplied",
      projectionOverWindow(breakdown, { maps: 0 }), null);
check("a string window is refused", projectionOverWindow(breakdown, { maps: "2" }), null);

// The trap that took an evening to find: the card's `date` field is a
// display string by the time it reaches propFor, and the ISO value lives on
// _sortKey. "Sep 21" is not a parse failure that announces itself -- V8
// reads it as Sep 21 2001 -- so every line falls outside the window and the
// app shows nothing at all, silently.
const formatUpcoming = new Function(
  src.slice(src.indexOf("function formatUpcoming"),
            src.indexOf("/* ---------- Game switcher")) + "\nreturn formatUpcoming;")();
const iso = "2026-09-21T10:00:00.000+00:00";
const shaped = formatUpcoming([{ teamA: "A", teamB: "B", date: iso }])[0];
check("formatUpcoming turns date into a display string", shaped.date.includes("T"), false);
check("and keeps the real timestamp on _sortKey", shaped._sortKey, iso);

const atTen = props([line(2, { line: 30.5, at: iso })]);
check("the display string matches nothing — this is the bug",
      propFor(atTen, "lol", "Faker", "kills", shaped.date), null);
check("_sortKey is what the card must pass",
      lazily(() => propFor(atTen, "lol", "Faker", "kills", shaped._sortKey).line), 30.5);

// The Edges view: every posted line for a game, ranked. The component is
// JSX and cannot be rendered here, but everything that decides WHAT it shows
// and IN WHAT ORDER is pure, so that part is tested against the real data
// files with a stubbed projection.
function extract(name) {
  // Brace-matched rather than "up to the next function", because top-level
  // consts sit between some of these and would come along for the ride.
  const start = src.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`no function ${name} in src/app.jsx`);
  let depth = 0, seen = false;
  for (let i = src.indexOf("{", start); i < src.length; i++) {
    if (src[i] === "{") { depth++; seen = true; }
    else if (src[i] === "}") { depth--; if (seen && depth === 0) return src.slice(start, i + 1); }
  }
  throw new Error(`unbalanced braces reading ${name}`);
}
const PER_MAP = 12;   // a flat per-map rate, so edges are arithmetic we control
const collectEdges = new Function(`
  ${extract("likelyStarters")}
  function project() { return { perGame: ${PER_MAP} }; }
  ${slice}
  return collectEdges;
`)();
const rankEdges = new Function(slice + "\nreturn rankEdges;")();

check("ranked by edge SIZE, so a big under outranks a small over",
      rankEdges([{ edge: 0.4 }, { edge: -7.1 }, { edge: 2.2 }]).map((r) => r.edge),
      [-7.1, 2.2, 0.4]);
check("lines with no usable edge sort to the bottom",
      rankEdges([{ edge: null }, { edge: 0.1 }, { edge: null }, { edge: -5 }])
        .map((r) => r.edge), [-5, 0.1, null, null]);
check("an empty board is not an error", rankEdges([]).length, 0);

// A fixture whose OPPONENT this app has no roster for. CS2 tracks ~50
// teams against ~100 in its fixture list, so most boards contain these,
// and every line posted on one used to be dropped entirely.
const halfKnown = {
  CS2: {
    past_matches: [],
    teams: { Sashi: { players: [{ name: "acoR", role: null, cur: { k: 20, g: 10 } }] } },
    upcoming_matches: [{ teamA: "Sashi", teamB: "SomeTeamWeNeverScraped",
                         date: "2026-09-21T10:00:00+00:00" }],
  },
};
const halfProps = { fetched_at: "2026-09-21T10:00:00+00:00", source: "t", props: { cs2: {
  acoR: [{ player: "acoR", stat: "kills", maps: 2, line: 25.5, odds_type: "standard",
           team: "Sashi", start_time: "2026-09-21T10:00:00+00:00" }] } } };
const halfRows = collectEdges(halfKnown, ["CS2"], halfProps, {}, "kills", "cs2");
check("a line shows even when the opponent is unrostered", halfRows.length, 1);
check("and is flagged as having no opponent adjustment",
      halfRows[0] && halfRows[0].oppKnown, false);

const bothUnknown = { CS2: { past_matches: [], teams: {},
  upcoming_matches: [{ teamA: "A", teamB: "B", date: "2026-09-21T10:00:00+00:00" }] } };
check("a fixture with neither side rostered still yields nothing",
      collectEdges(bothUnknown, ["CS2"], halfProps, {}, "kills", "cs2").length, 0);

const cs2 = JSON.parse(fs.readFileSync(path.join(root, "cs2_data.json"), "utf8"));
const realProps = fs.existsSync(realPathEarly()) ? JSON.parse(fs.readFileSync(realPathEarly(), "utf8")) : null;
function realPathEarly() { return path.join(root, "props.json"); }
if (realProps) {
  const edges = collectEdges(cs2.regions, Object.keys(cs2.regions), realProps, {}, "kills", "cs2");
  check("finds the real board's lines", edges.length > 0, true);
  const sizes = edges.filter((e) => e.edge !== null).map((e) => Math.abs(e.edge));
  check("and returns them largest-edge first",
        sizes.every((v, i) => i === 0 || sizes[i - 1] >= v), true);
  check("every row is projected over its own line's window",
        edges.every((e) => Math.abs(e.projection - PER_MAP * e.prop.maps) < 1e-9), true);
  check("every row carries the fixture it belongs to",
        edges.every((e) => e.team && e.opponent && e.team !== e.opponent), true);
  check("no row is a combo", edges.every((e) => e.prop.maps > 0), true);
  console.log(`(Edges view would list ${edges.length} lines from the committed props.json)`);
}

// Rendering a fixture whose opponent was never scraped. This crashed the
// live app: the card relaxed its guard to project the side it knows, but
// the header still reached into `.color` on the side it does not, and a
// throw inside render blanks the whole page rather than dropping a colour.
const teamColorOf = new Function(slice + "\nreturn teamColorOf;")();
check("a rostered team keeps its colour",
      teamColorOf({ T1: { color: "#e0c341" } }, "T1", "#fallback"), "#e0c341");
check("an unrostered team falls back instead of throwing",
      teamColorOf({ T1: { color: "#e0c341" } }, "Never Scraped", "#fallback"), "#fallback");
check("a team present but without a colour falls back",
      teamColorOf({ T1: {} }, "T1", "#fallback"), "#fallback");
check("no teams object at all", teamColorOf(undefined, "T1", "#fallback"), "#fallback");

// The record: when the projection disagreed with the line, which was right.
// projectPointInTime is stubbed to a controlled per-map rate so the
// win/loss arithmetic is checkable; what is under test is which rows count
// as a bet at all, and how they bucket.
const RATE = 5;   // per map, so a 2-map projection is 10.0
const modelRecord = new Function(`
  function projectPointInTime() { return { perGame: ${RATE} }; }
  ${slice}
  return modelRecord;
`)();
const recordByEdge = new Function(slice + "\nreturn recordByEdge;")();

const regions = { R: { past_matches: [], teams: {
  T1: { players: [{ name: "Faker", role: "MID", cur: { k: 4, g: 10 } }] } } } };
function graded(rows) { return { graded: rows.map((r) => ({
  game: "lol", player: "Faker", team: "T1", opponent: "GEN", stat: "kills",
  maps: 2, match_date: "2026-09-21", ...r })) }; }

check("a projection above the line that went over is a win",
      modelRecord(regions, ["R"], graded([{ line: 8.5, result: "over" }]), {}, "kills")[0].won, true);
check("a projection above the line that went under is a loss",
      modelRecord(regions, ["R"], graded([{ line: 8.5, result: "under" }]), {}, "kills")[0].won, false);
check("a projection below the line that went under is a win",
      modelRecord(regions, ["R"], graded([{ line: 11.5, result: "under" }]), {}, "kills")[0].won, true);
check("a push is not a bet",
      modelRecord(regions, ["R"], graded([{ line: 8.5, result: "push" }]), {}, "kills").length, 0);
check("a projection exactly on the line is not a disagreement",
      modelRecord(regions, ["R"], graded([{ line: 10.0, result: "over" }]), {}, "kills").length, 0);
check("another stat is not counted",
      modelRecord(regions, ["R"], graded([{ line: 8.5, result: "over", stat: "deaths" }]), {}, "kills").length, 0);
check("a player who has since left the roster is skipped, not crashed on",
      modelRecord(regions, ["R"], graded([{ line: 8.5, result: "over", player: "Gone" }]), {}, "kills").length, 0);
check("an unknown team is skipped",
      modelRecord(regions, ["R"], graded([{ line: 8.5, result: "over", team: "Nobody" }]), {}, "kills").length, 0);
check("no results at all is not an error",
      modelRecord(regions, ["R"], null, {}, "kills").length, 0);

const bucketed = recordByEdge([
  { edge: 0.5, won: true }, { edge: -0.75, won: false },
  { edge: 1.5, won: true }, { edge: 2.5, won: true }, { edge: 9.0, won: false },
]);
check("buckets on the SIZE of the disagreement, not its direction",
      bucketed.map((b) => b.n), [2, 1, 1, 1]);
check("and counts wins within each", bucketed.map((b) => b.won), [1, 1, 1, 0]);
check("an empty bucket reports no rate rather than zero",
      recordByEdge([]).every((b) => b.rate === null), true);

// The committed props.json, when there is one: the same rules against a
// real payload rather than a constructed one.
const realPath = path.join(root, "props.json");
if (fs.existsSync(realPath)) {
  const real = JSON.parse(fs.readFileSync(realPath, "utf8"));
  let checked = 0;
  for (const [game, players] of Object.entries(real.props || {})) {
    for (const [name, list] of Object.entries(players)) {
      for (const p of list) {
        const got = propFor(real, game, name, p.stat, p.start_time);
        if (!got) { check(`${game}/${name} resolves against its own start time`, got, "a line"); continue; }
        check(`${game}/${name} keeps the posted window`, got.maps, p.maps);
        checked++;
      }
    }
  }
  console.log(`(also checked ${checked} real props from props.json)`);
}

console.log(`${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

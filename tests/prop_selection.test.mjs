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
      propFor(twoMatches, "lol", "Faker", "kills", "2026-09-20T08:00:00Z").line, 25.5);
check("the late match gets the late line",
      propFor(twoMatches, "lol", "Faker", "kills", "2026-09-20T14:00:00Z").line, 24.5);
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
      propFor(noOdds, "lol", "Faker", "kills", AT).lineCount, 3);
check("identical lines are not ambiguous",
      propFor(props([line(2, { line: 8.5 }), line(2, { line: 8.5 })]),
              "lol", "Faker", "kills", AT).lineCount, 1);

// Nothing to show.
check("unknown player", propFor(props([line(2)]), "lol", "Nobody", "kills", AT), null);
check("unknown stat", propFor(props([line(2)]), "lol", "Faker", "deaths", AT), null);
check("unknown game", propFor(props([line(2)]), "cs2", "Faker", "kills", AT), null);
check("no props at all", propFor(null, "lol", "Faker", "kills", AT), null);
check("no match date still resolves a line",
      propFor(props([line(2)]), "lol", "Faker", "kills", null).maps, 2);

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
      propFor(atTen, "lol", "Faker", "kills", shaped._sortKey).line, 30.5);

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

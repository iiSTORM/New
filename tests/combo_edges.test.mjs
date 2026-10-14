/* Combo lines: two or more players added into one line.
 *
 * Pins the arithmetic that turns per-player projections into a combo's
 * projection and spread, and the rule that finds each player's fixture.
 * The spread is the part worth guarding: teammates' errors partly ADD, so
 * treating them as independent would make a same-roster combo look like a
 * surer thing than it is.
 *
 * Run: node tests/combo_edges.test.mjs
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = fs.readFileSync(path.join(root, "src/app.jsx"), "utf8");
const start = src.indexOf("const PROP_MATCH_WINDOW_HOURS");
const end = src.indexOf("function propsAgeMinutes");
if (start < 0 || end < 0 || !src.includes("function comboEdge")) {
  console.error("Could not find the combo functions in src/app.jsx — renamed?");
  process.exit(1);
}
const lib = new Function(src.slice(start, end)
  + "\nreturn { comboEdge, fixtureForLine, residualScale, SAME_TEAM_CORRELATION, OPPOSING_TEAMS_CORRELATION };")();

let pass = 0, fail = 0;
function check(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (ok) pass++; else { fail++; console.error(`FAIL  ${label}\n  got:  ${JSON.stringify(got)}\n  want: ${JSON.stringify(want)}`); }
}
const near = (a, b) => Math.abs(a - b) < 1e-9;

const s = lib.residualScale("lol", "kills", 3);

// Two teammates in one match: errors move together at the same-team rho.
{
  const r = lib.comboEdge([
    { projection: 9.4, team: "KT", fixture: "m1" },
    { projection: 11.8, team: "KT", fixture: "m1" },
  ], 24.5, "lol", "kills", 3);
  check("projection is the sum", near(r.projection, 21.2), true);
  check("edge is projection minus line", near(r.edge, 21.2 - 24.5), true);
  const want = Math.sqrt(2 * s * s * (1 + lib.SAME_TEAM_CORRELATION));
  check("same-roster spread uses the same-team correlation", near(r.spread, want), true);
  check("z is edge over spread", near(r.z, r.edge / r.spread), true);
}

// Opposing sides of one match correlate less; different matches not at all.
{
  const legs = (teamB, fixtureB) => [
    { projection: 9, team: "A", fixture: "m1" },
    { projection: 9, team: teamB, fixture: fixtureB },
  ];
  const same = lib.comboEdge(legs("A", "m1"), 18, "lol", "kills", 3).spread;
  const opposing = lib.comboEdge(legs("B", "m1"), 18, "lol", "kills", 3).spread;
  const apart = lib.comboEdge(legs("B", "m2"), 18, "lol", "kills", 3).spread;
  check("opposing sides use the opposing-teams correlation",
        near(opposing, Math.sqrt(2 * s * s * (1 + lib.OPPOSING_TEAMS_CORRELATION))), true);
  check("different matches are independent", near(apart, Math.sqrt(2) * s), true);
  check("teammates are the widest spread", same > opposing && opposing > apart, true);
}

// Three players: every pair contributes.
{
  const r = lib.comboEdge([1, 2, 3].map((p) => ({ projection: p, team: "A", fixture: "m" })),
                          5, "lol", "kills", 3);
  check("three teammates: 3 variances + 3 covariances",
        near(r.spread, Math.sqrt(3 * s * s + 6 * lib.SAME_TEAM_CORRELATION * s * s)), true);
}

// Refusals.
check("a leg without a projection is no combo",
      lib.comboEdge([{ projection: 9, team: "A", fixture: "m" }, { projection: null, team: "A", fixture: "m" }],
                    18, "lol", "kills", 3), null);
check("no legs is no combo", lib.comboEdge([], 18, "lol", "kills", 3), null);
{
  const r = lib.comboEdge([{ projection: 9, team: "A", fixture: "m" }, { projection: 9, team: "A", fixture: "m" }],
                          18, "lol", "points", 3);
  check("an unmeasured stat still projects but has no spread", [r.projection, r.spread, r.z], [18, null, null]);
}

// fixtureForLine: nearest start within the window, by the same rule as propsFor.
{
  const rd = { upcoming_matches: [
    { teamA: "KT", teamB: "BFX", date: "2026-10-14T09:00:00Z" },
    { teamA: "KT", teamB: "T1", date: "2026-10-14T15:00:00Z" },
    { teamA: "GEN", teamB: "HLE", date: "2026-10-14T09:00:00Z" },
  ] };
  check("nearest fixture for the team",
        lib.fixtureForLine(rd, "KT", "2026-10-14T05:00:00-04:00").teamB, "BFX");
  check("the later match when the line is for it",
        lib.fixtureForLine(rd, "KT", "2026-10-14T15:30:00Z").teamB, "T1");
  check("nothing inside the window is no fixture",
        lib.fixtureForLine(rd, "KT", "2026-10-14T12:00:00Z"), null);
  check("another team's match is never used",
        lib.fixtureForLine(rd, "T1", "2026-10-14T09:00:00Z"), null);
  const dated = { upcoming_matches: [{ teamA: "FUR", teamB: "MVK", date: "2026-10-15" }] };
  check("a date-only fixture matches on the day",
        lib.fixtureForLine(dated, "FUR", "2026-10-15T14:00:00-04:00").teamB, "MVK");
}

console.log(`${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

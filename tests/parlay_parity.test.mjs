/* The parlay combining rule, in two languages, on the same cases.
 *
 * This is the one piece of arithmetic in the app that turns into a number
 * someone stakes money on, and it is not a product of probabilities: the
 * graded record says two legs in one match land the same way 55.2% of the
 * time against 50.0% for legs in different matches, so within a match they
 * share a factor. A five-leg same-match parlay comes out at 0.112 where
 * independence says 0.078 -- a 44% relative difference, in the direction that
 * matters, because a parlay calculator that multiplies is understating how
 * often the whole map goes one way and carries every leg with it.
 *
 * So the rule gets a second implementation whose only job is to disagree if
 * the first one is wrong. scripts/dev/parlay_math.py deliberately uses the
 * SAME rational approximations rather than math.erf, because otherwise every
 * comparison measures the approximation's error instead of the two ports
 * agreeing -- the approximations are checked against an exact normal
 * separately, in tests/test_parlay_math.py.
 *
 * Run: node tests/parlay_parity.test.mjs
 */
import fs from "fs";
import os from "os";
import path from "path";
import { spawnSync } from "child_process";
import { fileURLToPath } from "url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = fs.readFileSync(path.join(root, "src/app.jsx"), "utf8");
const start = src.indexOf("/* ---------- Combining legs into a parlay");
const end = src.indexOf("/* ---------- Fixtures the posted board implies");
if (start < 0 || end < 0 || end <= start) {
  console.error("Could not find the parlay block in src/app.jsx — has it moved?");
  process.exit(1);
}
const slice = src.slice(start, end);
const app = new Function(slice + `
  return { jointHitProbability, groupHitProbability, fixtureHitProbability,
           standardNormalCdf, standardNormalQuantile, breakEvenPerLeg,
           parlayExpectedValue, SAME_MATCH_CORRELATION, SAME_TEAM_CORRELATION,
           OPPOSING_TEAMS_CORRELATION, SAME_MATCH_CORRELATION_LOW,
           SAME_MATCH_CORRELATION_HIGH, PAYOUT_MULTIPLIERS };
`)();

let pass = 0, fail = 0;
const check = (label, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (ok) pass++; else { fail++; console.error(`FAIL  ${label}\n        got  ${JSON.stringify(got)}\n        want ${JSON.stringify(want)}`); }
};
const near = (label, got, want, tol = 1e-12) => {
  const ok = typeof got === "number" && Math.abs(got - want) <= tol;
  if (ok) pass++; else { fail++; console.error(`FAIL  ${label}\n        got  ${got}\n        want ${want} (+-${tol})`); }
};

/* ---------- properties that must hold whatever the port ---------- */

near("independent legs are exactly the product",
     app.groupHitProbability([0.6, 0.5, 0.4], 0), 0.6 * 0.5 * 0.4);
near("one leg is its own probability, correlation or not",
     app.groupHitProbability([0.63], app.SAME_MATCH_CORRELATION), 0.63, 1e-9);
check("a leg with no probability makes the group unanswerable",
      app.groupHitProbability([0.6, null], 0.1), null);
check("an impossible or certain leg is refused rather than swallowed",
      [app.groupHitProbability([0.6, 0], 0.1), app.groupHitProbability([0.6, 1], 0.1)],
      [null, null]);
check("no legs is not a parlay", app.jointHitProbability([]), null);

/* Correlated legs in one match must come out MORE likely to all land than
   independence, which is the entire reason this is not a product. */
{
  const same = app.jointHitProbability(
    [0.6, 0.6, 0.6].map((p) => ({ p, matchKey: "m1" })));
  const spread = app.jointHitProbability(
    [0.6, 0.6, 0.6].map((p, i) => ({ p, matchKey: `m${i}` })));
  check("three legs on one match beat three on three matches",
        same > spread, true);
  near("and the spread version is exactly the product", spread, 0.216, 1e-12);
}
/* A leg with no match key must not be silently pooled with another. */
{
  const keyed = app.jointHitProbability([{ p: 0.6, matchKey: "m" }, { p: 0.6, matchKey: "m" }]);
  const unkeyed = app.jointHitProbability([{ p: 0.6 }, { p: 0.6 }]);
  near("two unkeyed legs are treated as independent", unkeyed, 0.36, 1e-12);
  check("which is not what two legs sharing a match give", keyed !== unkeyed, true);
}

/* ---------- the two levels ---------- */

check("the team level is above the fixture level, or the model is imaginary",
      app.SAME_TEAM_CORRELATION > app.OPPOSING_TEAMS_CORRELATION, true);
check("and the interval brackets the point estimate",
      app.SAME_MATCH_CORRELATION_LOW < app.SAME_MATCH_CORRELATION
      && app.SAME_MATCH_CORRELATION < app.SAME_MATCH_CORRELATION_HIGH, true);
near("both levels off is exactly the product",
     app.fixtureHitProbability([[0.6], [0.5]], 0, 0), 0.30, 1e-15);
{
  // The ordering the whole feature turns on: one roster is the most
  // concentrated, one fixture across both sides next, separate fixtures least.
  const p = 0.497;
  const roster = app.jointHitProbability(
    Array.from({ length: 5 }, () => ({ p, matchKey: "m", teamKey: "A" })));
  const fixture = app.jointHitProbability(
    Array.from({ length: 5 }, (_, i) => ({ p, matchKey: "m", teamKey: i < 3 ? "A" : "B" })));
  const spread = app.jointHitProbability(
    Array.from({ length: 5 }, (_, i) => ({ p, matchKey: `m${i}`, teamKey: `t${i}` })));
  check("one roster beats one fixture beats five fixtures",
        roster > fixture && fixture > spread, true);
  near("and five separate fixtures are the plain product", spread, Math.pow(p, 5), 1e-9);
}
check("a leg with no team key is its own side rather than pooled into one",
      app.jointHitProbability([{ p: 0.6, matchKey: "m" }, { p: 0.6, matchKey: "m" }])
      < app.jointHitProbability([{ p: 0.6, matchKey: "m", teamKey: "A" },
                                 { p: 0.6, matchKey: "m", teamKey: "A" }]), true);
check("an imaginary team loading falls back rather than returning NaN",
      [0.2, 0.05].map((rf) => {
        const got = app.fixtureHitProbability([[0.6], [0.6]], 0.05, rf);
        return typeof got === "number" && got > 0 && got < 1;
      }), [true, true]);

/* ---------- break-even, which is exact arithmetic ---------- */
near("a 2-leg at 3x needs 57.7% a leg", app.breakEvenPerLeg(3, 2), Math.sqrt(1 / 3), 1e-15);
check("a multiplier of 1 or less pays nothing to break even against",
      [app.breakEvenPerLeg(1, 2), app.breakEvenPerLeg(0, 2), app.breakEvenPerLeg(3, 0)],
      [null, null, null]);
near("EV is the payout times the chance, less the stake",
     app.parlayExpectedValue(0.4, 3), 0.2, 1e-15);
check("every payout in the table has a break-even",
      Object.keys(app.PAYOUT_MULTIPLIERS).every(
        (n) => app.breakEvenPerLeg(app.PAYOUT_MULTIPLIERS[n], Number(n)) > 0), true);

/* ---------- the same cases, through Python ---------- */

const cases = [];
const probs = [0.35, 0.45, 0.5, 0.52, 0.6, 0.68, 0.75, 0.9];
// single groups of every size, at the shipped correlation and at zero
for (const rho of [0, 0.05, app.SAME_MATCH_CORRELATION, 0.3, 0.6]) {
  for (let size = 1; size <= 6; size++) {
    for (let offset = 0; offset < probs.length; offset++) {
      const legs = [];
      for (let i = 0; i < size; i++) legs.push({ p: probs[(offset + i) % probs.length], matchKey: "m" });
      cases.push({ legs, rho });
    }
  }
}
// the nested path: one fixture, one or two sides, every size
for (const [rt, rf] of [[app.SAME_TEAM_CORRELATION, app.OPPOSING_TEAMS_CORRELATION],
                        [0.3, 0.1], [0.05, 0.05], [0.05, 0.2], [0.6, 0.02]]) {
  for (let size = 1; size <= 6; size++) {
    for (let split = 0; split <= size; split++) {
      const legs = [];
      for (let i = 0; i < size; i++) {
        legs.push({ p: probs[(i + split) % probs.length], matchKey: "m",
                    teamKey: i < split ? "A" : "B" });
      }
      cases.push({ legs, rhoTeam: rt, rhoFixture: rf });
    }
  }
}
// several fixtures, each with two sides
cases.push({ legs: [{ p: 0.6, matchKey: "a", teamKey: "a1" }, { p: 0.55, matchKey: "a", teamKey: "a2" },
                    { p: 0.7, matchKey: "b", teamKey: "b1" }, { p: 0.5, matchKey: "b", teamKey: "b1" }],
             rhoTeam: app.SAME_TEAM_CORRELATION, rhoFixture: app.OPPOSING_TEAMS_CORRELATION });
// a same-fixture group where some legs carry no side at all
cases.push({ legs: [{ p: 0.6, matchKey: "a", teamKey: "a1" }, { p: 0.6, matchKey: "a" },
                    { p: 0.6, matchKey: "a" }],
             rhoTeam: app.SAME_TEAM_CORRELATION, rhoFixture: app.OPPOSING_TEAMS_CORRELATION });
// mixed shapes: several matches, uneven group sizes, unkeyed legs
cases.push({ legs: [{ p: 0.6, matchKey: "a" }, { p: 0.55, matchKey: "a" },
                    { p: 0.7, matchKey: "b" }], rho: app.SAME_MATCH_CORRELATION });
cases.push({ legs: [{ p: 0.52, matchKey: "a" }, { p: 0.52, matchKey: "b" },
                    { p: 0.52, matchKey: "c" }, { p: 0.52, matchKey: "a" },
                    { p: 0.9 }], rho: app.SAME_MATCH_CORRELATION });
cases.push({ legs: [{ p: 0.9, matchKey: "z" }, { p: 0.9, matchKey: "z" },
                    { p: 0.9, matchKey: "z" }, { p: 0.9, matchKey: "z" },
                    { p: 0.9, matchKey: "z" }, { p: 0.9, matchKey: "z" }], rho: 0.104 });
cases.push({ legs: [{ p: 0.35, matchKey: "z" }, { p: 0.35, matchKey: "z" }], rho: 0.9 });

const handoff = cases.map((c) => {
  // Older cases carry a single rho, which means "both levels at this value" --
  // the flat one-factor shape the engine still has to reproduce.
  const rhoTeam = c.rhoTeam !== undefined ? c.rhoTeam : c.rho;
  const rhoFixture = c.rhoFixture !== undefined ? c.rhoFixture : c.rho;
  return { ...c, rhoTeam, rhoFixture,
           js: app.jointHitProbability(c.legs, rhoTeam, rhoFixture) };
});
if (handoff.some((c) => typeof c.js !== "number")) {
  console.error("FAIL  a case produced no JS answer at all");
  fail++;
}

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "parlay-parity-"));
const out = path.join(dir, "js.json");
fs.writeFileSync(out, JSON.stringify(handoff));
const py = spawnSync("python3", ["-c", `
import json, sys
sys.path.insert(0, ${JSON.stringify(path.join(root, "scripts/dev"))})
import parlay_math as pm
cases = json.load(open(${JSON.stringify(out)}))
worst, worst_case, checked = 0.0, None, 0
for c in cases:
    mine = pm.joint_hit_probability(c["legs"], c["rhoTeam"], c["rhoFixture"])
    if mine is None or c["js"] is None:
        print("MISMATCH: one side refused", c); raise SystemExit(1)
    checked += 1
    d = abs(mine - c["js"])
    if d > worst:
        worst, worst_case = d, c
print(f"{checked} case(s) compared, max |JS - Python| = {worst:.3e}")
if worst > 1e-12:
    print("the two ports disagree on:", json.dumps(worst_case))
    raise SystemExit(1)
print("The JS parlay engine and its Python port agree on every case.")
`], { encoding: "utf8", cwd: root });
process.stdout.write(py.stdout || "");
if (py.status === null) {
  console.error(`SKIP  could not run python3 (${py.error && py.error.message}) — parity NOT checked`);
  process.exit(fail ? 1 : 0);
}
if (py.stderr) process.stderr.write(py.stderr);
console.log(`${pass} passed, ${fail} failed`);
process.exit((py.status === 0 && !fail) ? 0 : 1);

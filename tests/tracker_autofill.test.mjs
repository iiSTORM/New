/* The tracker page's "fill results from the graded lines" button and the
 * CLI's `propedge autograde` must reach the same answer for the same leg:
 * either can grade a slip in the shared store, and a leg graded differently
 * depending on where it was opened is a wrong bankroll.
 *
 * Legs are built from the committed props_results.json -- real players and
 * lines -- plus the cases the rules exist for: the right line on the wrong
 * day, a line nobody graded, a non-esports leg, and a line graded only as a
 * MAP 2 posting. Both sides grade them; every leg's answer is compared.
 *
 * Run: node tests/tracker_autofill.test.mjs
 */
import fs from "fs";
import os from "os";
import path from "path";
import React from "react";
import { spawnSync } from "child_process";
import { fileURLToPath } from "url";
import { createRequire } from "module";

const require = createRequire(import.meta.url);
const { transformSync } = require("@babel/core");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const raw = fs.readFileSync(path.join(root, "src/app.jsx"), "utf8");
const body = raw.slice(raw.indexOf("try {") + "try {".length, raw.indexOf("const root = ReactDOM.createRoot"));
const { code } = transformSync(`${body}\nreturn { peSuggestedActuals };`,
  { presets: [["@babel/preset-react", { runtime: "classic" }]], filename: "app.jsx", compact: true,
    parserOpts: { allowReturnOutsideFunction: true } });
const w = { innerWidth: 1400, addEventListener() {}, removeEventListener() {},
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} } };
const app = new Function("React", "ReactDOM", "window", "document", "fetch", "localStorage", "console", code)(
  React, { createRoot: () => ({ render() {} }) }, w, { getElementById: () => null },
  () => new Promise(() => {}), w.localStorage, console);

const graded = JSON.parse(fs.readFileSync(path.join(root, "props_results.json"), "utf8")).graded;
const leg = (r, extra = {}) => ({ sport: r.game, player: r.player, stat: r.stat, line: r.line, side: "over",
  team: r.team || "T", maps: r.maps, result: "pending", actual: null, ...extra });
const placed = (day) => `${day}T12:00:00-04:00`;
const dayBefore = (day) => new Date(Date.parse(`${day}T12:00:00Z`) - 864e5).toISOString().slice(0, 10);

const slips = [];
graded.forEach((r, i) => {
  if (i % 9) return;
  const day = String(r.match_date).slice(0, 10);
  slips.push({ placed_at: placed(day), legs: [leg(r)] });                                  // the line itself
  slips.push({ placed_at: placed(dayBefore(day)), legs: [leg(r)] });                       // a day off
  slips.push({ placed_at: placed(day), legs: [leg(r, { line: r.line + 100 })] });          // never graded
});
slips.push({ placed_at: placed("2026-10-01"), legs: [leg({ game: "nfl", player: "Josh Allen", stat: "passing yards", line: 250, maps: null })] });
const map2 = graded.find((r) => r.map === 2);
if (map2) slips.push({ placed_at: placed(String(map2.match_date).slice(0, 10)), legs: [leg(map2, { line: map2.line + 0.25 })] });

const js = slips.map((s) => { const got = app.peSuggestedActuals(s, graded)[0]; return got ? got.actual : null; });

// The CLI, on the same slips, through its own autograde.
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "autofill-"));
fs.writeFileSync(path.join(tmp, "slips.json"), JSON.stringify(slips));
const PY = String.raw`
import json, sys
from propedge.slips import Slip, Leg
from propedge.store import Tracker
from propedge.autograde import autograde
raw = json.load(open(sys.argv[1]))
t = Tracker(sys.argv[2])
for s in raw:
    legs = [Leg(**{k: v for k, v in l.items()}) for l in s["legs"]]
    t.slips.append(Slip(mode="power", stake_cents=100, legs=legs, placed_at=s["placed_at"]))
autograde(t, sys.argv[3])
print(json.dumps([s.legs[0].actual for s in t.slips]))
`;
const py = spawnSync("python3", ["-c", PY, path.join(tmp, "slips.json"), path.join(tmp, "store.json"),
  path.join(root, "props_results.json")], { encoding: "utf8", maxBuffer: 64 << 20,
  env: { ...process.env, PYTHONPATH: path.join(root, "scripts") } });
if (py.status !== 0) { console.error(py.stderr); console.error("FAIL  the CLI side did not run"); process.exit(1); }
const cli = JSON.parse(py.stdout);

let fail = 0, filled = 0;
slips.forEach((s, i) => {
  const a = js[i], b = cli[i];
  if (a !== null) filled++;
  if (!((a === null && b === null) || (a !== null && b !== null && Number(a) === Number(b)))) {
    fail++;
    if (fail <= 10) console.error(`FAIL  ${JSON.stringify(s.legs[0])} on ${s.placed_at}: app ${a}, CLI ${b}`);
  }
});
if (filled < 50) { fail++; console.error(`FAIL  only ${filled} legs filled — the fixture is not exercising anything`); }
console.log(`${slips.length} legs: app and CLI agree on ${slips.length - fail}, ${filled} filled, ${fail} differ`);
process.exit(fail ? 1 : 0);

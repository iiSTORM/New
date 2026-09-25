/* Do the JS model and its Python port actually agree?
 *
 * Every weight in this repo was chosen by optimize_weights.py, which is a
 * hand-written Python port of the model in src/app.jsx. That only means
 * anything if the two compute the same number. Nothing checked, and they
 * had drifted in two places at once — a constant left at its pre-measurement
 * value in one copy, and elapsed days floored on one side and fractional on
 * the other. Neither is visible from either side alone; both were found by
 * diffing predictions row by row, which is what this now does on every run.
 *
 * Runs the real point-in-time model over real committed data, then hands
 * the results to the Python side to recompute and compare.
 *
 * Run: node tests/model_parity.test.mjs
 */
import fs from "fs";
import os from "os";
import path from "path";
import { fileURLToPath } from "url";
import { createRequire } from "module";
import { spawnSync } from "child_process";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(path.join(root, "package.json"));
const React = require("react");
const { transformSync } = require("@babel/core");

const raw = fs.readFileSync(path.join(root, "src/app.jsx"), "utf8");
const bootstrap = raw.indexOf("const root = ReactDOM.createRoot");
if (bootstrap < 0) throw new Error("bootstrap not found — has src/app.jsx changed shape?");
const body = raw.slice(raw.indexOf("try {") + "try {".length, bootstrap);
const { code } = transformSync(
  `${body}\nreturn { projectPointInTime, mapsCountedFor, DEFAULT_WEIGHTS_BY_GAME_AND_STAT };`,
  { presets: [["@babel/preset-react", { runtime: "classic" }]], filename: "app.jsx",
    parserOpts: { allowReturnOutsideFunction: true } });

const fakeWindow = { innerWidth: 1400, addEventListener() {}, removeEventListener() {},
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} } };
const app = new Function(
  "React", "ReactDOM", "window", "document", "fetch", "localStorage", "console", code
)(React, { createRoot: () => ({ render() {} }) }, fakeWindow, { getElementById: () => null },
  () => new Promise(() => {}), fakeWindow.localStorage, console);

const FILES = { lol: "data.json", valorant: "valorant_data.json", cs2: "cs2_data.json" };
const STAT_KEY = { kills: "k", deaths: "d", assists: "a" };
const PER_COMBO = 400;   // enough to cover every code path; the full set is slow in CI

/* The match INDEX, not its date: a team can play twice on one day, and
   keying on the date silently let the Python side re-project against the
   wrong opponent. That produced a fake 4-kill "drift" in LoL the first
   time this was run, which is exactly the sort of false alarm that gets a
   real check deleted. */
const predictions = {};
let produced = 0;
for (const [game, file] of Object.entries(FILES)) {
  const dataPath = path.join(root, file);
  if (!fs.existsSync(dataPath)) continue;
  const data = JSON.parse(fs.readFileSync(dataPath, "utf8"));
  for (const stat of Object.keys(STAT_KEY)) {
    const weights = app.DEFAULT_WEIGHTS_BY_GAME_AND_STAT[game][stat];
    const rows = [];
    outer:
    for (const [regionKey, region] of Object.entries(data.regions || {})) {
      const teams = region.teams || {}, past = region.past_matches || [];
      if (!Object.keys(teams).length || !past.length) continue;
      for (let i = 0; i < past.length; i++) {
        const m = past[i];
        if (!m.date) continue;
        for (const side of ["teamA", "teamB"]) {
          const team = m[side], opp = side === "teamA" ? m.teamB : m.teamA;
          if (!teams[team]) continue;
          for (const player of teams[team].players || []) {
            // app.mapsCountedFor, not `m.maps_counted || 2`. The map
            // count is part of what the two ports have to agree on, and
            // a harness carrying its OWN copy of the rule tests the
            // model against a third opinion. This one did: the Python
            // side moved to the shared helper and the harness did not,
            // so every CS2 Bo1 came back exactly 2x apart.
            const r = app.projectPointInTime(past, teams, player, team, opp,
              app.mapsCountedFor(m), weights, m.date, stat, m.patch);
            if (!r || typeof r.total !== "number" || !isFinite(r.total)) continue;
            rows.push([regionKey, i, team, player.name, Number(r.total.toFixed(9))]);
            if (rows.length >= PER_COMBO) break outer;
          }
        }
      }
    }
    if (rows.length) { predictions[`${game}|${stat}`] = rows; produced += rows.length; }
  }
}

if (!produced) {
  console.error("FAIL  the JS model produced no predictions at all — nothing was compared");
  process.exit(1);
}

const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "parity-")), "js.json");
fs.writeFileSync(out, JSON.stringify(predictions));

const py = spawnSync("python3", [path.join(root, "scripts/dev/check_model_parity.py"), out],
                     { encoding: "utf8", cwd: root });
process.stdout.write(py.stdout || "");
if (py.status === null) {
  // No interpreter at all is a skip, not a pass: say so loudly rather than
  // letting a green line imply the two ports were compared.
  console.error(`SKIP  could not run python3 (${py.error && py.error.message}) — parity NOT checked`);
  process.exit(0);
}
if (py.stderr) process.stderr.write(py.stderr);
process.exit(py.status === 0 ? 0 : 1);

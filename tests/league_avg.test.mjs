/* pointInTimeLeagueAvgStat was rewritten from one scan per team to one pass
 * with a cache, because the Record and Parlays tabs call it once per graded
 * line and Parlays had grown to 38 seconds to open. The rewrite must give the
 * same number to the last bit, which this checks against the per-team
 * definition on the committed data, at many cutoffs, for every game.
 *
 * Run: node tests/league_avg.test.mjs
 */
import fs from "fs";
import path from "path";
import React from "react";
import { fileURLToPath } from "url";
import { createRequire } from "module";

const require = createRequire(import.meta.url);
const { transformSync } = require("@babel/core");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const raw = fs.readFileSync(path.join(root, "src/app.jsx"), "utf8");
const body = raw.slice(raw.indexOf("try {") + "try {".length, raw.indexOf("const root = ReactDOM.createRoot"));
const { code } = transformSync(
  `${body}\nreturn { pointInTimeLeagueAvgStat, pointInTimeTeamStat, historyPool };`,
  { presets: [["@babel/preset-react", { runtime: "classic" }]], filename: "app.jsx", compact: true,
    parserOpts: { allowReturnOutsideFunction: true } });
const w = { innerWidth: 1400, addEventListener() {}, removeEventListener() {},
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} } };
const app = new Function("React", "ReactDOM", "window", "document", "fetch", "localStorage", "console", code)(
  React, { createRoot: () => ({ render() {} }) }, w, { getElementById: () => null },
  () => new Promise(() => {}), w.localStorage, console);

// The definition the fast version replaced: every team's own scan, averaged.
function reference(pastMatches, teams, statKey, cutoff) {
  const rates = Object.keys(teams).map((t) => app.pointInTimeTeamStat(pastMatches, t, statKey, cutoff))
    .filter((r) => r !== null);
  return rates.length ? rates.reduce((s, r) => s + r, 0) / rates.length : null;
}

let checked = 0, fail = 0;
for (const file of ["data.json", "valorant_data.json", "cs2_data.json"]) {
  const regions = JSON.parse(fs.readFileSync(path.join(root, file), "utf8")).regions;
  for (const [key, region] of Object.entries(regions)) {
    if (!Object.keys(region.teams || {}).length) continue;
    const pool = app.historyPool(regions, key);
    const dates = [...new Set(pool.map((m) => String(m.date || "").slice(0, 10)))].sort();
    const cutoffs = [null, ...dates.filter((_, i) => i % Math.max(1, Math.floor(dates.length / 6)) === 0)];
    for (const cutoff of cutoffs) for (const stat of ["k", "d"]) {
      const want = reference(pool, region.teams, stat, cutoff);
      const got = app.pointInTimeLeagueAvgStat(pool, region.teams, stat, cutoff);
      const again = app.pointInTimeLeagueAvgStat(pool, region.teams, stat, cutoff);   // cached
      checked++;
      if (got !== want || again !== want) {
        fail++;
        console.error(`FAIL  ${file} ${key} ${stat} @${cutoff}: ${got} / ${again}, per-team scan says ${want}`);
      }
    }
  }
}
console.log(`${checked} league averages checked against the per-team scan, ${fail} differ`);
process.exit(fail ? 1 : 0);

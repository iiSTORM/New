/* Are the edges making money?
 *
 * props_results.json grades every posted line whose match has been
 * played, but stores only the line and the outcome -- not what we said.
 * modelRecord() in the app re-projects each one point-in-time, so the
 * board's own record can be reconstructed. This runs it and asks the
 * three questions that matter, in order of how uncomfortable they are.
 *
 * READ THE CLUSTERING BEFORE THE NUMBERS. The 69 props come from EIGHT
 * matches -- six CS2, two Valorant -- and props within a match share
 * rounds, pace and how one-sided it was, so they are nowhere near 69
 * independent observations. Clustered on the match, which is the right
 * unit:
 *
 *   win rate per match      43.7%  +/- 21.7   ->  22% to 65%
 *   our MAE minus line MAE  +0.29  +/- 0.57   ->  -0.28 to +0.85
 *   per-match win rates     36, 40, 63, 60, 0, 100, 11, 40 (%)
 *
 * Breakeven sits inside the first interval and zero inside the second.
 * So NOTHING HERE IS ESTABLISHED: not that the model loses money, not
 * that the line forecasts better. The first version of this file said
 * both, on a Wilson interval that assumed 69 independent rows. It was
 * wrong to.
 *
 * The per-prop numbers below are still the point estimates, and still
 * worth reading -- they are just not evidence yet.
 *
 * RESULT, on 69 decided props over four days (2026-09-21 to 24):
 *
 *   model won                30/69   43.5%   [95% CI 32-55%]
 *   breakeven at -110                52.4%
 *   breakeven, 2-pick at 3x          57.7%
 *
 * Not close. But a win rate alone does not say WHY, and the next two
 * cuts do.
 *
 *   always-under would have won  48/69   69.6%
 *
 * 70% of outcomes in this sample landed under the line, and the model's
 * own unders won 61% -- WORSE than taking every under blindly. So the
 * model is not merely unprofitable here, it is worse than ignoring it.
 *
 *   our MAE 5.13   line MAE 4.89   we were closer on 41% of props
 *
 * And that is the structural answer. The line is a better forecast than
 * our projection, on both games. There is no edge to extract from a
 * counterparty that predicts better than you do; every disagreement is
 * more likely to be our error than theirs.
 *
 * Per game, where the two failures are different:
 *
 *   cs2       n=40   our projection +1.07 from actual | line -0.23
 *   valorant  n=29   our projection -5.48 from actual | line -5.09
 *
 * CS2's market is calibrated to a quarter of a kill across 40 props.
 * Beating that with a model whose own MAE is 4.3 needs an edge we do
 * not have. Valorant is a different story: we and the market were BOTH
 * about five kills too high, so the outcomes came in below what anyone
 * expected -- these are Champions fixtures projected from home-region
 * form, and the step up in class costs more than either side priced.
 *
 * WHY THE SAMPLE IS EIGHT MATCHES, AND WHAT FIXES IT. 646 of 724 CS2
 * lines old enough to grade were refused for "no completed match on
 * that date", and 645 of those were dated AFTER the latest match held
 * for that team. This scraper takes the most recent NOTABLE matches
 * while the prop provider posts far wider, so fixtures were projected,
 * played, and never scraped. 63 missing results across 51 teams unlock
 * all 646 -- about 70 graded matches instead of 8. scrape_cs2.py now
 * fetches them (teams_awaiting_results), so this file gets an answer
 * worth acting on in a few runs rather than a few months.
 *
 * Usage:
 *     node scripts/dev/model_vs_market.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { transformSync } from "@babel/core";
import React from "react";

const root = process.cwd();
const raw = fs.readFileSync(path.join(root, "src/app.jsx"), "utf8");
const bootstrap = raw.indexOf("const root = ReactDOM.createRoot");
const body = raw.slice(raw.indexOf("try {") + "try {".length, bootstrap);
const { code } = transformSync(
  `${body}\nreturn { modelRecord, DEFAULT_WEIGHTS_BY_GAME_AND_STAT };`,
  { presets: [["@babel/preset-react", { runtime: "classic" }]], filename: "app.jsx",
    parserOpts: { allowReturnOutsideFunction: true } });

const store = {};
const win = { innerWidth: 1400, addEventListener() {}, removeEventListener() {},
  localStorage: { getItem: (k) => (k in store ? store[k] : null),
                  setItem(k, v) { store[k] = String(v); }, removeItem(k) { delete store[k]; } } };
const app = new Function("React", "ReactDOM", "window", "document", "fetch", "localStorage", "console", code)(
  React, { createRoot: () => ({ render() {} }) }, win,
  { getElementById: () => null, addEventListener() {}, removeEventListener() {} },
  () => new Promise(() => {}), win.localStorage, console);

const results = JSON.parse(fs.readFileSync(path.join(root, "props_results.json"), "utf8"));
const FILES = { lol: "data.json", valorant: "valorant_data.json", cs2: "cs2_data.json" };
const W = app.DEFAULT_WEIGHTS_BY_GAME_AND_STAT;

let all = [];
for (const [game, file] of Object.entries(FILES)) {
  if (!fs.existsSync(path.join(root, file))) continue;
  const { regions } = JSON.parse(fs.readFileSync(path.join(root, file), "utf8"));
  const regionList = Object.keys(regions);
  for (const stat of ["kills", "deaths", "assists"]) {
    const graded = results.graded.filter((r) => r.game === game && r.stat === stat);
    if (!graded.length) continue;
    all = all.concat(app.modelRecord(regions, regionList, { graded }, W[game][stat], stat)
                        .map((r) => ({ ...r, game, stat })));
  }
}

const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;
const pct = (w, n) => `${((100 * w) / n).toFixed(1)}%`;
function wilson(w, n) {
  if (!n) return [0, 0];
  const z = 1.96, p = w / n, d = 1 + (z * z) / n;
  const c = (p + (z * z) / (2 * n)) / d;
  const m = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / d;
  return [100 * (c - m), 100 * (c + m)];
}

const won = all.filter((r) => r.won).length;
const underBase = all.filter((r) => r.result === "under").length;
const [lo, hi] = wilson(won, all.length);

console.log(`graded in file ${results.graded.length}, re-projectable and decided ${all.length}`);
console.log(`dates ${[...new Set(all.map((r) => r.match_date))].sort().join(", ")}\n`);
console.log(`  model won            ${won}/${all.length}   ${pct(won, all.length)}   [95% CI ${lo.toFixed(0)}-${hi.toFixed(0)}%]`);
console.log(`  always-under         ${underBase}/${all.length}   ${pct(underBase, all.length)}`);
console.log(`  breakeven -110                  52.4%`);
console.log(`  breakeven 2-pick at 3x         57.7%\n`);

for (const side of ["over", "under"]) {
  const sel = all.filter((r) => r.side === side);
  if (sel.length) console.log(`  took ${side.padEnd(6)} ${sel.filter((r) => r.won).length}/${sel.length}  ${pct(sel.filter((r) => r.won).length, sel.length)}`);
}

console.log("\n  who forecasts better");
for (const g of [...new Set(all.map((r) => r.game)), "ALL"]) {
  const sel = g === "ALL" ? all : all.filter((r) => r.game === g);
  if (!sel.length) continue;
  const ourMae = mean(sel.map((r) => Math.abs(r.projection - r.actual)));
  const lineMae = mean(sel.map((r) => Math.abs(r.line - r.actual)));
  const closer = sel.filter((r) => Math.abs(r.projection - r.actual) < Math.abs(r.line - r.actual)).length;
  console.log(`    ${g.padEnd(9)} n=${String(sel.length).padStart(3)}  our MAE ${ourMae.toFixed(2)}  line MAE ${lineMae.toFixed(2)}  we were closer on ${pct(closer, sel.length)}`);
}

console.log("\n  bias (negative = the number was too high)");
for (const g of [...new Set(all.map((r) => r.game))]) {
  const sel = all.filter((r) => r.game === g);
  console.log(`    ${g.padEnd(9)} n=${String(sel.length).padStart(3)}  ours ${mean(sel.map((r) => r.actual - r.projection)).toFixed(2)}  line ${mean(sel.map((r) => r.actual - r.line)).toFixed(2)}`);
}

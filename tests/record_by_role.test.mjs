/* The Record tab's "By position" table: the League record split by role.
 *
 * Checked on hand-built rows where the counts are arithmetic, then on the
 * committed data.json and props_results.json, so a roster file that stops
 * carrying `role` shows up here as an empty table rather than quietly as a
 * missing card in the app.
 *
 * Run: node tests/record_by_role.test.mjs
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
const { code } = transformSync(`${body}\nreturn { recordByRole, modelRecord, DEFAULT_WEIGHTS_BY_GAME_AND_STAT };`,
  { presets: [["@babel/preset-react", { runtime: "classic" }]], filename: "app.jsx", compact: true,
    parserOpts: { allowReturnOutsideFunction: true } });
const w = { innerWidth: 1400, addEventListener() {}, removeEventListener() {},
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} } };
const app = new Function("React", "ReactDOM", "window", "document", "fetch", "localStorage", "console", code)(
  React, { createRoot: () => ({ render() {} }) }, w, { getElementById: () => null },
  () => new Promise(() => {}), w.localStorage, console);

let pass = 0, fail = 0;
const check = (label, fn) => { try { fn(); pass++; } catch (e) { fail++; console.error(`FAIL  ${label}\n      ${e.message}`); } };
const eq = (a, b, what) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${what}: ${JSON.stringify(a)} != ${JSON.stringify(b)}`); };

const row = (role, side, won, line, actual, projection, team = "A") => ({
  game: "lol", role, side, won, line, actual, projection, team, opponent: "B", match_date: "2026-10-01" });

check("counts, sides and the line's lean, in lane order", () => {
  const out = app.recordByRole([
    row("SUP", "under", true, 1.5, 1, 1.0),
    row("BOT", "under", true, 8.5, 6, 7.0),
    row("BOT", "over", false, 7.5, 5, 8.0),
    row("TOP", "over", true, 3.5, 5, 4.0),
    row(null, "over", true, 3.5, 5, 4.0),          // CS2/Valorant: no role, left out
  ]);
  eq(out.map((r) => r.role), ["TOP", "BOT", "SUP"], "order");
  const bot = out[1];
  eq([bot.n, bot.won, bot.over, bot.under], [2, 1, { n: 1, won: 0 }, { n: 1, won: 1 }], "bot counts");
  eq(bot.lineMinusActual, 2.5, "line ran high");              // (2.5 + 2.5) / 2
  eq(bot.errorEdge, 0.5, "closer");                           // ((2.5-1) + (2.5-3)) / 2
  eq(bot.matches, 1, "one match");
});

check("a record with no roles yields no table", () => {
  eq(app.recordByRole([row(null, "over", true, 3.5, 5, 4)]), [], "empty");
});

check("the committed League record splits into all five positions", () => {
  const data = JSON.parse(fs.readFileSync(path.join(root, "data.json"), "utf8"));
  const results = JSON.parse(fs.readFileSync(path.join(root, "props_results.json"), "utf8"));
  const rows = app.modelRecord(data.regions, Object.keys(data.regions), results,
    app.DEFAULT_WEIGHTS_BY_GAME_AND_STAT.lol.kills, "kills");
  const roles = app.recordByRole(rows);
  eq(roles.map((r) => r.role), ["TOP", "JNG", "MID", "BOT", "SUP"], "roles");
  const total = roles.reduce((a, r) => a + r.n, 0);
  if (total !== rows.filter((r) => r.role).length) throw new Error("rows lost");
  for (const r of roles) if (r.over.n + r.under.n !== r.n) throw new Error(`${r.role} sides`);
  console.log(`      ${roles.map((r) => `${r.role} ${r.won}/${r.n}`).join(", ")}`);
});

console.log(`${pass} by-position checks passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

/* The class gap at international League events: which side a team is on,
 * the graded record on bets against it, and the caution on the Edges board.
 *
 * Run: node tests/class_gap.test.mjs
 */
import fs from "fs";
import path from "path";
import React from "react";
import { fileURLToPath } from "url";
import { createRequire } from "module";

const require = createRequire(import.meta.url);
const { transformSync } = require("@babel/core");
const { renderToStaticMarkup } = require("react-dom/server");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const raw = fs.readFileSync(path.join(root, "src/app.jsx"), "utf8");
const body = raw.slice(raw.indexOf("try {") + "try {".length, raw.indexOf("const root = ReactDOM.createRoot"));
const { code } = transformSync(`${body}\nreturn { classGapSide, againstClassGapRecord, classGapMultiplier, CLASS_GAP_KILLS, isEventRegion, EdgeRow, ThemeContext, BASE_TOKENS, STAT_TYPES };`,
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

const teams = { T1: { from_home_region: "LCK" }, LG: { from_home_region: "CBLOL" },
                KC: { from_home_region: "LEC" }, Home: {} };

check("sides of the gap", () => {
  eq([app.classGapSide(teams, "T1", "LG"), app.classGapSide(teams, "LG", "T1")], ["stronger", "weaker"], "1v3");
  eq([app.classGapSide(teams, "T1", "KC"), app.classGapSide(teams, "KC", "LG"), app.classGapSide(teams, "T1", "Home")],
     [null, null, null], "no gap measured / league play");
  eq(app.classGapMultiplier(teams, "LG", "T1", app.STAT_TYPES.kills), 1 / app.CLASS_GAP_KILLS, "multiplier agrees");
});

check("events are never homes", () => {
  eq(["Worlds", "Demacia Cup", "VCT Champions", "LCK", "VCT EMEA"].map(app.isEventRegion), [true, true, true, false, false], "events");
});

check("the record against the gap counts only event lines, in that direction", () => {
  const regions = {
    LCK: { teams: { T1: {} }, past_matches: [] }, CBLOL: { teams: { LG: {} }, past_matches: [] },
    Worlds: { teams: {}, past_matches: [{ date: "2026-10-23", teamA: "T1", teamB: "LG" }] },
  };
  const g = (team, opponent, side, won, date = "2026-10-23") => ({ team, opponent, side, won, match_date: date });
  const out = app.againstClassGapRecord([
    g("LG", "T1", "over", false), g("LG", "T1", "over", true),   // against: weaker over
    g("T1", "LG", "under", false),                               // against: stronger under
    g("T1", "LG", "over", true), g("LG", "T1", "under", true),   // with the gap: not counted
    g("LG", "T1", "over", false, "2026-09-01"),                  // not an event match
  ], regions);
  eq(out, { n: 3, won: 1 }, "tally");
});

check("the board flags a bet against the gap, and only that", () => {
  const theme = { ...app.BASE_TOKENS, accent: "#c9a86a", accentSoft: "#0000", accentBorder: "#0000", cornerStyle: "rounded" };
  const base = { name: "Feisty", team: "LG", opponent: "T1", role: "MID", oppKnown: true, region: "Worlds",
    prop: { line: 2.5, maps: 1, odds_type: "standard", start_time: "2099-01-01T00:00:00Z" },
    projection: 2.7, breakdown: { evidenceGames: 20 }, evidence: 20, maps: 1, game: "lol", statType: "kills" };
  const render = (row) => renderToStaticMarkup(React.createElement(app.ThemeContext.Provider, { value: theme },
    React.createElement(app.EdgeRow, { row, theme, cfg: app.STAT_TYPES.kills, fresh: true, ageMinutes: 5,
      isDesktop: true, againstGap: { n: 59, won: 26 } })));
  const flagged = render({ ...base, classGap: "weaker", edge: 0.2, adjustedEdge: 0.2 });
  if (!flagged.includes("against the class gap") || !flagged.includes("26/59")) throw new Error("no caution");
  const under = render({ ...base, classGap: "weaker", edge: -0.4, adjustedEdge: -0.4, projection: 2.1 });
  if (under.includes("against the class gap")) throw new Error("an under on the weaker side goes WITH the gap");
  const none = render({ ...base, classGap: null, edge: 0.2, adjustedEdge: 0.2 });
  if (none.includes("against the class gap")) throw new Error("no gap, no caution");
});

console.log(`${pass} class-gap checks passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

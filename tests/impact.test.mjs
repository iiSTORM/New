/* Impact ±, the swing-style stat: a player's rating against the average of
 * everyone on the same map, as a percentage.
 *
 * Checked on hand-built lobbies where the answer is arithmetic, and on the
 * committed cs2_data.json so a change in the data's shape (a renamed rating
 * field, a dropped per-map "rt") shows up as an empty panel here rather than
 * quietly in the app.
 *
 * Run: node tests/impact.test.mjs
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
const { code } = transformSync(
  `${body}\nreturn { impactByPlayer, impactSummary, ImpactPanel, ThemeContext, BASE_TOKENS };`,
  { presets: [["@babel/preset-react", { runtime: "classic" }]], filename: "app.jsx", compact: true,
    parserOpts: { allowReturnOutsideFunction: true } });
const fakeWindow = { innerWidth: 1400, addEventListener() {}, removeEventListener() {},
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} } };
const app = new Function("React", "ReactDOM", "window", "document", "fetch", "localStorage", "console", code)(
  React, { createRoot: () => ({ render() {} }) }, fakeWindow, { getElementById: () => null },
  () => new Promise(() => {}), fakeWindow.localStorage, console);

let pass = 0, fail = 0;
const check = (label, fn) => { try { fn(); pass++; } catch (e) { fail++; console.error(`FAIL  ${label}\n      ${e.message}`); } };
const near = (a, b, what) => { if (Math.abs(a - b) > 1e-9) throw new Error(`${what}: ${a} != ${b}`); };

// Ten players; "star" rates 2.0 against nine at 1.0, so the lobby mean is 1.1.
const lobby = (key, ratings) => {
  const team = (names) => Object.fromEntries(names.map((n) => [n, { k: 10, [key]: ratings[n] }]));
  const names = Object.keys(ratings);
  return { A: team(names.slice(0, 5)), B: team(names.slice(5)) };
};
const ten = (star) => Object.fromEntries([["star", star], ...Array.from({ length: 9 }, (_, i) => [`p${i}`, 1])]);

check("series lobby: +81.8% for a 2.0 among nine 1.0s", () => {
  const out = app.impactByPlayer([{ date: "2026-10-01", actual: lobby("rating", ten(2)) }]);
  near(out.star[0].value, (2 / 1.1 - 1) * 100, "star");
  near(out.p0[0].value, (1 / 1.1 - 1) * 100, "p0");
  if (out.star[0].perMap) throw new Error("a series value is not a map value");
});

check("per-map ratings win over the series one, one value per map", () => {
  const m = { date: "2026-10-02", actual: lobby("rating", ten(1)),
    per_game: [lobby("rt", ten(2)), lobby("rt", ten(0.5))] };
  const out = app.impactByPlayer([m]);
  if (out.star.length !== 2 || !out.star[0].perMap) throw new Error(JSON.stringify(out.star));
  if (!(out.star[0].value > 0 && out.star[1].value < 0)) throw new Error("signs");
});

check("a partial box score (under eight rated players) is skipped, not averaged", () => {
  const r = ten(2); delete r.p0; delete r.p1; delete r.p2;
  const out = app.impactByPlayer([{ date: "d", actual: lobby("rating", r) }]);
  if (Object.keys(out).length) throw new Error("should be empty");
});

check("labels: consistent needs both the share and enough samples", () => {
  const s = (vals) => vals.map((value, i) => ({ when: `${i}`, value, perMap: true }));
  const label = (vals, w = 15) => app.impactSummary(s(vals), w).label;
  if (label([5, 3, 8, -1, 4, 2, 6]) !== "positive") throw new Error("positive");
  if (label([-5, -3, 2, -1, -4, -2]) !== "negative") throw new Error("negative");
  if (label([5, -3, 2, -1, 4, -2]) !== "mixed") throw new Error("mixed");
  if (label([5, 3, 8]) !== "few") throw new Error("few");
  // The window takes the most recent: an old hot streak does not count.
  if (label([9, 9, 9, 9, 9, -1, -2, -3, -4, -5], 5) !== "negative") throw new Error("window");
});

check("the real CS2 data yields players on both sides, averaging near zero", () => {
  const data = JSON.parse(fs.readFileSync(path.join(root, "cs2_data.json"), "utf8"));
  const region = Object.values(data.regions)[0];
  const out = app.impactByPlayer(region.past_matches);
  const sums = Object.values(out).map((v) => app.impactSummary(v, 15)).filter(Boolean);
  if (sums.length < 100) throw new Error(`only ${sums.length} players`);
  const labels = sums.reduce((o, s) => ({ ...o, [s.label]: (o[s.label] || 0) + 1 }), {});
  if (!labels.positive || !labels.negative) throw new Error(JSON.stringify(labels));
  // Every value is relative to its own lobby, so all values together centre
  // near zero; a drift here means a lobby is being averaged wrongly.
  const all = Object.values(out).flat();
  const mean = all.reduce((a, b) => a + b.value, 0) / all.length;
  if (Math.abs(mean) > 3) throw new Error(`grand mean ${mean.toFixed(2)}%`);
  console.log(`      ${sums.length} players: ${JSON.stringify(labels)}`);
});

check("the panel renders, and renders nothing without ratings", () => {
  const theme = { ...app.BASE_TOKENS, accent: "#c9a86a", accentSoft: "#0000", accentBorder: "#0000", cornerStyle: "rounded" };
  const render = (props) => renderToStaticMarkup(React.createElement(app.ThemeContext.Provider, { value: theme },
    React.createElement(app.ImpactPanel, props)));
  const teams = { A: { color: "#f00", players: [{ name: "star" }, { name: "p0" }] } };
  const matches = Array.from({ length: 6 }, (_, i) => ({ date: `2026-09-2${i}`, actual: lobby("rating", ten(2)) }));
  for (const isDesktop of [true, false]) {
    const html = render({ teams, pastMatches: matches, windowSize: 15, isDesktop });
    if (!html.includes("Impact ±") || !html.includes("star")) throw new Error("panel missing");
  }
  if (render({ teams, pastMatches: [{ date: "d", actual: { A: { star: { k: 1 } } } }], windowSize: 15, isDesktop: true }) !== "") {
    throw new Error("should render nothing for League-shaped data");
  }
});

console.log(`${pass} impact checks passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

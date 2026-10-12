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
const { code } = transformSync(`${body}\nreturn { recordByRole, recordByAgentClass, agentClassBefore, RecordGroupCard, ThemeContext, BASE_TOKENS, modelRecord, teamKillSeat, DEFAULT_WEIGHTS_BY_GAME_AND_STAT };`,
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

// Kill-rank seats, for CS2 and Valorant rosters that carry no position.
const roster = (names) => ({ T: { players: names.map((name) => ({ name })) } });
const game = (date, kills, games = 2) => ({ date, games, teamA: "T", teamB: "O",
  actual: { T: Object.fromEntries(Object.entries(kills).map(([n, k]) => [n, { k }])) } });
const five = { a: 50, b: 40, c: 30, d: 20, e: 10 };

check("seats rank by kills per map over the matches before the date", () => {
  const past = ["2026-09-01", "2026-09-02", "2026-09-03"].map((d) => game(d, five));
  const teams = roster(Object.keys(five));
  eq(["a", "c", "e"].map((n) => app.teamKillSeat(past, teams, "T", n, "2026-10-01")), [1, 3, 5], "seats");
});

check("the graded match itself never moves a seat", () => {
  const past = ["2026-09-01", "2026-09-02", "2026-09-03"].map((d) => game(d, five));
  past.push(game("2026-10-01", { a: 0, b: 0, c: 0, d: 0, e: 500 }));   // e goes off on the day
  eq(app.teamKillSeat(past, roster(Object.keys(five)), "T", "e", "2026-10-01"), 5, "same-day match leaked");
});

check("too little history gives no seat", () => {
  const two = ["2026-09-01", "2026-09-02"].map((d) => game(d, five));
  eq(app.teamKillSeat(two, roster(Object.keys(five)), "T", "a", "2026-10-01"), null, "under SEAT_MIN");
  const three = ["2026-09-01", "2026-09-02", "2026-09-03"].map((d) => game(d, { a: 5, b: 4, c: 3 }));
  eq(app.teamKillSeat(three, roster(["a", "b", "c"]), "T", "a", "2026-10-01"), null, "under four ranked");
});

check("seat rows group in rank order and League rows keep positions", () => {
  const out = app.recordByRole([row(null, "over", true, 3.5, 5, 4), { ...row(null, "under", true, 20.5, 18, 19), seat: 3 },
    { ...row(null, "over", false, 20.5, 18, 22), seat: 1 }]);
  eq(out.map((r) => r.role), ["Top fragger", "3rd on team"], "seat order");
});

// Agent classes, from the per-map "ac" the Valorant scraper writes.
const mapsOf = (date, classes) => ({ date, games: classes.length, teamA: "T", teamB: "O", actual: { T: { p: { k: 1 } } },
  per_game: classes.map((ac) => ({ T: { p: { k: 1, ...(ac ? { ag: "x", ac } : {}) } } })) });

check("the class played most before the date, flex when none holds half", () => {
  const past = [mapsOf("2026-09-01", ["duelist", "duelist"]), mapsOf("2026-09-02", ["duelist", "initiator"]),
                mapsOf("2026-10-01", ["sentinel", "sentinel", "sentinel", "sentinel"])];   // the graded day
  eq(app.agentClassBefore(past, "T", "p", "2026-10-01"), "duelist", "majority");
  const split = [mapsOf("2026-09-01", ["duelist", "initiator"]), mapsOf("2026-09-02", ["controller", "sentinel"])];
  eq(app.agentClassBefore(split, "T", "p", "2026-10-01"), "flex", "flex");
});

check("maps scraped before agents were read give no class", () => {
  const past = [mapsOf("2026-09-01", [null, null]), mapsOf("2026-09-02", [null, "duelist"])];
  eq(app.agentClassBefore(past, "T", "p", "2026-10-01"), null, "under SEAT_MIN classed maps");
});

check("agent-class rows group in class order; the card renders", () => {
  const groups = app.recordByAgentClass([{ ...row(null, "over", true, 20.5, 22, 21), agentClass: "sentinel" },
    { ...row(null, "under", true, 20.5, 18, 19), agentClass: "duelist" }, row(null, "over", true, 3.5, 5, 4)]);
  eq(groups.map((g) => g.role), ["Duelist", "Sentinel"], "order");
  const { renderToStaticMarkup } = require("react-dom/server");
  const theme = { ...app.BASE_TOKENS, accent: "#c9a86a", accentSoft: "#0000", accentBorder: "#0000", cornerStyle: "rounded" };
  const html = renderToStaticMarkup(React.createElement(app.ThemeContext.Provider, { value: theme },
    React.createElement(app.RecordGroupCard, { title: "By agent role", groups, note: "n" })));
  if (!html.includes("By agent role") || !html.includes("Duelist")) throw new Error("card missing");
});

for (const [g, file] of [["cs2", "cs2_data.json"], ["valorant", "valorant_data.json"]]) {
  check(`the committed ${g} record gets kill-rank seats`, () => {
    const data = JSON.parse(fs.readFileSync(path.join(root, file), "utf8"));
    const results = JSON.parse(fs.readFileSync(path.join(root, "props_results.json"), "utf8"));
    const rows = app.modelRecord(data.regions, Object.keys(data.regions), results,
      app.DEFAULT_WEIGHTS_BY_GAME_AND_STAT[g].kills, "kills");
    const seated = rows.filter((r) => r.seat).length;
    if (seated < 0.8 * rows.length) throw new Error(`only ${seated} of ${rows.length} seated`);
    const roles = app.recordByRole(rows);
    eq(roles.slice(0, 5).map((r) => r.role), ["Top fragger", "2nd on team", "3rd on team", "4th on team", "5th on team"], "seats");
    console.log(`      ${g}: ${roles.map((r) => `${r.role} ${r.won}/${r.n}`).join(", ")}`);
  });
}

console.log(`${pass} by-position checks passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

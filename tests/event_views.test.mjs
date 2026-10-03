/* The event views infer structure the API does not give: Swiss rounds and
 * pools, which knockout match feeds which, group tables. Each inference is
 * checked here against real events.
 *
 *   - tests/fixtures/lol_events_2026-10-03.json is what the schedule scrape
 *     returned for the Demacia Cup and Worlds 2026 on the morning the Demacia
 *     Cup started (rebuilt from a probe run; match ids are synthetic).
 *   - WORLDS_2025_KO is the real Worlds 2025 knockout stage, in the order the
 *     API listed it. The bracket lines rest on that order being bracket
 *     order, so it is tested on a bracket whose results are known.
 *
 * Run: node tests/event_views.test.mjs
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
const bootstrap = raw.indexOf("const root = ReactDOM.createRoot");
const body = raw.slice(raw.indexOf("try {") + "try {".length, bootstrap);
const { code } = transformSync(
  `${body}\nreturn { eventStages, eventStageKind, eventStageStatus, swissModel, bracketRounds, groupTable,`
  + ` eventTeamsAfter, eventWinner, EventTab, ThemeContext, BASE_TOKENS, GAME_ACCENTS };`,
  { presets: [["@babel/preset-react", { runtime: "classic" }]], filename: "app.jsx", compact: true,
    parserOpts: { allowReturnOutsideFunction: true } });
const fakeWindow = { innerWidth: 1400, addEventListener() {}, removeEventListener() {},
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} } };
const app = new Function("React", "ReactDOM", "window", "document", "fetch", "localStorage", "console", code)(
  React, { createRoot: () => ({ render() {} }) }, fakeWindow, { getElementById: () => null },
  () => new Promise(() => {}), fakeWindow.localStorage, console);

let pass = 0, fail = 0;
function check(label, fn) {
  try { fn(); pass++; } catch (e) { fail++; console.error(`FAIL  ${label}\n      ${e.message}`); }
}
function eq(a, b, what) {
  if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(`${what}: got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
}
const clone = (x) => JSON.parse(JSON.stringify(x));

const fixture = JSON.parse(fs.readFileSync(path.join(root, "tests/fixtures/lol_events_2026-10-03.json"), "utf8"));
const DC = fixture.events["Demacia Cup"];
const WORLDS = fixture.events.Worlds;

/* ------------------------------------------------------------ stages */

check("Demacia Cup: Round 4 and Round 5 fold into the Swiss stage", () => {
  const st = app.eventStages(DC);
  eq(st.map((s) => [s.name, s.kind, s.matches.length]), [["Swiss", "swiss", 20], ["Playoffs", "bracket", 7]], "stages");
  eq(st[0].matches.filter((m) => m.round).map((m) => m.round), [4, 4, 4, 5, 5], "explicit rounds");
});

check("Worlds 2026: play-ins listed, Swiss, knockouts as a bracket", () => {
  eq(app.eventStages(WORLDS).map((s) => [s.name, s.kind]),
    [["Play-Ins", "list"], ["Swiss", "swiss"], ["Knockouts", "bracket"]], "kinds");
});

check("a stage with several group sections is drawn as groups", () => {
  eq(app.eventStageKind({ name: "Group Stage", sections: [{}, {}], matches: [] }), "groups", "kind");
});

check("stage status", () => {
  const st = app.eventStages(DC);
  eq(st.map(app.eventStageStatus), ["upcoming", "upcoming"], "before the first game");
});

/* ------------------------------------------------------------- Swiss */

function playRound1(event, winners) {
  const ev = clone(event);
  const sw = ev.stages[0].sections[0].matches;
  sw.slice(0, 6).forEach((m, i) => {
    m.state = "completed";
    m.teams.forEach((t, j) => { t.outcome = j === winners[i] ? "win" : "loss"; t.wins = j === winners[i] ? 1 : 0; });
  });
  return ev;
}

check("before a game: twelve teams at 0-0, one round of six, the rest undrawn by day", () => {
  const sw = app.eventStages(DC)[0];
  const model = app.swissModel(sw, new Set());
  eq(model.standings.length, 12, "teams");
  eq(model.standings.every((r) => r.w === 0 && r.l === 0 && r.status === "alive"), true, "all 0-0 alive");
  eq(model.rounds.map((r) => [r.round, r.pools.map((p) => [p.pool, p.matches.length])]), [[1, [["0–0", 6]]]], "rounds");
  eq(model.undrawn.reduce((n, d) => n + d.matches.length, 0), 14, "undrawn");
});

check("after round 1 is played and round 2 is drawn, pools follow records", () => {
  const ev = playRound1(DC, [0, 1, 0, 1, 0, 1]);
  const sw = ev.stages[0].sections[0].matches;
  const name = (m, j) => ({ ...m.teams[j] });
  const r1 = sw.slice(0, 6);
  // Winners meet winners, losers meet losers.
  const winners = r1.map((m, i) => name(m, [0, 1, 0, 1, 0, 1][i]));
  const losers = r1.map((m, i) => name(m, 1 - [0, 1, 0, 1, 0, 1][i]));
  const strip = (t) => ({ name: t.name, code: t.code, team: t.team });
  sw[6].teams = [strip(winners[0]), strip(winners[1])];
  sw[7].teams = [strip(losers[0]), strip(losers[1])];
  const model = app.swissModel(app.eventStages(ev)[0], new Set());
  eq(model.rounds.map((r) => [r.round, r.pools.map((p) => [p.pool, p.matches.length])]),
    [[1, [["0–0", 6]]], [2, [["1–0", 1], ["0–1", 1]]]], "pools");
  eq(model.standings.filter((r) => r.w === 1).length, 6, "six at 1-0");
  eq(model.standings[0].w - model.standings[0].l >= model.standings[11].w - model.standings[11].l, true, "sorted");
});

check("a team named in the bracket is shown as through", () => {
  const ev = playRound1(DC, [0, 0, 0, 0, 0, 0]);
  const stages = app.eventStages(ev);
  stages[1].matches[0].teams[0] = { name: "RED Kalunga", code: "RED" };
  const model = app.swissModel(stages[0], app.eventTeamsAfter(stages, 0));
  eq(model.standings.find((r) => r.key === "RED Kalunga").status, "advanced", "status");
});

/* ------------------------------------------------------------ bracket */

const T = (code, outcome, wins) => ({ name: code, code, outcome, wins });
const ko = (block, a, b) => ({ id: `${a.code}-${b.code}`, state: "completed", block, teams: [a, b] });
// Worlds 2025 knockouts, real results, in the API's order.
const WORLDS_2025_KO = [
  ko("Quarterfinals", T("T1", "win", 3), T("AL", "loss", 2)),
  ko("Quarterfinals", T("G2", "loss", 1), T("TES", "win", 3)),
  ko("Quarterfinals", T("HLE", "loss", 1), T("GEN", "win", 3)),
  ko("Quarterfinals", T("CFO", "loss", 0), T("KT", "win", 3)),
  ko("Semifinals", T("TES", "loss", 0), T("T1", "win", 3)),
  ko("Semifinals", T("KT", "win", 3), T("GEN", "loss", 1)),
  ko("Finals", T("T1", "win", 3), T("KT", "loss", 2)),
];

check("Worlds 2025: the API's order is bracket order, so the tree's feeders are right", () => {
  const { rounds, tree } = app.bracketRounds(WORLDS_2025_KO);
  eq(tree, true, "tree");
  eq(rounds.map((r) => r.matches.length), [4, 2, 1], "sizes");
  for (let r = 1; r < rounds.length; r++) {
    rounds[r].matches.forEach((m, i) => {
      const fed = [app.eventWinner(rounds[r - 1].matches[2 * i]), app.eventWinner(rounds[r - 1].matches[2 * i + 1])];
      const sides = m.teams.map((t) => t.name).sort();
      eq(sides, [...fed].sort(), `${rounds[r].name} ${i + 1} is fed by matches ${2 * i + 1} and ${2 * i + 2}`);
    });
  }
});

check("Demacia Cup playoffs: quarterfinals, semifinals, final", () => {
  const { rounds, tree } = app.bracketRounds(app.eventStages(DC)[1].matches);
  eq(rounds.map((r) => [r.name, r.matches.length]), [["Quarterfinals", 4], ["Semifinals", 2], ["Finals", 1]], "rounds");
  eq(tree, true, "tree");
});

check("seven unnamed matches halve into a tree; fourteen do not", () => {
  const blank = (n) => Array.from({ length: n }, (_, i) => ({ id: `x${i}`, teams: [] }));
  eq(app.bracketRounds(blank(7)).rounds.map((r) => r.matches.length), [4, 2, 1], "7");
  eq(app.bracketRounds(blank(7)).tree, true, "7 tree");
  eq(app.bracketRounds(blank(14)).tree, false, "14 is not single elimination");
});

check("MSI-style double elimination is not drawn with connectors", () => {
  const ms = [...Array(13)].map((_, i) => ({ id: `k${i}`, block: "Knockouts", teams: [] }))
    .concat([{ id: "f", block: "Finals", teams: [] }]);
  eq(app.bracketRounds(ms).tree, false, "tree");
});

/* ------------------------------------------------------------- groups */

check("group table: series, then game difference", () => {
  const table = app.groupTable([
    ko("Groups", T("BLG", "win", 2), T("BFX", "loss", 1)),
    ko("Groups", T("GEN", "win", 2), T("BFX", "loss", 0)),
    ko("Groups", T("GEN", "win", 2), T("BLG", "loss", 0)),
    { id: "u", state: "unstarted", teams: [T("BLG"), T("GEN")] },
  ]);
  eq(table.map((r) => [r.key, r.w, r.l, r.gw, r.gl]),
    [["GEN", 2, 0, 4, 0], ["BLG", 1, 1, 2, 3], ["BFX", 0, 2, 1, 4]], "table");
});

/* ------------------------------------------------------------- render */

const theme = { ...app.BASE_TOKENS, accent: app.GAME_ACCENTS.lol.accent, accentSoft: "#0000", accentBorder: "#0000", cornerStyle: "rounded" };
const render = (event, teams, isDesktop) => renderToStaticMarkup(
  React.createElement(app.ThemeContext.Provider, { value: theme },
    React.createElement(app.EventTab, { event, teams, isDesktop })));

check("renders: the Demacia Cup on its first morning shows its matchups and the bracket shell", () => {
  const teams = { "RED Kalunga": { color: "#f00" } };
  for (const desktop of [true, false]) {
    const html = render(DC, teams, desktop);
    const rounds = desktop ? ["QUARTERFINALS", "SEMIFINALS", "FINALS"] : ["QUARTERS", "SEMIS", "FINAL"];
    for (const want of ["Swiss", "ROUND 1", "0–0 pool", ...rounds, "Bo1", "Bo5", "<path"]) {
      if (!html.includes(want)) throw new Error(`${desktop ? "desktop" : "phone"} markup lacks ${want}`);
    }
  }
  if (!render(DC, teams, false).includes(">RED<")) throw new Error("phone view should use team codes");
});

check("renders: Worlds with every slot TBD, and an event with no stages", () => {
  const html = render(WORLDS, {}, true);
  if (!html.includes("Play-Ins") || !html.includes("TBD")) throw new Error("Worlds shell missing");
  render({ name: "x", stages: [] }, {}, false);
});

console.log(`${pass} event-view checks passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

/* The board-fixture inference runs twice, in two languages, and they have to
 * agree.
 *
 * scripts/board_fixtures.py runs in the scrape workflow so the inferred
 * fixtures are committed and auditable; withBoardFixtures in src/app.jsx runs
 * at read time so a line posted since the last scrape is visible for the
 * hours it is live. Two implementations of one rule is a drift surface, and
 * the drift would be silent: the app would list a fixture the data file does
 * not have, or hide one it does, and either way the number beside a line
 * would be right while the line was on the wrong game.
 *
 * So this runs both over the same inputs and compares fixture by fixture.
 * The cases are defined HERE and handed to the Python side, which recomputes
 * them rather than keeping its own copy of the list.
 *
 * Run: node tests/board_parity.test.mjs
 */
import fs from "fs";
import os from "os";
import path from "path";
import React from "react";
import { spawnSync } from "child_process";
import { fileURLToPath } from "url";
import { createRequire } from "module";

/* Re-exec once in a zone that is NOT UTC.
 *
 * A naive stamp ("2026-09-26T20:00:00") is read as UTC by Python and as LOCAL
 * time by `new Date`, so parseStamp appends the Z itself. Under a UTC runner
 * local IS UTC, and the bug and the fix produce identical answers -- the check
 * passes by accident and would go on passing after someone removed the line.
 * Forcing a zone with an offset is what makes this test able to see it. */
if (!process.env.BOARD_PARITY_TZ) {
  const { status, error } = spawnSync(process.execPath, [fileURLToPath(import.meta.url)],
    { stdio: "inherit", env: { ...process.env, TZ: "America/New_York",
                              BOARD_PARITY_TZ: "1" } });
  if (error) { console.error(`FAIL  could not re-exec under TZ: ${error.message}`); process.exit(1); }
  process.exit(status === null ? 1 : status);
}
if (new Date().getTimezoneOffset() === 0) {
  console.error("FAIL  re-exec did not take effect — still on a UTC offset, so a "
    + "naive stamp read as local time would look correct here");
  process.exit(1);
}

const require = createRequire(import.meta.url);
const { transformSync } = require("@babel/core");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const raw = fs.readFileSync(path.join(root, "src/app.jsx"), "utf8");
const bootstrap = raw.indexOf("const root = ReactDOM.createRoot");
if (bootstrap < 0) throw new Error("bootstrap not found — has src/app.jsx changed shape?");
const body = raw.slice(raw.indexOf("try {") + "try {".length, bootstrap);
const { code } = transformSync(
  `${body}\nreturn { withBoardFixtures, parseStamp, resolveBoardTeam, boardTeamIndex,`
  + ` fixtureMatchesSlot, PROP_MATCH_WINDOW_HOURS, BOARD_MAX_AGE_HOURS, MAX_BOARD_AGE_DAYS };`,
  { presets: [["@babel/preset-react", { runtime: "classic" }]], filename: "app.jsx",
    parserOpts: { allowReturnOutsideFunction: true } });

const fakeWindow = { innerWidth: 1400, addEventListener() {}, removeEventListener() {},
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} } };
const app = new Function(
  "React", "ReactDOM", "window", "document", "fetch", "localStorage", "console", code
)(React, { createRoot: () => ({ render() {} }) }, fakeWindow, { getElementById: () => null },
  () => new Promise(() => {}), fakeWindow.localStorage, console);

const NOW = "2026-09-26T12:00:00Z";
const now = new Date(NOW);
const at = (hours) => new Date(now.getTime() + hours * 3600000).toISOString()
  .replace(/\.\d{3}Z$/, "Z");

/* ---------- case helpers ---------- */

const region = (teams, upcoming = []) => ({
  teams: Object.fromEntries(Object.entries(teams).map(([name, players]) =>
    [name, { players: players.map((p) => ({ name: p })) }])),
  upcoming_matches: upcoming,
});

const board = (rows, fetched = NOW, game = "lol") => {
  const byPlayer = {};
  for (const r of rows) (byPlayer[r.player] = byPlayer[r.player] || []).push(r);
  return { fetched_at: fetched, props: { [game]: byPlayer } };
};

const line = (player, team, when, regionName = "LCS", maps = 3) =>
  ({ player, team, start_time: when, region: regionName, stat: "kills", maps, line: 10.5 });

/* ---------- the cases ----------
 *
 * The three real files first, because they are the only inputs with the
 * shapes nobody thought to invent: eight CS2 teams with no fixture, a
 * Valorant event whose fixtures carry a bare date and live under a region
 * the board never names, a playoff bracket with one side open. Then the
 * synthetic ones, each pinning a decision that a real board exercises rarely
 * enough that it could break for weeks unnoticed. */
const cases = [
  { name: "real:lol", game: "lol", dataFile: "data.json", propsFile: "props.json" },
  { name: "real:cs2", game: "cs2", dataFile: "cs2_data.json", propsFile: "props.json" },
  { name: "real:valorant", game: "valorant", dataFile: "valorant_data.json",
    propsFile: "props.json" },

  { name: "hole: one candidate", game: "lol",
    regions: { LCS: region({ LYON: ["Bvoy"], "Shopify Rebellion": ["Tomio"] },
      [{ date: at(6), teamA: "TBD", teamB: "LYON", block: "Playoffs" }]) },
    props: board([line("Bvoy", "LYON", at(6)), line("Tomio", "Shopify Rebellion", at(6))]) },

  { name: "hole: two candidates", game: "lol",
    regions: { LCS: region({ LYON: ["Bvoy"], "Shopify Rebellion": ["Tomio"],
      Disguised: ["Yeon"] }, [{ date: at(6), teamA: "TBD", teamB: "LYON" }]) },
    props: board([line("Bvoy", "LYON", at(6)), line("Tomio", "Shopify Rebellion", at(6)),
      line("Yeon", "Disguised", at(6))]) },

  { name: "no fixture at all", game: "lol",
    regions: { TCL: region({ "SU Esports": ["Zeitnot"], "PCIFIC Esports": ["Serin"] }) },
    props: board([line("Zeitnot", "SU Esports", at(2), "TCL", 1),
      line("Serin", "PCIFIC Esports", at(3), "TCL", 1)]) },

  { name: "two teams, one kickoff", game: "cs2",
    regions: { CS2: region({ K27: ["sdaim"], "ex-RUBY": ["z1k4"] }) },
    props: board([line("sdaim", "K27", at(3), "CS2", 2),
      line("z1k4", "ex-RUBY", at(3), "CS2", 2)], NOW, "cs2") },

  { name: "six teams, one kickoff", game: "cs2",
    regions: { CS2: region({ HEROIC: ["cadiaN"], Imperial: ["decent"],
      "Bounty Hunters": ["nqz"], Kaleido: ["kiR"], "Rare Atom": ["Kaze"],
      "The Huns": ["Cozen"] }, [{ date: at(4), teamA: "HEROIC", teamB: "Imperial" }]) },
    props: board(["cadiaN|HEROIC", "decent|Imperial", "nqz|Bounty Hunters", "kiR|Kaleido",
      "Kaze|Rare Atom", "Cozen|The Huns"].map((pair) =>
      line(pair.split("|")[0], pair.split("|")[1], at(4), "CS2", 2)), NOW, "cs2") },

  { name: "shared handle, board names one", game: "cs2",
    regions: { CS2: region({ paiN: ["tatu"], "paiN Academy": ["tatu"] }) },
    props: board([line("tatu", "paiN", at(3), "CS2", 2)], NOW, "cs2") },

  { name: "shared handle, board names neither", game: "cs2",
    regions: { CS2: region({ paiN: ["tatu"], "paiN Academy": ["tatu"] }) },
    props: board([line("tatu", "Furia", at(3), "CS2", 2)], NOW, "cs2") },

  { name: "bare-dated fixture covers", game: "valorant",
    regions: { "VCT Champions": region({ "Paper Rex": ["Jinggg"], "G2 Esports": ["leaf"] },
      [{ date: "2026-09-26", teamA: "G2 Esports", teamB: "Paper Rex" }]),
      "VCT Pacific": region({ "Paper Rex": ["Jinggg"] }) },
    props: board([line("Jinggg", "Paper Rex", "2026-09-26T09:00:00Z", "VCT Pacific", 2)],
      NOW, "valorant") },

  { name: "stale board", game: "lol",
    regions: { TCL: region({ "SU Esports": ["Zeitnot"] }) },
    props: board([line("Zeitnot", "SU Esports", at(3), "TCL", 1)],
      new Date(now.getTime() - (app.MAX_BOARD_AGE_DAYS + 1) * 86400000).toISOString()) },

  { name: "kickoff long gone", game: "lol",
    regions: { TCL: region({ "SU Esports": ["Zeitnot"] }) },
    props: board([line("Zeitnot", "SU Esports", at(-(app.BOARD_MAX_AGE_HOURS + 1)), "TCL", 1)]) },

  { name: "kickoff under way", game: "lol",
    regions: { TCL: region({ "SU Esports": ["Zeitnot"] }) },
    props: board([line("Zeitnot", "SU Esports", at(-1), "TCL", 1)]) },

  { name: "unrostered handle", game: "lol",
    regions: { LCS: region({ LYON: ["Bvoy"] }) },
    props: board([line("Faker", "T1", at(6))]) },

  { name: "undated line", game: "lol",
    regions: { LCS: region({ LYON: ["Bvoy"] }) },
    props: board([{ ...line("Bvoy", "LYON", at(6)), start_time: null }]) },

  // new Date("Sep 26") is a valid date in 2001 and Python's fromisoformat
  // refuses it; both sides must refuse it, or one reads a display string as
  // a timestamp eight hundred years out.
  { name: "display string is not a date", game: "lol",
    regions: { LCS: region({ LYON: ["Bvoy"] }, [{ date: "Sep 26", teamA: "LYON", teamB: "TBD" }]) },
    props: board([line("Bvoy", "LYON", at(6))]) },

  /* And a non-ISO date new Date() reads happily, which is the version that
     changes the answer: loosely parsed it covers the slot and nothing is
     added, strictly parsed it does not. "Sep 26" above does not discriminate,
     because parseStamp's own space-to-T rewrite mangles it into something
     new Date() also rejects -- the guard has to be tested against a string
     that survives the rest of the function. */
  { name: "a date in another format", game: "lol",
    regions: { LCS: region({ LYON: ["Bvoy"] },
      [{ date: "2026/09/26", teamA: "LYON", teamB: "TBD" }]) },
    props: board([line("Bvoy", "LYON", at(6))]) },

  // A naive stamp. Read as the viewer's local time rather than UTC this is
  // hours from the board and the fixture stops covering it.
  { name: "naive fixture stamp", game: "lol",
    regions: { LCS: region({ LYON: ["Bvoy"], Disguised: ["Yeon"] },
      [{ date: at(6).replace("Z", ""), teamA: "LYON", teamB: "Disguised" }]) },
    props: board([line("Bvoy", "LYON", at(6)), line("Yeon", "Disguised", at(6))]) },

  // Two roster entries differing only in case: nothing can choose between
  // them, so naming one is a coin flip.
  { name: "roster entries differ in case", game: "cs2",
    regions: { CS2: region({ Nemesis: ["tex1y"], NEMESIS: ["tex1y"] }) },
    props: board([line("tex1y", "nemesis", at(3), "CS2", 2)], NOW, "cs2") },

  /* The board names the players' home region and the same roster is also
     listed under the event. Without the scoping the fixture lands in the
     wrong region -- there is no covering fixture here to hide it. */
  { name: "home region, no cover", game: "valorant",
    regions: { "VCT Champions": region({ "Paper Rex": ["Jinggg"] }),
               "VCT Pacific": region({ "Paper Rex": ["Jinggg"] }) },
    props: board([line("Jinggg", "Paper Rex", at(6), "VCT Pacific", 2)], NOW, "valorant") },

  /* A league this game has no region for, so the row is judged on the roster
     alone and both regions raise the same kickoff. One game, one fixture:
     the second pass has to see what the first just added. */
  { name: "unknown league, two rosters", game: "valorant",
    regions: { "VCT Champions": region({ "Paper Rex": ["Jinggg"] }),
               "VCT Pacific": region({ "Paper Rex": ["Jinggg"] }) },
    props: board([line("Jinggg", "Paper Rex", at(6), "VAL", 2)], NOW, "valorant") },

  // A bare-dated fixture on another day must not cover the slot.
  { name: "bare date, wrong day", game: "valorant",
    regions: { "VCT Champions": region({ "Paper Rex": ["Jinggg"], "G2 Esports": ["leaf"] },
      [{ date: "2026-09-30", teamA: "G2 Esports", teamB: "Paper Rex" }]) },
    props: board([line("Jinggg", "Paper Rex", "2026-09-26T09:00:00Z", "VCT Champions", 2)],
      NOW, "valorant") },
];

/* ---------- run the JS side ---------- */

let failures = 0;
const handoff = { now: NOW, root, cases: [] };
for (const c of cases) {
  const regions = c.dataFile
    ? JSON.parse(fs.readFileSync(path.join(root, c.dataFile), "utf8")).regions || {}
    : JSON.parse(JSON.stringify(c.regions));
  const props = c.propsFile
    ? JSON.parse(fs.readFileSync(path.join(root, c.propsFile), "utf8"))
    : c.props;

  const out = app.withBoardFixtures(regions, props, c.game, now);
  const result = {};
  for (const [key, data] of Object.entries(out)) result[key] = data.upcoming_matches || [];

  /* Idempotence, checked here rather than in a test of its own: the workflow
     writes its inferred fixtures into the file this then reads, so a second
     pass that added anything would double every fixture the pipeline found. */
  const again = app.withBoardFixtures(out, props, c.game, now);
  const secondPass = {};
  for (const [key, data] of Object.entries(again)) secondPass[key] = data.upcoming_matches || [];
  if (JSON.stringify(secondPass) !== JSON.stringify(result)) {
    console.error(`FAIL  ${c.name}: a second pass changed the fixture list — not idempotent`);
    failures++;
  }
  if (again !== out) {
    console.error(`FAIL  ${c.name}: a second pass returned a new object with nothing to add`);
    failures++;
  }

  handoff.cases.push({ ...c, regions: undefined, props: c.propsFile ? undefined : c.props,
                       result });
}

const produced = handoff.cases.reduce((n, c) =>
  n + Object.values(c.result).reduce((m, list) => m + list.length, 0), 0);
if (!produced) {
  console.error("FAIL  the JS side produced no fixtures at all — nothing was compared");
  process.exit(1);
}

// The synthetic regions have to survive the handoff. They were stripped above
// so the 7MB real files are named rather than copied; the inline ones go back.
for (let i = 0; i < handoff.cases.length; i++) {
  if (!cases[i].dataFile) handoff.cases[i].regions = cases[i].regions;
}

/* A case that reached the Python side with no inputs would have it compare
   two empty answers and pass. Checked after the restore above, because
   getting THAT order wrong is exactly how a parity test goes quietly
   vacuous. */
for (const c of handoff.cases) {
  if (!c.dataFile && !c.regions) {
    console.error(`FAIL  ${c.name}: synthetic case lost its regions in the handoff`);
    failures++;
  }
  if (!c.propsFile && !c.props) {
    console.error(`FAIL  ${c.name}: synthetic case lost its board in the handoff`);
    failures++;
  }
}

const dir = fs.mkdtempSync(path.join(os.tmpdir(), "board-parity-"));
const out = path.join(dir, "js.json");
fs.writeFileSync(out, JSON.stringify(handoff));

const py = spawnSync("python3", [path.join(root, "scripts/dev/check_board_parity.py"), out],
                     { encoding: "utf8", cwd: root });
process.stdout.write(py.stdout || "");
if (py.status === null) {
  console.error(`SKIP  could not run python3 (${py.error && py.error.message}) — parity NOT checked`);
  process.exit(failures ? 1 : 0);
}
if (py.stderr) process.stderr.write(py.stderr);
process.exit((py.status === 0 && !failures) ? 0 : 1);

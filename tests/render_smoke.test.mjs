/* Does the page render, or does it go blank?
 *
 * Every other frontend test here exercises pure functions. That leaves the
 * single worst failure class completely uncovered: an exception thrown
 * inside JSX. React unmounts the whole tree on a render throw, so a missing
 * property does not degrade one number — it blanks the page. That is what
 * happened when a match card printed a team name this app had never
 * scraped, and nothing in a green suite could see it.
 *
 * So this mounts the real components out of src/app.jsx, against data
 * chosen to be hostile in the ways real data actually is: fixtures naming
 * unrostered teams, players with no history, empty rosters, absent props.
 *
 * Run: node tests/render_smoke.test.mjs
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { createRequire } from "module";

const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const { transformSync } = require("@babel/core");

/* The file is a `try { ... }` block that ends by mounting itself into the
   DOM. Take everything above that bootstrap and hand back the components
   instead, so they can be mounted here one at a time. */
const raw = fs.readFileSync(path.join(root, "src/app.jsx"), "utf8");
const bootstrap = raw.indexOf("const root = ReactDOM.createRoot");
if (bootstrap < 0) throw new Error("bootstrap not found — has src/app.jsx changed shape?");
const body = raw.slice(raw.indexOf("try {") + "try {".length, bootstrap);

const EXPORTS = [
  "KillProjector", "FutureMatchCard", "EdgesTab", "RecordTab", "PropReadout",
  "MatchPlayerRow", "HeadToHeadCard", "AccuracySummary", "ThemeContext",
  "PropsContext", "BASE_TOKENS", "GAME_ACCENTS",
  "FutureTab", "PastResultsTab", "ConsistencyTab", "StandingsTab",
  "ProjectionDetail", "historyPool", "project", "ScoreBar",
];
const available = EXPORTS.filter((name) =>
  new RegExp(`(function|const)\\s+${name}\\b`).test(body));

const { code } = transformSync(
  `${body}\nreturn { ${available.join(", ")} };`,
  { presets: [["@babel/preset-react", { runtime: "classic" }]], filename: "app.jsx",
    // The slice ends in a `return`, which is illegal at the top level of a
    // Program but is exactly what `new Function` wants around it.
    parserOpts: { allowReturnOutsideFunction: true } }
);

// Browser globals the module touches at definition time or first render.
const store = {};
const fakeWindow = {
  innerWidth: 1400,
  localStorage: { getItem: (k) => (k in store ? store[k] : null),
                  setItem: (k, v) => { store[k] = String(v); },
                  removeItem: (k) => { delete store[k]; } },
  addEventListener() {}, removeEventListener() {},
};
const fakeDocument = { getElementById: () => null };
const neverResolves = () => new Promise(() => {});

const app = new Function(
  "React", "ReactDOM", "window", "document", "fetch", "localStorage", "console",
  code
)(React, { createRoot: () => ({ render() {} }) }, fakeWindow, fakeDocument,
  neverResolves, fakeWindow.localStorage, console);

let pass = 0, fail = 0;
function renders(label, element) {
  try {
    const html = renderToStaticMarkup(element);
    if (typeof html !== "string") throw new Error("no markup");
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  ${label}\n        ${err.message}`);
  }
}

/* ---- fixtures, deliberately awkward ---- */
/* Built exactly as KillProjector builds it, so the components get the
   shape they actually receive rather than a stand-in that might be more
   forgiving than the real thing. */
const accents = app.GAME_ACCENTS.lol;
const theme = { ...app.BASE_TOKENS, accent: accents.accent,
                accentSoft: accents.accentSoft, accentBorder: accents.accentBorder,
                cornerStyle: "angular" };
const player = (name, k = 4) => ({ name, role: "MID", cur: { k, d: 2, a: 5, g: 12 }, hist: { k, d: 2, a: 5 } });
const rostered = { color: "#e0c341", players: [player("Faker"), player("Zeus", 3)] };
const weights = { history: 0.3, career: 0.2, opponent: 1, kp: 0.5, recencyHalfLife: 4, patchDiscount: 0.2 };

function wrap(children, props) {
  const el = app.ThemeContext
    ? React.createElement(app.ThemeContext.Provider, { value: theme }, children)
    : children;
  return app.PropsContext
    ? React.createElement(app.PropsContext.Provider, { value: props ?? null }, el)
    : el;
}

const propsData = {
  fetched_at: new Date().toISOString(), source: "test",
  props: { lol: { Faker: [{ player: "Faker", stat: "kills", maps: 2, line: 8.5,
                            odds_type: "standard", team: "T1",
                            start_time: "2026-09-21T10:00:00+00:00" }] } },
};

const card = (teams, match, props) => wrap(React.createElement(app.FutureMatchCard, {
  teams, pastMatches: [], match, weights, statType: "kills",
  isDesktop: true, games: 2, game: "lol",
}), props);

const fixture = (a, b) => ({ teamA: a, teamB: b, date: "Sep 21", time: "5:00 AM",
                             _sortKey: "2026-09-21T10:00:00+00:00" });

if (app.FutureMatchCard) {
  renders("both teams rostered",
    card({ T1: rostered, GEN: rostered }, fixture("T1", "GEN"), propsData));
  // The one that blanked the live page.
  renders("opponent never scraped",
    card({ T1: rostered }, fixture("T1", "Never Scraped"), propsData));
  renders("our side never scraped",
    card({ GEN: rostered }, fixture("Never Scraped", "GEN"), propsData));
  renders("neither side rostered",
    card({}, fixture("A", "B"), propsData));
  renders("no props loaded at all",
    card({ T1: rostered, GEN: rostered }, fixture("T1", "GEN"), null));
  renders("props loaded but stale",
    card({ T1: rostered, GEN: rostered }, fixture("T1", "GEN"),
         { ...propsData, fetched_at: "2020-01-01T00:00:00Z" }));
  renders("a team with an empty roster",
    card({ T1: { color: "#fff", players: [] }, GEN: rostered }, fixture("T1", "GEN"), propsData));
  renders("a team with no colour",
    card({ T1: { players: [player("Faker")] }, GEN: rostered }, fixture("T1", "GEN"), propsData));
  renders("a fixture with no date at all",
    card({ T1: rostered, GEN: rostered }, { teamA: "T1", teamB: "GEN" }, propsData));
  renders("several lines posted on one player",
    card({ T1: rostered, GEN: rostered }, fixture("T1", "GEN"), {
      ...propsData,
      props: { lol: { Faker: [
        { player: "Faker", stat: "kills", maps: 2, line: 10.5, team: "T1", start_time: "2026-09-21T10:00:00+00:00" },
        { player: "Faker", stat: "kills", maps: 2, line: 6.5, team: "T1", start_time: "2026-09-21T10:00:00+00:00" }] } },
    }));
}

if (app.EdgesTab) {
  const regionsData = { R: { teams: { T1: rostered, GEN: rostered }, past_matches: [],
                             upcoming_matches: [fixture("T1", "GEN")] } };
  renders("edges with lines", wrap(React.createElement(app.EdgesTab, {
    regionsData, regionList: ["R"], regionLabels: {}, weights, statType: "kills",
    game: "lol", isDesktop: true }), propsData));
  renders("edges with no props at all", wrap(React.createElement(app.EdgesTab, {
    regionsData, regionList: ["R"], regionLabels: {}, weights, statType: "kills",
    game: "lol", isDesktop: true }), null));
  renders("edges where a fixture's opponent is unscraped", wrap(React.createElement(app.EdgesTab, {
    regionsData: { R: { teams: { T1: rostered }, past_matches: [],
                        upcoming_matches: [fixture("T1", "Never Scraped")] } },
    regionList: ["R"], regionLabels: {}, weights, statType: "kills",
    game: "lol", isDesktop: true }), propsData));
  renders("edges with an empty region", wrap(React.createElement(app.EdgesTab, {
    regionsData: {}, regionList: ["R"], regionLabels: {}, weights, statType: "kills",
    game: "lol", isDesktop: true }), propsData));
}

/* A not-yet-started international event: rosters lent from the teams' home
   regions, and no past matches at all. Champions arrives in exactly this
   shape, and it is one no region has ever had before — every other region
   carries a season of results behind it. Anything that assumes at least one
   completed match blanks the page the day that event goes live. */
const eventShaped = { Champions: {
  teams: { T1: rostered, "Paper Rex": rostered },
  past_matches: [],
  upcoming_matches: [fixture("T1", "Paper Rex")],
  rosters_from_home_regions: true,
} };

if (app.EdgesTab) {
  renders("a not-yet-started event, no past matches", wrap(React.createElement(app.EdgesTab, {
    regionsData: eventShaped, regionList: ["Champions"], regionLabels: {},
    weights, statType: "kills", game: "valorant", isDesktop: true }), propsData));
}
renders("a card in an event with no completed matches",
  wrap(React.createElement(app.FutureMatchCard, {
    teams: eventShaped.Champions.teams, pastMatches: [],
    match: fixture("T1", "Paper Rex"), weights, statType: "kills",
    isDesktop: true, games: 2, game: "valorant" }), propsData));

if (app.AccuracySummary) {
  renders("the accuracy headline with nothing to backtest against",
    wrap(React.createElement(app.AccuracySummary, {
      teams: eventShaped.Champions.teams, pastMatches: [], weights,
      statType: "kills", isDesktop: true }), propsData));
}

if (app.HeadToHeadCard) {
  renders("head-to-head between two teams that have never met",
    wrap(React.createElement(app.HeadToHeadCard, {
      teams: eventShaped.Champions.teams, pastMatches: [],
      teamA: "T1", teamB: "Paper Rex", bare: true }), propsData));
}

/* Every tab, against the event shape. A region with rosters and no results
   is selectable in all of them the day Champions goes live, and a throw in
   any one takes the page down just as completely as a throw in the card. */
const tabProps = {
  teams: eventShaped.Champions.teams,
  pastMatches: [],
  upcomingMatches: eventShaped.Champions.upcoming_matches,
  regionsData: eventShaped, regionList: ["Champions"],
  regionLabels: { Champions: "Champions" },
  weights, statType: "kills", isDesktop: true, games: 2, game: "valorant",
};
for (const name of ["FutureTab", "PastResultsTab", "ConsistencyTab", "StandingsTab"]) {
  if (app[name]) renders(`${name} with rosters but no results`,
    wrap(React.createElement(app[name], tabProps), propsData));
  if (app[name]) renders(`${name} with nothing at all`,
    wrap(React.createElement(app[name], {
      ...tabProps, teams: {}, upcomingMatches: [], regionsData: {} }), null));
}

if (app.RecordTab) {
  renders("record before anything is graded", wrap(React.createElement(app.RecordTab, {
    regionsData: { R: { teams: { T1: rostered }, past_matches: [] } },
    regionList: ["R"], weights, statType: "kills", isDesktop: true }), propsData));
}

/* ---- the expanded card in a borrowed-roster event ----
 *
 * Everything above asks only "does it throw". That is the right question
 * for a blank page and the wrong one here: before the history pool
 * existed, the Champions card rendered perfectly and simply had no form
 * chart in it, which no mount test would ever have noticed. So this one
 * asserts on the markup.
 */
function containsText(label, element, needle, shouldContain = true) {
  try {
    const html = renderToStaticMarkup(element);
    const has = html.includes(needle);
    if (has !== shouldContain) throw new Error(
      `expected markup ${shouldContain ? "to contain" : "not to contain"} ${JSON.stringify(needle)}`);
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  ${label}\n        ${err.message}`);
  }
}

if (app.ProjectionDetail && app.historyPool && app.project) {
  const series = (date, teamA, teamB, kills) => ({
    date, teamA, teamB, maps_counted: 2,
    actual: { [teamA]: { Faker: { k: kills, d: 2, a: 5 } }, [teamB]: {} },
  });
  const home = Array.from({ length: 8 }, (_, i) =>
    series(`2026-0${i + 1}-01`, "T1", "Rival", 20 + i));
  const world = {
    Pacific: { teams: { T1: rostered }, past_matches: home, upcoming_matches: [] },
    Champions: { teams: { T1: rostered, "Paper Rex": rostered },
                 past_matches: [], upcoming_matches: [],
                 rosters_from_home_regions: true },
  };
  const pool = app.historyPool(world, "Champions");
  const detail = (pastMatches) => {
    const player = rostered.players[0];
    const r = app.project(world.Champions.teams, pastMatches, player, "T1",
                          "Paper Rex", 2, weights, "kills");
    return wrap(React.createElement(app.ProjectionDetail, {
      r, p: player, cfg: { key: "k", label: "Kills", singular: "kill", useKP: true },
      games: 2, pastMatches, team: "T1" }), propsData);
  };

  containsText("the expanded card shows the last 8 matches from the home region",
               detail(pool), "Last 8 matches");
  containsText("which is exactly what the region's own empty list cannot do",
               detail(world.Champions.past_matches), "Last 8 matches", false);
  containsText("and a region with its own history is unaffected",
               detail(app.historyPool(world, "Pacific")), "Last 8 matches");
}

/* ---- a stat the player has no season rate for ----
 *
 * This blanked the page in production. A CS2 player can have headshots in
 * their match history and no headshot rate in p.cur, because the two are
 * built separately and a stat can postdate the aggregate — 134 of 271
 * players were in exactly that state the day headshots shipped. The
 * projection worked, because the model reads past_matches; ScoreBar then
 * called .toFixed on the missing season rate and took the whole tree
 * down. React unmounts on a render throw, so the expanded card did not
 * lose a row, it lost everything.
 *
 * The existing mounts here all hand every component a complete player.
 * That is the gap: real data is complete until the day a new field
 * arrives, and then it is complete for some players and not others.
 */
if (app.ProjectionDetail && app.project) {
  const hsCfg = { key: "hs", label: "Headshots", singular: "headshot", oppBasis: "d", useKP: false };
  const weights = { history: 0, opponent: 0, kp: 0, recencyHalfLife: 20,
                    patchDiscount: 0, career: 0, share: 0, shrink: 3 };
  const series = (date, hs) => ({
    date, teamA: "A", teamB: "B", maps_counted: 2,
    actual: { A: { Ghost: { k: 20, d: 15, a: 5, hs } }, B: {} } });
  const history = [series("2026-01-01", 9), series("2026-02-01", 11)];

  // The exact production shape: history has the stat, p.cur does not.
  const noSeasonRate = { name: "Ghost", role: null,
                         cur: { g: 10, k: 20, d: 15, a: 5, kp: 26 }, hist: null };
  const teams = { A: { players: [noSeasonRate] }, B: { players: [] } };
  const r = app.project(teams, history, noSeasonRate, "A", "B", 2, weights, "headshots");
  renders("expanded card for a stat with history but no season rate",
    wrap(React.createElement(app.ProjectionDetail, {
      r, p: noSeasonRate, cfg: hsCfg, games: 2, pastMatches: history, team: "A" }), propsData));

  // And the degenerate versions of the same thing.
  renders("expanded card for a player with no cur at all",
    wrap(React.createElement(app.ProjectionDetail, {
      r, p: { name: "Ghost", role: null, hist: null }, cfg: hsCfg, games: 2,
      pastMatches: history, team: "A" }), propsData));
  renders("expanded card with no history to chart either",
    wrap(React.createElement(app.ProjectionDetail, {
      r, p: noSeasonRate, cfg: hsCfg, games: 2, pastMatches: [], team: "A" }), propsData));
}

if (app.ScoreBar) {
  /* Asserted on the markup, not just mounted: the bug was a value that
     should not have been printed, and "it rendered" is exactly what a
     crash-free wrong answer looks like. */
  const bar = (value) => renderToStaticMarkup(
    React.createElement(app.ThemeContext.Provider, { value: theme },
      React.createElement(app.ScoreBar, { label: "Season average", value, max: 10, unit: "hs/g", color: "#fff" })));
  for (const [label, value] of [["undefined", undefined], ["null", null],
                                ["NaN", NaN], ["Infinity", Infinity], ["a string", "7"]]) {
    try {
      const html = bar(value);
      if (html !== "") throw new Error(`rendered ${JSON.stringify(html.slice(0, 60))} instead of nothing`);
      pass++;
    } catch (err) {
      fail++;
      console.error(`FAIL  ScoreBar draws nothing for ${label}\n        ${err.message}`);
    }
  }
  try {
    const html = bar(4.25);
    if (!html.includes("4.3")) throw new Error(`a real value must still print, got ${html.slice(0, 80)}`);
    pass++;
  } catch (err) { fail++; console.error(`FAIL  ScoreBar still draws a real value\n        ${err.message}`); }
}

console.log(`${pass} rendered, ${fail} failed`);
process.exit(fail ? 1 : 0);

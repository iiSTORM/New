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
  "ProjectionDetail", "historyPool", "project", "ScoreBar", "collectEdges",
  "EvidenceChip", "evidenceTier",
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

/* A second instance of the same module whose collapsed panels start open.
   The expandable rows keep `open` in local state, and renderToStaticMarkup
   does no interaction, so the markup a user actually reads -- the
   breakdown behind a projection -- was rendered by nothing here. Rather
   than add a DOM and a click, hand the module factory a React whose
   useState(false) answers true; app.jsx destructures useState from that
   object at line 3, so every `const [open, setOpen] = useState(false)`
   in the tree opens at once.

   Only a literal `false` is intercepted. Other state passes straight
   through, so a panel holding an object or a string is untouched and this
   cannot quietly rewrite state it was not aimed at. */
const openReact = { ...React, useState: (init) => (init === false ? [true, () => {}] : React.useState(init)) };
const appOpen = new Function(
  "React", "ReactDOM", "window", "document", "fetch", "localStorage", "console",
  code
)(openReact, { createRoot: () => ({ render() {} }) }, fakeWindow, fakeDocument,
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

function wrapWith(mod, children, props) {
  const el = mod.ThemeContext
    ? React.createElement(mod.ThemeContext.Provider, { value: theme }, children)
    : children;
  return mod.PropsContext
    ? React.createElement(mod.PropsContext.Provider, { value: props ?? null }, el)
    : el;
}

const wrap = (children, props) => wrapWith(app, children, props);
// appOpen is a separate instance of the module, so it has its own
// createContext identities. Providing app's contexts to appOpen's
// components leaves them reading the default -- null -- and the first
// theme lookup throws.
const wrapOpen = (children, props) => wrapWith(appOpen, children, props);

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


/* ---- the Edges rows expand, like the match cards do ----
 *
 * The Edges tab listed a projection and a line and gave no way to see why
 * the projection said what it did, while the identical number on a match
 * card opened into a full breakdown. Same component now sits behind both.
 *
 * Two things are worth asserting beyond "it mounted". First that
 * collectEdges actually carries the player object and the history pool on
 * each row -- the panel is fed entirely from those, and dropping either
 * would leave the row opening onto nothing. Second that the panel
 * describes the LINE's map window: collectEdges projects over prop.maps,
 * so a detail panel handed any other number would print per-game maths
 * that does not multiply out to the projection printed directly above it.
 */
if (app.collectEdges && appOpen.EdgesTab) {
  const kills = (date, k) => ({
    date, teamA: "T1", teamB: "GEN", maps_counted: 2,
    actual: { T1: { Faker: { k, d: 2, a: 5 } }, GEN: {} } });
  const history = Array.from({ length: 8 }, (_, i) => kills(`2026-0${i + 1}-01`, 20 + i));
  const regionsData = { R: { teams: { T1: rostered, GEN: rostered }, past_matches: history,
                             upcoming_matches: [fixture("T1", "GEN")] } };
  // maps: 3 so the window is distinguishable from every other 2 in scope.
  const threeMap = {
    fetched_at: new Date().toISOString(), source: "test",
    props: { lol: { Faker: [{ player: "Faker", stat: "kills", maps: 3, line: 24.5,
                              odds_type: "standard", team: "T1",
                              start_time: "2026-09-21T10:00:00+00:00" }] } },
  };
  const rows = app.collectEdges(regionsData, ["R"], threeMap, weights, "kills", "lol");

  try {
    if (!rows.length) throw new Error("no edge rows built — fixture is wrong, not the code");
    const row = rows[0];
    for (const key of ["player", "pastMatches", "breakdown"]) {
      if (row[key] == null) throw new Error(`row is missing ${key}, so an expanded row has nothing to draw`);
    }
    if (row.player.name !== "Faker") throw new Error("row.player is not the player object");
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  an edge row carries what its expanded panel needs\n        ${err.message}`);
  }

  const openTab = (props) => wrapOpen(React.createElement(appOpen.EdgesTab, {
    regionsData, regionList: ["R"], regionLabels: {}, weights, statType: "kills",
    game: "lol", isDesktop: true }), props);

  containsText("an expanded edge row shows the projection breakdown",
               openTab(threeMap), "Recent form");
  containsText("and charts the player's history, same as the match card",
               openTab(threeMap), "Last 8 matches");
  containsText("and describes the line's map window, not some other one",
               openTab(threeMap), "× 3g");
  containsText("a collapsed row shows none of it",
               wrap(React.createElement(app.EdgesTab, {
                 regionsData, regionList: ["R"], regionLabels: {}, weights,
                 statType: "kills", game: "lol", isDesktop: true }), threeMap),
               "Recent form", false);
}

/* The headshots crash again, reached the way a user reached it: not by
   mounting ProjectionDetail directly with hand-built props, but by
   opening a row on the Edges board. A CS2 player with headshots in
   history and none in p.cur took the page down here too. */
if (appOpen.EdgesTab) {
  const hsWeights = { history: 0, opponent: 0, kp: 0, recencyHalfLife: 20,
                      patchDiscount: 0, career: 0, share: 0, shrink: 3 };
  const series = (date, hs) => ({
    date, teamA: "A", teamB: "B", maps_counted: 2,
    actual: { A: { Ghost: { k: 20, d: 15, a: 5, hs } }, B: {} } });
  const noSeasonRate = { name: "Ghost", role: null,
                         cur: { g: 10, k: 20, d: 15, a: 5, kp: 26 }, hist: null };
  const regionsData = { R: {
    teams: { A: { color: "#fff", players: [noSeasonRate] }, B: { color: "#fff", players: [] } },
    past_matches: [series("2026-01-01", 9), series("2026-02-01", 11)],
    upcoming_matches: [{ teamA: "A", teamB: "B", date: "Sep 21", time: "5:00 AM",
                         _sortKey: "2026-09-21T10:00:00+00:00" }] } };
  const hsProps = {
    fetched_at: new Date().toISOString(), source: "test",
    props: { cs2: { Ghost: [{ player: "Ghost", stat: "headshots", maps: 2, line: 18.5,
                              odds_type: "standard", team: "A",
                              start_time: "2026-09-21T10:00:00+00:00" }] } },
  };
  renders("an expanded edge row for a stat the player has no season rate for",
    wrapOpen(React.createElement(appOpen.EdgesTab, {
      regionsData, regionList: ["R"], regionLabels: {}, weights: hsWeights,
      statType: "headshots", game: "cs2", isDesktop: true }), hsProps));
}


/* The match card's own row, which EdgeRow was modelled on. Mutating its
   `open &&` away was caught by nothing before this.

   Mounted directly rather than through FutureMatchCard, and that is the
   whole point: the card body sits behind the CARD's own `open`, so a
   collapsed card renders no rows at all and an assertion made through it
   passes whatever the row does. Only the row in isolation can show that
   its own collapse is what hides the breakdown. */
if (appOpen.MatchPlayerRow && app.MatchPlayerRow) {
  const withHistory = Array.from({ length: 8 }, (_, i) => ({
    date: `2026-0${i + 1}-01`, teamA: "T1", teamB: "GEN", maps_counted: 2,
    actual: { T1: { Faker: { k: 20 + i, d: 2, a: 5 } }, GEN: {} } }));
  const p0 = rostered.players[0];
  const cfg = { key: "k", label: "Kills", singular: "kill", useKP: true };
  const r = app.project({ T1: rostered, GEN: rostered }, withHistory, p0, "T1",
                        "GEN", 2, weights, "kills");
  const rowIn = (mod, wrapper) => wrapper(React.createElement(mod.MatchPlayerRow, {
    theme, teamColor: "#e0c341", name: p0.name, role: p0.role, stats: [],
    r, p: p0, cfg, games: 2, pastMatches: withHistory, team: "T1" }), propsData);

  containsText("an expanded match-card row shows the breakdown",
               rowIn(appOpen, wrapOpen), "Recent form");
  containsText("and a collapsed one does not",
               rowIn(app, wrap), "Recent form", false);
}
/* ---- the evidence marker ----
 *
 * The product is sold on these projections, so the difference between a
 * number off three games and one off forty has to be visible. These
 * assert the marker appears exactly where it should and, just as
 * importantly, stays quiet where it should -- a badge on every row is
 * wallpaper, and wallpaper cannot warn anyone.
 */
if (app.EvidenceChip) {
  const chip = (games) => renderToStaticMarkup(
    React.createElement(app.ThemeContext.Provider, { value: theme },
      React.createElement(app.EvidenceChip, { games, compact: true })));

  containsText("a thin projection is marked with its actual count",
               React.createElement(app.ThemeContext.Provider, { value: theme },
                 React.createElement(app.EvidenceChip, { games: 3, compact: true })), "3g");
  try {
    if (chip(40) !== "") throw new Error(`drew ${JSON.stringify(chip(40))} on a solid projection`);
    pass++;
  } catch (err) { fail++; console.error(`FAIL  a solid projection draws no chip\n        ${err.message}`); }
  for (const [label, value] of [["undefined", undefined], ["null", null], ["NaN", NaN]]) {
    try {
      if (chip(value) !== "") throw new Error("drew something for " + label);
      pass++;
    } catch (err) { fail++; console.error(`FAIL  no chip for ${label}\n        ${err.message}`); }
  }
}

/* On the board itself, which is the surface that matters: the rows are
   ranked by edge SIZE, so a big edge off little evidence sorts straight
   to the top. That is exactly the row a subscriber must not mistake for
   the strongest bet on the screen. */
if (app.collectEdges && appOpen.EdgesTab && app.EdgesTab) {
  const kills = (date, k) => ({
    date, teamA: "T1", teamB: "GEN", maps_counted: 2,
    actual: { T1: { Faker: { k, d: 2, a: 5 } }, GEN: {} } });
  const thinPlayer = { name: "Faker", role: "MID", cur: { k: 4, d: 2, a: 5, g: 12 }, hist: null };
  const thinTeam = { color: "#e0c341", players: [thinPlayer] };
  // One match of maps_counted 2 is two games of evidence, which is below
  // the measured four-game line. Two matches would be four and read as
  // "limited" -- an earlier draft of this asserted "2g" against exactly
  // that and failed, which is the arithmetic worth pinning down here.
  const oneMatch = [kills("2026-01-01", 20)];
  const twoMatches = [kills("2026-01-01", 20), kills("2026-02-01", 22)];
  const boardOver = (past) => ({ R: { teams: { T1: thinTeam, GEN: rostered },
                                      past_matches: past,
                                      upcoming_matches: [fixture("T1", "GEN")] } });
  const board = (mod, wrapper, past) => wrapper(React.createElement(mod.EdgesTab, {
    regionsData: boardOver(past), regionList: ["R"], regionLabels: {}, weights,
    statType: "kills", game: "lol", isDesktop: true }), propsData);

  containsText("a thin row on the board carries its evidence count",
               board(app, wrap, oneMatch), "2g");
  containsText("a limited row does too, with its own count",
               board(app, wrap, twoMatches), "4g");
  containsText("the expanded panel spells out what that means",
               board(appOpen, wrapOpen, oneMatch), "games of evidence");
  containsText("and says plainly that a thin one is a weaker read",
               board(appOpen, wrapOpen, oneMatch), "weaker read");
  containsText("while a limited one is described as around average, not weak",
               board(appOpen, wrapOpen, twoMatches), "around the model", false);
}

/* The panel states the evidence on EVERY projection, including strong
   ones -- "solid" only carries information if the same line would have
   said otherwise. */
if (app.ProjectionDetail && app.project) {
  const many = Array.from({ length: 20 }, (_, i) => ({
    date: `2026-${String((i % 12) + 1).padStart(2, "0")}-01`,
    teamA: "T1", teamB: "Rival", maps_counted: 2,
    actual: { T1: { Faker: { k: 20 + (i % 5), d: 2, a: 5 } }, Rival: {} } }));
  const teams = { T1: rostered, Rival: rostered };
  const r = app.project(teams, many, rostered.players[0], "T1", "Rival", 2, weights, "kills");
  const panel = wrap(React.createElement(app.ProjectionDetail, {
    r, p: rostered.players[0], cfg: { key: "k", label: "Kills", singular: "kill", useKP: true },
    games: 2, pastMatches: many, team: "T1" }), propsData);
  containsText("a well-evidenced projection still states its evidence",
               panel, "games of evidence");
  containsText("and says it is in the range the model does better in",
               panel, "more accurate than its own average");
}
/* ---- the board says when it cut an edge ----
 *
 * The board is ranked by the adjusted number, so the figure on the row
 * has to be that same number or the order contradicts the screen. And
 * the raw edge still has to be visible, because it is the one anyone can
 * recompute from the projection and line printed beside it -- a ranking
 * that cannot be checked against them is worth less than one that can.
 */
if (app.collectEdges && app.EdgesTab) {
  const kills = (date, k, who) => ({
    date, teamA: "T1", teamB: "GEN", maps_counted: 2,
    actual: { T1: { [who]: { k, d: 2, a: 5 } }, GEN: {} } });
  const thinP = { name: "Thin", role: "MID", cur: { k: 10, d: 2, a: 5, g: 2 }, hist: null };
  const past = [kills("2026-01-01", 20, "Thin")];
  const regionsData = { R: { teams: { T1: { color: "#e0c341", players: [thinP] }, GEN: rostered },
                             past_matches: past,
                             upcoming_matches: [fixture("T1", "GEN")] } };
  const line = (v) => ({ fetched_at: new Date().toISOString(), source: "test",
    props: { lol: { Thin: [{ player: "Thin", stat: "kills", maps: 2, line: v,
                             odds_type: "standard", team: "T1",
                             start_time: "2026-09-21T10:00:00+00:00" }] } } });
  const tab = (props) => wrap(React.createElement(app.EdgesTab, {
    regionsData, regionList: ["R"], regionLabels: {}, weights, statType: "kills",
    game: "lol", isDesktop: true }), props);

  containsText("the board says it ranks on the adjusted edge",
               tab(line(6)), "ranked by evidence-adjusted edge");
  // The headline figure must BE the adjusted one. Asserting only that
  // the raw value appears somewhere let a mutation printing the raw
  // number as the headline pass: the board would then have been ordered
  // by one figure and labelled with another.
  // 7.3, not 14.0: the projection is calibrated toward the league rate
  // first (two games of evidence, factor 0.52) and the edge taken from
  // there. Scaling the raw edge instead would give 7.3 only by
  // coincidence of this fixture -- the assertions below pin which.
  containsText("the headline figure is the adjusted edge",
               tab(line(6)), "OVER +7.3");
  containsText("and not the raw one it was cut from",
               tab(line(6)), "OVER +14.0", false);
  containsText("a discounted row shows the figure it was cut from",
               tab(line(6)), "from +14.0");
  containsText("and the footer explains why the order is what it is",
               tab(line(6)), "realises about a third of its face value");
}

/* A well-evidenced row is left alone. At twelve games or more the
   multiplier is 0.94, which is not a discount worth interrupting a
   reader for -- flagging on the arithmetic instead of the band marked
   160 of 219 rows on a real board.

   Built carefully after an earlier version passed for two wrong
   reasons at once: it looked for "from +" against a row whose edge was
   negative, and it reused one roster object for BOTH teams, so the
   opponent contributed a second, zero-evidence row that was marked. */
if (app.collectEdges && app.EdgesTab) {
  const many = Array.from({ length: 16 }, (_, i) => ({
    date: `2026-${String((i % 12) + 1).padStart(2, "0")}-01`,
    teamA: "T1", teamB: "GEN", maps_counted: 2,
    actual: { T1: { Faker: { k: 20, d: 2, a: 5 } }, GEN: {} } }));
  const opponent = { color: "#888", players: [{ name: "Oner", role: "JNG",
    cur: { k: 3, d: 2, a: 5, g: 12 }, hist: { k: 3, d: 2, a: 5 } }] };
  const regionsData = { R: { teams: { T1: rostered, GEN: opponent }, past_matches: many,
                             upcoming_matches: [fixture("T1", "GEN")] } };
  // Below the projection, so the edge is positive and a "from +N" would
  // actually appear if the row were being marked.
  const solid = { fetched_at: new Date().toISOString(), source: "test",
    props: { lol: { Faker: [{ player: "Faker", stat: "kills", maps: 2, line: 10.0,
                              odds_type: "standard", team: "T1",
                              start_time: "2026-09-21T10:00:00+00:00" }] } } };
  const board = wrap(React.createElement(app.EdgesTab, {
    regionsData, regionList: ["R"], regionLabels: {}, weights,
    statType: "kills", game: "lol", isDesktop: true }), solid);
  containsText("a well-evidenced row shows an OVER, so it is on the board at all",
               board, "OVER +");
  containsText("and is not marked as cut", board, "from ", false);
}
/* ---- a one-sided board says so ----
 *
 * Real edges scatter: the market is wrong in both directions. When
 * nearly every line reads the same way, the likelier reading is a level
 * disagreement about the fixture, which no per-player accuracy fixes and
 * which a reader cannot see by scrolling.
 */
if (app.EdgesTab && app.collectEdges) {
  const many = Array.from({ length: 16 }, (_, i) => ({
    date: `2026-${String((i % 12) + 1).padStart(2, "0")}-01`,
    teamA: "T1", teamB: "GEN", maps_counted: 2,
    actual: { T1: { Faker: { k: 20, d: 2, a: 5 } }, GEN: { Zeus: { k: 4, d: 2, a: 5 } } } }));
  const region = (extra) => ({ R: { teams: { T1: rostered, GEN: rostered },
    past_matches: many, upcoming_matches: [fixture("T1", "GEN")], ...extra } });
  // Two players, both lined far below their form, so every edge is an over.
  const lopsided = { fetched_at: new Date().toISOString(), source: "test",
    props: { lol: {
      Faker: [{ player: "Faker", stat: "kills", maps: 2, line: 2.0, odds_type: "standard",
                team: "T1", start_time: "2026-09-21T10:00:00+00:00" }],
      Zeus: [{ player: "Zeus", stat: "kills", maps: 2, line: 2.0, odds_type: "standard",
               team: "T1", start_time: "2026-09-21T10:00:00+00:00" }] } } };
  const tab = (extra) => wrap(React.createElement(app.EdgesTab, {
    regionsData: region(extra), regionList: ["R"], regionLabels: {}, weights,
    statType: "kills", game: "lol", isDesktop: true }), lopsided);

  // Only two rows here, under the ten-row floor, so it must stay quiet:
  // a two-row board leaning one way is not evidence of anything.
  containsText("a board too small to judge says nothing about its lean",
               tab({}), "read OVER", false);
}

/* And the borrowed-context chip, which is what a Champions board shows. */
if (app.EvidenceChip) {
  const chip = (games, borrowed) => renderToStaticMarkup(
    React.createElement(app.ThemeContext.Provider, { value: theme },
      React.createElement(app.EvidenceChip, { games, compact: true, borrowed })));
  containsText("a borrowed row is named, not given a misleading count",
               React.createElement(app.ThemeContext.Provider, { value: theme },
                 React.createElement(app.EvidenceChip,
                   { games: app.effectiveEvidence ? 7 : 7, compact: true, borrowed: true })),
               "other event");
  try {
    const html = chip(7, true);
    if (/\d+g/.test(html)) throw new Error("showed a map count for borrowed form");
    pass++;
  } catch (err) {
    fail++; console.error(`FAIL  a borrowed chip does not show a map count\n        ${err.message}`);
  }
}

console.log(`${pass} rendered, ${fail} failed`);
process.exit(fail ? 1 : 0);

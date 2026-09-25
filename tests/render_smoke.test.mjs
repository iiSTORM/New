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
  "EvidenceChip", "evidenceTier", "windowSections", "projectionOverWindow",
  "DataStatus", "oldestRegion", "recordVsLine", "calibration", "clusteredMean",
  "rankingIsInformative", "pointInTimeCS2CareerRate", "offeredStats", "postedLineCounts",
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

/* An UPCOMING fixture, and therefore in the future -- which these
   fixtures stopped being. The timestamp was pinned at 2026-09-21 and the
   suite passed until the day a line's usefulness started depending on
   whether its match had been played; then every row rendered "started"
   and three assertions failed at once, correctly. Relative to now, so a
   fixture that is supposed to be upcoming always is. */
const SOON = new Date(Date.now() + 3 * 3600 * 1000);
const SOON_ISO = SOON.toISOString();
const SOON_DATE = SOON.toLocaleDateString(undefined, { month: "short", day: "numeric" });
const SOON_TIME = SOON.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

const propsData = {
  fetched_at: new Date().toISOString(), source: "test",
  props: { lol: { Faker: [{ player: "Faker", stat: "kills", maps: 2, line: 8.5,
                            odds_type: "standard", team: "T1",
                            start_time: SOON_ISO }] } },
};

const card = (teams, match, props) => wrap(React.createElement(app.FutureMatchCard, {
  teams, pastMatches: [], match, weights, statType: "kills",
  isDesktop: true, games: 2, game: "lol",
}), props);

const fixture = (a, b) => ({ teamA: a, teamB: b, date: SOON_DATE, time: SOON_TIME,
                             _sortKey: SOON_ISO });

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

  /* The match card's own readout, driven directly.
   *
   * It is a different component from the Edges board's row, and it is
   * the one a mutation dropping the age note slipped past -- the card
   * renders collapsed, so nothing inside it reaches the markup and
   * "does it render" could not see the change. */
  if (app.PropReadout) {
    // Freshness computed here rather than pulled off the harness: the
    // export list does not carry propsAreFresh, so reaching for it gave
    // undefined and every case rendered as if the payload were old --
    // which made the fresh case fail for a reason that had nothing to
    // do with the component.
    const PAYLOAD_WINDOW_MINUTES = 90;
    const readout = (prop, fetchedAt) => {
      const ageMinutes = (Date.now() - new Date(fetchedAt).getTime()) / 60000;
      return wrap(React.createElement(app.PropReadout, {
        prop, projection: 11.2,
        fresh: ageMinutes <= PAYLOAD_WINDOW_MINUTES, ageMinutes,
      }), null);
    };

    const upcoming = { line: 8.5, maps: 2, lineCount: 1, start_time: SOON_ISO };
    const sixHoursAgo = new Date(Date.now() - 6 * 3600 * 1000).toISOString();

    containsText("an old payload still prints the line",
                 readout(upcoming, sixHoursAgo), "8.5");
    containsText("and the edge against it", readout(upcoming, sixHoursAgo), "+2.7");
    containsText("and says how old the line is",
                 readout(upcoming, sixHoursAgo), "6h old");
    containsText("a fresh payload says nothing about age",
                 readout(upcoming, new Date().toISOString()), " old", false);

    const kicked = { ...upcoming,
                     start_time: new Date(Date.now() - 3 * 3600 * 1000).toISOString() };
    containsText("a fixture that has kicked off says so instead of an edge",
                 readout(kicked, new Date().toISOString()), "started");
    containsText("and prices nothing",
                 readout(kicked, new Date().toISOString()), "+2.7", false);
  }
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
        { player: "Faker", stat: "kills", maps: 2, line: 10.5, team: "T1", start_time: SOON_ISO },
        { player: "Faker", stat: "kills", maps: 2, line: 6.5, team: "T1", start_time: SOON_ISO }] } },
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
                              start_time: SOON_ISO }] } },
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

/* Two map windows on one fixture, end to end.
 *
 * Map 1 and maps 1-3 are separate markets. They were being pooled into
 * one candidate list, read as two alternate payouts with no market line
 * named, and BOTH edges suppressed -- a failure that shows up on screen
 * as a line with no number beside it, which looks like a quiet day
 * rather than a bug.
 *
 * So this asserts the whole path: two rows out of collectEdges, one per
 * window, each with its own projection; and a board that draws them
 * under their own headings rather than interleaved.
 */
if (app.collectEdges && app.EdgesTab) {
  const kills = (date, k) => ({
    date, teamA: "T1", teamB: "GEN", maps_counted: 2,
    actual: { T1: { Faker: { k, d: 2, a: 5 } }, GEN: {} } });
  const history = Array.from({ length: 8 }, (_, i) => kills(`2026-0${i + 1}-01`, 20 + i));
  // Two distinct rosters on purpose: sharing one puts Faker on both
  // sides of the fixture, which doubles every row and would hide a real
  // duplicate behind a fixture artefact.
  const opposition = { color: "#4488cc", players: [player("Chovy", 5)] };
  const regionsData = { R: { teams: { T1: rostered, GEN: opposition }, past_matches: history,
                             upcoming_matches: [fixture("T1", "GEN")] } };
  const bothWindows = {
    fetched_at: new Date().toISOString(), source: "test",
    props: { lol: { Faker: [
      { player: "Faker", stat: "kills", maps: 1, line: 8.5,
        odds_type: "standard", team: "T1", start_time: SOON_ISO },
      { player: "Faker", stat: "kills", maps: 3, line: 24.5,
        odds_type: "standard", team: "T1", start_time: SOON_ISO }] } },
  };
  const rows = app.collectEdges(regionsData, ["R"], bothWindows, weights, "kills", "lol");

  try {
    const windows = rows.map((r) => r.maps).sort();
    if (windows.join(",") !== "1,3") {
      throw new Error(`got windows [${windows}], wanted both 1 and 3 -- `
        + "a collapsed window never reaches the board at all");
    }
    for (const row of rows) {
      if (row.edge === null) {
        throw new Error(`the ${row.maps}-map row has no edge: the two windows were `
          + "read as alternate payouts on one market");
      }
      // Each window's projection is the per-map rate times its OWN map
      // count. One number serving both would be right for at most one.
      const want = row.breakdown.perGame * row.maps;
      if (Math.abs(row.projection - want) > 1e-9) {
        throw new Error(`the ${row.maps}-map row projects ${row.projection}, not ${want}`);
      }
    }
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  both posted windows become their own priced row\n        ${err.message}`);
  }

  /* Asserted on the helper, not on the board's text. Every EdgeRow
     already prints its own window label, so "the markup says map 1" is
     true whether the board is sectioned or a single undivided list --
     which is exactly the regression this is here to catch. */
  if (app.windowSections) {
    try {
      const sections = app.windowSections(rows);
      const shape = sections.map((s) => `${s.maps}:${s.rows.length}`).join(" ");
      if (shape !== "1:1 3:1") {
        throw new Error(`sections came out as [${shape}], wanted one per window in map order`);
      }
      pass++;
    } catch (err) {
      fail++;
      console.error(`FAIL  the board splits into one section per window\n        ${err.message}`);
    }
  }

  const board = wrap(React.createElement(app.EdgesTab, {
    regionsData, regionList: ["R"], regionLabels: {}, weights,
    statType: "kills", game: "lol", isDesktop: true }), bothWindows);
  containsText("and prints both lines", board, "8.5");
  containsText("including the longer window's", board, "24.5");

  if (appOpen.FutureMatchCard) {
    // appOpen, because a match card renders COLLAPSED: its player rows,
    // and every posted line on them, exist only once the card is open.
    // A test against the collapsed markup passes whatever the rows say.
    const card = wrapOpen(React.createElement(appOpen.FutureMatchCard, {
      match: fixture("T1", "GEN"), teams: { T1: rostered, GEN: opposition },
      pastMatches: history, games: 2, weights, statType: "kills",
      game: "lol", regionsData, regionList: ["R"], isDesktop: true }), bothWindows);
    containsText("a match card shows the map 1 line", card, "8.5");
    containsText("and the maps 1-3 line beside it", card, "24.5");

    /* And each line's EDGE is against its own window's projection.
       Feeding every readout the first window's number leaves both lines
       on screen and both edges wrong, which nothing above would notice:
       the maps 1-3 line would read as a huge under purely because it was
       compared with a one-map projection. */
    if (app.project && app.projectionOverWindow) {
      const faker = rostered.players[0];
      const breakdown = app.project({ T1: rostered, GEN: opposition }, history,
                                    faker, "T1", "GEN", 1, weights, "kills");
      const edgeFor = (line, maps) =>
        app.projectionOverWindow(breakdown, { maps }) - line;
      const oneMap = edgeFor(8.5, 1), threeMap = edgeFor(24.5, 3);
      const fmt = (e) => `${e > 0 ? "+" : ""}${e.toFixed(1)}`;
      if (fmt(oneMap) === fmt(threeMap)) {
        throw new Error("fixture is wrong: the two windows' edges print identically, "
          + "so this cannot tell them apart");
      }
      containsText("the map 1 edge is against a one-map projection", card, fmt(oneMap));
      containsText("and the maps 1-3 edge against a three-map one", card, fmt(threeMap));
    }
  }
}

/* The stat selector follows the market, not a hardcoded list of four.
 *
 * Across every line this app has recorded: CS2 kills 736, CS2 headshots
 * 592, Valorant kills 151, LoL kills 35 -- and deaths and assists, both
 * first-class in the model, four lines between them, ever. Two of the
 * four tabs led to an empty board every time, and headshots at 39% of
 * the market sat behind a tab nobody had a reason to press.
 */
if (app.offeredStats && app.postedLineCounts) {
  const board = (byStat) => ({
    fetched_at: new Date().toISOString(), source: "test",
    props: { cs2: { Faker: Object.entries(byStat).flatMap(([stat, n]) =>
      Array.from({ length: n }, () => ({ player: "Faker", stat, maps: 2, line: 8.5 }))) } },
  });

  try {
    const counts = app.postedLineCounts(board({ kills: 3, headshots: 2 }), "cs2");
    if (counts.kills !== 3 || counts.headshots !== 2) throw new Error(JSON.stringify(counts));
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  counts the lines actually posted\n        ${err.message}`);
  }

  try {
    const keys = app.offeredStats("cs2", board({ kills: 3, headshots: 2 }), "kills").map(([k]) => k);
    if (keys.includes("deaths") || keys.includes("assists")) {
      throw new Error(`offered ${keys} — a stat with no lines leads to an empty board`);
    }
    if (!keys.includes("headshots")) throw new Error("dropped a stat that HAS lines");
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  a stat with no posted lines is not offered\n        ${err.message}`);
  }

  try {
    // Never yank the tab a reader is standing on.
    const keys = app.offeredStats("cs2", board({ kills: 3 }), "deaths").map(([k]) => k);
    if (!keys.includes("deaths")) throw new Error("the selected stat vanished from under the reader");
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  the selected stat is always offered\n        ${err.message}`);
  }

  try {
    // No board loaded is not evidence that a stat has no market.
    for (const empty of [null, undefined, { props: {} }]) {
      const keys = app.offeredStats("cs2", empty, "kills").map(([k]) => k);
      if (!keys.includes("deaths") || !keys.includes("assists")) {
        throw new Error(`hid stats with no board loaded: ${keys}`);
      }
    }
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  with no board loaded every stat is still offered\n        ${err.message}`);
  }

  try {
    // A game whose board is empty must not end up with zero tabs.
    const keys = app.offeredStats("cs2", board({ kills: 0 }), "kills").map(([k]) => k);
    if (!keys.length) throw new Error("offered nothing at all");
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  never offers an empty selector\n        ${err.message}`);
  }
}

/* A career game that does not RECORD a stat is skipped, not zeroed.
 *
 * `g[statKey] || 0` folded "bo3.gg has no headshot figure for this map"
 * into "this player got no headshots". It never bit for k/d/a, because
 * the career scraper drops a game missing any of them -- so every game
 * on file has all three and the gap never existed. It bites the moment
 * a stat is captured for SOME games and not others, which is exactly
 * what a newly added field looks like while the career cache fills.
 *
 * The parity harness cannot see this: no career game currently lacks a
 * stat, so zeroing and skipping give identical answers on today's data
 * and would keep doing so right up until headshots lands.
 */
if (app.pointInTimeCS2CareerRate) {
  const at = (d) => `2026-0${d}-01T00:00:00+00:00`;
  const player = (games) => ({ career_games: games });
  const CUTOFF = "2026-09-01";

  try {
    // Two games with the stat, one without. The answer must be the
    // average of the two that have it -- not of three, one counted 0.
    const mixed = player([
      { date: at(8), k: 20, hs: 10 },
      { date: at(8), k: 20, hs: 10 },
      { date: at(8), k: 20 },
    ]);
    const got = app.pointInTimeCS2CareerRate(mixed, "hs", CUTOFF);
    if (Math.abs(got - 10) > 1e-9) {
      throw new Error(`got ${got}, wanted 10 (a missing figure counted as a zero gives ~6.67)`);
    }
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  a game without the stat is skipped, not averaged in as zero\n        ${err.message}`);
  }

  try {
    // A genuine zero is real data and must still count.
    const withZero = player([
      { date: at(8), k: 20, hs: 10 },
      { date: at(8), k: 20, hs: 0 },
    ]);
    const got = app.pointInTimeCS2CareerRate(withZero, "hs", CUTOFF);
    if (Math.abs(got - 5) > 1e-9) throw new Error(`got ${got}, wanted 5`);
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  a real zero still counts\n        ${err.message}`);
  }

  try {
    // No game records it at all: no rate, rather than a confident 0.
    const none = player([{ date: at(8), k: 20 }, { date: at(8), k: 20 }]);
    if (app.pointInTimeCS2CareerRate(none, "hs", CUTOFF) !== null) {
      throw new Error("returned a rate from games that record nothing");
    }
    // ...and the stat they DO record is unaffected.
    if (app.pointInTimeCS2CareerRate(none, "k", CUTOFF) !== 20) {
      throw new Error("k/d/a behaviour changed");
    }
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  a stat no game records has no rate at all\n        ${err.message}`);
  }
}

/* Does the board's ORDER mean anything?
 *
 * The Edges tab sorts by the size of the disagreement, and a sorted
 * list asserts that its top is better than its middle. That assertion
 * is testable, and on the record so far it is false: at edge >= 1 the
 * bigger disagreements have won 48.7% against 55.3% for the smaller
 * ones. The board says so on itself rather than leaving the reader to
 * infer confidence from position.
 *
 * The thing to guard is the direction of the claim. Saying "the top of
 * this board is better" when it is not is the single most expensive
 * thing this page could get wrong.
 */
if (app.rankingIsInformative) {
  const row = (edge, won, i, date = "2026-09-20") => ({
    game: "cs2", match_date: date, team: `T${i}`, opponent: `O${i}`,
    result: "over", won, edge, projection: 20, actual: 20, line: 18,
  });
  // 40 matches either side of the threshold, so the sample floor is met.
  const make = (bigWinRate, smallWinRate) => {
    const out = [];
    for (let i = 0; i < 40; i++) {
      out.push(row(3, i / 40 < bigWinRate, `b${i}`));
      out.push(row(0.5, i / 40 < smallWinRate, `s${i}`));
    }
    return out;
  };

  try {
    const flat = app.rankingIsInformative(make(0.5, 0.5), 2);
    if (!flat) throw new Error("returned null on a sufficient sample");
    if (flat.informative) throw new Error("called a flat curve informative");
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  a flat edge curve is not called informative\n        ${err.message}`);
  }

  try {
    // Big band clearly and consistently better.
    const real = app.rankingIsInformative(make(0.95, 0.30), 2);
    if (!real || !real.informative) {
      throw new Error(`a genuinely predictive curve was not recognised (${real && real.informative})`);
    }
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  a real edge curve IS recognised\n        ${err.message}`);
  }

  try {
    // Backwards: the big band does WORSE. Must never read as informative.
    const inverted = app.rankingIsInformative(make(0.20, 0.90), 2);
    if (inverted && inverted.informative) throw new Error("an inverted curve was called informative");
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  an inverted edge curve is never informative\n        ${err.message}`);
  }

  try {
    const thin = app.rankingIsInformative([row(3, true, 1), row(0.5, false, 2)], 2);
    if (thin !== null) throw new Error("reported a verdict off two rows");
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  too few rows returns no verdict at all\n        ${err.message}`);
  }

  try {
    /* The sample floor, tested where it actually bites. Ten matches a
       side is enough for an interval to exist but nowhere near enough
       to claim the board's order means something, and a 90%-vs-10%
       split is exactly the flattering result a thin sample invents. */
    const few = [];
    for (let i = 0; i < 10; i++) {
      few.push(row(3, i < 9, `b${i}`));
      few.push(row(0.5, i < 1, `s${i}`));
    }
    if (app.rankingIsInformative(few, 2) !== null) {
      throw new Error("claimed a verdict on ten matches a side");
    }
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  a sample below the floor gets no verdict however lopsided\n        ${err.message}`);
  }

  try {
    /* The real-world shape, and the one a loose test misses: the top
       band's MEAN is a little higher, but its interval is nowhere near
       clearing the bottom band. On the live record that is 51.7%
       against 50.4%. Comparing means alone calls this a working
       ranking; it is two noisy numbers that happen to be ordered. */
    const barely = app.rankingIsInformative(make(0.55, 0.50), 2);
    if (!barely) throw new Error("returned null on a sufficient sample");
    if (barely.big.mean <= barely.small.mean) {
      throw new Error("fixture is wrong: the top band must LOOK better for this to test anything");
    }
    if (barely.informative) {
      throw new Error("a higher mean with an overlapping interval was called informative");
    }
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  looking better is not the same as being better\n        ${err.message}`);
  }
}

/* The record against the posted line, clustered on the match.
 *
 * This is the number that decides whether any of this is worth paying
 * for, so the app computes it itself now rather than leaving it in a
 * dev script. The risk in moving it is that the two quietly disagree
 * and nobody notices which is right, so the grouping rule is pinned
 * directly -- it is where the mistake actually happened.
 *
 * Keying on `team` rather than the sorted PAIR split every match into
 * two clusters and reported 75 where there were 49. Both sides of a map
 * share its rounds and its pace; they are one dependent unit. Caught
 * only by cross-checking against scripts/dev/model_vs_market.mjs.
 */
if (app.recordVsLine && app.clusteredMean) {
  const prop = (over) => ({
    game: "cs2", match_date: "2026-09-20", team: "A", opponent: "B",
    result: "over", won: true, projection: 20, actual: 20, line: 18, ...over,
  });

  try {
    // Two rows, opposite sides of ONE map. One cluster, not two.
    const both = [prop({ team: "A", opponent: "B" }), prop({ team: "B", opponent: "A" })];
    const got = app.recordVsLine(both);
    if (got.matches !== 1) throw new Error(`grouped into ${got.matches} clusters, wanted 1`);
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  both sides of a map are one cluster\n        ${err.message}`);
  }

  try {
    const twoMatches = [
      prop({ match_date: "2026-09-20" }), prop({ match_date: "2026-09-21" })];
    if (app.recordVsLine(twoMatches).matches !== 2) throw new Error("different dates merged");
    const twoGames = [prop({ game: "cs2" }), prop({ game: "valorant" })];
    if (app.recordVsLine(twoGames).matches !== 2) throw new Error("different games merged");
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  genuinely different matches stay separate\n        ${err.message}`);
  }

  try {
    // Signed so POSITIVE means the line was closer. Getting this
    // backwards would flatter the model on the one number that matters.
    const weAreWorse = [
      prop({ team: "A", opponent: "B", projection: 30, line: 20, actual: 20 }),
      prop({ team: "C", opponent: "D", match_date: "2026-09-21", projection: 30, line: 20, actual: 20 }),
    ];
    const gap = app.recordVsLine(weAreWorse).maeGap;
    if (!(gap.mean > 0)) throw new Error(`gap ${gap.mean}, wanted positive when the line is closer`);
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  the accuracy gap is signed the unflattering way\n        ${err.message}`);
  }

  try {
    // One match cannot carry an interval, and reporting a mean with no
    // spread is the overclaim this is built to prevent.
    if (app.clusteredMean([0.5]) !== null) throw new Error("one value returned an interval");
    if (app.clusteredMean([]) !== null) throw new Error("no values returned an interval");
    const two = app.clusteredMean([0.4, 0.6]);
    if (!(two && two.lo < two.mean && two.mean < two.hi)) throw new Error("interval does not bracket the mean");
    pass++;
  } catch (err) {
    fail++;
    console.error(`FAIL  an interval needs at least two matches\n        ${err.message}`);
  }
}

/* How old the data is, against when the scraper ran.
 *
 * The badge read generated_at -- the run timestamp -- and the LoL
 * scraper stamps that fresh even when a region it could not reach fell
 * back to committed data. So a region carrying five-day-old matches sat
 * under "Live data, updated 9h ago". A total outage was already handled
 * (that scraper refuses to rewrite the file); a PARTIAL one, which is
 * the common case, was silently mislabelled.
 *
 * For something people pay for, a badge asserting a freshness the data
 * does not have is worse than no badge.
 */
if (app.oldestRegion) {
  const hoursAgo = (h) => new Date(Date.now() - h * 3600 * 1000).toISOString();

  const cases = [
    ["reports the oldest region, not the freshest",
     { A: { refreshed_at: hoursAgo(2) }, B: { refreshed_at: hoursAgo(120) } }, null, "B"],
    ["one region is trivially the oldest",
     { A: { refreshed_at: hoursAgo(9) } }, null, "A"],
    ["ignores a region with no timestamp rather than treating it as ancient",
     { A: { refreshed_at: hoursAgo(2) }, B: {} }, null, "A"],
    ["falls back to the run stamp when NO region records one",
     { A: {}, B: {} }, hoursAgo(9), null],
    ["and survives an unparseable timestamp",
     { A: { refreshed_at: "not a date" }, B: { refreshed_at: hoursAgo(3) } }, null, "B"],
  ];
  for (const [name, regions, fallback, wantRegion] of cases) {
    try {
      const got = app.oldestRegion(regions, fallback);
      if (got.region !== wantRegion) {
        throw new Error(`named ${got.region}, wanted ${wantRegion}`);
      }
      if (wantRegion === null && got.at !== fallback) {
        throw new Error(`fell back to ${got.at}, wanted ${fallback}`);
      }
      pass++;
    } catch (err) {
      fail++;
      console.error(`FAIL  ${name}\n        ${err.message}`);
    }
  }

  // Empty and missing inputs reach this from a first paint, before any
  // fetch has landed.
  for (const [name, arg] of [["null", null], ["undefined", undefined], ["empty", {}]]) {
    try {
      const got = app.oldestRegion(arg, null);
      if (got.at !== null || got.region !== null) throw new Error(JSON.stringify(got));
      pass++;
    } catch (err) {
      fail++;
      console.error(`FAIL  ${name} regions does not throw\n        ${err.message}`);
    }
  }

  if (app.DataStatus) {
    const badge = (regions, lastUpdated) => wrap(React.createElement(app.DataStatus, {
      status: "live", lastUpdated, regions, errorDetail: null, onRetry: () => {},
    }), null);

    // The real shape of the bug: run stamped minutes ago, one region days behind.
    const partialOutage = { LCS: { refreshed_at: hoursAgo(1) }, LEC: { refreshed_at: hoursAgo(120) } };
    // "updated 5d ago" is the LABEL. Asserting a bare "5d ago" passes on
    // the lag note alone, which left the original bug -- the label
    // reading the run stamp -- alive through a mutation run.
    containsText("a partially stale board reports the STALE age in its label",
                 badge(partialOutage, hoursAgo(1)), "updated 5d ago");
    containsText("and does not report the run stamp",
                 badge(partialOutage, hoursAgo(1)), "updated 1h ago", false);
    containsText("and names the region that is behind",
                 badge(partialOutage, hoursAgo(1)), "LEC is");
    // Deliberately unequal, so "the oldest" is deterministic and the
    // assertion cannot pass by the tie landing on the other region.
    containsText("a healthy board names no region, though one is nominally oldest",
                 badge({ LCS: { refreshed_at: hoursAgo(2) }, LEC: { refreshed_at: hoursAgo(3) } },
                       hoursAgo(2)),
                 " is ", false);
    containsText("an old-shaped file with no per-region stamps still reports the run",
                 badge({ LCS: {}, LEC: {} }, hoursAgo(9)), "9h ago");
  }
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
    upcoming_matches: [{ teamA: "A", teamB: "B", date: SOON_DATE, time: SOON_TIME,
                         _sortKey: SOON_ISO }] } };
  const hsProps = {
    fetched_at: new Date().toISOString(), source: "test",
    props: { cs2: { Ghost: [{ player: "Ghost", stat: "headshots", maps: 2, line: 18.5,
                              odds_type: "standard", team: "A",
                              start_time: SOON_ISO }] } },
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
                             start_time: SOON_ISO }] } } });
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

  /* An old payload still prices a match that has not been played.
   *
   * This is the behaviour that changed. Past 90 minutes the board used
   * to print "Nm old" where the edge goes and draw nothing at all -- and
   * on a board refreshed twice a day that fired on fixtures still hours
   * from kickoff. The age is still shown, because lines do move; it just
   * no longer decides whether there is an edge. */
  const stale = (v) => ({
    fetched_at: new Date(Date.now() - 6 * 3600 * 1000).toISOString(), source: "test",
    props: { lol: { Thin: [{ player: "Thin", stat: "kills", maps: 2, line: v,
                             odds_type: "standard", team: "T1",
                             start_time: SOON_ISO }] } } });
  /* A started fixture needs BOTH clocks moved, which is the honest
     shape of it: propFor pairs a line to a fixture by start time, so a
     past line against a future match is simply no row at all. This is a
     real state -- a fixture stays in upcoming_matches from kickoff
     until the next scrape moves it, which can be hours. */
  const GONE = new Date(Date.now() - 3 * 3600 * 1000).toISOString();
  const goneData = { R: { teams: { T1: { color: "#e0c341", players: [thinP] }, GEN: rostered },
                          past_matches: past,
                          upcoming_matches: [{ teamA: "T1", teamB: "GEN",
                                               date: "earlier", time: "", _sortKey: GONE }] } };
  const kickedOff = (v) => ({
    fetched_at: new Date().toISOString(), source: "test",
    props: { lol: { Thin: [{ player: "Thin", stat: "kills", maps: 2, line: v,
                             odds_type: "standard", team: "T1",
                             start_time: GONE }] } } });
  const goneTab = (props) => wrap(React.createElement(app.EdgesTab, {
    regionsData: goneData, regionList: ["R"], regionLabels: {}, weights,
    statType: "kills", game: "lol", isDesktop: true }), props);

  containsText("an old payload on an unplayed fixture still shows its edge",
               tab(stale(6)), "OVER +7.3");
  containsText("and says how old it is rather than hiding the number",
               tab(stale(6)), "6h old");
  containsText("the header no longer claims edges are withheld",
               tab(stale(6)), "no edges are drawn", false);
  containsText("but it still warns that lines move",
               tab(stale(6)), "still priced, but they move");

  containsText("a fixture that has started says so instead of pricing it",
               goneTab(kickedOff(6)), "started");
  containsText("and draws no edge for it",
               goneTab(kickedOff(6)), "OVER +7.3", false);
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
                              start_time: SOON_ISO }] } } };
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
                team: "T1", start_time: SOON_ISO }],
      Zeus: [{ player: "Zeus", stat: "kills", maps: 2, line: 2.0, odds_type: "standard",
               team: "T1", start_time: SOON_ISO }] } } };
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

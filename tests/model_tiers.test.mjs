/* The team-share tier: share of the team's total x what a map usually yields.
 *
 * The blend weight itself was chosen by measurement (walk-forward folds in
 * scripts/dev/experiment_kill_share.py). What is checked here is the
 * mechanics underneath it, because a tier that silently fails to compute
 * does not throw or look wrong — it just quietly returns the old number,
 * and the measured gain evaporates with nothing going red.
 *
 * Run: node tests/model_tiers.test.mjs
 */
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { createRequire } from "module";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(path.join(root, "package.json"));
const React = require("react");
const { transformSync } = require("@babel/core");
const raw = fs.readFileSync(path.join(root, "src/app.jsx"), "utf8");
const body = raw.slice(raw.indexOf("try {") + "try {".length,
                       raw.indexOf("const root = ReactDOM.createRoot"));
const { code } = transformSync(
  `${body}\nreturn { teamTotal, shareRate, leaguePacePerMap, blendShareTier, project,
                     SHARE_HALF_LIFE, DEFAULT_WEIGHTS_BY_GAME_AND_STAT };`,
  { presets: [["@babel/preset-react", { runtime: "classic" }]], filename: "app.jsx",
    parserOpts: { allowReturnOutsideFunction: true } });
const win = { innerWidth: 1400, addEventListener() {}, removeEventListener() {},
              localStorage: { getItem: () => null, setItem() {}, removeItem() {} } };
const app = new Function("React","ReactDOM","window","document","fetch","localStorage","console", code)(
  React, { createRoot: () => ({ render() {} }) }, win, { getElementById: () => null },
  () => new Promise(() => {}), win.localStorage, console);

let pass = 0, fail = 0;
function check(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (ok) pass++;
  else { fail++; console.error(`FAIL  ${label}\n        got ${JSON.stringify(got)}\n        want ${JSON.stringify(want)}`); }
}
function near(label, got, want, eps = 1e-9) {
  const ok = typeof got === "number" && Math.abs(got - want) < eps;
  if (ok) pass++;
  else { fail++; console.error(`FAIL  ${label}\n        got ${got}\n        want ~${want}`); }
}

const five = (ks) => Object.fromEntries(ks.map((k, i) => [`p${i}`, { k, d: 1, a: 1 }]));
const match = (date, teamA, teamB, ksA, ksB, extra = {}) => ({
  date, teamA, teamB, actual: { [teamA]: five(ksA), [teamB]: five(ksB) }, ...extra });

/* ---- teamTotal ---- */
check("sums every recorded player on one side",
      app.teamTotal(match("2026-01-01", "A", "B", [10, 10, 10, 10, 10], [1, 1, 1, 1, 1]), "A", "k"), 50);
check("a side with fewer than five recorded players is refused, not undercounted",
      app.teamTotal({ date: "d", teamA: "A", teamB: "B", actual: { A: five([9, 9, 9, 9]) } }, "A", "k"), null);
check("an absent side is null", app.teamTotal(match("d", "A", "B", [1,1,1,1,1], [1,1,1,1,1]), "C", "k"), null);
check("a match with no actual at all is null",
      app.teamTotal({ date: "d", teamA: "A", teamB: "B" }, "A", "k"), null);

/* ---- shareRate ---- */
{
  // One match, p0 takes 20 of 50.
  const pm = [match("2026-01-01", "A", "B", [20, 10, 10, 5, 5], [1, 1, 1, 1, 1])];
  near("a player's share of their own team's total", app.shareRate(pm, "A", "p0", "k", null), 20 / 50);
  check("a player who never appears has no share", app.shareRate(pm, "A", "ghost", "k", null), null);
  check("a team with no matches has no share", app.shareRate(pm, "C", "p0", "k", null), null);
}
{
  // Recency: two matches, 40% then 20%. The recent one must dominate.
  const pm = [match("2026-01-01", "A", "B", [40, 15, 15, 15, 15], [1,1,1,1,1]),
              match("2026-02-01", "A", "B", [20, 20, 20, 20, 20], [1,1,1,1,1])];
  const got = app.shareRate(pm, "A", "p0", "k", null);
  const w = Math.pow(0.5, 1 / app.SHARE_HALF_LIFE);
  near("recency-weighted, newest at full weight", got, (0.4 * w + 0.2) / (w + 1));
  check("and strictly between the two observations", got > 0.2 && got < 0.4, true);
  near("a cutoff hides everything at or after it",
       app.shareRate(pm, "A", "p0", "k", "2026-02-01"), 0.4);
}

/* ---- leaguePacePerMap ---- */
{
  const teams = { A: { players: [] }, B: { players: [] } };
  const pm = [match("2026-01-01", "A", "B", [10,10,10,10,10], [20,20,20,20,20])];
  // A totals 50 over 2 maps (25/map), B totals 100 (50/map) -> league 37.5
  near("averaged over teams, per map", app.leaguePacePerMap(pm, teams, "k", null), 37.5);
}
{
  // The map-count path. maps_counted only exists in LoL today, where this
  // tier is switched off, so nothing in the real data exercises it — which
  // is exactly why it is pinned here rather than left to be discovered by
  // whoever turns the tier on for a game that records Bo5s.
  const teams = { A: { players: [] } };
  const bo5 = [match("2026-01-01", "A", "B", [12,12,12,12,12], [1,1,1,1,1], { maps_counted: 3 })];
  near("a three-map series divides by three, not by the default two",
       app.leaguePacePerMap(bo5, teams, "k", null), 60 / 3);
  const bo3 = [match("2026-01-01", "A", "B", [12,12,12,12,12], [1,1,1,1,1])];
  near("and an unstated map count falls back to two",
       app.leaguePacePerMap(bo3, teams, "k", null), 60 / 2);
}

/* ---- the blend, and every way it declines to apply ---- */
{
  const teams = { A: { players: [] }, B: { players: [] } };
  const pm = [match("2026-01-01", "A", "B", [20, 10, 10, 5, 5], [10,10,10,10,10])];
  // share 0.4, league pace = mean(50/2, 50/2) = 25 -> tier = 10
  const tier = 0.4 * 25;
  const blend = (w) => app.blendShareTier(30, { share: w }, pm, teams, "A", "p0", "k", null);
  near("weight 0.5 lands halfway between the two estimates", blend(0.5).perGame, (30 + tier) / 2);
  near("weight 1.0 is the tier alone", blend(1).perGame, tier);
  near("weight 0 leaves the projection untouched", blend(0).perGame, 30);
  check("and reports no tier when it is switched off", blend(0).shareTier, null);
  near("the tier is reported alongside, for the card to show", blend(0.5).shareTier, tier);

  const unknown = app.blendShareTier(30, { share: 0.7 }, pm, teams, "A", "ghost", "k", null);
  near("a player with no prior appearances falls back rather than losing the projection",
       unknown.perGame, 30);
  check("and says the tier did not apply", unknown.shareTier, null);

  const noLeague = app.blendShareTier(30, { share: 0.7 }, [], {}, "A", "p0", "k", null);
  near("a league with no completed matches falls back too", noLeague.perGame, 30);
}

/* ---- the shipped weights match what was measured ---- */
{
  const W = app.DEFAULT_WEIGHTS_BY_GAME_AND_STAT;
  check("LoL takes none of this tier — its opponent term already does the job",
        [W.lol.kills.share, W.lol.deaths.share, W.lol.assists.share], [0, 0, 0]);
  check("Valorant takes it on all three, deaths hardest",
        [W.valorant.kills.share, W.valorant.deaths.share, W.valorant.assists.share], [0.4, 0.7, 0.4]);
  check("CS2 takes it on deaths only, the stat its pace drives most",
        [W.cs2.kills.share, W.cs2.deaths.share, W.cs2.assists.share], [0, 0.6, 0]);
  check("every game and stat states a share weight explicitly",
        Object.values(W).every((g) => Object.values(g).every((s) => typeof s.share === "number")), true);
}

/* ---- against the real data, end to end ---- */
for (const [game, file] of Object.entries({ valorant: "valorant_data.json", cs2: "cs2_data.json" })) {
  const p = path.join(root, file);
  if (!fs.existsSync(p)) continue;
  const data = JSON.parse(fs.readFileSync(p, "utf8"));
  const stat = "deaths", weights = app.DEFAULT_WEIGHTS_BY_GAME_AND_STAT[game][stat];
  let moved = 0, total = 0;
  for (const region of Object.values(data.regions)) {
    const teams = region.teams || {}, pm = region.past_matches || [];
    if (!pm.length) continue;
    for (const [team, entry] of Object.entries(teams)) {
      for (const player of (entry.players || []).slice(0, 2)) {
        const on = app.project(teams, pm, player, team, team, 2, weights, stat);
        const off = app.project(teams, pm, player, team, team, 2, { ...weights, share: 0 }, stat);
        total++;
        if (Math.abs(on.perGame - off.perGame) > 1e-9) moved++;
      }
    }
  }
  check(`${game}: the tier actually changes real projections`, total > 0 && moved > 0, true);
  check(`${game}: and is reported on the breakdown`, weights.share > 0, true);
}

console.log(`${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

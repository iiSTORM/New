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
                     leaguePlayerRate, shrinkToPrior, ROSTER_SIZE,
                     matchKP, pointInTimeKP, kpMultiplier, leagueAvgKP, KP_SHRINK,
                     STAT_TYPES, statsForGame,
                     SHARE_HALF_LIFE, DEFAULT_WEIGHTS_BY_GAME_AND_STAT,
                     evidenceTier, careerGameCount, evidenceGamesFor,
                     EVIDENCE_THIN, EVIDENCE_SOLID,
                     edgeMultiplier, adjustEdge, EDGE_REALIZATION, rankEdges,
                     historyIsBorrowed, effectiveEvidence, EVIDENCE_BORROWED_CAP,
                     historyPoolFor, homeRegionLookup, shortRegionLabel,
                     propIsLive, ageLabel, propsAreFresh, PROPS_MAX_AGE_MINUTES };`,
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
        [W.valorant.kills.share, W.valorant.deaths.share, W.valorant.assists.share], [0.4, 0.7, 0.5]);
  /* Deaths came down 0.6 -> 0.4 once the career tier was real: the
     team-pace tier had been carrying weight that now belongs to a
     per-player rate. Chosen for robustness over size -- 0.2 and 0.3
     score better in total but 0.4 is a majority at every granularity
     and perfect at the finer ones (4/4, 6/6, 8/8, 9/9). A share weight
     for KILLS came out of the same sweep at 4/6 and was rejected: 1/4
     at four folds, 4/8 at eight. */
  check("CS2 takes it on deaths only, the stat its pace drives most",
        [W.cs2.kills.share, W.cs2.deaths.share, W.cs2.assists.share], [0, 0.4, 0]);
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

/* ---- thin-sample shrinkage ---- */
{
  const teams = { A: { players: [] }, B: { players: [] } };
  const pm = [match("2026-01-01", "A", "B", [20, 10, 10, 5, 5], [10,10,10,10,10])];
  // league pace = mean(50/2, 50/2) = 25 per team per map -> 5 per player
  near("the prior is one player's share of an average team's map",
       app.leaguePlayerRate(pm, teams, "k", null), 5);

  const pull = (n, k) => app.shrinkToPrior(15, { shrink: k }, n, pm, teams, "k", null);
  near("no history at all lands entirely on the prior", pull(0, 4).perGame, 5);
  near("n equal to k sits halfway", pull(4, 4).perGame, (15 + 5) / 2);
  near("a large sample is barely moved", pull(400, 4).perGame, 400/404*15 + 4/404*5, 1e-6);
  check("and the pull shrinks as the sample grows",
        pull(1, 4).shrinkPull > pull(20, 4).shrinkPull, true);
  near("k=0 is off entirely, whatever the sample", pull(1, 0).perGame, 15);
  check("and reports nothing when off", pull(1, 0).shrunkTo, null);
  near("a league with nothing on record cannot shrink",
       app.shrinkToPrior(15, { shrink: 4 }, 1, [], {}, "k", null).perGame, 15);
}

/* The constant the shrinkage prior divides by, checked against the data
   rather than trusted.

   Written first as "every side has exactly five", which failed on its
   first run and was right to: Valorant has 5 sides of six players and CS2
   has one of six and one of eight, where a substitute played some of the
   maps. That does NOT make five wrong. Five is the number of SLOTS, and
   the prior answers "what does a typical player do in a map" — dividing a
   team's total by six recorded bodies, two of whom split one slot, would
   understate a full-time player by a sixth. Dividing by slots is right.

   What would break the prior is a side recorded with FEWER than five,
   because then the team total itself is incomplete and the league pace
   built on it is low. teamTotal already refuses those; this checks none
   exist to refuse, and holds substitutions to the rarity that makes the
   slot count a safe divisor. */
{
  const sizes = {};
  let sides = 0, oversized = 0, undersized = 0;
  for (const [game, file] of Object.entries(
        { lol: "data.json", valorant: "valorant_data.json", cs2: "cs2_data.json" })) {
    const fp = path.join(root, file);
    if (!fs.existsSync(fp)) continue;
    for (const region of Object.values(JSON.parse(fs.readFileSync(fp, "utf8")).regions)) {
      for (const m of region.past_matches || []) {
        for (const side of Object.values(m.actual || {})) {
          const n = Object.values(side).filter((x) => x && typeof x === "object").length;
          if (!n) continue;
          sides++; sizes[n] = (sizes[n] || 0) + 1;
          if (n < app.ROSTER_SIZE) undersized++;
          if (n > app.ROSTER_SIZE) oversized++;
        }
      }
    }
  }
  check("no recorded side is short of a full roster — a partial side would " +
        `drag league pace down (sizes seen: ${JSON.stringify(sizes)})`, undersized, 0);
  check("substitutions stay rare enough that slots are a safe divisor (<2% of sides)",
        oversized / sides < 0.02, true);
}

{
  const W = app.DEFAULT_WEIGHTS_BY_GAME_AND_STAT;
  check("LoL shrinks nothing — career and a prior split already ground it",
        [W.lol.kills.shrink, W.lol.deaths.shrink, W.lol.assists.shrink], [0, 0, 0]);
  /* Assists moved 1 -> 4 when the history behind it deepened. The prior
     event's maps used to be discarded, so a Valorant player carried a
     median of 8; they now carry 20, and a longer record earns a harder
     pull toward the prior rather than a softer one -- the shrink is
     answering to a sample whose own mean is worth more. Kills and
     deaths were re-searched at the same time and both failed the
     holdout, so they stand. */
  check("Valorant shrinks kills and assists, not deaths",
        [W.valorant.kills.shrink, W.valorant.deaths.shrink, W.valorant.assists.shrink], [4, 0, 4]);
  /* CS2 now shrinks all three hard. It did not until the roster roughly
     doubled: the scraper used to rebuild its team list from one page of
     the global feed each run, and once past matches carried over, 58
     teams of thin-history players joined. Pulling a thin sample toward
     the league rate went from worthless to the largest accuracy gain on
     this game -- walk-forward over 6 folds, kills 6/6 (-8.54%), assists
     6/6 (-3.56%), deaths 4/6 (-2.95%).

     headshots is deliberately NOT in that list and stays at 3: the same
     sweep gives it 1/6 at k=2 and 2/6 at k=16 with the sign flipping in
     between, which is a knife edge rather than a plateau. */
  /* Deaths came back down to 3 once the career tier was actually being
     written. That scrape had been saving nothing for weeks, so 1,124 of
     1,404 players had no career grounding and this weight was carrying
     the slack; with the tier present at a median of 41 games per player
     there is less to carry. Majority at 3/4, 6/6, 7/8 and 7/9 folds. */
  check("CS2 shrinks every career-backed stat hard, since its roster is full of thin histories",
        [W.cs2.kills.shrink, W.cs2.deaths.shrink, W.cs2.assists.shrink], [8, 3, 8]);
  check("but headshots was left alone, the sweep being noise there",
        W.cs2.headshots.shrink, 3);
  check("every game and stat states a shrink constant explicitly",
        Object.values(W).every((g) => Object.values(g).every((s) => typeof s.shrink === "number")), true);
}

/* ---- kill participation, and the leak that used to be in it ----
 *
 * kpMultiplier read player.cur.kp, a whole-season figure, so a backtest of
 * a match in May was handed a kill participation partly built from games
 * played in August. Worth 0.66pp against its own leave-one-out version,
 * and it was the whole of the kp layer's apparent value — every leak-free
 * setting turned out to be no better than switching the layer off.
 */
{
  const withKP = (k, a, extra = {}) => ({ k, d: 1, a, ...extra });
  const side = (spec) => Object.fromEntries(Object.entries(spec).map(([n, [k, a, e]]) => [n, withKP(k, a, e || {})]));
  const m = (date, ksA) => ({
    date, teamA: "A", teamB: "B",
    actual: { A: side(ksA), B: side({ q0:[1,1], q1:[1,1], q2:[1,1], q3:[1,1], q4:[1,1] }) } });

  // p0 takes 10 kills + 10 assists of a 50-kill team -> 40%.
  const one = m("2026-01-01", { p0:[10,10], p1:[10,0], p2:[10,0], p3:[10,0], p4:[10,0] });
  near("KP derived from k + a over the team's kills", app.matchKP(one, "A", "p0"), 40);
  check("an unknown player has none", app.matchKP(one, "A", "ghost"), null);

  // kp_denominator wins where present: it is the team's kills WHILE THAT
  // PLAYER PLAYED, which is why two players on one side can differ.
  const subbed = m("2026-01-01", { p0:[10,10,{kp_numerator:20, kp_denominator:25}],
                                   p1:[10,0], p2:[10,0], p3:[10,0], p4:[10,0] });
  near("a substitute's own denominator is preferred over the side total",
       app.matchKP(subbed, "A", "p0"), 80);

  /* The regression test for the leak. A match ON or AFTER the cutoff must
     not reach the estimate, however extreme it is. */
  const history = [
    m("2026-01-01", { p0:[10,10], p1:[10,0], p2:[10,0], p3:[10,0], p4:[10,0] }),   // 40%
    m("2026-06-01", { p0:[0,0],   p1:[20,0], p2:[10,0], p3:[10,0], p4:[10,0] }),   // 0%, the future
  ];
  const teams = { A: { players: [{ name: "p0", cur: { kp: 40 } }] }, B: { players: [] } };
  const before = app.pointInTimeKP(history, teams, "A", "p0", "2026-03-01");
  const after = app.pointInTimeKP(history, teams, "A", "p0", null);
  check("a match after the cutoff cannot reach the estimate", before > after, true);
  check("and the estimate is bounded by the league it is shrunk toward",
        before <= 40 && before > 0, true);

  const noHistory = app.pointInTimeKP([], teams, "A", "p0", null);
  check("no prior appearances yields nothing to build from", noHistory, null);

  // Shrinkage toward the league: one observation is pulled most of the way.
  {
    const solo = [m("2026-01-01", { p0:[20,0], p1:[10,0], p2:[10,0], p3:[5,0], p4:[5,0] })]; // p0 = 40%
    const t = { A: { players: [{ name: "p0", cur: { kp: 20 } }, { name: "p1", cur: { kp: 20 } }] }, B: { players: [] } };
    const league = app.leagueAvgKP(t);
    const got = app.pointInTimeKP(solo, t, "A", "p0", null);
    near(`a single observation is shrunk toward the league (k=${app.KP_SHRINK})`,
         got, (1/(1+app.KP_SHRINK)) * 40 + (app.KP_SHRINK/(1+app.KP_SHRINK)) * league);
  }

  const player = { name: "p0", cur: { kp: 40 }, hist: null };

  /* Through kpMultiplier, not just pointInTimeKP. The leak lived in the
     WIRING — which cutoff the multiplier passed down — so testing the
     estimator alone leaves it uncovered. Written after a mutation that
     re-hardcoded the cutoff to null sailed through a green suite. */
  const multAt = (cutoff) => app.kpMultiplier(player, 0.3, 0.5, teams, history, "A", cutoff);
  check("the multiplier itself honours the cutoff it is given",
        multAt("2026-03-01") !== multAt(null), true);
  check("and a later cutoff, which admits the poor match, gives a lower multiplier",
        multAt(null) < multAt("2026-03-01"), true);

  check("strength 0 is exactly neutral and does no work",
        app.kpMultiplier(player, 0.3, 0, teams, history, "A", null), 1);
  check("with no history at all it falls back to the season figure rather than giving up",
        typeof app.kpMultiplier(player, 0.3, 0.5, teams, [], "A", null), "number");
}

{
  const W = app.DEFAULT_WEIGHTS_BY_GAME_AND_STAT;
  /* Zero everywhere BY MEASUREMENT except CS2 kills, and that one moved
     only because the data underneath it changed.
     
     The old reading was honest: with the leak removed, every leak-free
     kp setting scored no better than the layer being off — CS2 kills
     6.0302 off against 6.0300 at its best. But that was taken while
     1,124 of 1,404 CS2 players had no career record at all, because the
     career scrape had been failing silently for weeks. kp is a
     MULTIPLIER on the base rate, and scaling noise by role does
     nothing. With an independent per-game history behind 98% of players
     at a median of 41 games, there is a real number to adjust: 6/6
     folds at every value from 0.1 to 0.8, and a majority at every fold
     count tried (4/4, 6/6, 7/8, 6/9).

     The bar that comment set — a fresh out-of-sample run, not the
     in-sample search reaching for it — is the bar this cleared, and the
     leak was re-checked rather than assumed gone. kp_multiplier still
     falls back to the SEASON aggregate when no point-in-time figure
     exists, which would leak; it fires 1,690 times and every one of
     those rows is dropped before scoring, because a player with no
     prior appearances has prior === 0 and CS2 has no hist tier to save
     them. Re-running with the fallback forced neutral gives identical
     MAE to four decimal places at every kp setting, which is the proof
     rather than the argument. */
  const nonZero = Object.entries(W).flatMap(([game, stats]) =>
    Object.entries(stats).filter(([, s]) => s.kp !== 0).map(([stat]) => `${game}/${stat}`));
  check(`kp is zero everywhere except cs2/kills${nonZero.length ? ` (${nonZero})` : ""}`,
        nonZero, ["cs2/kills"]);
  check("and cs2/kills carries the value the sweep plateaued on",
        W.cs2.kills.kp, 0.8);
}

/* ---- a stat only one game records ----
 *
 * Headshots come from bo3.gg and nothing else carries them. Offering the
 * tab on LoL would not error: every projection would find no rate, return
 * null, and render as an absent number with nothing to explain it, which
 * is the worst of the three possible outcomes.
 */
{
  check("headshots is declared CS2-only", app.STAT_TYPES.headshots.games, ["cs2"]);
  check("and reads the hs field", app.STAT_TYPES.headshots.key, "hs");
  check("CS2 offers it", app.statsForGame("cs2").map(([k]) => k).includes("headshots"), true);
  for (const game of ["lol", "valorant"]) {
    check(`${game} does not offer it`,
          app.statsForGame(game).map(([k]) => k).includes("headshots"), false);
    check(`${game} still offers the three it records`,
          app.statsForGame(game).map(([k]) => k), ["kills", "deaths", "assists"]);
  }
  check("a stat with no games list is offered everywhere",
        ["lol", "valorant", "cs2"].every((g) => app.statsForGame(g).map(([k]) => k).includes("kills")), true);
  check("every game offers at least one stat, so the selector is never empty",
        ["lol", "valorant", "cs2"].every((g) => app.statsForGame(g).length > 0), true);
}

{
  // A projection for a stat the player has no rate for must come back
  // null, not NaN. NaN renders as a blank where a number should be, and
  // nothing downstream can tell it apart from a real value.
  const weights = app.DEFAULT_WEIGHTS_BY_GAME_AND_STAT.cs2.headshots;
  const noHS = { name: "p", role: null, cur: { g: 10, k: 20, d: 15, a: 5, kp: 26 }, hist: null };
  const teams = { A: { players: [noHS] }, B: { players: [] } };
  const got = app.project(teams, [], noHS, "A", "B", 2, weights, "headshots");
  check("a player with no rate for the stat projects null, not NaN", got, null);

  const withHS = { ...noHS, cur: { ...noHS.cur, hs: 7 } };
  const teams2 = { A: { players: [withHS] }, B: { players: [] } };
  const ok = app.project(teams2, [], withHS, "A", "B", 2, weights, "headshots");
  check("and one who has it projects a real number",
        typeof ok === "object" && ok !== null && isFinite(ok.perGame), true);
}

{
  const W = app.DEFAULT_WEIGHTS_BY_GAME_AND_STAT;
  check("every game carries a headshots entry, so the lookup never returns undefined",
        ["lol", "valorant", "cs2"].every((g) => typeof W[g].headshots === "object"), true);
  check("headshots carries no share weight in any game — the knockout calls it inert",
        [W.lol.headshots.share, W.valorant.headshots.share, W.cs2.headshots.share], [0, 0, 0]);
  check("CS2 is the only game with a non-zero headshots parameter at all",
        [W.lol.headshots.shrink, W.valorant.headshots.shrink, W.cs2.headshots.shrink], [0, 0, 3.0]);
}

/* ---- how much evidence is behind a number ----
 *
 * The app now tells people this, so it has to be right about it. The
 * thresholds are not opinions: scripts/dev/evidence_vs_accuracy.py
 * buckets out-of-sample error by the evidence each row had, and the
 * model's accuracy turns at eight games (valorant kills: +12.1% worse
 * than its own MAE at 0-1 games, +0.0% at 6-8, -5.2% at 8-12).
 */
check("below four games reads as thin", app.evidenceTier(3), "thin");
check("four games is no longer thin", app.evidenceTier(4), "limited");
check("seven games is still only limited", app.evidenceTier(7), "limited");
check("eight games is where the model gets better than its average",
      app.evidenceTier(8), "solid");
check("and more than eight stays solid", app.evidenceTier(40), "solid");
check("the thresholds match the measured turning points",
      [app.EVIDENCE_THIN, app.EVIDENCE_SOLID], [4, 8]);
check("a missing count is not silently called solid", app.evidenceTier(undefined), null);
check("nor is a NaN", app.evidenceTier(NaN), null);

// Career game counts, which differ in shape per game.
check("CS2 counts its per-game career log",
      app.careerGameCount({ career_games: [1, 2, 3] }), 3);
check("LoL reads the game count off its career aggregate",
      app.careerGameCount({ career: { g: 430, k: 4 } }), 430);
check("a player with neither has no career evidence",
      app.careerGameCount({ name: "x" }), 0);
check("an empty career log is zero, not a crash",
      app.careerGameCount({ career_games: [] }), 0);

/* The weighting is the part that stops the number lying in either
   direction. CS2 kills is career 1.0, so a player with 6 matches on file
   and 46 career games is NOT thin -- counting only matches would call it
   thin, and ignoring the weight would call a Valorant player solid off a
   career log the model never reads. */
const cs2Kills = { career: 1.0 };
near("a CS2 kills projection is evidenced by the career log",
     app.evidenceGamesFor({ career_games: new Array(46).fill(0) }, cs2Kills, 0.8, 6), 46);
check("and that player is solid, not thin",
      app.evidenceTier(app.evidenceGamesFor(
        { career_games: new Array(46).fill(0) }, cs2Kills, 0.8, 6)), "solid");

const noCareer = { career: 0 };
near("with no career weight only matches count",
     app.evidenceGamesFor({ career_games: new Array(46).fill(0) }, noCareer, null, 6), 6);

near("a half-weighted tier blends the two",
     app.evidenceGamesFor({ career: { g: 20 } }, { career: 0.5 }, 4.0, 10), 15);

/* The career log only counts when the career tier actually fired. A
   player with no career data has a null careerRate, and crediting them
   with career evidence would be inventing it. */
near("no career rate means no career evidence",
     app.evidenceGamesFor({ career_games: new Array(46).fill(0) }, cs2Kills, null, 6), 6);

/* And the field reaches the UI through project(), which is what every
   surface actually reads. */
{
  const history = Array.from({ length: 12 }, (_, i) =>
    match(`2026-0${(i % 9) + 1}-0${(i % 9) + 1}`, "A", "B", [20, 1, 1, 1, 1], [1, 1, 1, 1, 1]));
  const teams = { A: { players: [{ name: "p0", cur: { k: 20, d: 1, a: 1, g: 2 } }] },
                  B: { players: [] } };
  const w = { ...app.DEFAULT_WEIGHTS_BY_GAME_AND_STAT.valorant.kills };
  const r = app.project(teams, history, teams.A.players[0], "A", "B", 2, w, "kills");
  check("project() reports the evidence behind its own number",
        typeof r.evidenceGames === "number" && r.evidenceGames > 0, true);
}

/* ---- ranking by the edge that survives its own evidence ----
 *
 * Measured by scripts/dev/edge_realization.py: of the deviation the
 * model claims, the fraction that actually materialises is 0.34 below
 * four games and about 0.94 from twelve up. Ranking on the raw number
 * therefore promoted the rows the model knew least about, because
 * knowing less produces bigger disagreements.
 */
near("an edge off three games is worth about half its face value",
     app.edgeMultiplier(3), 0.52);
near("four games is the first step up", app.edgeMultiplier(4), 0.72);
near("eight games again", app.edgeMultiplier(8), 0.93);
near("and twelve or more is nearly face value", app.edgeMultiplier(12), 0.98);
near("well beyond twelve stays there", app.edgeMultiplier(400), 0.98);

check("the multiplier never inflates an edge",
      app.EDGE_REALIZATION.every((b) => b.factor <= 1), true);
check("and never increases as evidence falls",
      app.EDGE_REALIZATION.every((b, i, all) => i === 0 || b.factor >= all[i - 1].factor), true);

/* An unknown evidence count takes the worst factor, not the best: a row
   that cannot say what it rests on must not outrank one that can. */
near("unknown evidence is treated as the thinnest case",
     app.edgeMultiplier(undefined), 0.52);
near("and so is a NaN", app.edgeMultiplier(NaN), 0.52);

/* With no league rate to calibrate toward there is nothing to be
   typical OF, so the edge is scaled directly rather than guessed at. */
near("a positive edge is discounted, not flipped", app.adjustEdge(10, 2), 5.2);
near("a negative edge keeps its sign", app.adjustEdge(-10, 2), -5.2);
check("no edge stays no edge", app.adjustEdge(null, 2), null);
check("a non-numeric edge is refused", app.adjustEdge("4", 30), null);

/* Given a league rate, the correction belongs to the PROJECTION and the
   edge follows from it. Scaling the edge directly is only the same thing
   when the line happens to sit at the league average, and the two
   disagreed in sign on 5% of a real board.

   mean 20, projection 33, line 30, three games (0.52):
     calibrated projection = 20 + 0.52 * 13 = 26.76
     edge                  = 26.76 - 30     = -3.24
   whereas scaling the raw +3 edge would have said +1.56 -- opposite
   side of the line, on the same inputs. */
near("the correction is applied to the projection, not the edge",
     app.adjustEdge(3, 3, 33, 30, 20), -3.24);
near("a line sitting exactly at the league average makes the two agree",
     app.adjustEdge(13, 3, 33, 20, 20), 13 * 0.52);
near("a well-evidenced projection is barely moved",
     app.adjustEdge(3, 40, 33, 30, 20), (20 + 0.98 * 13) - 30);

/* The ordering itself, which is the whole point. */
{
  const rows = [
    { name: "thin", edge: 14, adjustedEdge: 14 * 0.52 },   // 7.28
    { name: "deep", edge: -12, adjustedEdge: -12 * 0.98 }, // -11.76
  ];
  check("a big edge off little evidence no longer outranks a solid one",
        app.rankEdges(rows).map((r) => r.name), ["deep", "thin"]);
  check("and the raw ordering would have had it the other way",
        [...rows].sort((a, b) => Math.abs(b.edge) - Math.abs(a.edge)).map((r) => r.name),
        ["thin", "deep"]);
}
{
  // Ambiguous rows (several posted lines, none named the market one)
  // still sort last, which they did before and must keep doing.
  const rows = [
    { name: "ambiguous", edge: null, adjustedEdge: null },
    { name: "real", edge: 2, adjustedEdge: 1.88 },
  ];
  check("rows with no usable edge stay at the bottom",
        app.rankEdges(rows).map((r) => r.name), ["real", "ambiguous"]);
}
{
  // A row from before adjustedEdge existed must still sort sanely.
  const rows = [{ name: "a", edge: 3 }, { name: "b", edge: 9 }];
  check("a row carrying only a raw edge falls back to it",
        app.rankEdges(rows).map((r) => r.name), ["b", "a"]);
}

/* ---- evidence borrowed from another competition ----
 *
 * A not-yet-started international event has no matches of its own, so
 * historyPool lends it the teams' home-region form. The map count behind
 * a projection then looks large while every one of those maps was played
 * against a different field.
 *
 * Found on a real board: all 78 Valorant lines were VCT Champions
 * fixtures, our projections sat 0.48 kills below each player's own
 * regional rate and the market's sat 1.48 below. What a step up in class
 * is worth cannot be fitted here -- there are zero cross-region matches
 * in any of the three games -- so the projection is left alone and only
 * the confidence in it is cut.
 */
check("a borrowed-roster event with nothing played is borrowed",
      app.historyIsBorrowed({ rosters_from_home_regions: true, past_matches: [] }), true);
check("once it has played, it is its own evidence again",
      app.historyIsBorrowed({ rosters_from_home_regions: true, past_matches: [{}] }), false);
check("an ordinary region is never borrowed",
      app.historyIsBorrowed({ past_matches: [] }), false);
check("and neither is a missing region", app.historyIsBorrowed(null), false);

/* Per team, not per region.
 *
 * Both halves of borrowing -- the scraper lending rosters and the app
 * lending history -- used to turn off the moment the event played one
 * match. A real board caught what that costs: Champions had exactly one
 * completed match, so its two participants counted as "the event has
 * started" and the other FOURTEEN teams with fixtures, every one of them
 * sitting in its home region with a full season behind it, lost their
 * roster and their history in the same tick. An event fills up one match
 * at a time.
 */
const mixed = {
  "VCT Champions": {
    teams: { "Team Liquid": {}, "Paper Rex": {}, "T1": { from_home_region: "VCT Pacific" } },
    past_matches: [{ date: "2026-09-24", teamA: "Team Liquid", teamB: "Paper Rex" }],
  },
  "VCT Pacific": {
    teams: { T1: {} },
    past_matches: [{ date: "2026-08-01", teamA: "T1", teamB: "Gen.G" },
                   { date: "2026-08-08", teamA: "DRX", teamB: "Gen.G" }],
  },
};

check("a team that has played at the event is its own evidence",
      app.historyIsBorrowed(mixed["VCT Champions"], "Team Liquid"), false);
check("a team that has not is borrowed, even though the event has started",
      app.historyIsBorrowed(mixed["VCT Champions"], "T1"), true);
check("a team nobody asked about is not borrowed",
      app.historyIsBorrowed(mixed["VCT Champions"], "Nobody"), false);

{
  const pool = app.historyPoolFor(mixed, "VCT Champions");
  check("the pool keeps the event's own match", pool.some((m) => m.teamB === "Paper Rex"), true);
  check("and adds the borrowed team's home season",
        pool.some((m) => m.teamA === "T1"), true);
  check("but not a home-region match the borrowed team is not in",
        pool.some((m) => m.teamA === "DRX"), false);
  check("three teams, two sources, no duplicates",
        pool.length, new Set(pool.map((m) => `${m.date}|${m.teamA}|${m.teamB}`)).size);
}

/* Files written before the scraper marked provenance per team carry only
 * the region-wide flag, which meant every roster or none. */
{
  const legacy = {
    "VCT Champions": { teams: { T1: {} }, past_matches: [], rosters_from_home_regions: true },
    "VCT Pacific": { teams: { T1: {} },
                     past_matches: [{ date: "2026-08-01", teamA: "T1", teamB: "Gen.G" }] },
  };
  check("a legacy borrowed region still borrows",
        app.historyPoolFor(legacy, "VCT Champions").length, 1);
  check("and still reads as borrowed",
        app.historyIsBorrowed(legacy["VCT Champions"], "T1"), true);
}

/* The current event is not the whole record.
 *
 * The scraper fetched the prior event, aggregated it into the "hist"
 * tier and threw the matches away, capping every Valorant player at a
 * median of 8 maps and resetting them to zero the day an event rolled
 * over. They are kept in history_matches now -- apart from past_matches,
 * which answers "what has happened at THIS event" for standings, where a
 * previous split's games would be wrong. */
{
  const carried = {
    R: {
      teams: { X: {} },
      past_matches: [{ date: "2026-06-01", teamA: "X", teamB: "Y" }],
      history_matches: [{ date: "2026-02-01", teamA: "X", teamB: "Z" },
                        { date: "2026-01-01", teamA: "X", teamB: "W" }],
    },
  };
  const pool = app.historyPoolFor(carried, "R");
  check("carried history joins the pool", pool.length, 3);
  check("the current event is still in it", pool.some((m) => m.teamB === "Y"), true);
  check("and so is what came before it", pool.some((m) => m.teamB === "W"), true);
}

check("an ordinary region's pool is just its own matches",
      app.historyPoolFor({ A: { teams: { X: {} },
                                past_matches: [{ date: "1", teamA: "X", teamB: "Y" }] } },
                         "A").length, 1);

/* The home-league tag on an international board.
 *
 * "Paper Rex vs Team Liquid" at Champions is two teams whose form, and
 * whose opponents all season, came from opposite sides of the world.
 * That is the most useful fact about the fixture and the card did not
 * say it.
 *
 * The rule carries no notion of "is this an international event",
 * deliberately: a team is tagged when its home league differs from the
 * tab, which is false for every team in a regional tab and true for
 * every visiting team in an international one. Nothing to maintain.
 */
{
  const world = {
    "VCT Champions": {
      teams: { "Paper Rex": {}, "Team Liquid": { from_home_region: "VCT EMEA" },
               "Wildcard": {} },
      past_matches: [{ date: "2026-09-24", teamA: "Paper Rex", teamB: "Team Liquid" }],
    },
    "VCT Pacific": {
      teams: { "Paper Rex": {}, "T1": {} },
      past_matches: [{ date: "2026-08-01", teamA: "Paper Rex", teamB: "T1" },
                     { date: "2026-08-08", teamA: "Paper Rex", teamB: "T1" }],
    },
    "VCT EMEA": {
      teams: { "Team Liquid": {}, "Wildcard": {} },
      past_matches: [{ date: "2026-08-01", teamA: "Team Liquid", teamB: "Wildcard" }],
    },
  };
  const at = (key) => app.homeRegionLookup(world, key);

  check("a visiting team is tagged with the league it actually plays in",
        at("VCT Champions")("Paper Rex"), "Pacific");
  check("including one that has already played at the event",
        // Paper Rex has a Champions match of its own and is still Pacific
        at("VCT Champions")("Paper Rex"), "Pacific");
  check("the scraper's own record of where a roster came from is honoured",
        at("VCT Champions")("Team Liquid"), "EMEA");
  check("a team that has played nowhere else is still placed",
        at("VCT Champions")("Wildcard"), "EMEA");

  check("nothing is tagged in the league it belongs to",
        at("VCT Pacific")("Paper Rex"), null);
  check("nor in another regional tab",
        at("VCT EMEA")("Team Liquid"), null);
  check("a team nobody has heard of is not invented",
        at("VCT Champions")("Nobody"), null);
  check("and neither is an empty name", at("VCT Champions")(""), null);
  check("an unknown tab yields no tags",
        app.homeRegionLookup(world, "Nowhere")("Paper Rex"), null);
  check("missing data is not a crash",
        app.homeRegionLookup(null, "VCT Champions")("Paper Rex"), null);
}

/* The league prefix is what every team on an international board
 * shares, so it carries no information there -- what distinguishes them
 * is what follows it, and card width is scarce. */
check("the shared league prefix is dropped", app.shortRegionLabel("VCT Americas"), "Americas");
check("and it is case-insensitive", app.shortRegionLabel("vct pacific"), "pacific");
check("a name that is only a prefix is kept rather than emptied",
      app.shortRegionLabel("VCT"), "VCT");
check("a region with no prefix is untouched", app.shortRegionLabel("LCS"), "LCS");

/* Against the committed Valorant file, which is where this was wanted. */
{
  const fs2 = fs.readFileSync(path.join(root, "valorant_data.json"), "utf8");
  const { regions } = JSON.parse(fs2);
  const intl = Object.keys(regions).find((k) => /champions/i.test(k));
  if (intl) {
    const at = app.homeRegionLookup(regions, intl);
    const names = Object.keys(regions[intl].teams || {});
    const tagged = names.filter((n) => at(n));
    check(`${intl}: every team on the board is placed (${tagged.length}/${names.length})`,
          tagged.length, names.length);
    check("and no tag repeats the tab it is shown in",
          tagged.every((n) => at(n) !== app.shortRegionLabel(intl)), true);
  }
  for (const key of Object.keys(regions)) {
    if (/champions/i.test(key)) continue;
    const at = app.homeRegionLookup(regions, key);
    const stray = Object.keys(regions[key].teams || {}).filter((n) => at(n));
    check(`${key} tags nothing, being the teams' own league`, stray, []);
  }
}

/* A line is live until its own fixture starts.
 *
 * It used to be live until the PAYLOAD hit 90 minutes old, and past
 * that the edge was suppressed entirely. That answers the wrong
 * question: what makes a line worthless is the match being played, not
 * the file being fetched a while ago, and on a board refreshed twice a
 * day the suppression fired constantly on fixtures that had not even
 * kicked off.
 *
 * The age is still shown, because lines do move. It just no longer
 * decides whether an edge exists.
 */
{
  const soon = new Date(Date.now() + 3 * 3600 * 1000).toISOString();
  const past = new Date(Date.now() - 3 * 3600 * 1000).toISOString();

  check("a line on a fixture that has not started is live",
        app.propIsLive({ start_time: soon }), true);
  check("and one whose fixture has started is not",
        app.propIsLive({ start_time: past }), false);
  check("a line with no start time stays live rather than being discarded",
        app.propIsLive({ line: 10.5 }), true);
  check("as does one whose start time will not parse",
        app.propIsLive({ start_time: "whenever" }), true);
  check("a missing prop is not a crash", app.propIsLive(null), true);

  /* The age of the PAYLOAD no longer enters into it. This is the whole
     behaviour change, so it is pinned directly: an ancient payload
     carrying a line on a match that has not happened is still live. */
  check("payload age does not decide liveness",
        app.propIsLive({ start_time: soon }, Date.now()), true);
  check("and the freshness helper still reports it, for the caveat",
        app.propsAreFresh({ fetched_at: new Date(
          Date.now() - (app.PROPS_MAX_AGE_MINUTES + 60) * 60000).toISOString() }), false);
}

/* Minutes are how the data arrives and not how anyone reads them. */
check("minutes stay minutes while they are readable", app.ageLabel(45), "45m old");
check("and become hours when they are not", app.ageLabel(312), "5h old");
check("and days past a couple of them", app.ageLabel(60 * 24 * 3), "3d old");
check("nothing to say about a missing age", app.ageLabel(null), null);
check("nor about a broken one", app.ageLabel(NaN), null);

check("borrowed evidence cannot read as solid",
      app.evidenceTier(app.effectiveEvidence(400, true)), "limited");
check("the cap sits one band below solid",
      app.EVIDENCE_BORROWED_CAP < app.EVIDENCE_SOLID, true);
near("an unborrowed count passes through untouched",
     app.effectiveEvidence(400, false), 400);
near("a borrowed count already below the cap is not raised",
     app.effectiveEvidence(2, true), 2);
check("a thin borrowed row stays thin rather than being promoted",
      app.evidenceTier(app.effectiveEvidence(2, true)), "thin");
check("a missing count is not turned into a number by the cap",
      app.effectiveEvidence(undefined, true), undefined);

/* The consequence that matters: the board stops treating a pile of
   somebody else's matches as though it vouched for this fixture. */
near("a borrowed row is discounted like a limited one",
     app.edgeMultiplier(app.effectiveEvidence(400, true)), 0.72);
near("the same row unborrowed would have been near face value",
     app.edgeMultiplier(400), 0.98);

console.log(`${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

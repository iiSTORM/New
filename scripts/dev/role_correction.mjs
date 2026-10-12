/* Does a player's role make the kills projection better?
 *
 * The Record tab splits the graded lines by role -- League position, CS2
 * box-score role, Valorant agent class -- and some splits lean: supports
 * and bot laners projected high, AWPers losing overs. The obvious model
 * change is a per-role correction to the projection. This tests it the
 * only way that counts, on data the correction never saw:
 *
 *   1. Backtest. Every player in every match since --since is projected
 *      point-in-time (projectPointInTime, data before the match only),
 *      with the role they held going into it.
 *   2. Rolling folds. A multiplier per role is fitted on everything
 *      before each fold (chosen to minimise absolute error, since a line
 *      is a median) and scored on the fold. Against it, ONE multiplier
 *      for everybody: if the role version cannot beat that, the role is
 *      not what helped.
 *   3. The graded lines. Each is re-projected with the multiplier fitted
 *      on matches before its own date, and the record against the posted
 *      line is compared with and without it.
 *
 * RESULT, 2026-10-12 (backtest since 2026-06-01):
 *
 *   backtest MAE       base     one multiplier   per role
 *   League             2.800    2.790            2.774   (supports -6.4%, the rest +-0.4%)
 *   CS2                6.082    6.094            6.094   (worse)
 *   Valorant           5.963    5.973            5.998   (worse)
 *
 *   graded lines       wins before -> after      model MAE before -> after
 *   League  (326)      212 -> 215                2.718 -> 2.633
 *                      bot 48 -> 53, supports 23 -> 20 (15 sides flipped)
 *   CS2     (1909)     999 -> 985                5.104 -> 5.113
 *   Valorant (356)     180 -> 176                5.702 -> 5.722
 *
 * NOT ADOPTED. CS2 and Valorant get worse. League's per-role gain over a
 * single multiplier is 0.016 of a kill, all of it supports, whose few
 * kills put the model's mean above their typical game -- a median-versus-
 * mean effect, not a role signal. Against the line it nets three bets
 * over 17 matches while losing three on supports. Rerun as the graded
 * record grows; adopt only if the per-role column beats the single
 * multiplier in the backtest AND the graded record improves in every game
 * it is applied to.
 *
 * Usage:
 *     node scripts/dev/role_correction.mjs [--since 2026-06-01] [--game lol|cs2|valorant]
 */
import fs from "node:fs";
import path from "node:path";
import { transformSync } from "@babel/core";
import React from "react";

const root = process.cwd();
const arg = (name, fallback) => {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : fallback;
};
const SINCE = arg("since", "2026-06-01");
const ONLY = arg("game", null);

const raw = fs.readFileSync(path.join(root, "src/app.jsx"), "utf8");
const body = raw.slice(raw.indexOf("try {") + "try {".length, raw.indexOf("const root = ReactDOM.createRoot"));
const { code } = transformSync(
  `${body}\nreturn { modelRecord, projectPointInTime, getActualStat, mapsCountedFor, historyPool,
                     cs2RolesBefore, agentClassBefore, DEFAULT_WEIGHTS_BY_GAME_AND_STAT };`,
  { presets: [["@babel/preset-react", { runtime: "classic" }]], filename: "app.jsx",
    parserOpts: { allowReturnOutsideFunction: true } });
const win = { innerWidth: 1400, addEventListener() {}, removeEventListener() {},
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} } };
const app = new Function("React", "ReactDOM", "window", "document", "fetch", "localStorage", "console", code)(
  React, { createRoot: () => ({ render() {} }) }, win,
  { getElementById: () => null, addEventListener() {}, removeEventListener() {} },
  () => new Promise(() => {}), win.localStorage, console);

const FILES = { lol: "data.json", cs2: "cs2_data.json", valorant: "valorant_data.json" };
const GRID = Array.from({ length: 81 }, (_, i) => 0.5 + 0.01 * i);
const absErr = (rows, m) => rows.reduce((a, r) => a + Math.abs(r.actual - m * r.proj), 0);
const bestMultiplier = (rows) => (rows.length < 100 ? 1 : GRID.reduce((best, m) => (absErr(rows, m) < absErr(rows, best) ? m : best), 1));
const results = JSON.parse(fs.readFileSync(path.join(root, "props_results.json"), "utf8"));

for (const [game, file] of Object.entries(FILES)) {
  if (ONLY && game !== ONLY) continue;
  const data = JSON.parse(fs.readFileSync(path.join(root, file), "utf8"));
  const weights = app.DEFAULT_WEIGHTS_BY_GAME_AND_STAT[game].kills;

  // 1. Backtest.
  const bt = [];
  for (const key of Object.keys(data.regions)) {
    const rd = data.regions[key];
    if (!rd.teams) continue;
    const pool = app.historyPool(data.regions, key);
    for (const m of rd.past_matches || []) {
      if (!m.date || m.date < SINCE || !m.actual) continue;
      const games = app.mapsCountedFor(m);
      for (const team of [m.teamA, m.teamB]) {
        if (!rd.teams[team] || !m.actual[team]) continue;
        const opp = team === m.teamA ? m.teamB : m.teamA;
        const cs2 = game === "cs2" ? app.cs2RolesBefore(pool, rd.teams, team, m.date) : null;
        for (const p of rd.teams[team].players || []) {
          const actual = app.getActualStat(m, team, p.name, "k");
          if (typeof actual !== "number") continue;
          const b = app.projectPointInTime(pool, rd.teams, p, team, opp, games, weights, m.date, "kills", m.patch);
          if (!b || typeof b.perGame !== "number") continue;
          const role = game === "lol" ? p.role : game === "cs2" ? (cs2.get(p.name) || null)
            : app.agentClassBefore(pool, team, p.name, m.date);
          bt.push({ date: m.date, role, proj: b.perGame * games, actual });
        }
      }
    }
  }
  bt.sort((a, b) => (a.date < b.date ? -1 : 1));

  // 2. Rolling folds: one multiplier against one per role.
  const dates = [...new Set(bt.map((r) => r.date))];
  const cuts = [1, 2, 3, 4].map((i) => dates[Math.floor(dates.length * i / 5)]).concat(["9999"]);
  let base = 0, single = 0, perRole = 0, n = 0;
  for (let i = 0; i < 4; i++) {
    const train = bt.filter((r) => r.date < cuts[i]);
    const test = bt.filter((r) => r.date >= cuts[i] && r.date < cuts[i + 1]);
    const g = bestMultiplier(train);
    const byRole = {};
    for (const role of new Set(train.map((r) => r.role))) byRole[role] = bestMultiplier(train.filter((r) => r.role === role));
    base += absErr(test, 1); single += absErr(test, g);
    perRole += test.reduce((a, r) => a + Math.abs(r.actual - (byRole[r.role] ?? 1) * r.proj), 0);
    n += test.length;
  }
  console.log(`\n${game}: ${bt.length} player-matches since ${SINCE}`);
  console.log(`  backtest MAE  base ${(base / n).toFixed(3)}  one multiplier ${(single / n).toFixed(3)}  per role ${(perRole / n).toFixed(3)}`);

  // 3. The graded lines, each with the multiplier fitted before its date.
  const graded = app.modelRecord(data.regions, Object.keys(data.regions), results, weights, "kills");
  const cache = new Map();
  const multFor = (role, date) => {
    const key = `${role}|${date}`;
    if (!cache.has(key)) cache.set(key, bestMultiplier(bt.filter((r) => r.role === role && r.date < date)));
    return cache.get(key);
  };
  const tally = new Map();
  for (const r of graded) {
    const role = r.role || r.cs2Role || r.agentClass || "none";
    const m = role === "none" ? 1 : multFor(role, r.match_date);
    const proj = r.projection * m;
    const side = proj > r.line ? "over" : proj < r.line ? "under" : null;
    const t = tally.get(role) || { n: 0, before: 0, after: 0, aeBefore: 0, aeAfter: 0, flipped: 0 };
    t.n++; t.before += r.won ? 1 : 0; t.after += side === r.result ? 1 : 0;
    t.aeBefore += Math.abs(r.actual - r.projection); t.aeAfter += Math.abs(r.actual - proj);
    if (side !== r.side) t.flipped++;
    tally.set(role, t);
  }
  const all = { n: 0, before: 0, after: 0, aeBefore: 0, aeAfter: 0, flipped: 0 };
  for (const t of tally.values()) for (const k of Object.keys(all)) all[k] += t[k];
  for (const [role, t] of [...tally.entries(), ["ALL", all]]) {
    if (!t.n) continue;
    console.log(`  ${String(role).padEnd(11)} lines ${String(t.n).padStart(4)}  wins ${t.before} -> ${t.after}`
      + `  model MAE ${(t.aeBefore / t.n).toFixed(3)} -> ${(t.aeAfter / t.n).toFixed(3)}  sides flipped ${t.flipped}`);
  }
}

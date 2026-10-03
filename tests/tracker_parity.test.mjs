/* The tracker runs in two languages over one file, and they have to agree.
 *
 * scripts/propedge (the CLI) and the Tracker page in src/app.jsx read and
 * write the SAME store.json in the private repo. A slip logged on the phone is
 * settled by the CLI and the other way round, so the two are not "parallel
 * implementations" that can be allowed to differ a little: any disagreement
 * about a payout, a refund or a rounding is a wrong bankroll.
 *
 * So the same operations are replayed through both and compared:
 *   - every scenario's resulting slips and ledger, field by field;
 *   - every settlement's status, payout, multiplier, estimate flag and reasons;
 *   - every rule check's sentences;
 *   - every write the Python refuses, the JS refuses too;
 *   - money parsing and rounding on awkward inputs;
 *   - the serialiser: a store written by Python, read and re-written by the
 *     JS, must come out byte for byte the same, or every phone write would
 *     show up in git as a reformat of the whole file.
 *
 * If the private store is checked out next to this repo, its balance is read
 * by both sides and compared too. It is opened READ-ONLY and nothing about
 * its contents is printed.
 *
 * Run: node tests/tracker_parity.test.mjs
 */
import fs from "fs";
import path from "path";
import React from "react";
import { spawnSync } from "child_process";
import { fileURLToPath } from "url";
import { createRequire } from "module";

/* Re-exec in a zone with an offset: a stake's timestamp is LOCAL time with
 * its offset, and under UTC a wrong offset would look right. */
if (!process.env.TRACKER_PARITY_TZ) {
  const { status, error } = spawnSync(process.execPath, [fileURLToPath(import.meta.url)],
    { stdio: "inherit", env: { ...process.env, TZ: "America/New_York", TRACKER_PARITY_TZ: "1" } });
  if (error) { console.error(`FAIL  could not re-exec under TZ: ${error.message}`); process.exit(1); }
  process.exit(status === null ? 1 : status);
}

const require = createRequire(import.meta.url);
const { transformSync } = require("@babel/core");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const raw = fs.readFileSync(path.join(root, "src/app.jsx"), "utf8");
const bootstrap = raw.indexOf("const root = ReactDOM.createRoot");
if (bootstrap < 0) throw new Error("bootstrap not found — has src/app.jsx changed shape?");
const body = raw.slice(raw.indexOf("try {") + "try {".length, bootstrap);
const { code } = transformSync(
  `${body}\nreturn { peCents, peMultiply, peFmt, peNow, peEmptyStore, pePlace, peGradeLegs, peSettle,`
  + ` peMoney, peRemoveSlip, peProblems, peSlip, peBalance, peExposure, peHistory, peSerialize, PeError };`,
  { presets: [["@babel/preset-react", { runtime: "classic" }]], filename: "app.jsx",
    parserOpts: { allowReturnOutsideFunction: true } });
const fakeWindow = { innerWidth: 1400, addEventListener() {}, removeEventListener() {},
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} } };
const pe = new Function(
  "React", "ReactDOM", "window", "document", "fetch", "localStorage", "console", code
)(React, { createRoot: () => ({ render() {} }) }, fakeWindow, { getElementById: () => null },
  () => new Promise(() => {}), fakeWindow.localStorage, console);

/* ------------------------------------------------------------ scenarios */

const leg = (player, team, stat, line, side, extra = {}) =>
  ({ sport: "cs2", player, team, opponent: "", stat, line, side, maps: 2, ...extra });
const AT = "2026-09-30T15:58:00-04:00";
const slip = (id, mode, stake_cents, legs, extra = {}) =>
  ({ id, mode, stake_cents, legs, placed_at: AT, ...extra });

const SCENARIOS = {
  "power 2-pick at the printed multiplier": [
    { op: "deposit", amount: "19.19", at: "2026-09-28T15:00:00-04:00" },
    { op: "place", fields: slip("slip_a", "power", 100, [leg("nyezin", "MEIA", "headshots", 16.5, "under"),
      leg("bexyz", "Huns", "headshots", 18.5, "under")], { multiplier: "3.8" }) },
    { op: "grade", slip: "slip_a", grades: [{ actual: 12 }, { actual: 14 }] },
    { op: "settle", slip: "slip_a" },
  ],
  "a whole-number push shrinks a power 3-pick": [
    { op: "deposit", amount: "10" },
    { op: "place", fields: slip("slip_b", "power", 150, [leg("d1maje", "A", "headshots", 8, "under"),
      leg("x", "B", "kills", 15.5, "over"), leg("y", "C", "kills", 20.5, "over")], { multiplier: "6" }) },
    { op: "grade", slip: "slip_b", grades: [{ actual: 8 }, { actual: 20 }, { actual: 22 }] },
    { op: "settle", slip: "slip_b" },
  ],
  "flex 4-pick, 3 of 4, no multiplier on the slip": [
    { op: "deposit", amount: "5" },
    { op: "place", fields: slip("slip_c", "flex", 300, [leg("a", "A", "kills", 10.5, "over"),
      leg("b", "B", "kills", 10.5, "over"), leg("c", "C", "kills", 10.5, "under"), leg("d", "D", "kills", 10.5, "under")]) },
    { op: "grade", slip: "slip_c", grades: [{ result: "won" }, { result: "won" }, { result: "won" }, { result: "lost" }] },
    { op: "settle", slip: "slip_c" },
  ],
  "flex 3-pick with a push and a DNP is refunded": [
    { op: "deposit", amount: "5" },
    { op: "place", fields: slip("slip_d", "flex", 200, [leg("a", "A", "kills", 10, "over"),
      leg("b", "B", "kills", 10.5, "over"), leg("c", "C", "kills", 9.5, "under")]) },
    { op: "grade", slip: "slip_d", grades: [{ actual: 10 }, { dnp: true }, { actual: 3 }] },
    { op: "settle", slip: "slip_d" },
  ],
  "a lost power slip pays nothing and writes no entry": [
    { op: "deposit", amount: "5" },
    { op: "place", fields: slip("slip_e", "power", 100, [leg("a", "A", "kills", 10.5, "over"),
      leg("b", "B", "kills", 10.5, "over")], { multiplier: "3" }) },
    { op: "grade", slip: "slip_e", grades: [{ actual: 11 }, { actual: 4 }] },
    { op: "settle", slip: "slip_e" },
  ],
  "the recorded payout overrides the estimate": [
    { op: "deposit", amount: "5" },
    { op: "place", fields: slip("slip_f", "flex", 300, [leg("a", "A", "kills", 10.5, "over"),
      leg("b", "B", "kills", 10.5, "over"), leg("c", "C", "kills", 10.5, "over")]) },
    { op: "grade", slip: "slip_f", grades: [{ actual: 11 }, { actual: 12 }, { actual: 2 }] },
    { op: "settle", slip: "slip_f", payout: "3.60" },
  ],
  "an unknown leg needs the real payout": [
    { op: "deposit", amount: "5" },
    { op: "place", fields: slip("slip_g", "power", 100, [leg("a", "A", "kills", 10.5, "over"),
      leg("b", "B", "kills", 10.5, "over")], { multiplier: "3" }) },
    { op: "grade", slip: "slip_g", grades: [{ actual: 11 }, { result: "unknown" }] },
    { op: "settle", slip: "slip_g" },
    { op: "settle", slip: "slip_g", payout: "3" },
  ],
  "odd stakes round half-up": [
    { op: "deposit", amount: "$1,234.565" },
    { op: "place", force: true, fields: slip("slip_h", "power", 91, [leg("a", "A", "kills", 1.5, "over"),
      leg("b", "B", "kills", 1.5, "over"), leg("c", "C", "kills", 1.5, "over"), leg("d", "D", "kills", 1.5, "over"),
      leg("e", "E", "kills", 1.5, "over"), leg("f", "F", "kills", 1.5, "over")]) },
    { op: "grade", slip: "slip_h", grades: [2, 2, 2, 2, 2, 2].map((actual) => ({ actual })) },
    { op: "settle", slip: "slip_h" },
    { op: "place", fields: slip("slip_i", "flex", 333, [leg("a", "A", "kills", 1.5, "over"),
      leg("b", "B", "kills", 1.5, "over"), leg("c", "C", "kills", 1.5, "over"), leg("d", "D", "kills", 1.5, "over"),
      leg("e", "E", "kills", 1.5, "over")]) },
    { op: "grade", slip: "slip_i", grades: [2, 2, 2, 0, 0].map((actual) => ({ actual })) },
    { op: "settle", slip: "slip_i" },
    { op: "withdrawal", amount: "0.07" },
  ],
  "rule breaks are refused unless forced": [
    { op: "deposit", amount: "5" },
    { op: "place", fields: slip("slip_j", "power", 100, [leg("Tomás", "Same", "kills", 10.5, "over"),
      leg("tomás", "Same", "assists", 4.5, "over")]) },
    { op: "place", fields: slip("slip_k", "power", 100, [leg("a", "", "kills", 10.5, "over"),
      leg("b", "B", "kills", 10.5, "over")]) },
    { op: "place", fields: slip("slip_l", "power", 50, [leg("solo", "A", "kills", 10.5, "over")]) },
    { op: "place", force: true, fields: slip("slip_j", "power", 100, [leg("Tomás", "Same", "kills", 10.5, "over"),
      leg("tomás", "Same", "assists", 4.5, "over")]) },
  ],
  "tennis players are their own team": [
    { op: "deposit", amount: "5" },
    { op: "place", fields: slip("slip_m", "power", 100, [
      leg("Sinner", "", "total games", 22.5, "over", { sport: "tennis", maps: null }),
      leg("Alcaraz", "", "aces", 6.5, "under", { sport: "tennis", maps: null })]) },
  ],
  "no paying twice, no settling an open leg": [
    { op: "deposit", amount: "5" },
    { op: "place", fields: slip("slip_n", "power", 100, [leg("a", "A", "kills", 10.5, "over"),
      leg("b", "B", "kills", 10.5, "over")], { multiplier: "3" }) },
    { op: "settle", slip: "slip_n" },
    { op: "grade", slip: "slip_n", grades: [{ actual: 11 }, null] },
    { op: "settle", slip: "slip_n" },
    { op: "grade", slip: "slip_n", grades: [null, { actual: 30 }] },
    { op: "settle", slip: "slip_n" },
    { op: "settle", slip: "slip_n" },
    { op: "place", fields: slip("slip_n", "power", 100, [leg("c", "C", "kills", 10.5, "over"),
      leg("d", "D", "kills", 10.5, "over")]) },
  ],
};

/* ------------------------------------------------------------- the JS side */

function runJs(ops) {
  const store = pe.peEmptyStore();
  const results = [];
  for (const o of ops) {
    // Each op runs on a copy and is kept only if it succeeds, as peCommit
    // does: a refused write leaves the stored file as it was.
    const next = JSON.parse(JSON.stringify(store));
    try {
      let out = null;
      if (o.op === "deposit" || o.op === "withdrawal") pe.peMoney(next, o.op, o.amount, "", o.at || "2026-10-01T09:00:00-04:00");
      else if (o.op === "place") out = { problems: pe.peProblems(pe.pePlace(next, o.fields, !!o.force)) };
      else if (o.op === "grade") pe.peGradeLegs(next, o.slip, o.grades);
      else if (o.op === "settle") {
        const got = pe.peSettle(next, o.slip, o.payout);
        out = { status: got.status, payout_cents: got.payout_cents, multiplier: got.multiplier,
          estimated: got.estimated, reasons: got.reasons };
      }
      Object.assign(store, next);
      results.push({ ok: true, ...(out || {}) });
    } catch (e) {
      if (!(e instanceof pe.PeError)) throw e;
      results.push({ ok: false });
    }
  }
  return { store, results };
}

/* --------------------------------------------------------- the Python side */

const PY = String.raw`
import json, sys
from propedge.store import Tracker
from propedge.slips import Slip, Leg
from propedge.ledger import Ledger, LedgerError
from propedge.money import to_cents, multiply

FLOATS = ("line", "model_prob", "line_at_placement", "closing_line", "actual")

def as_cli(fields):
    # The CLI parses every numeric leg field with float(); do the same so
    # the stored file looks like one the CLI wrote.
    fields = dict(fields)
    legs = []
    for leg in fields.pop("legs"):
        leg = {k: (float(v) if k in FLOATS and v is not None else v) for k, v in leg.items()}
        legs.append(Leg(**leg))
    return Slip(legs=legs, **fields)

def run(ops):
    t = Tracker("/nonexistent/never-saved.json")
    results = []
    for o in ops:
        snapshot = json.dumps(blob(t))
        try:
            out = {}
            if o["op"] in ("deposit", "withdrawal"):
                t.ledger.add(o["op"], to_cents(o["amount"]), None, note="",
                             at=o.get("at") or "2026-10-01T09:00:00-04:00")
            elif o["op"] == "place":
                s = t.place(as_cli(o["fields"]), force=bool(o.get("force")))
                out = {"problems": s.problems()}
            elif o["op"] == "grade":
                s = t.find(o["slip"])
                for leg, g in zip(s.legs, o["grades"]):
                    if not g:
                        continue
                    if g.get("dnp"):
                        leg.grade(dnp=True)
                    elif g.get("actual") is not None:
                        leg.grade(g["actual"])
                    else:
                        leg.result = g["result"]
            elif o["op"] == "settle":
                if t.find(o["slip"]).is_settled:
                    raise ValueError("already settled")
                got = t.settle(o["slip"], o.get("payout"))
                out = {"status": got.status, "payout_cents": got.payout_cents,
                       "multiplier": None if got.multiplier is None else str(got.multiplier),
                       "estimated": got.estimated, "reasons": list(got.reasons)}
            results.append({"ok": True, **out})
        except (ValueError, KeyError, LedgerError):
            restore(t, json.loads(snapshot))
            results.append({"ok": False})
    return {"store": blob(t), "results": results}

def blob(t):
    return {"version": 1, "slips": [s.as_json() for s in t.slips],
            "ledger": t.ledger.as_json(), "payout_table": t.table.as_json()}

def restore(t, b):
    t.slips = [Slip.from_json(s) for s in b["slips"]]
    t.ledger = Ledger(b["ledger"])

req = json.load(sys.stdin)
out = {"scenarios": {name: run(ops) for name, ops in req["scenarios"].items()},
       "cents": [], "multiply": []}
for a in req["cents"]:
    try:
        out["cents"].append(to_cents(a))
    except Exception:
        out["cents"].append(None)
out["multiply"] = [multiply(c, m) for c, m in req["multiply"]]
out["texts"] = {name: json.dumps(r["store"], indent=1, sort_keys=True) + "\n"
                for name, r in out["scenarios"].items()}
if req.get("real"):
    t = Tracker.load(req["real"])
    out["real"] = {"balance": t.balance(), "exposure": t.exposure()}
print(json.dumps(out))
`;

const CENTS = ["19.19", "1", "0.005", "0.004", "2.675", "$1,234.565", " 7.10 ", "-1.005", "abc", "", "1.2.3", ".5", "100."];
const MULTIPLY = [[91, "37.5"], [333, "0.4"], [125, "1.25"], [1, "2.25"], [150, "3.8"], [199, "0.4"], [1919, "6.5"]];
const REAL = [path.resolve(root, "../propedge/store.json")].find((p) => fs.existsSync(p));

const py = spawnSync("python3", ["-c", PY], {
  cwd: root, encoding: "utf8", maxBuffer: 64 << 20,
  env: { ...process.env, PYTHONPATH: path.join(root, "scripts") },
  input: JSON.stringify({ scenarios: SCENARIOS, cents: CENTS, multiply: MULTIPLY, real: REAL || null }),
});
if (py.status !== 0) { console.error(py.stderr); console.error("FAIL  the Python side did not run"); process.exit(1); }
const want = JSON.parse(py.stdout);

/* -------------------------------------------------------------- compare */

let failures = 0, checks = 0;
const fail = (msg) => { failures++; console.error(`FAIL  ${msg}`); };
const same = (a, b) => JSON.stringify(normalise(a)) === JSON.stringify(normalise(b));
function normalise(v) {
  if (Array.isArray(v)) return v.map(normalise);
  if (v && typeof v === "object") return Object.keys(v).sort().reduce((o, k) => { o[k] = normalise(v[k]); return o; }, {});
  return v;
}
// Ledger ids are random on both sides, and a payout's timestamp is "now" on
// both sides; everything else must match exactly.
const comparableLedger = (ledger) => ledger.map((e) => ({ ...e, id: undefined,
  at: e.kind === "payout" || e.kind === "refund" ? undefined : e.at }));

for (const [name, ops] of Object.entries(SCENARIOS)) {
  const js = runJs(ops);
  const py = want.scenarios[name];
  checks++;
  ops.forEach((o, i) => {
    if (!same(js.results[i], py.results[i])) {
      fail(`${name}: op ${i} (${o.op}) differs\n  js: ${JSON.stringify(js.results[i])}\n  py: ${JSON.stringify(py.results[i])}`);
    }
  });
  if (!same(js.store.slips, py.store.slips)) {
    fail(`${name}: slips differ\n  js: ${JSON.stringify(js.store.slips)}\n  py: ${JSON.stringify(py.store.slips)}`);
  }
  if (!same(comparableLedger(js.store.ledger), comparableLedger(py.store.ledger))) {
    fail(`${name}: ledgers differ\n  js: ${JSON.stringify(js.store.ledger)}\n  py: ${JSON.stringify(py.store.ledger)}`);
  }
  if (!same(js.store.payout_table, py.store.payout_table)) fail(`${name}: payout tables differ`);
  // Byte-for-byte: Python's file, read and rewritten by the JS, is unchanged.
  const text = want.texts[name];
  const rewritten = pe.peSerialize(JSON.parse(text));
  if (rewritten !== text) {
    const at = [...text].findIndex((c, i) => c !== rewritten[i]);
    fail(`${name}: the JS rewrite of Python's file is not identical, first difference at char ${at}:\n`
      + `  py: ${JSON.stringify(text.slice(Math.max(0, at - 40), at + 40))}\n`
      + `  js: ${JSON.stringify(rewritten.slice(Math.max(0, at - 40), at + 40))}`);
  }
}

CENTS.forEach((a, i) => {
  checks++;
  let got;
  try { got = pe.peCents(a); } catch (e) { got = null; }
  if (got !== want.cents[i]) fail(`peCents(${JSON.stringify(a)}) = ${got}, to_cents says ${want.cents[i]}`);
});
MULTIPLY.forEach(([c, m], i) => {
  checks++;
  const got = pe.peMultiply(c, m);
  if (got !== want.multiply[i]) fail(`peMultiply(${c}, ${m}) = ${got}, multiply says ${want.multiply[i]}`);
});

// The stake's timestamp: local time with the offset, as ledger._now writes it.
checks++;
const stamp = pe.peNow(new Date("2026-10-03T01:30:05Z"));
if (stamp !== "2026-10-02T21:30:05-04:00") fail(`peNow wrote ${stamp}, expected 2026-10-02T21:30:05-04:00`);

if (REAL) {
  checks++;
  const store = JSON.parse(fs.readFileSync(REAL, "utf8"));
  if (pe.peBalance(store) !== want.real.balance || pe.peExposure(store) !== want.real.exposure) {
    fail("the private store's balance or exposure differs between the app and the CLI");
  }
  const reread = JSON.parse(pe.peSerialize(store));
  if (!same(reread, store)) fail("rewriting the private store through the app would change its contents");
  console.log("ok    private store: app and CLI agree on balance and exposure (read-only)");
} else {
  console.log("skip  private store not checked out next to this repo");
}

if (failures) { console.error(`\n${failures} of ${checks} tracker parity checks FAILED`); process.exit(1); }
console.log(`ok    ${checks} tracker parity checks: the app and the CLI agree`);

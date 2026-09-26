/* Every suite, one command, honest exit code.
 *
 * This exists because of a specific mistake. There was no `npm test`, so
 * each run was a hand-written loop, and one of them piped every node
 * suite through `tail -1` before chaining on `&&`. A pipeline's exit
 * status is its LAST command's, so tail's zero masked a failing suite --
 * which printed its worst row and scrolled past looking like a pass. A
 * broken parity harness reached main that way and was caught by CI two
 * minutes later, which is the wrong place to catch it.
 *
 * CI already runs each of these as its own step (see
 * .github/workflows/tests.yml). This is the same SET -- the node suites are
 * discovered here and hand-listed there, so the two orders differ -- which
 * is what makes "it passed locally" and "it passed in CI" mean the same
 * thing. Adding a tests/*.test.mjs file gets it run here automatically and
 * still needs a step adding there.
 *
 *   npm test
 */
import { spawnSync } from "child_process";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const suites = [
  ["python tests", "python3", ["-m", "pytest", "tests/", "-q"]],
  ["index.html matches src/", "node", ["build/build-frontend.js", "--check"]],
  ...fs.readdirSync(path.join(root, "tests"))
    .filter((f) => f.endsWith(".test.mjs"))
    .sort()
    .map((f) => [f, "node", [path.join("tests", f)]]),
];

const failed = [];
for (const [name, cmd, args] of suites) {
  process.stdout.write(`\n=== ${name} ${"=".repeat(Math.max(0, 56 - name.length))}\n`);
  // stdio inherit, so a failing suite's own output is what you read --
  // not a summary line this script decided to keep.
  const r = spawnSync(cmd, args, { cwd: root, stdio: "inherit" });
  if (r.status !== 0 || r.error) failed.push(name);
}

console.log("");
if (failed.length) {
  console.error(`FAILED: ${failed.join(", ")}`);
  process.exit(1);
}
console.log(`All ${suites.length} suites passed.`);

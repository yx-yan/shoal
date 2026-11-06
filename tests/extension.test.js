// Run: node --test tests/extension.test.js
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");
const { belongsTo, newWorkers, readRuns } = require("../vscode-terminals/extension.js");

const run = (repo, workers) => ({ id: "r1", repo, workers });

test("matches the repo, a folder inside it, and a parent of it", () => {
  assert.ok(belongsTo("/a/repo", ["/a/repo"]));
  assert.ok(belongsTo("/a/repo", ["/a/repo/src"]));
  assert.ok(belongsTo("/a/repo", ["/a"]));
  assert.ok(!belongsTo("/a/repo", ["/a/repo2"]));
  assert.ok(!belongsTo("/a/repo", ["/b"]));
});

test("opens each running worker once and skips the rest", () => {
  const seen = new Set();
  const workers = {
    api: { engine: "codex", status: "running", log: "/l/api.log" },
    old: { engine: "claude", status: "done", log: "/l/old.log" },
  };
  assert.deepStrictEqual(newWorkers([run("/a/repo", workers)], ["/a"], seen),
    [{ name: "codex: api", log: "/l/api.log" }]);
  assert.deepStrictEqual(newWorkers([run("/a/repo", workers)], ["/a"], seen), []);
  assert.deepStrictEqual(newWorkers([run("/x/other", workers)], ["/a"], new Set()), []);
});

test("skips unreadable runs", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "shoal-"));
  fs.mkdirSync(path.join(dir, "bad"));
  fs.writeFileSync(path.join(dir, "bad", "run.json"), "{");
  fs.mkdirSync(path.join(dir, "good"));
  fs.writeFileSync(path.join(dir, "good", "run.json"), JSON.stringify(run("/a", {})));
  assert.strictEqual(readRuns(dir).length, 1);
  assert.deepStrictEqual(readRuns(path.join(dir, "missing")), []);
});

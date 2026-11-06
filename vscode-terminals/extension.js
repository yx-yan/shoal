// Shows each running /shoal worker's log in its own terminal. The worker doesn't run in the
// terminal, so closing it stops nothing.
const fs = require("fs");
const os = require("os");
const path = require("path");

const POLL_MS = 2000;

function runsDir() {
  return path.join(process.env.SHOAL_HOME || path.join(os.homedir(), ".shoal"), "runs");
}

function readRuns(dir) {
  let names = [];
  try {
    names = fs.readdirSync(dir);
  } catch {
    return [];
  }
  const runs = [];
  for (const name of names) {
    try {
      runs.push(JSON.parse(fs.readFileSync(path.join(dir, name, "run.json"), "utf8")));
    } catch {
      // Not a run, or caught mid-write: the next poll sees it.
    }
  }
  return runs;
}

// A workspace can be the repo itself, a folder inside it, or a parent of several repos.
function belongsTo(repo, folders) {
  return folders.some(
    (f) => f === repo || f.startsWith(repo + path.sep) || repo.startsWith(f + path.sep),
  );
}

function newWorkers(runs, folders, seen) {
  const found = [];
  for (const run of runs) {
    if (!belongsTo(run.repo, folders)) continue;
    for (const [id, w] of Object.entries(run.workers || {})) {
      const key = `${run.id}/${id}`;
      if (w.status === "running" && !seen.has(key)) {
        seen.add(key);
        found.push({ name: `${w.engine}: ${id}`, log: w.log });
      }
    }
  }
  return found;
}

function activate(context) {
  const vscode = require("vscode");
  const seen = new Set();

  const poll = () => {
    const folders = (vscode.workspace.workspaceFolders || []).map((f) => {
      try {
        return fs.realpathSync(f.uri.fsPath);
      } catch {
        return f.uri.fsPath;
      }
    });
    for (const w of newWorkers(readRuns(runsDir()), folders, seen)) {
      vscode.window
        .createTerminal({ name: w.name, shellPath: "/usr/bin/tail", shellArgs: ["-n", "+1", "-F", w.log] })
        .show(true);
    }
  };

  poll();
  const timer = setInterval(poll, POLL_MS);
  context.subscriptions.push({ dispose: () => clearInterval(timer) });
}

module.exports = { activate, belongsTo, newWorkers, readRuns };

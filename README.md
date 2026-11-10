# Shoal

A Claude Code skill that runs a small team of coding agents. You give Claude Code one request
with `/shoal`. It splits the work, runs Codex and Claude Code workers in parallel, checks the
combined result itself, and hands you a short report.

```text
/shoal <request>
   │
   ▼
Claude Code (lead): splits the work, you confirm once
   ├─ codex worker   in its own git worktree
   └─ claude worker  in its own git worktree
   │
   ▼
Lead reads the diffs and runs the acceptance commands; failures go back to the worker
   │
   ▼
Merge onto shoal/<run>/integration, run the full tests
   │
   ▼
A fresh read-only agent writes the report; the code computes the verdict
```

Your current branch and your remote are never touched. Merging the integration branch is your call.

## Requirements

- Claude Code and the Codex CLI, both logged in. The CLIs bundled with their VS Code extensions
  are found automatically.
- Python 3.10+ and git.

## Install

```bash
ln -s "$PWD" ~/.claude/skills/shoal
```

Optional: open a VS Code terminal for each running worker.

```bash
python3 vscode-terminals/build_vsix.py
code --install-extension vscode-terminals/shoal-terminals.vsix
```

Without it, follow a worker with `tail -F ~/.shoal/runs/<run>/logs/<id>.log`.

## Limits

- At most 3 workers at once, and 1 on Claude, since the lead shares that quota.
- A worker runs for 30 minutes at most by default.
- Workers can't be messaged while running. Send feedback afterwards with `--resume`.
- New worktrees have no `.env`, `.venv` or `node_modules`. Secrets are never copied in.
- The verdict comes from what the lead records in `verification.json`. The code catches a
  requirement marked passed with a failing exit code, not a made-up exit code, and it doesn't
  check the report's wording against the verdict.

## Tests

```bash
python3 tests/test_shoal.py
node --test tests/extension.test.js
```

## License

[MIT](LICENSE)

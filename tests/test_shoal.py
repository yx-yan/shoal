"""Offline tests: fake claude and codex scripts, real git. Run: python3 tests/test_shoal.py"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SHOAL = SCRIPTS / "shoal.py"
sys.path.insert(0, str(SCRIPTS))
import shoal  # noqa: E402

GOOD = {"status": "done", "summary": "ok", "changed_files": [], "tests_run": [], "unresolved": []}

FAKE_CODEX = r"""#!/usr/bin/env python3
import json, os, sys, time, pathlib
args = sys.argv[1:]
open(os.environ["FAKE_ARGS"], "a").write(json.dumps(args) + "\n")
out = args[args.index("-o") + 1]
print(json.dumps({"type": "thread.started", "thread_id": "codex-session"}), flush=True)
b = os.environ.get("FAKE_BEHAVIOR", "ok")
if b == "sleep": time.sleep(60)
if b == "crash": sys.exit(3)
pathlib.Path("made-by-" + os.environ.get("FAKE_FILE", "codex") + ".txt").write_text("x")
pathlib.Path(out).write_text("not json" if b == "invalid" else os.environ["FAKE_RESULT"])
"""

FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json, os, sys, pathlib
open(os.environ["FAKE_ARGS"], "a").write(json.dumps(sys.argv[1:]) + "\n")
print(json.dumps({"type": "system", "subtype": "init", "session_id": "claude-session"}), flush=True)
pathlib.Path("made-by-claude.txt").write_text("x")
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": "claude-session",
                  "structured_output": json.loads(os.environ["FAKE_RESULT"])}), flush=True)
"""


class Base(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        for args in (["init", "-q", "-b", "main"], ["config", "user.name", "t"],
                     ["config", "user.email", "t@example.com"], ["commit", "-q", "--allow-empty", "-m", "first"]):
            subprocess.run(["git", "-C", str(self.repo), *args], check=True)
        self.env = {**os.environ, "SHOAL_HOME": str(self.tmp / "home"), "FAKE_ARGS": str(self.tmp / "args"),
                    "FAKE_RESULT": json.dumps(GOOD),
                    "SHOAL_CODEX_BIN": self.fake("codex", FAKE_CODEX),
                    "SHOAL_CLAUDE_BIN": self.fake("claude", FAKE_CLAUDE)}
        self.prompt = self.tmp / "prompt.md"
        self.prompt.write_text("do it")
        self.run_id = self.shoal("init", "--repo", str(self.repo), "--request", "r").stdout.strip()
        self.dir = self.tmp / "home" / "runs" / self.run_id

    def fake(self, name: str, source: str) -> str:
        p = self.tmp / f"fake-{name}"
        p.write_text(source)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
        return str(p)

    def shoal(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SHOAL), *args], capture_output=True, text=True,
                              env={**self.env, **env})

    def start(self, worker: str, engine: str = "codex", mode: str = "write", *extra: str, **env: str):
        return self.shoal("start", self.run_id, "--id", worker, "--engine", engine, "--mode", mode,
                         "--prompt-file", str(self.prompt), *extra, **env)

    def state(self) -> dict:
        return json.loads((self.dir / "run.json").read_text())

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True)

    def commands(self) -> list[list[str]]:
        return [json.loads(line) for line in Path(self.env["FAKE_ARGS"]).read_text().splitlines()]


class Workers(Base):
    def test_write_worker_commits_on_its_own_branch(self) -> None:
        p = self.start("api")
        self.assertEqual(p.returncode, 0, p.stderr)
        w = self.state()["workers"]["api"]
        self.assertEqual((w["status"], w["session_id"]), ("done", "codex-session"))
        self.assertEqual(w["branch"], f"shoal/{self.run_id}/api")
        self.assertIn("made-by-codex.txt", self.git("show", "--name-only", w["branch"]).stdout)
        self.assertEqual(self.git("rev-list", "--count", "main").stdout.strip(), "1")
        self.assertFalse((self.repo / "made-by-codex.txt").exists())

    def test_claude_worker_result_comes_from_the_result_event(self) -> None:
        self.assertEqual(self.start("tests", "claude", "write", "--allow-bash", "Bash(pytest:*)").returncode, 0)
        self.assertEqual(self.state()["workers"]["tests"]["session_id"], "claude-session")
        self.assertEqual(json.loads((self.dir / "results" / "tests.json").read_text()), GOOD)

    def test_failures_are_recorded_as_they_happened(self) -> None:
        self.assertEqual(self.start("a", FAKE_BEHAVIOR="crash").returncode, 1)
        self.assertEqual(self.start("b", FAKE_BEHAVIOR="invalid").returncode, 1)
        self.assertEqual(self.start("c", FAKE_RESULT=json.dumps({**GOOD, "status": "blocked"})).returncode, 1)
        self.assertEqual(self.start("d", "codex", "write", "--timeout", "1", FAKE_BEHAVIOR="sleep").returncode, 1)
        workers = self.state()["workers"]
        self.assertEqual([workers[k]["status"] for k in "abcd"],
                         ["failed", "invalid_result", "blocked", "timeout"])

    def test_concurrency_limit(self) -> None:
        def fill() -> None:
            self.start("slow", "codex", "write", "--timeout", "4", FAKE_BEHAVIOR="sleep")

        thread = threading.Thread(target=fill)
        thread.start()
        for _ in range(100):
            if (self.dir / "run.json").exists() and "slow" in self.state()["workers"]:
                break
            time.sleep(0.05)
        with shoal.locked(self.dir) as state:  # two more slots taken by live processes
            for name in ("x", "y"):
                state["workers"][name] = {**state["workers"]["slow"], "pid": os.getpid()}
        p = self.start("over")
        self.assertEqual(p.returncode, 2)
        self.assertIn("Already running 3", p.stderr)
        with shoal.locked(self.dir) as state:
            for name in ("x", "y"):
                del state["workers"][name]
        thread.join()

    def test_one_claude_worker_at_a_time(self) -> None:
        with shoal.locked(self.dir) as state:
            state["workers"]["other"] = {"engine": "claude", "mode": "read", "status": "running",
                                         "pid": os.getpid()}
        p = self.start("second", "claude", "read")
        self.assertEqual(p.returncode, 2)
        self.assertIn("Claude worker", p.stderr)

    def test_dead_worker_is_reported_lost(self) -> None:
        with shoal.locked(self.dir) as state:
            state["workers"]["gone"] = {"engine": "codex", "mode": "read", "status": "running",
                                        "pid": 99999999, "attempts": 1}
        self.shoal("status", self.run_id)
        self.assertEqual(self.state()["workers"]["gone"]["status"], "lost")

    def test_resume_reuses_session_and_worktree(self) -> None:
        self.start("api", FAKE_BEHAVIOR="crash")
        first = self.state()["workers"]["api"]
        p = self.shoal("start", self.run_id, "--id", "api", "--resume", "--prompt-file", str(self.prompt))
        self.assertEqual(p.returncode, 0, p.stderr)
        w = self.state()["workers"]["api"]
        self.assertEqual((w["attempts"], w["worktree"], w["status"]), (2, first["worktree"], "done"))
        last = self.commands()[-1]
        self.assertEqual(last[last.index("resume") + 1], "codex-session")
        self.assertLess(last.index("-s"), last.index("resume"))

    def test_read_worker_has_no_worktree_and_is_sandboxed(self) -> None:
        self.assertEqual(self.start("look", "codex", "read").returncode, 0)
        self.assertIsNone(self.state()["workers"]["look"]["branch"])
        cmd = self.commands()[-1]
        self.assertEqual(cmd[cmd.index("-s") + 1], "read-only")

    def test_write_task_needs_git(self) -> None:
        plain = self.tmp / "plain"
        plain.mkdir()
        run = self.shoal("init", "--repo", str(plain), "--request", "r").stdout.strip()
        p = self.shoal("start", run, "--id", "a", "--engine", "codex", "--mode", "write",
                      "--prompt-file", str(self.prompt))
        self.assertEqual(p.returncode, 2)
        self.assertIn("git repository", p.stderr)


class Safety(unittest.TestCase):
    def test_commands_have_no_forbidden_flags(self) -> None:
        os.environ["SHOAL_CODEX_BIN"] = os.environ["SHOAL_CLAUDE_BIN"] = "bin"
        self.addCleanup(os.environ.pop, "SHOAL_CODEX_BIN")
        self.addCleanup(os.environ.pop, "SHOAL_CLAUDE_BIN")
        for mode in ("write", "read"):
            w = {"mode": mode, "session_id": None, "allow_bash": ["Bash(pytest:*)"]}
            for cmd in (shoal.codex_command(w, "p", ".", Path("r"), git_repo=True), shoal.claude_command(w, "p")):
                self.assertFalse([a for a in cmd for bad in shoal.FORBIDDEN_FLAGS if bad in a])

    def test_forbidden_flag_is_refused(self) -> None:
        for flag in ("--permission-mode=bypassPermissions", "danger-full-access",
                     "--dangerously-bypass-approvals-and-sandbox"):
            with self.assertRaises(shoal.ShoalError):
                shoal.check_flags([flag])

    def test_broad_bash_rules_are_refused(self) -> None:
        shoal.check_bash_rule("Bash(pytest:*)")
        for rule in ("Bash", "Bash(*)", "Bash(* *)", "Edit"):
            with self.assertRaises(shoal.ShoalError):
                shoal.check_bash_rule(rule)

    def test_prompt_comes_before_variadic_flags(self) -> None:
        os.environ["SHOAL_CLAUDE_BIN"] = "bin"
        self.addCleanup(os.environ.pop, "SHOAL_CLAUDE_BIN")
        cmd = shoal.claude_command({"mode": "write", "session_id": None, "allow_bash": ["Bash(ls:*)"]}, "p")
        self.assertEqual(cmd[1:3], ["-p", "p"])


class FindCli(unittest.TestCase):
    def test_picks_newest_version_numerically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for v in ("2.1.9", "2.1.309"):
                b = root / f"anthropic.claude-code-{v}-darwin-arm64" / "resources" / "native-binary"
                b.mkdir(parents=True)
                (b / "claude").write_text("")
            os.environ["SHOAL_VSCODE_EXTENSIONS"] = tmp
            self.addCleanup(os.environ.pop, "SHOAL_VSCODE_EXTENSIONS")
            self.assertIn("2.1.309", shoal.find_cli("claude"))


class Finish(Base):
    def verification(self, *items: dict) -> None:
        (self.dir / "verification.json").write_text(json.dumps({"requirements": list(items)}))

    def finalize(self, body: str = "## 完成了什么\n内容") -> subprocess.CompletedProcess:
        f = self.tmp / "body.md"
        f.write_text(body)
        return self.shoal("finalize", self.run_id, "--body", str(f))

    def report(self) -> str:
        return (self.dir / "report.md").read_text()

    def test_verdicts(self) -> None:
        ok = {"requirement": "a", "passed": True, "command": "t", "exit_code": 0}
        bad = {"requirement": "b", "passed": False, "command": "t", "exit_code": 1}
        lied = {"requirement": "c", "passed": True, "command": "t", "exit_code": 1}
        state = {"workers": {}, "integration": None}
        self.assertEqual(shoal.verdict(state, None), "未核验")
        self.assertEqual(shoal.verdict(state, {"requirements": []}), "未核验")
        self.assertEqual(shoal.verdict(state, {"requirements": [ok]}), "通过")
        self.assertEqual(shoal.verdict(state, {"requirements": [ok, bad]}), "部分通过")
        self.assertEqual(shoal.verdict(state, {"requirements": [bad]}), "未通过")
        self.assertEqual(shoal.verdict(state, {"requirements": [lied]}), "未通过")
        self.assertEqual(shoal.verdict(state, {"requirements": [{"passed": True}]}), "未通过")

    def test_failed_worker_or_conflict_caps_the_verdict(self) -> None:
        ok = {"requirements": [{"passed": True, "exit_code": 0}]}
        done = {"status": "done", "mode": "write"}
        self.assertEqual(shoal.verdict({"workers": {"a": {**done, "status": "failed"}}, "integration": None}, ok), "部分通过")
        self.assertEqual(shoal.verdict({"workers": {"a": done}, "integration": {"status": "conflict"}}, ok), "部分通过")
        self.assertEqual(shoal.verdict({"workers": {"a": done}, "integration": None}, ok), "部分通过")
        self.assertEqual(shoal.verdict({"workers": {"a": done}, "integration": {"status": "merged"}}, ok), "通过")

    def test_report_gets_the_computed_verdict_and_run_info(self) -> None:
        self.start("api")
        self.shoal("integrate", self.run_id)
        self.verification({"requirement": "a", "passed": False, "exit_code": 1})
        self.assertEqual(self.finalize().returncode, 0)
        self.assertTrue(self.report().startswith("# 结论：未通过\n"))
        self.assertIn("## 运行信息", self.report())
        self.assertIn(f"shoal/{self.run_id}/api", self.report())

    def test_body_cannot_set_its_own_conclusion(self) -> None:
        p = self.finalize("# 结论：通过")
        self.assertEqual(p.returncode, 2)
        self.assertFalse((self.dir / "report.md").exists())

    def test_integrate_merges_workers_without_touching_main(self) -> None:
        self.start("a", FAKE_FILE="a")
        self.start("b", FAKE_FILE="b")
        self.assertEqual(self.shoal("integrate", self.run_id).returncode, 0)
        i = self.state()["integration"]
        self.assertEqual((i["status"], i["merged"]), ("merged", ["a", "b"]))
        self.assertTrue((Path(i["worktree"]) / "made-by-a.txt").exists())
        self.assertTrue((Path(i["worktree"]) / "made-by-b.txt").exists())
        self.assertEqual(self.git("rev-list", "--count", "main").stdout.strip(), "1")

    def test_integrate_reports_conflicts(self) -> None:
        self.start("a")
        self.start("b")
        for worker, text in (("a", "one"), ("b", "two")):
            wt = Path(self.state()["workers"][worker]["worktree"])
            (wt / "made-by-codex.txt").write_text(text)
            subprocess.run(["git", "-C", str(wt), "commit", "-qam", "edit"], check=True)
        self.assertEqual(self.shoal("integrate", self.run_id).returncode, 1)
        i = self.state()["integration"]
        self.assertEqual((i["status"], i["conflict"]), ("conflict", "b"))

    def test_clean_refuses_unmerged_work_until_forced(self) -> None:
        self.start("api")
        branch = self.state()["workers"]["api"]["branch"]
        p = self.shoal("clean", self.run_id)
        self.assertEqual(p.returncode, 2)
        self.assertIn("isn't merged", p.stderr)
        self.assertEqual(self.git("rev-parse", "--verify", "-q", branch).returncode, 0)
        self.assertEqual(self.shoal("clean", self.run_id, "--force").returncode, 0)
        self.assertNotEqual(self.git("rev-parse", "--verify", "-q", branch).returncode, 0)

    def test_clean_removes_merged_work_only_from_this_run(self) -> None:
        self.git("branch", "keep-me")
        self.start("api")
        self.git("merge", "-q", self.state()["workers"]["api"]["branch"])
        self.assertEqual(self.shoal("clean", self.run_id).returncode, 0)
        self.assertEqual(self.git("rev-parse", "--verify", "-q", "keep-me").returncode, 0)
        self.assertTrue((self.dir / "run.json").exists())


if __name__ == "__main__":
    unittest.main()

"""yamato worktree / pr (design-p1 §8) against a throwaway git repo and a fake gh."""
from __future__ import annotations

import io
import json
import os
import subprocess
import threading
import time
from contextlib import redirect_stdout
from pathlib import Path

from tests.helpers import HERE, ShipTestCase
from yamato.util import YamatoError


def run(*args, cwd):
    return subprocess.run(list(args), cwd=str(cwd), check=True, capture_output=True, text=True).stdout.strip()


class GitShipTestCase(ShipTestCase):
    """The dev ship with a git workspace (``main``) that has a local bare ``origin``, and a fake gh."""

    def setUp(self):
        super().setUp()
        gitconfig = self.tmp / "gitconfig"
        gitconfig.write_text("")
        env = {
            "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
            "YAMATO_GH": str(HERE / "fake_gh.py"), "FAKE_GH_STATE": str(self.tmp / "gh.json"),
        }
        for k, v in env.items():
            self._old.setdefault(k, os.environ.get(k))
        os.environ.update(env)
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        self._old.setdefault("CLAUDE_CODE_SESSION_ID", None)
        self.remote = self.tmp / "remote.git"
        run("git", "init", "-q", "--bare", "-b", "main", str(self.remote), cwd=self.tmp)
        ws = self.workspace
        run("git", "init", "-q", "-b", "main", cwd=ws)
        (ws / "README.md").write_text("hello\n")
        run("git", "add", ".", cwd=ws)
        run("git", "commit", "-q", "-m", "init", cwd=ws)
        run("git", "remote", "add", "origin", str(self.remote), cwd=ws)
        run("git", "push", "-q", "-u", "origin", "main", cwd=ws)
        from yamato import pr

        self._wait = pr.MERGEABLE_WAIT
        pr.MERGEABLE_WAIT = 0

    def tearDown(self):
        from yamato import pr

        pr.MERGEABLE_WAIT = self._wait
        super().tearDown()

    def board(self):
        from yamato.board import Board

        return Board(self.shipdir, self.team())

    def item(self, **fields):
        return self.board().add("gcd を足す", {"assignee": "impl", **fields}, by="pm")["id"]

    def gh(self) -> dict:
        try:
            return json.loads((self.tmp / "gh.json").read_text())
        except FileNotFoundError:
            return {"prs": {}, "calls": []}

    def set_gh(self, **kw):
        st = self.gh()
        st.update(kw)
        (self.tmp / "gh.json").write_text(json.dumps(st))

    def quiet(self, fn, *a, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = fn(*a, **kw)
        return ret, buf.getvalue()


class WorktreeTest(GitShipTestCase):
    def test_add_is_idempotent_and_records_on_the_item(self):
        from yamato import worktree

        tid = self.item()
        wt, created = worktree.add(self.shipdir, self.team(), tid, by="impl")
        self.assertTrue(created)
        self.assertEqual(wt, (self.shipdir / "worktrees" / tid).resolve())
        self.assertEqual(run("git", "branch", "--show-current", cwd=wt), f"yamato/t1/{tid}")
        meta, body, _ = self.board().read(tid)
        self.assertEqual(meta["worktree"], str(wt))
        self.assertEqual(meta["branch"], f"yamato/t1/{tid}")
        self.assertIn("impl: worktree", body)
        # the second call (a later shift) finds the same place and does not touch the item
        again, created = worktree.add(self.shipdir, self.team(), tid)
        self.assertEqual((again, created), (wt, False))
        self.assertEqual(self.board().read(tid)[1], body)
        self.assertEqual(worktree.path_of(self.shipdir, self.team(), tid), wt)

    def test_add_uses_the_item_branch_and_cuts_from_origin_base(self):
        from yamato import worktree

        # origin/main moves ahead of the local main: the new branch starts from origin
        other = self.tmp / "other"
        run("git", "clone", "-q", str(self.remote), str(other), cwd=self.tmp)
        (other / "new.txt").write_text("x\n")
        run("git", "add", ".", cwd=other)
        run("git", "commit", "-q", "-m", "ahead", cwd=other)
        run("git", "push", "-q", "origin", "main", cwd=other)
        tid = self.item(branch="feat/gcd")
        wt, _ = worktree.add(self.shipdir, self.team(), tid)
        self.assertEqual(run("git", "branch", "--show-current", cwd=wt), "feat/gcd")
        self.assertTrue((wt / "new.txt").is_file())
        self.assertFalse((self.workspace / "new.txt").exists())

    def test_explicit_branch_base_and_path(self):
        from yamato import worktree

        first = run("git", "rev-parse", "HEAD", cwd=self.workspace)
        (self.workspace / "b.txt").write_text("b\n")
        run("git", "add", ".", cwd=self.workspace)
        run("git", "commit", "-q", "-m", "b", cwd=self.workspace)
        tid = self.item()
        where = self.tmp / "elsewhere" / "wt"
        wt, _ = worktree.add(self.shipdir, self.team(), tid, branch="x/y", base=first, path=str(where))
        self.assertEqual(wt, where.resolve())
        self.assertEqual(run("git", "rev-parse", "HEAD", cwd=wt), first)
        self.assertEqual(self.board().read(tid)[0]["branch"], "x/y")
        self.assertEqual(worktree.path_of(self.shipdir, self.team(), tid), wt)

    def test_rm_refuses_uncommitted_and_unpushed_work(self):
        from yamato import worktree

        tid = self.item()
        wt, _ = worktree.add(self.shipdir, self.team(), tid)
        (wt / "gcd.py").write_text("def gcd(a, b): ...\n")
        with self.assertRaisesRegex(YamatoError, "commit していない変更"):
            worktree.rm(self.shipdir, self.team(), tid)
        run("git", "add", ".", cwd=wt)
        run("git", "commit", "-q", "-m", "gcd", cwd=wt)
        with self.assertRaisesRegex(YamatoError, "push していない commit が 1 件"):
            worktree.rm(self.shipdir, self.team(), tid)
        rows = worktree.listing(self.shipdir, self.team())
        self.assertEqual([(r["item"], r["unpushed"]) for r in rows], [(tid, 1)])
        run("git", "push", "-q", "-u", "origin", "HEAD", cwd=wt)
        self.assertEqual(worktree.rm(self.shipdir, self.team(), tid, by="pm"), wt)
        self.assertFalse(wt.exists())
        meta, body, _ = self.board().read(tid)
        self.assertIsNone(meta["worktree"])
        self.assertEqual(meta["branch"], f"yamato/t1/{tid}")
        self.assertIn("pm: worktree", body)
        with self.assertRaisesRegex(YamatoError, "worktree がありません"):
            worktree.path_of(self.shipdir, self.team(), tid)
        # a later shift comes back to the pushed branch
        again, created = worktree.add(self.shipdir, self.team(), tid)
        self.assertTrue(created)
        self.assertTrue((again / "gcd.py").is_file())

    def test_rm_force(self):
        from yamato import worktree

        tid = self.item()
        wt, _ = worktree.add(self.shipdir, self.team(), tid)
        (wt / "junk").write_text("j")
        worktree.rm(self.shipdir, self.team(), tid, force=True)
        self.assertFalse(wt.exists())

    def test_path_fails_without_a_worktree_and_non_git_workspace_fails(self):
        from yamato import worktree

        tid = self.item()
        with self.assertRaisesRegex(YamatoError, "worktree がありません"):
            worktree.path_of(self.shipdir, self.team(), tid)
        with self.assertRaisesRegex(YamatoError, "board に T-999"):
            worktree.add(self.shipdir, self.team(), "T-999")
        for bad in ("--orphan", "a..b", "x y"):
            with self.assertRaisesRegex(YamatoError, "ブランチ名が不正"):
                worktree.add(self.shipdir, self.team(), tid, branch=bad)
        self.assertFalse((self.shipdir / "worktrees").exists())
        team = self.team()
        plain = self.tmp / "plain"
        plain.mkdir()
        team["workspace"] = str(plain)
        with self.assertRaisesRegex(YamatoError, "git repo ではない"):
            worktree.add(self.shipdir, team, tid)

    def test_caller_is_recorded_from_the_session(self):
        from yamato import roster, worktree

        roster.start_shift(self.shipdir, "impl", session_id="sid-impl", short_id="sid-impl"[:8],
                           session_name="t1.impl", how="new")
        os.environ["CLAUDE_CODE_SESSION_ID"] = "sid-impl"
        self.assertEqual(worktree.caller(self.shipdir, None), "impl")
        self.assertEqual(worktree.caller(self.shipdir, "pm"), "pm")
        os.environ["CLAUDE_CODE_SESSION_ID"] = "unknown"   # owner's own Claude Code session
        self.assertEqual(worktree.caller(self.shipdir, None), "owner")
        del os.environ["CLAUDE_CODE_SESSION_ID"]
        self.assertEqual(worktree.caller(self.shipdir, None), "owner")

    def test_cli(self):
        from yamato import cli

        tid = self.item()
        rc, out = self.quiet(cli.main, ["worktree", "add", str(self.shipdir), tid])
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), str((self.shipdir / "worktrees" / tid).resolve()))
        rc, out2 = self.quiet(cli.main, ["worktree", "path", str(self.shipdir), tid])
        self.assertEqual(out2, out)
        rc, out3 = self.quiet(cli.main, ["worktree", "list", str(self.shipdir)])
        self.assertIn(f"{tid}  yamato/t1/{tid}", out3)
        rc, _ = self.quiet(cli.main, ["worktree", "rm", str(self.shipdir), tid])
        self.assertEqual(rc, 0)


class PrTest(GitShipTestCase):
    def ready(self, **fields):
        """An item with a pushed worktree branch and an open PR."""
        from yamato import pr, worktree

        tid = self.item(**fields)
        wt, _ = worktree.add(self.shipdir, self.team(), tid)
        (wt / f"{tid}.txt").write_text("x\n")
        run("git", "add", ".", cwd=wt)
        run("git", "commit", "-q", "-m", tid, cwd=wt)
        run("git", "push", "-q", "-u", "origin", "HEAD", cwd=wt)
        self.quiet(pr.open_pr, self.shipdir, self.team(), tid, by="impl")
        return tid

    def set_requires(self, reqs):
        ty = self.shipdir / "team.yaml"
        text = ty.read_text()
        start = text.index("  merge_requires:")
        end = text.index("\n", start)
        ty.write_text(text[:start] + f"  merge_requires: {json.dumps(reqs)}" + text[end:])

    def test_open_records_the_pr_and_tells_the_hub(self):
        from yamato import inbox, pr

        tid = self.ready()
        meta, body, _ = self.board().read(tid)
        self.assertEqual(meta["pr"], "1")
        self.assertIn("PR #1 を開いた: https://github.com/o/r/pull/1", body)
        create = next(c for c in self.gh()["calls"] if c["argv"][:2] == ["pr", "create"])
        a = create["argv"]
        self.assertEqual(a[a.index("--base") + 1], "main")
        self.assertEqual(a[a.index("--head") + 1], f"yamato/t1/{tid}")
        self.assertEqual(a[a.index("--title") + 1], f"{tid} gcd を足す")
        self.assertEqual(Path(create["cwd"]).resolve(), Path(meta["worktree"]).resolve())
        msgs = inbox.entries(self.shipdir, "pm")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["from"], "impl")
        self.assertIn(f"{tid} の PR #1 を開いた", msgs[0]["text"])
        # idempotent: a second open does not create another PR
        _, out = self.quiet(pr.open_pr, self.shipdir, self.team(), tid)
        self.assertIn("既にある: #1", out)
        self.assertEqual(sum(c["argv"][:2] == ["pr", "create"] for c in self.gh()["calls"]), 1)

    def test_open_tells_the_items_reviewer_and_moves_to_review_column(self):
        from yamato import inbox

        self.team_columns()
        tid = self.ready(reviewer="impl")
        meta = self.board().read(tid)[0]
        self.assertEqual(meta["column"], "review")
        self.assertEqual(inbox.entries(self.shipdir, "pm"), [])   # reviewer == caller: nobody to tell

    def team_columns(self):
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text().replace("board:\n", "board:\n  columns:\n"
                                             "    - {name: todo, state: open}\n"
                                             "    - {name: doing, state: active}\n"
                                             "    - {name: review, state: active}\n"
                                             "    - {name: rebase, state: active}\n"
                                             "    - {name: done, state: done}\n", 1))

    def test_open_needs_a_branch(self):
        from yamato import pr

        tid = self.item()
        with self.assertRaisesRegex(YamatoError, "branch がありません"):
            pr.open_pr(self.shipdir, self.team(), tid)

    def test_merge_checks_merge_requires(self):
        from yamato import pr

        tid = self.ready()
        self.board().add("merge してよいか", {"kind": "decision", "category": "merge", "links": tid})
        self.set_gh(checks_rc=1, checks_out="test  fail")
        with self.assertRaises(YamatoError) as cm:
            self.quiet(pr.merge_pr, self.shipdir, self.team(), tid, by="pm")
        msg = str(cm.exception)
        self.assertIn("review=approved がない", msg)
        self.assertIn("test  fail", msg)
        self.assertIn("merge の判断 T-002 が閉じていない", msg)
        self.assertFalse(any(c["argv"][:2] == ["pr", "merge"] for c in self.gh()["calls"]))
        # meet them one by one
        self.board().set(tid, {"review": "approved"})
        self.board().set("T-002", {"state": "done"})
        self.set_gh(checks_rc=1, checks_out="no checks reported on the 'x' branch")   # a repo without CI
        _, out = self.quiet(pr.merge_pr, self.shipdir, self.team(), tid, by="pm")
        self.assertIn("PR #1 を merge した (squash)", out)
        merge = next(c for c in self.gh()["calls"] if c["argv"][:2] == ["pr", "merge"])
        self.assertEqual(merge["argv"], ["pr", "merge", "1", "--squash"])
        meta, body, _ = self.board().read(tid)
        self.assertEqual(meta["merged_by"], "pm")
        self.assertIn("pm: PR #1 を merge した", body)
        self.assertNotEqual(meta["state"], "done")   # closing the task is the role's call

    def test_merge_without_requires_and_caller_is_not_checked(self):
        from yamato import pr

        self.set_requires([])
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text().replace("strategy: squash", "strategy: rebase"))
        tid = self.ready()
        _, out = self.quiet(pr.merge_pr, self.shipdir, self.team(), tid, by="impl")
        self.assertIn("(rebase)", out)
        self.assertEqual(self.board().read(tid)[0]["merged_by"], "impl")
        self.assertFalse(any(c["argv"][:2] == ["pr", "checks"] for c in self.gh()["calls"]))
        # already merged (e.g. by hand): just record it
        _, out = self.quiet(pr.merge_pr, self.shipdir, self.team(), tid, by="pm")
        self.assertIn("既に merge されている", out)
        self.assertEqual(sum(c["argv"][:2] == ["pr", "merge"] for c in self.gh()["calls"]), 1)

    def test_merge_needs_a_pr(self):
        from yamato import pr

        tid = self.item()
        with self.assertRaisesRegex(YamatoError, "pr がありません"):
            pr.merge_pr(self.shipdir, self.team(), tid)

    def test_merge_tells_conflicting_prs(self):
        from yamato import inbox, pr

        self.team_columns()
        self.set_requires([])
        first = self.ready()
        second = self.ready()
        third = self.ready()
        st = self.gh()
        st["prs"]["2"]["mergeable"] = "CONFLICTING"
        self.set_gh(prs=st["prs"])
        _, out = self.quiet(pr.merge_pr, self.shipdir, self.team(), first, by="pm")
        self.assertIn(f"衝突: {second} の PR #2", out)
        self.assertNotIn(third, out)
        meta, body, _ = self.board().read(second)
        self.assertEqual(meta["column"], "rebase")
        self.assertIn("rebase して push", body)
        self.assertEqual(self.board().read(third)[0]["column"], "review")
        msgs = inbox.entries(self.shipdir, "impl")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["from"], "pm")
        self.assertIn(f"{second} の PR #2 が衝突している", msgs[0]["text"])

    def test_conflict_without_rebase_column_notes_only_and_sends_to_configured_seat(self):
        from yamato import inbox, pr

        self.set_requires([])
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text().replace("conflict: author", "conflict: pm"))
        first = self.ready()
        second = self.ready()
        st = self.gh()
        st["prs"]["2"]["mergeable"] = "CONFLICTING"
        self.set_gh(prs=st["prs"])
        self.quiet(pr.merge_pr, self.shipdir, self.team(), first, by="impl")
        meta, body, _ = self.board().read(second)
        self.assertNotIn("column", meta)
        self.assertIn("衝突している", body)
        self.assertIn("衝突している", inbox.entries(self.shipdir, "pm")[-1]["text"])

    def test_merges_run_one_at_a_time(self):
        from yamato import pr
        from yamato.util import merge_lock

        self.set_requires([])
        a = self.ready()
        done = threading.Event()

        def merge():
            self.quiet(pr.merge_pr, self.shipdir, self.team(), a, by="pm")
            done.set()

        with merge_lock(self.shipdir):
            t = threading.Thread(target=merge)
            t.start()
            time.sleep(0.5)
            self.assertFalse(done.is_set())
            self.assertFalse(any(c["argv"][:2] == ["pr", "merge"] for c in self.gh()["calls"]))
        t.join(10)
        self.assertTrue(done.is_set())


class TeamGitTest(ShipTestCase):
    def test_template_git_section(self):
        g = self.team()["git"]
        self.assertEqual(g, {"base": "main", "strategy": "squash", "merge_requires": ["review", "ci", "decision"],
                             "merge_decision": "off", "conflict": "author"})

    def test_deny_defaults_follow_the_git_flow(self):
        deny = self.team()["deny"]
        self.assertNotIn("Bash(git push*)", deny)          # own-branch push is part of the flow
        for rule in ("Bash(git push --force*)", "Bash(git push -f*)", "Bash(git push *+*)", "Bash(gh pr create*)",
                     "Bash(gh pr merge*)", f"Edit(/{self.workspace}/**)", f"Write(/{self.workspace}/**)"):
            self.assertIn(rule, deny)

    def test_validation(self):
        from yamato.team import git_conf, validate

        base = {"name": "x", "hub": "pm", "workspace": str(self.workspace), "roles": {"pm": {}}}
        self.assertEqual(validate(base, self.shipdir)["git"]["merge_requires"], [])
        self.assertEqual(validate({**base, "git": {"merge_decision": False}}, self.shipdir)["git"]["merge_decision"],
                         "off")
        for bad in ({"strategy": "ff"}, {"merge_requires": ["lgtm"]}, {"nope": 1}, {"merge_decision": "on"}):
            with self.assertRaises(YamatoError):
                validate({**base, "git": bad}, self.shipdir)
        self.assertEqual(git_conf({})["base"], "main")   # a .runtime/team.json from before P1

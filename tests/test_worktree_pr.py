"""yamato worktree / pr (design-p1 §8) against a throwaway git repo and a fake gh."""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests import fake_gh
from tests.helpers import HERE, ShipTestCase, fake_command
from yamato import runtime
from yamato.util import YamatoError


def run(*args, cwd, env=None):
    return subprocess.run(list(args), cwd=str(cwd), check=True, capture_output=True, text=True, encoding="utf-8",
                          env=env).stdout.strip()


def git_env(tmp: Path) -> dict:
    gitconfig = tmp / "gitconfig"
    gitconfig.write_text("", encoding="utf-8")
    return {
        "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    }


FAKE_GH = str(HERE / "fake_gh.py")
FAKE_GH_ARGV, FAKE_GH_ENV = fake_command(FAKE_GH)


class _InProcessGh:
    """``yamato.pr``'s ``subprocess``, with the fake gh run in-process (speed only)."""

    def __getattr__(self, name):
        return getattr(subprocess, name)

    def run(self, args, *, cwd=None, **kw):
        n = len(FAKE_GH_ARGV)
        if list(args[:n]) != FAKE_GH_ARGV:
            return subprocess.run(args, cwd=cwd, **kw)
        out, err = io.StringIO(), io.StringIO()
        rc = fake_gh.main(list(args[n:]), cwd=os.path.realpath(cwd) if cwd else None, out=out, err=err)
        return subprocess.CompletedProcess(args, rc, out.getvalue(), err.getvalue())


class GitShipTestCase(ShipTestCase):
    """The dev ship with a git workspace (``main``) that has a local bare ``origin``, and a fake gh.

    The repo pair is made once per class and copied into each test (git start-ups are
    what makes these tests slow)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        tmp = Path(tempfile.mkdtemp()).resolve()
        cls.addClassCleanup(shutil.rmtree, tmp, True)
        env = {**os.environ, **git_env(tmp)}
        remote, ws = tmp / "remote.git", tmp / "ws"
        run("git", "init", "-q", "--bare", "-b", "main", str(remote), cwd=tmp, env=env)
        ws.mkdir()
        run("git", "init", "-q", "-b", "main", cwd=ws, env=env)
        (ws / "README.md").write_text("hello\n", encoding="utf-8")
        run("git", "add", ".", cwd=ws, env=env)
        run("git", "commit", "-q", "-m", "init", cwd=ws, env=env)
        run("git", "remote", "add", "origin", str(remote), cwd=ws, env=env)
        run("git", "push", "-q", "-u", "origin", "main", cwd=ws, env=env)
        cls._template = (remote, ws)

    def setUp(self):
        super().setUp()
        env = {**git_env(self.tmp),
               "YAMATO_GH": FAKE_GH_ENV, "FAKE_GH_STATE": str(self.tmp / "gh.json")}
        for k, v in env.items():
            self._old.setdefault(k, os.environ.get(k))
        os.environ.update(env)
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        self._old.setdefault("CLAUDE_CODE_SESSION_ID", None)
        self.remote = self.tmp / "remote.git"
        remote, ws = self._template
        shutil.copytree(remote, self.remote, symlinks=True)
        shutil.copytree(ws, self.workspace, symlinks=True, dirs_exist_ok=True)
        # config の URL を書き換えず set-url で向ける (Windows の git は \ を \\ にエスケープして書くので、文字列の置換は当たらない)
        run("git", "remote", "set-url", "origin", str(self.remote), cwd=self.workspace, env=env)
        from yamato import pr

        p = mock.patch.object(pr, "subprocess", _InProcessGh())
        p.start()
        self.addCleanup(p.stop)

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
            return json.loads((self.tmp / "gh.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"prs": {}, "calls": []}

    def set_gh(self, **kw):
        st = self.gh()
        st.update(kw)
        (self.tmp / "gh.json").write_text(json.dumps(st), encoding="utf-8")

    def quiet(self, fn, *a, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = fn(*a, **kw)
        return ret, buf.getvalue()

    def recorded(self, *kinds) -> list[dict]:
        """The events.jsonl lines of these kinds, in the order written."""
        from yamato import events

        return events.read(self.shipdir, kinds=kinds)


class TwoRepoTest(GitShipTestCase):
    """One ship over two repos (``ws`` first, then ``lib``): a task's worktrees, PRs and merges per repo."""

    def setUp(self):
        super().setUp()
        remote, ws = self._template
        env = {**os.environ, **git_env(self.tmp)}
        self.lib_remote, self.lib = self.tmp / "libremote.git", self.tmp / "lib"
        shutil.copytree(remote, self.lib_remote, symlinks=True)
        shutil.copytree(ws, self.lib, symlinks=True)
        run("git", "remote", "set-url", "origin", str(self.lib_remote), cwd=self.lib, env=env)

    def team(self):
        team = super().team()
        team["workspaces"] = [{"name": "ws", "path": str(self.workspace)}, {"name": "lib", "path": str(self.lib)}]
        return team

    def commit(self, wt: Path, name="x.txt"):
        (wt / name).write_text("x\n", encoding="utf-8")
        run("git", "add", ".", cwd=wt)
        run("git", "commit", "-q", "-m", name, cwd=wt)

    def test_worktrees_sit_side_by_side_and_are_recorded_per_repo(self):
        from yamato import worktree

        tid = self.item()
        team = self.team()
        app, _ = worktree.add(self.shipdir, team, tid)             # no --repo: the first repo
        lib, _ = worktree.add(self.shipdir, team, tid, repo="lib", branch="feature/issues/12")
        self.assertEqual(app, (self.shipdir / "worktrees" / tid / "ws").resolve())
        self.assertEqual(lib, (self.shipdir / "worktrees" / tid / "lib").resolve())
        self.assertEqual((app / ".." / "lib").resolve(), lib)       # ../lib from the app is the same task's lib
        self.assertEqual(run("git", "branch", "--show-current", cwd=lib), "feature/issues/12")
        self.assertEqual(run("git", "branch", "--show-current", cwd=app), f"yamato/t1/{tid}")
        meta = self.board().read(tid)[0]
        self.assertEqual(meta["worktree"], {"ws": str(app), "lib": str(lib)})
        self.assertEqual(meta["branch"], {"ws": f"yamato/t1/{tid}", "lib": "feature/issues/12"})
        self.assertEqual(worktree.path_of(self.shipdir, team, tid), app)
        self.assertEqual(worktree.path_of(self.shipdir, team, tid, "lib"), lib)
        # idempotent per repo
        self.assertEqual(worktree.add(self.shipdir, team, tid, repo="lib"), (lib, False))
        rows = {(r["repo"], r["item"]) for r in worktree.listing(self.shipdir, team)}
        self.assertEqual(rows, {("ws", tid), ("lib", tid)})
        self.assertEqual({e["data"]["repo"] for e in self.recorded("worktree_add")}, {"ws", "lib"})
        # a worktree the item no longer points at still lists under the task id, not the repo name
        self.board().set(tid, {"worktree": ""}, by="impl")
        self.assertEqual({r["item"] for r in worktree.listing(self.shipdir, team)}, {tid})
        with self.assertRaisesRegex(YamatoError, "workspace にありません"):
            worktree.add(self.shipdir, team, tid, repo="nope")

    def test_rm_is_per_repo(self):
        from yamato import worktree

        tid = self.item()
        team = self.team()
        app, _ = worktree.add(self.shipdir, team, tid)
        lib, _ = worktree.add(self.shipdir, team, tid, repo="lib")
        worktree.rm(self.shipdir, team, tid, repo="lib")
        self.assertFalse(lib.exists())
        self.assertTrue(app.is_dir())
        self.assertEqual(self.board().read(tid)[0]["worktree"], {"ws": str(app)})

    def test_cli_branch_per_repo(self):
        from yamato import worktree

        team = self.team()
        self.assertEqual(worktree.per_repo(team, ["ws", "lib"], ["lib=feature/a", "common"], "branch"),
                         {"lib": "feature/a", "ws": "common"})
        with self.assertRaisesRegex(YamatoError, "選んでいない"):
            worktree.per_repo(team, ["ws"], ["lib=x"], "branch")

    def test_pr_open_and_merge_per_repo(self):
        from yamato import pr, worktree

        tid = self.item()
        team = self.team()
        app, _ = worktree.add(self.shipdir, team, tid)
        lib, _ = worktree.add(self.shipdir, team, tid, repo="lib", branch="feature/issues/12")
        with self.assertRaisesRegex(YamatoError, "PR を開く repo がありません"):
            pr.open_pr(self.shipdir, team, tid, by="impl")           # nothing to open yet
        self.commit(lib)
        self.quiet(pr.open_pr, self.shipdir, team, tid, by="impl")   # only lib has commits: only lib gets a PR
        self.assertEqual(list(self.board().read(tid)[0]["pr"]), ["lib"])
        self.commit(app)
        self.quiet(pr.open_pr, self.shipdir, team, tid, by="impl")
        meta = self.board().read(tid)[0]
        self.assertEqual(set(meta["pr"]), {"ws", "lib"})
        self.assertNotEqual(meta["pr"]["ws"], meta["pr"]["lib"])
        heads = {n: p["head"] for n, p in self.gh()["prs"].items()}
        self.assertEqual(heads[meta["pr"]["lib"]], "feature/issues/12")
        self.assertEqual(heads[meta["pr"]["ws"]], f"yamato/t1/{tid}")
        calls = [c for c in self.gh()["calls"] if c["argv"][:2] == ["pr", "create"]]
        self.assertEqual({Path(c["cwd"]) for c in calls}, {app.resolve(), lib.resolve()})
        self.assertEqual({e["data"]["repo"] for e in self.recorded("pr_open")}, {"ws", "lib"})
        # merge: the repos named, in the order given; only that repo is marked merged
        self.board().set(tid, {"review": "approved"}, by="reviewer")
        self.quiet(pr.merge_pr, self.shipdir, team, tid, "reviewer", ["lib"])
        meta = self.board().read(tid)[0]
        self.assertEqual(meta["merged_by"], {"lib": "reviewer"})
        self.assertEqual(self.gh()["prs"][meta["pr"]["lib"]]["state"], "MERGED")
        self.assertEqual(self.gh()["prs"][meta["pr"]["ws"]]["state"], "OPEN")
        # no --repo: whatever is still open
        self.quiet(pr.merge_pr, self.shipdir, team, tid, "reviewer")
        meta = self.board().read(tid)[0]
        self.assertEqual(meta["merged_by"], {"lib": "reviewer", "ws": "reviewer"})
        self.assertEqual(self.gh()["prs"][meta["pr"]["ws"]]["state"], "MERGED")
        self.assertEqual([e["data"]["repo"] for e in self.recorded("pr_merge")], ["lib", "ws"])
        with self.assertRaisesRegex(YamatoError, "merge する PR がありません"):
            pr.merge_pr(self.shipdir, team, tid, "reviewer")

    def test_merge_requires_is_checked_per_repo_and_conflicts_stay_in_the_repo(self):
        from yamato import pr, worktree

        def commit(wt, name):                                    # empty commit: one git call instead of add + commit
            run("git", "commit", "-q", "--allow-empty", "-m", name, cwd=wt)

        team = self.team()
        a, b, c = self.item(), self.item(), self.item()
        for t in (a, b):                                         # a, b: lib only
            worktree.add(self.shipdir, team, t, repo="lib")
            commit(worktree.path_of(self.shipdir, team, t, "lib"), t)
        worktree.add(self.shipdir, team, c)                      # c: the first repo (ws) only
        commit(worktree.path_of(self.shipdir, team, c), c)
        for t in (a, b, c):
            self.quiet(pr.open_pr, self.shipdir, team, t, by="impl")
        pa, pb, pc = (self.board().read(t)[0]["pr"] for t in (a, b, c))
        self.assertEqual((set(pa), set(pb), set(pc)), ({"lib"}, {"lib"}, {"ws"}))
        # b's lib PR and c's ws PR both conflict; a's lib merge concerns only the lib one
        prs = self.gh()["prs"]
        self.set_gh(prs={**prs, pb["lib"]: {**prs[pb["lib"]], "mergeable": "CONFLICTING"},
                         pc["ws"]: {**prs[pc["ws"]], "mergeable": "CONFLICTING"}})
        self.board().set(a, {"review": "approved"}, by="reviewer")
        self.quiet(pr.merge_pr, self.shipdir, team, a, "reviewer", ["lib"])
        conflicts = self.recorded("pr_conflict")
        self.assertEqual([(e["item"], e["data"]["repo"]) for e in conflicts], [(b, "lib")])
        with self.assertRaisesRegex(YamatoError, "merge の条件"):       # b has no review=approved
            pr.merge_pr(self.shipdir, team, b, "reviewer", ["lib"])

    def test_one_repo_ship_keeps_the_plain_record(self):
        from yamato import pr, worktree

        tid = self.item()
        team = super().team()                       # workspaces: just the one
        wt, _ = worktree.add(self.shipdir, team, tid)
        self.assertEqual(wt, (self.shipdir / "worktrees" / tid).resolve())
        self.commit(wt)
        self.quiet(pr.open_pr, self.shipdir, team, tid, by="impl")
        meta = self.board().read(tid)[0]
        self.assertEqual(meta["worktree"], str(wt))
        self.assertIsInstance(meta["pr"], str)
        self.assertNotIn("repo", self.recorded("pr_open")[0]["data"])


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
        (other / "new.txt").write_text("x\n", encoding="utf-8")
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
        (self.workspace / "b.txt").write_text("b\n", encoding="utf-8")
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
        (wt / "gcd.py").write_text("def gcd(a, b): ...\n", encoding="utf-8")
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
        (wt / "junk").write_text("j", encoding="utf-8")
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
        team["workspaces"] = [{"name": "plain", "path": str(plain)}]
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

    def test_add_and_rm_are_recorded_in_events(self):
        from yamato import worktree

        tid = self.item()
        wt, _ = worktree.add(self.shipdir, self.team(), tid, by="impl")
        worktree.add(self.shipdir, self.team(), tid, by="impl")   # finds the same place: nothing happened
        (added,) = self.recorded(worktree.WORKTREE_ADD)
        self.assertEqual((added["item"], added["seat"], added["by"]), (tid, "impl", "impl"))
        self.assertEqual(added["data"], {"path": str(wt), "branch": f"yamato/t1/{tid}"})
        # refused: the reason is on the line, and so is --force
        (wt / "junk").write_text("j", encoding="utf-8")
        with self.assertRaises(YamatoError):
            worktree.rm(self.shipdir, self.team(), tid, by="pm")
        (refused,) = self.recorded(worktree.WORKTREE_RM_FAILED)
        self.assertEqual((refused["item"], refused["seat"], refused["by"]), (tid, "impl", "pm"))
        self.assertIn("commit していない変更が 1 件", refused["data"]["reason"])
        self.assertIs(refused["data"]["force"], False)
        self.assertEqual(self.recorded(worktree.WORKTREE_RM), [])
        worktree.rm(self.shipdir, self.team(), tid, force=True, by="pm")
        (removed,) = self.recorded(worktree.WORKTREE_RM)
        self.assertEqual((removed["item"], removed["by"]), (tid, "pm"))
        self.assertEqual(removed["data"], {"path": str(wt), "branch": f"yamato/t1/{tid}", "force": True})
        self.assertEqual(self.recorded(worktree.WORKTREE_ADD_FAILED), [])

    def test_failures_are_recorded_even_without_an_item(self):
        from yamato import worktree

        tid = self.item()
        with self.assertRaisesRegex(YamatoError, "ブランチ名が不正"):
            worktree.add(self.shipdir, self.team(), tid, branch="a..b", by="impl")
        with self.assertRaisesRegex(YamatoError, "board に T-999"):
            worktree.add(self.shipdir, self.team(), "T-999")
        with self.assertRaisesRegex(YamatoError, "worktree がありません"):
            worktree.rm(self.shipdir, self.team(), tid)
        bad, missing = self.recorded(worktree.WORKTREE_ADD_FAILED)
        self.assertEqual((bad["item"], bad["seat"], bad["by"]), (tid, "impl", "impl"))
        self.assertIn("ブランチ名が不正", bad["data"]["reason"])
        self.assertEqual((missing["item"], missing["seat"], missing["by"]), ("T-999", None, "owner"))
        self.assertIn("board に T-999", missing["data"]["reason"])
        (rm_failed,) = self.recorded(worktree.WORKTREE_RM_FAILED)
        self.assertEqual(rm_failed["item"], tid)
        self.assertEqual(self.recorded(worktree.WORKTREE_ADD, worktree.WORKTREE_RM), [])

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
    def ready(self, worktree=False, **fields):
        """An item with a branch and an open PR. yamato pr only talks to gh (the fake), so the
        branch is set on the item as `board set T branch=...` does, without git (git start-ups
        are what makes these tests slow); ``worktree=True`` makes it with `worktree add`."""
        from yamato import pr
        from yamato import worktree as wt

        tid = self.item(**fields)
        if worktree:
            wt.add(self.shipdir, self.team(), tid)
        else:
            self.board().set(tid, {"branch": f"yamato/t1/{tid}"}, by="impl")
        self.quiet(pr.open_pr, self.shipdir, self.team(), tid, by="impl")
        return tid

    def set_requires(self, reqs):
        ty = self.shipdir / "team.yaml"
        text = ty.read_text(encoding="utf-8")
        start = text.index("  merge_requires:")
        end = text.index("\n", start)
        ty.write_text(text[:start] + f"  merge_requires: {json.dumps(reqs)}" + text[end:], encoding="utf-8")

    def test_open_records_the_pr_and_tells_the_hub(self):
        from yamato import inbox, pr

        tid = self.ready(worktree=True)
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
        ty.write_text(ty.read_text(encoding="utf-8").replace("board:\n", "board:\n  columns:\n"
                                             "    - {name: todo, state: open}\n"
                                             "    - {name: doing, state: active}\n"
                                             "    - {name: review, state: active}\n"
                                             "    - {name: rebase, state: active}\n"
                                             "    - {name: done, state: done}\n", 1), encoding="utf-8")

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
        ty.write_text(ty.read_text(encoding="utf-8").replace("strategy: squash", "strategy: rebase"), encoding="utf-8")
        tid = self.ready()
        _, out = self.quiet(pr.merge_pr, self.shipdir, self.team(), tid, by="impl")
        self.assertIn("(rebase)", out)
        self.assertEqual(self.board().read(tid)[0]["merged_by"], "impl")
        self.assertFalse(any(c["argv"][:2] == ["pr", "checks"] for c in self.gh()["calls"]))
        # already merged (e.g. by hand): just record it
        _, out = self.quiet(pr.merge_pr, self.shipdir, self.team(), tid, by="pm")
        self.assertIn("既に merge されている", out)
        self.assertEqual(sum(c["argv"][:2] == ["pr", "merge"] for c in self.gh()["calls"]), 1)

    def test_open_is_recorded_in_events(self):
        from yamato import pr

        self.team_columns()
        tid = self.ready()
        self.quiet(pr.open_pr, self.shipdir, self.team(), tid, by="impl")   # already has a PR: nothing happened
        (opened,) = self.recorded(pr.PR_OPEN)
        self.assertEqual((opened["item"], opened["seat"], opened["by"]), (tid, "impl", "impl"))
        self.assertEqual(opened["data"], {"pr": "1", "url": "https://github.com/o/r/pull/1",
                                          "branch": f"yamato/t1/{tid}", "column": "review"})
        self.assertEqual(self.recorded(pr.PR_OPEN_FAILED), [])

    def test_open_failures_are_recorded_in_events(self):
        from yamato import pr

        tid = self.item()
        with self.assertRaisesRegex(YamatoError, "branch がありません"):
            pr.open_pr(self.shipdir, self.team(), tid, by="impl")
        branched = self.item(branch="feat/x")
        os.environ["YAMATO_GH"] = str(self.tmp / "no-such-gh")
        with self.assertRaisesRegex(YamatoError, "gh コマンドが見つかりません"):
            pr.open_pr(self.shipdir, self.team(), branched)
        no_branch, no_gh = self.recorded(pr.PR_OPEN_FAILED)
        self.assertEqual((no_branch["item"], no_branch["seat"], no_branch["by"]), (tid, "impl", "impl"))
        self.assertIn("branch がありません", no_branch["data"]["reason"])
        self.assertEqual((no_gh["item"], no_gh["by"]), (branched, "owner"))
        self.assertIn("gh コマンドが見つかりません", no_gh["data"]["reason"])
        self.assertEqual(self.recorded(pr.PR_OPEN), [])
        self.assertEqual(self.board().read(branched)[0].get("pr"), None)

    def test_merge_is_recorded_in_events(self):
        from yamato import pr

        tid = self.ready()
        self.set_gh(checks_rc=1, checks_out="test  fail")
        with self.assertRaises(YamatoError):
            self.quiet(pr.merge_pr, self.shipdir, self.team(), tid, by="pm")
        (refused,) = self.recorded(pr.PR_MERGE_FAILED)
        self.assertEqual((refused["item"], refused["seat"], refused["by"]), (tid, "impl", "pm"))
        self.assertEqual(refused["data"]["pr"], "1")
        self.assertEqual(len(refused["data"]["unmet"]), 2)   # review + ci (no merge decision on the board)
        self.assertTrue(refused["data"]["unmet"][0].startswith("review: "))
        self.assertTrue(refused["data"]["unmet"][1].startswith("ci: "))
        self.assertNotIn("\n", "".join(refused["data"]["unmet"]))
        self.assertEqual(self.recorded(pr.PR_MERGE), [])
        # met: merged, and who merged is in both the `by` column and the data
        self.set_requires([])
        self.quiet(pr.merge_pr, self.shipdir, self.team(), tid, by="pm")
        (merged,) = self.recorded(pr.PR_MERGE)
        self.assertEqual((merged["item"], merged["seat"], merged["by"]), (tid, "impl", "pm"))
        self.assertEqual(merged["data"], {"pr": "1", "strategy": "squash", "mergedBy": "pm"})
        self.assertEqual(self.board().read(tid)[0]["merged_by"], "pm")
        # merged by hand already: recorded once more, and marked
        self.quiet(pr.merge_pr, self.shipdir, self.team(), tid, by="owner")
        again = self.recorded(pr.PR_MERGE)[-1]
        self.assertEqual((again["by"], again["data"]["mergedBy"], again["data"]["alreadyMerged"]),
                         ("owner", "owner", True))

    def test_merge_without_a_pr_is_recorded_as_failed(self):
        from yamato import pr

        tid = self.item()
        with self.assertRaisesRegex(YamatoError, "pr がありません"):
            pr.merge_pr(self.shipdir, self.team(), tid, by="pm")
        (failed,) = self.recorded(pr.PR_MERGE_FAILED)
        self.assertEqual((failed["item"], failed["by"]), (tid, "pm"))
        self.assertIn("pr がありません", failed["data"]["reason"])
        self.assertNotIn("unmet", failed["data"])

    def test_conflict_is_recorded_in_events(self):
        from yamato import pr

        self.team_columns()
        self.set_requires([])
        first = self.ready()
        second = self.ready()
        third = self.ready()
        st = self.gh()
        st["prs"]["2"]["mergeable"] = "CONFLICTING"
        self.set_gh(prs=st["prs"])
        self.quiet(pr.merge_pr, self.shipdir, self.team(), first, by="pm")
        (conflict,) = self.recorded(pr.PR_CONFLICT)   # third does not conflict
        self.assertEqual((conflict["item"], conflict["seat"], conflict["by"]), (second, "impl", "pm"))
        self.assertEqual(conflict["data"], {"pr": "2", "mergedItem": first, "mergedPr": "1", "mergedBy": "pm",
                                            "column": "rebase", "notified": "impl"})
        self.assertNotIn(third, conflict["summary"])

    def test_conflict_record_when_nobody_is_told(self):
        from yamato import pr

        self.set_requires([])
        first = self.ready()
        second = self.ready(assignee="pm")
        st = self.gh()
        st["prs"]["2"]["mergeable"] = "CONFLICTING"
        self.set_gh(prs=st["prs"])
        self.quiet(pr.merge_pr, self.shipdir, self.team(), first, by="pm")   # the merger is the one to rebase
        (conflict,) = self.recorded(pr.PR_CONFLICT)
        self.assertEqual((conflict["item"], conflict["seat"]), (second, "pm"))
        self.assertNotIn("notified", conflict["data"])   # no rebase column, and not sent to oneself
        self.assertNotIn("column", conflict["data"])

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
        ty.write_text(ty.read_text(encoding="utf-8").replace("conflict: author", "conflict: pm"), encoding="utf-8")
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
            time.sleep(0.3)
            self.assertFalse(done.is_set())
            self.assertFalse(any(c["argv"][:2] == ["pr", "merge"] for c in self.gh()["calls"]))
        t.join(10)
        self.assertTrue(done.is_set())


class TeamGitTest(ShipTestCase):
    def test_template_git_section(self):
        g = self.team()["git"]
        self.assertEqual(g, {"base": "main", "strategy": "squash", "merge_requires": ["review", "ci", "decision"],
                             "conflict": "author"})

    def test_deny_defaults_follow_the_git_flow(self):
        deny = self.team()["deny"]
        self.assertNotIn("Bash(git push*)", deny)          # own-branch push is part of the flow
        for rule in ("Bash(git push --force*)", "Bash(git push -f*)", "Bash(git push *+*)", "Bash(gh pr create*)",
                     "Bash(gh pr merge*)", f"Edit(/{runtime.rule_path(self.workspace)}/**)", f"Write(/{runtime.rule_path(self.workspace)}/**)"):
            self.assertIn(rule, deny)

    def test_validation(self):
        from yamato.team import git_conf, validate

        base = {"name": "x", "hub": "pm", "workspace": str(self.workspace), "roles": {"pm": {}}}
        self.assertEqual(validate(base, self.shipdir)["git"]["merge_requires"], [])
        for bad in ({"strategy": "ff"}, {"merge_requires": ["lgtm"]}, {"nope": 1}):
            with self.assertRaises(YamatoError):
                validate({**base, "git": bad}, self.shipdir)
        self.assertEqual(git_conf({})["base"], "main")   # a .runtime/team.json from before P1

    def test_merge_decision_is_dropped_but_not_rejected(self):
        # D-021 案 b (#5): merge_decision は外れた。古い team.yaml に残っていても落とさず、
        # 警告 1 行を出して無視する (git の項目には出さない)。
        from yamato.team import validate

        base = {"name": "x", "hub": "pm", "workspace": str(self.workspace), "roles": {"pm": {}},
                "git": {"merge_decision": "auto"}}
        team = validate(base, self.shipdir)
        self.assertNotIn("merge_decision", team["git"])
        self.assertTrue(any("git.merge_decision" in w for w in team["warnings"]))

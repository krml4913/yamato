"""Shared fixtures: a throwaway YAMATO_HOME / CLAUDE_CONFIG_DIR and a dev ship in it."""
from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import fake_claude

HERE = Path(__file__).resolve().parent
FAKE_CLAUDE = str(HERE / "fake_claude.py")


def fake_command(script: str) -> tuple[list[str], str]:
    """``(argv prefix, $YAMATO_* value)`` that run a fake script: the script itself on POSIX
    (shebang), ``python script`` on Windows, where a ``.py`` cannot be run directly."""
    if os.name == "nt":
        return [sys.executable, script], subprocess.list2cmdline([sys.executable, script])
    return [script], script


FAKE_CLAUDE_ARGV, FAKE_CLAUDE_ENV = fake_command(FAKE_CLAUDE)


def _outside_git(path) -> bool:
    """No ``.git`` in ``path`` or above it: ``git rev-parse`` there can only fail."""
    if os.environ.get("GIT_DIR"):
        return False
    p = Path(path).absolute()
    return not any((d / ".git").exists() for d in (p, *p.parents))


def _git_root(real):
    def git_root(path):
        if _outside_git(path):
            return None
        p = Path(path)
        if (p / ".git").is_dir():   # a main working tree is its own top level
            return p.resolve()
        return real(path)
    return git_root


_YAML: dict[str, object] = {}
_TMP_MARK = "/yamato-test-tmp"


def _put_tmp(data, tmp: str):
    if isinstance(data, str):
        return data.replace(_TMP_MARK, tmp)
    if isinstance(data, dict):
        return {_put_tmp(k, tmp): _put_tmp(v, tmp) for k, v in data.items()}
    if isinstance(data, list):
        return [_put_tmp(v, tmp) for v in data]
    return copy.deepcopy(data)


def _safe_load(real, tmp: str):
    """The same text parses to the same data: the pure-python yaml is the slowest part of
    ``load_team`` (several times per test). Every test's team.yaml differs only in its
    throwaway dir (a plain path, as is the mark put in its place), so the text is parsed
    with the mark and the dir put back into the result."""
    def safe_load(stream):
        if not isinstance(stream, str) or _TMP_MARK in stream:
            return real(stream)
        key = stream.replace(tmp, _TMP_MARK)
        if key not in _YAML:
            if len(_YAML) > 64:
                _YAML.clear()
            _YAML[key] = real(key)
        return _put_tmp(_YAML[key], tmp)
    return safe_load


def patch_fast(case: unittest.TestCase, tmp: Path) -> None:
    """Speed only (memory fast-unit-tests); what the tests check is unchanged:
    - ``claude agents`` / ``--bg`` / ``stop`` ... run tests/fake_claude.py in-process instead
      of a python start-up each (``-p`` and a ``YAMATO_CLAUDE`` other than the fake still
      go through the real subprocess)
    - ``git rev-parse`` is not run where its answer is plain from the files: no ``.git``
      at all (the throwaway workspaces), or a ``.git`` directory (the repo's own top level)
    - ``yaml.safe_load`` of a text it has already parsed returns a copy of that result
    """
    import yaml
    from yamato import claude

    real_run, real_main = claude._run, claude.main_repo_root

    def run(args, *, cwd=None, env=None, timeout=120):
        if claude.claude_cmd() != FAKE_CLAUDE_ARGV or "-p" in args:
            return real_run(args, cwd=cwd, env=env, timeout=timeout)
        out, err = io.StringIO(), io.StringIO()
        rc = fake_claude.main(list(args), cwd=os.path.realpath(cwd) if cwd else None,
                              env=os.environ if env is None else env, out=out, err=err)
        return subprocess.CompletedProcess([*FAKE_CLAUDE_ARGV, *args], rc, out.getvalue(), err.getvalue())

    for target, attr, new in ((claude, "_run", run),
                              (claude, "git_root", _git_root(claude.git_root)),
                              (claude, "main_repo_root", lambda path: None if _outside_git(path) else real_main(path)),
                              (yaml, "safe_load", _safe_load(yaml.safe_load, str(tmp)))):
        p = mock.patch.object(target, attr, new)
        p.start()
        case.addCleanup(p.stop)


class ShipTestCase(unittest.TestCase):
    """Creates ``self.shipdir`` (dev template) with a trusted workspace and a fake claude."""

    team_yaml_extra = ""
    real_claude_process = False   # True: no patch_fast (every fake claude call is a real subprocess)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.workspace = self.tmp / "ws"
        self.workspace.mkdir()
        self.config = self.tmp / "claude-config"
        self.config.mkdir()
        (self.config / ".claude.json").write_text(json.dumps(
            {"projects": {str(self.workspace): {"hasTrustDialogAccepted": True}}}))
        self.fake_state = self.tmp / "fake.json"
        self._env = {
            "YAMATO_HOME": str(self.tmp / "home"),
            "CLAUDE_CONFIG_DIR": str(self.config),
            "YAMATO_CLAUDE": FAKE_CLAUDE_ENV,
            "FAKE_CLAUDE_STATE": str(self.fake_state),
            "FAKE_ALIVE_PID": str(os.getpid()),
        }
        self._old = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)
        if not self.real_claude_process:
            patch_fast(self, self.tmp)

        from yamato import ship

        self.shipdir, self.create_warnings = ship.create("t1", str(self.workspace), None, "dev")
        if self.team_yaml_extra:
            ty = self.shipdir / "team.yaml"
            ty.write_text(ty.read_text() + self.team_yaml_extra)

    def tearDown(self):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def team(self):
        from yamato.team import load_team

        return load_team(self.shipdir)

    def fake(self) -> dict:
        try:
            return json.loads(self.fake_state.read_text())
        except FileNotFoundError:
            return {"sessions": [], "calls": []}

    def set_fake_mode(self, **mode):
        st = self.fake()
        st["mode"] = mode
        self.fake_state.write_text(json.dumps(st))

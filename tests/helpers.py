"""Shared fixtures: a throwaway YAMATO_HOME / CLAUDE_CONFIG_DIR and a dev ship in it."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class ShipTestCase(unittest.TestCase):
    """Creates ``self.shipdir`` (dev template) with a trusted workspace and a fake claude."""

    team_yaml_extra = ""

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
            "YAMATO_CLAUDE": str(HERE / "fake_claude.py"),
            "FAKE_CLAUDE_STATE": str(self.fake_state),
            "FAKE_ALIVE_PID": str(os.getpid()),
        }
        self._old = {k: os.environ.get(k) for k in self._env}
        os.environ.update(self._env)

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

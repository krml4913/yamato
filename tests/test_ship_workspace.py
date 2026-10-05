"""W5: ship.create puts a workspace with `\\`, `"` and spaces into team.yaml as a valid YAML string."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from yamato import runtime, ship
from yamato.team import load_team
from yamato.util import YamatoError
from tests.helpers import ShipTestCase


class WorkspaceEscapeTest(ShipTestCase):
    def make(self, dirname):
        ws = Path(self._tmp_ws.name) / dirname
        ws.mkdir()
        shipdir, _ = ship.create("t2", str(ws), str(Path(self._tmp_ws.name) / "ship2"), "dev")
        return ws.resolve(), load_team(shipdir)

    def setUp(self):
        super().setUp()
        self._tmp_ws = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_ws.cleanup)

    @unittest.skipIf(os.name == "nt", "Windows のフォルダ名に \" は使えない")
    def test_backslash_quote_and_space_survive(self):
        # what a Windows path looks like to YAML ("\U" is an unknown escape): the folder name has them
        ws, team = self.make('Users\\Shogo "K" Kuroyama')
        self.assertEqual(Path(team["workspace"]), ws)

    @unittest.skipIf(os.name == "nt", "Windows では C:\\x y を名前にできない (実機の形は test_windows_w1 / w4)")
    def test_windows_rule_form_in_the_deny_rules(self):
        with mock.patch.object(runtime, "is_windows", return_value=True):
            ws, team = self.make("C:\\x y")
        denied = [r for r in team["deny"] if r.startswith("Edit(")]
        self.assertTrue(denied, team["deny"])
        self.assertNotIn("\\\\", denied[0])


class MultiWorkspaceTest(ShipTestCase):
    def setUp(self):
        super().setUp()
        self._tmp_ws = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp_ws.cleanup)
        self.base = Path(self._tmp_ws.name)
        for n in ("lib", "app"):
            (self.base / n).mkdir()

    def create(self, *names):
        return ship.create("t3", [str(self.base / n) for n in names], str(self.base / "ship3"), "dev")[0]

    def test_create_many_gives_a_list_and_rules_per_repo(self):
        shipdir = self.create("app", "lib")
        team = load_team(shipdir)
        self.assertEqual([w["name"] for w in team["workspaces"]], ["app", "lib"])
        self.assertEqual(Path(team["workspace"]), (self.base / "app").resolve())
        for kind in ("Edit", "Write"):
            rules = [r for r in team["deny"] if r.startswith(kind + "(/") and r.endswith(("/app/**)", "/lib/**)"))]
            self.assertEqual(len(rules), 2, rules)
        self.assertIn("workspace:", (shipdir / "team.yaml").read_text(encoding="utf-8"))

    def test_create_one_stays_a_string(self):
        shipdir = self.create("app")
        self.assertRegex((shipdir / "team.yaml").read_text(encoding="utf-8"), r'(?m)^workspace: "')
        team = load_team(shipdir)
        self.assertEqual(len(team["workspaces"]), 1)
        self.assertEqual(team["workspaces"][0]["name"], "app")

    def test_same_basename_is_an_error(self):
        (self.base / "x").mkdir()
        (self.base / "x" / "app").mkdir()
        with self.assertRaises(YamatoError) as cm:
            ship.create("t3", [str(self.base / "app"), str(self.base / "x" / "app")], str(self.base / "ship3"), "dev")
        self.assertIn("かぶっている", str(cm.exception))
        self.assertFalse((self.base / "ship3").exists())

    def test_missing_repo_is_an_error(self):
        with self.assertRaises(YamatoError):
            self.create("app", "nope")


if __name__ == "__main__":
    unittest.main()

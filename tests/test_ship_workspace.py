"""W5: ship.create puts a workspace with `\\`, `"` and spaces into team.yaml as a valid YAML string."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from yamato import runtime, ship
from yamato.team import load_team
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

    def test_backslash_quote_and_space_survive(self):
        # what a Windows path looks like to YAML ("\U" is an unknown escape): the folder name has them
        ws, team = self.make('Users\\Shogo "K" Kuroyama')
        self.assertEqual(Path(team["workspace"]), ws)

    def test_windows_rule_form_in_the_deny_rules(self):
        with mock.patch.object(runtime, "is_windows", return_value=True):
            ws, team = self.make("C:\\x y")
        denied = [r for r in team["deny"] if r.startswith("Edit(")]
        self.assertTrue(denied, team["deny"])
        self.assertNotIn("\\\\", denied[0])


if __name__ == "__main__":
    unittest.main()

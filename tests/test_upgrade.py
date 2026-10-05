"""T-070 / D-081: `yamato ship upgrade` (backup + materials + the upgrade-only claude) and the template record."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from yamato import __version__, cli, upgrade
from yamato.team import load_team
from yamato.util import YamatoError
from tests.helpers import ShipTestCase

MIG = """# 移行手順

intro

## 未リリース

### 新しい項目

## v1.0.0 → v1.1.0

### 中の項目

## v0.9.0 → v1.0.0

### 古い項目
"""


class TemplateRecordTest(ShipTestCase):
    def test_create_writes_template_and_load_team_reads_it(self):
        self.assertEqual(load_team(self.shipdir)["template"], {"name": "dev", "version": __version__})
        text = (self.shipdir / "team.yaml").read_text(encoding="utf-8")
        self.assertEqual(text.splitlines()[2].split(":")[0], "name")
        self.assertTrue(text.splitlines()[3].startswith("template: {name: dev"))

    def test_no_record_is_none_and_bad_shape_is_an_error(self):
        ty = self.shipdir / "team.yaml"
        base = ty.read_text(encoding="utf-8")
        ty.write_text(upgrade._TEMPLATE_RE.sub("", base), encoding="utf-8")
        self.assertIsNone(load_team(self.shipdir)["template"])
        ty.write_text(base.replace("template: {name: dev,", "template: {nam: dev,"), encoding="utf-8")
        with self.assertRaises(YamatoError):
            load_team(self.shipdir)

    def test_upgrade_done_moves_the_version_and_keeps_comments(self):
        ty = self.shipdir / "team.yaml"
        before = ty.read_text(encoding="utf-8")
        upgrade.set_template(self.shipdir, "dev", "0.9.0")
        self.assertEqual(load_team(self.shipdir)["template"]["version"], "0.9.0")
        self.assertEqual(upgrade.done(self.shipdir, "v7.0.1"), "7.0.1")
        t = load_team(self.shipdir)["template"]
        self.assertEqual(t, {"name": "dev", "version": "7.0.1"})
        self.assertEqual(len(ty.read_text(encoding="utf-8").splitlines()), len(before.splitlines()))

    def test_done_adds_the_record_to_a_ship_without_one(self):
        ty = self.shipdir / "team.yaml"
        ty.write_text(upgrade._TEMPLATE_RE.sub("", ty.read_text(encoding="utf-8")), encoding="utf-8")
        upgrade.done(self.shipdir, None)
        self.assertEqual(load_team(self.shipdir)["template"], {"name": "dev", "version": __version__})

    def test_migration_range(self):
        got = upgrade.migration_range(MIG, "1.0.0")
        self.assertIn("新しい項目", got)
        self.assertIn("中の項目", got)
        self.assertNotIn("古い項目", got)
        self.assertNotIn("intro", got)


class UpgradeCommandTest(ShipTestCase):
    def setUp(self):
        super().setUp()
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)
        ty = self.shipdir / "team.yaml"
        ty.write_text(upgrade._TEMPLATE_RE.sub("", ty.read_text(encoding="utf-8")), encoding="utf-8")   # a v1.0.0 ship
        (self.shipdir / "roles" / "impl.md").write_text("手直し済み\n", encoding="utf-8")
        self.calls = []

    def run_upgrade(self, from_version="v1.0.0", template=None):
        return upgrade.upgrade(self.shipdir, from_version, template, execvp=lambda f, a: self.calls.append((f, a)),
                               out=lambda *_: None)

    def test_no_record_and_no_from_asks_for_from(self):
        with self.assertRaises(YamatoError) as cm:
            self.run_upgrade(None)
        self.assertIn("--from", str(cm.exception))
        self.assertEqual(self.calls, [])

    def test_unknown_tag_is_an_error_and_leaves_no_backup(self):
        with self.assertRaises(YamatoError):
            self.run_upgrade("v0.0.1")
        self.assertFalse((self.shipdir / ".upgrade").exists())

    def test_backup_materials_and_the_claude_it_starts(self):
        self.assertEqual(self.run_upgrade(), 0)
        (backup,) = list((self.shipdir / ".upgrade").iterdir())
        self.assertTrue(backup.name.startswith(f"1.0.0-{__version__}-"))
        self.assertEqual((backup / "before" / "roles" / "impl.md").read_text(encoding="utf-8"), "手直し済み\n")
        self.assertTrue((backup / "before" / "team.yaml").is_file())
        self.assertTrue((backup / "template-old" / "team.yaml").is_file())
        self.assertTrue((backup / "template-new" / "team.yaml").is_file())
        self.assertIn("未リリース", (backup / "migration.md").read_text(encoding="utf-8"))
        self.assertTrue((backup / "template-diff.patch").is_file())
        # the claude: foreground (no --bg), cwd = the ship, its own settings, not the ship's seat settings
        (prog, argv), = self.calls
        self.assertEqual(Path.cwd().resolve(), self.shipdir.resolve())
        self.assertNotIn("--bg", argv)
        self.assertEqual(argv[argv.index("--settings") + 1], str(backup / "settings.json"))
        perms = json.loads((backup / "settings.json").read_text(encoding="utf-8"))["permissions"]
        self.assertTrue(any(r.startswith("Edit(") and r.endswith("/roles/*.md)") for r in perms["allow"]))
        self.assertTrue(any(r.endswith("/team.yaml)") for r in perms["allow"]))
        self.assertFalse(any("charter" in r for r in perms["allow"]))
        sysprompt = argv[argv.index("--append-system-prompt") + 1]
        self.assertIn(str(backup), sysprompt)
        self.assertIn("upgrade-done", sysprompt)
        self.assertNotIn("{{", sysprompt.replace("{{workspace}}", ""))
        # the roles were not touched
        self.assertEqual((self.shipdir / "roles" / "impl.md").read_text(encoding="utf-8"), "手直し済み\n")

    def test_template_name_comes_from_the_record(self):
        upgrade.set_template(self.shipdir, "dev", "1.0.0")
        self.assertEqual(self.run_upgrade(None), 0)
        self.assertEqual(len(self.calls), 1)

    def test_cli_parses_upgrade_and_done(self):
        with mock.patch.object(upgrade, "upgrade", return_value=0) as up:
            self.assertEqual(cli.main(["ship", "upgrade", str(self.shipdir), "--from", "v1.0.0", "--template", "dev"]), 0)
        up.assert_called_once_with(self.shipdir.resolve(), "v1.0.0", "dev")
        self.assertEqual(cli.main(["ship", "upgrade-done", str(self.shipdir), "--version", "1.1.0"]), 0)
        self.assertEqual(load_team(self.shipdir)["template"]["version"], "1.1.0")


if __name__ == "__main__":
    unittest.main()

"""templates/admiral/ (D-011, D-013): the admiral's role prompt and settings.

Only the static template is under test here (T-023, completion 1-2). The ``yamato
admiral`` command that builds ``_admiral/`` from it (T-020 laid the ``register=False``
/ ``time_limit: none`` groundwork; the CLI verb itself is a later task) and the
``ships_summary`` inject part (T-022, completion 3) are out of scope."""
import json
import unittest
from unittest import mock

from tests.helpers import ShipTestCase
from yamato import decide, ship
from yamato.team import load_team
from yamato.util import YamatoError

# a name is enough to build and validate the template; the real seat lives at
# $YAMATO_HOME/_admiral (a name check_name forbids for an ordinary `ship create`,
# T-020) is the future `yamato admiral` command's business, not this template's
ADMIRAL_NAME = "admiraltest"


class AdmiralTemplateTest(ShipTestCase):
    def setUp(self):
        super().setUp()
        self.adir, self.awarn = ship.create(ADMIRAL_NAME, None, str(self.tmp / ADMIRAL_NAME),
                                            "admiral", register=False)
        self.ateam = load_team(self.adir)
        with mock.patch("yamato.claude.git_root", return_value=None):
            from yamato import runtime

            runtime.generate(self.adir, self.ateam)

    def settings(self) -> dict:
        from yamato import runtime

        return json.loads(runtime.settings_path(self.adir, "admiral").read_text())

    # --- ship create ---------------------------------------------------------

    def test_create_without_workspace_uses_the_ship_folder(self):
        self.assertEqual(self.ateam["workspace"], str(self.adir))
        self.assertEqual(list(self.ateam["seats"]), ["admiral"])
        self.assertEqual(self.ateam["hub"], "admiral")
        self.assertEqual(self.ateam["talk_default"], "admiral")
        for f in ("charter.md", "knowledge.md", "roles/admiral.md", "roles/_memory-curator.md"):
            self.assertTrue((self.adir / f).is_file(), f)

    def test_register_false_keeps_the_ship_out_of_the_registry(self):
        from yamato.util import load_registry

        self.assertNotIn(ADMIRAL_NAME, load_registry())

    # --- D-013: no time limit -------------------------------------------------

    def test_time_limit_is_none(self):
        self.assertIsNone(self.ateam["time_limit"])

    # --- D-011 Q2: auto + WebFetch/WebSearch allowed, ~/dev/yamato read-only --

    def test_admiral_runs_auto_with_web_allowed(self):
        st = self.settings()
        self.assertEqual(st["permissions"]["defaultMode"], "auto")
        deny = st["permissions"]["deny"]
        self.assertNotIn("WebFetch", deny)
        self.assertNotIn("WebSearch", deny)

    def test_dev_yamato_checkout_is_read_only(self):
        deny = self.settings()["permissions"]["deny"]
        self.assertIn("Edit(~/dev/yamato/**)", deny)
        self.assertIn("Write(~/dev/yamato/**)", deny)

    def test_remote_control_is_on(self):
        self.assertTrue(self.ateam["roles"]["admiral"]["remote_control"])
        self.assertIs(self.settings()["remoteControlAtStartup"], True)

    def test_no_placeholder_or_variable_expansion_left_in_rules(self):
        perms = self.settings()["permissions"]
        for rule in perms["allow"] + perms["deny"]:
            self.assertNotIn("{{", rule)
            self.assertNotIn("$", rule)

    def test_role_prompt_has_no_placeholder_left_after_rendering(self):
        from yamato import runtime

        prompt = (self.adir / "roles" / "admiral.md").read_text()
        rendered = runtime.render_prompt(prompt, self.adir, self.ateam)
        self.assertNotIn("{{", rendered)
        self.assertIn("艦の中身に踏み込まない", rendered)

    # --- role prompt covers the requirement doc's 6 jobs + "しないこと" -------

    def test_role_prompt_covers_the_six_jobs(self):
        prompt = (self.adir / "roles" / "admiral.md").read_text()
        for phrase in ("艦を作る・構成を変える", "出撃・帰投", "一望", "判断の代筆",
                      "yamato の使い方に答える", "表示"):
            self.assertIn(phrase, prompt, phrase)

    # --- decisions fall back to the hub (no decisions: table needed) ---------

    def test_decisions_default_to_the_admiral_seat(self):
        rows = decide.categories(self.ateam)
        self.assertIn(("default", "admiral", None), rows)

    # --- dev still needs --workspace; admiral does not ------------------------

    def test_dev_template_still_needs_a_workspace(self):
        with self.assertRaises(YamatoError):
            ship.create("d2", None, str(self.tmp / "d2"), "dev")


if __name__ == "__main__":
    unittest.main()

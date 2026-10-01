"""W4: path forms in prompts and permission rules on Windows (T-041, work/windows-research.md §2.4,
W0 [9] / Issue #68). Windows is never available here: ``runtime.is_windows`` is replaced and the
ship path is a Windows-looking string, and the tests assert the text handed to Claude Code."""
import contextlib
import io
import re
import shlex
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import ShipTestCase
from tests.test_research import rule_matches
from yamato import runtime, ship
from yamato.team import load_team
from yamato.view import layout

WIN = Path("C:\\Users\\John Doe\\ships\\t1")
PLAIN = Path("C:\\Users\\jdoe\\ships\\t1")
EXE = "C:\\Program Files\\Python311\\python.exe"


def on_windows():
    return mock.patch.object(runtime, "is_windows", return_value=True)


class FormsTest(unittest.TestCase):
    def test_path_forms(self):
        with on_windows():
            self.assertEqual(runtime.posix_path(WIN), "C:/Users/John Doe/ships/t1")
            self.assertEqual(runtime.rule_path(WIN), "/c/Users/John Doe/ships/t1")
            self.assertEqual(runtime.ship_arg(WIN), "'C:/Users/John Doe/ships/t1'")
            self.assertEqual(runtime.ship_arg(PLAIN), "C:/Users/jdoe/ships/t1")

    def test_nothing_changes_off_windows(self):
        with mock.patch.object(runtime, "is_windows", return_value=False):
            p = Path("/Users/x/ships/t1")
            self.assertEqual(runtime.posix_path(p), "/Users/x/ships/t1")
            self.assertEqual(runtime.rule_path(p), "/Users/x/ships/t1")
            self.assertEqual(runtime.render_rule("Edit(/{{ship}}/team.yaml)", p, "s"), "Edit(//Users/x/ships/t1/team.yaml)")

    def test_rule_kinds(self):
        with on_windows():
            self.assertEqual(runtime.render_rule("Edit(/{{ship}}/seats/*/inbox.*)", PLAIN, "s"),
                             "Edit(//c/Users/jdoe/ships/t1/seats/*/inbox.*)")
            self.assertEqual(runtime.render_rule("Write(/{{ship}}/seats/{{seat}}/handoff.md)", WIN, "impl"),
                             "Write(//c/Users/John Doe/ships/t1/seats/impl/handoff.md)")
            with mock.patch.object(runtime.sys, "executable", EXE):
                rule = runtime.render_rule("Bash({{yamato}} inbox {{ship}} {{seat}}*)", WIN, "impl")
        inv = f"'C:/Program Files/Python311/python.exe' {shlex.quote(runtime.posix_path(runtime.YAMATO_BIN))}"
        self.assertTrue(rule.startswith(f"Bash({inv} inbox 'C:/Users/John Doe/ships/t1' impl*"), rule)

    def test_invocation_is_as_posix(self):
        with on_windows(), mock.patch.object(runtime.sys, "executable", EXE):
            self.assertEqual(shlex.split(runtime.yamato_invocation())[0], "C:/Program Files/Python311/python.exe")
        self.assertNotIn("\\", runtime.yamato_invocation())


class TemplatesOnWindowsTest(ShipTestCase):
    def setUp(self):
        super().setUp()
        self.ships = {"dev": self.shipdir}
        self.ships["research"], _ = ship.create("r1", None, str(self.tmp / "r1"), "research")
        self.ships["admiral"], _ = ship.create("a1", None, str(self.tmp / "a1"), "admiral")

    def render(self, kind, shipdir):
        team = load_team(self.ships[kind])
        out = {}
        with on_windows(), mock.patch.object(runtime.sys, "executable", EXE):
            for seat in team["seats"]:
                s = runtime.build_settings(shipdir, team, seat)["permissions"]
                out[seat] = (s["allow"], s["deny"])
            prompts = {role: runtime.render_prompt(
                (self.ships[kind] / "roles" / f"{role}.md").read_text(encoding="utf-8"), shipdir, team)
                for role in team["roles"]}
        return out, prompts

    def test_path_rules_are_the_double_slash_form_and_bash_rules_match_the_prompt(self):
        for kind in self.ships:
            for shipdir in (WIN, PLAIN):
                rules, prompts = self.render(kind, shipdir)
                with on_windows():
                    typed = runtime.ship_arg(shipdir)
                    root = runtime.rule_path(shipdir)
                for seat, (allow, deny) in rules.items():
                    for r in allow + deny:
                        with self.subTest(kind=kind, ship=str(shipdir), seat=seat, rule=r):
                            self.assertNotIn("\\", r)
                            self.assertNotIn("{{", r)
                            if not r.startswith("Bash("):
                                self.assertNotIn("C:", r)
                                self.assertNotRegex(r, r"^\w+\(/[^/]") if "ships" in r else None
                                if "ships" in r:
                                    self.assertTrue(r.split("(", 1)[1].startswith("/" + root), r)
                            elif re.search(r" (inbox|board|log) ", r):
                                self.assertIn(f" {typed}", r)
                # prompts spell the ship the same way and never with backslashes or {{
                for role, text in prompts.items():
                    with self.subTest(kind=kind, role=role):
                        self.assertNotIn("{{", text)
                        self.assertNotIn("C:\\", text)

    def test_a_spaced_ship_path_is_one_shell_word_and_allow_rules_match_it(self):
        rules, prompts = self.render("research", WIN)
        allow = rules["researcher-1"][0]
        (rule,) = [r for r in allow if " inbox " in r]
        with on_windows(), mock.patch.object(runtime.sys, "executable", EXE):
            inv = runtime.yamato_invocation()
            typed = runtime.ship_arg(WIN)
        cmd = f"{inv} inbox {typed} researcher-1"
        self.assertEqual(shlex.split(cmd)[3], "C:/Users/John Doe/ships/t1")
        self.assertTrue(rule_matches(rule, "Bash", cmd), (rule, cmd))
        # the prompt line for the same command is what the rule was written from
        self.assertIn(f"{inv} inbox {typed} <seat>", prompts["researcher"])

    def test_file_paths_in_prompts_stay_unquoted(self):
        _, prompts = self.render("research", WIN)
        self.assertIn("C:/Users/John Doe/ships/t1/work/", prompts["researcher"])
        self.assertNotIn("'C:/Users/John Doe/ships/t1'/work", prompts["researcher"])


class InjectionOnWindowsTest(ShipTestCase):
    """seat-facing injected text uses the same ship word as the prompt / allow rules (T-044)."""

    def test_session_start_and_first_prompt_use_ship_arg(self):
        from yamato import inject, seat
        team = load_team(self.shipdir)
        with on_windows(), mock.patch.object(runtime.sys, "executable", EXE):
            typed = runtime.ship_arg(WIN)
            inv = runtime.yamato_invocation()
            text, _ = inject.build(WIN, team, "impl")
            first = seat._first_prompt(WIN, "impl")
        self.assertIn("'C:/Users/John Doe/ships/t1'", typed)
        self.assertIn(f"{inv} inbox {typed} impl", first)
        self.assertIn(f"<ship> には {typed} を", text)
        self.assertIn("C:/Users/John Doe/ships/t1/seats/impl/handoff.md", text)
        self.assertNotIn("C:\\", text + first)

    def test_hook_messages_use_ship_arg(self):
        from yamato import hooks
        with on_windows(), mock.patch.object(runtime.sys, "executable", EXE):
            typed = runtime.ship_arg(WIN)
            msg = hooks._wrapup_message(WIN, "impl")
        self.assertIn(f"seat-stop {typed} impl", msg)

    def test_wrap_up_past_the_deadline_uses_ship_arg(self):
        """the seat-stop line the seat is told to type (inject at OVER, send by a seat at OVER)."""
        from yamato import deadline, inject, seat
        team = load_team(self.shipdir)
        over = deadline.write(self.shipdir, limit=-10, grace=3600, token="t")
        self.assertEqual(deadline.phase(over), deadline.OVER)
        with mock.patch.object(inject, "ship_arg", return_value="SHIPARG"):
            text, _ = inject.build(self.shipdir, team, "impl")
        self.assertIn("seat-stop SHIPARG impl", text)
        self.assertNotIn(f"seat-stop {self.shipdir} impl", text)
        with mock.patch.object(seat, "ship_arg", return_value="SHIPARG"):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                seat.send(self.shipdir, "impl", "hi", "pm")
            printed = buf.getvalue()
        self.assertIn("seat-stop SHIPARG pm", printed)


class CuratorOnWindowsTest(ShipTestCase):
    def test_curator_deny_uses_the_seat_rule_form(self):
        from yamato import memory
        team = {"deny": ["Edit(/{{ship}}/team.yaml)", "Bash({{yamato}} board set {{ship}}*)"]}
        with on_windows(), mock.patch.object(runtime.sys, "executable", EXE):
            deny = memory.curator_settings(WIN, team)["permissions"]["deny"]
            want = [runtime.render_rule(r, WIN, "") for r in team["deny"]]
        self.assertEqual(deny, want)
        self.assertEqual(deny[0], "Edit(//c/Users/John Doe/ships/t1/team.yaml)")
        self.assertIn("'C:/Users/John Doe/ships/t1'", deny[1])


class PowerShellToolTest(ShipTestCase):
    """D-051 A: the seat's settings `env` turns the PowerShell tool off on Windows only."""

    def test_env_is_set_on_windows_and_kept_with_env_unset(self):
        team = load_team(self.shipdir)
        team["env_unset"] = ["GH_TOKEN"]
        with on_windows(), mock.patch.object(runtime.sys, "executable", EXE):
            env = runtime.build_settings(self.shipdir, team, "impl")["env"]
        self.assertEqual(env["CLAUDE_CODE_USE_POWERSHELL_TOOL"], "0")
        self.assertEqual(env["GH_TOKEN"], "")

    def test_env_is_absent_elsewhere(self):
        team = load_team(self.shipdir)
        with mock.patch.object(runtime, "is_windows", return_value=False):
            self.assertNotIn("env", runtime.build_settings(self.shipdir, team, "impl"))


class LayoutOnWindowsTest(unittest.TestCase):
    def test_pane_runs_python_exe_with_the_script_as_its_first_arg(self):
        with on_windows(), mock.patch.object(layout.sys, "executable", EXE), \
                mock.patch.object(layout, "is_windows", return_value=True):
            text = "\n".join(layout._pane("impl", "C:/x/t1", "C:/dev/yamato/yamato", ""))
        self.assertIn('command="C:\\\\Program Files\\\\Python311\\\\python.exe"', text)
        self.assertIn('args "C:/dev/yamato/yamato" "view" "attach"', text)

    def test_unchanged_off_windows(self):
        with mock.patch.object(layout, "is_windows", return_value=False):
            text = "\n".join(layout._pane("impl", "/x/t1", "/dev/yamato/yamato", ""))
        self.assertIn('command="/dev/yamato/yamato"', text)
        self.assertIn('args "view" "attach"', text)


if __name__ == "__main__":
    unittest.main()

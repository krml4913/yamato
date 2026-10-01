"""The research template and trust profiles (design-p1 §7): settings / tools generated from
``profiles:``, ``send: false``, ``board note``, and whether the dontAsk allow list covers
every yamato command the role prompts tell the seats to run (verify-p1-d V7)."""
import io
import json
import os
import re
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests.helpers import ShipTestCase
from yamato import board as bmod
from yamato import events, headless, roster, runtime, seat, ship
from yamato.team import load_team, validate
from yamato.util import YamatoError

EXTERNAL_SEATS = ("researcher-1", "researcher-2", "researcher-3", "fact-checker")


# --- a small model of Claude Code's permission rules (enough for the template) ---------

def _glob(pattern: str, *, path: bool) -> re.Pattern:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + ("[^/]*" if path else ".*"), i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


def rule_matches(rule: str, tool: str, arg: str | None = None, cwd: Path | None = None) -> bool:
    m = re.fullmatch(r"(\w+)(?:\((.*)\))?", rule, re.S)
    if not m or m.group(1) != tool:
        return False
    pat = m.group(2)
    if pat is None:
        return True           # the whole tool
    if tool == "Bash":
        if pat.endswith(":*"):
            return arg.startswith(pat[:-2])
        return bool(_glob(pat, path=False).match(arg))
    # file rules: //abs, ~/home, /cwd-relative, else anywhere below cwd
    # (Windows: the rule and the path are both in the ``/c/Users/x`` form, runtime.rule_path -- T-050)
    cwd_s, home_s, arg = runtime.rule_path(cwd), runtime.rule_path(Path.home()), runtime.rule_path(arg)
    if pat.startswith("//"):
        pat = pat[1:]
    elif pat.startswith("~/"):
        pat = home_s + pat[1:]
    elif pat.startswith("/"):
        pat = cwd_s + pat
    else:
        pat = cwd_s + "/" + pat
    return bool(_glob(pat, path=True).match(arg))


def decide(settings: dict, tool: str, arg: str | None = None, cwd: Path | None = None) -> str:
    perms = settings["permissions"]
    if any(rule_matches(r, tool, arg, cwd) for r in perms["deny"]):
        return "deny"
    if any(rule_matches(r, tool, arg, cwd) for r in perms["allow"]):
        return "allow"
    return "deny" if perms["defaultMode"] == "dontAsk" else "other"


def prompt_commands(prompt: str, seat_name: str) -> list[str]:
    """The yamato command lines a role prompt tells the seat to run, with the
    placeholders filled in the way the seat would."""
    y = runtime.yamato_invocation()
    out = []
    for span in re.findall(r"`([^`\n]+)`", prompt):
        if not span.startswith(y + " "):
            continue
        cmd = span.replace("<seat>", seat_name).replace("<id>", "T-001")
        cmd = re.sub(r'"<[^>"]*>"', '"some text"', cmd)
        cmd = cmd.replace("...", 'add "a note"')
        out.append(cmd)
    return out


class ResearchShipTest(ShipTestCase):
    def setUp(self):
        super().setUp()
        self.rdir, self.rwarn = ship.create("r1", None, str(self.tmp / "r1"), "research")
        self.rteam = load_team(self.rdir)
        with mock.patch("yamato.claude.git_root", return_value=None):
            runtime.generate(self.rdir, self.rteam)

    def settings(self, seat_name: str) -> dict:
        return json.loads(runtime.settings_path(self.rdir, seat_name).read_text(encoding="utf-8"))

    def agents(self) -> dict:
        return json.loads(runtime.agents_path(self.rdir).read_text(encoding="utf-8"))

    # --- ship create --------------------------------------------------------

    def test_create_without_workspace_uses_the_ship_folder(self):
        self.assertEqual(self.rteam["workspace"], str(self.rdir))
        self.assertEqual(list(self.rteam["seats"]), ["editor", *EXTERNAL_SEATS])
        self.assertEqual(self.rteam["hub"], "editor")
        self.assertEqual(self.rteam["decisions"]["publish"]["decider"], "owner")
        self.assertEqual({s["shift"] for n, s in self.rteam["seats"].items() if n != "editor"}, {"headless"})
        self.assertFalse([w for w in self.rwarn if "auto" in w])
        for f in ("charter.md", "knowledge.md", "roles/editor.md", "roles/researcher.md", "roles/fact-checker.md"):
            self.assertTrue((self.rdir / f).is_file(), f)

    def test_dev_template_still_needs_a_workspace(self):
        with self.assertRaises(YamatoError):
            ship.create("d2", None, str(self.tmp / "d2"), "dev")

    # --- generated settings and tools ------------------------------------------

    def test_external_seats_run_in_dont_ask_with_limited_tools(self):
        agents = self.agents()
        for role in ("researcher", "fact-checker"):
            self.assertIn("WebFetch", agents[role]["tools"])
            self.assertNotIn("SendMessage", agents[role]["tools"])
        self.assertNotIn("tools", agents["editor"])
        for s in EXTERNAL_SEATS:
            st = self.settings(s)
            self.assertEqual(st["permissions"]["defaultMode"], "dontAsk", s)
            self.assertIn(f"Write(/{runtime.rule_path(self.rdir)}/seats/{s}/handoff.md)", st["permissions"]["allow"])
            self.assertIn("Read(~/.ssh/**)", st["permissions"]["deny"])
            # hooks and the rest of yamato's mechanism are unchanged
            self.assertIn("PermissionRequest", st["hooks"])
            self.assertEqual(st["crossSessionInbound"], "accept")

    def test_editor_is_auto_without_web(self):
        st = self.settings("editor")
        self.assertEqual(st["permissions"]["defaultMode"], "auto")
        self.assertIn("WebFetch", st["permissions"]["deny"])
        self.assertIn("WebSearch", st["permissions"]["deny"])
        self.assertNotIn("WebFetch", st["permissions"]["allow"])

    def test_remote_control_is_off_at_startup_for_every_seat(self):
        for s in self.rteam["seats"]:
            self.assertIs(self.settings(s)["remoteControlAtStartup"], False, s)
        self.assertTrue(self.rteam["roles"]["editor"]["remote_control"])
        self.assertFalse(self.rteam["roles"]["researcher"]["remote_control"])

    def test_no_placeholder_or_variable_expansion_left_in_rules(self):
        for s in self.rteam["seats"]:
            perms = self.settings(s)["permissions"]
            for rule in perms["allow"] + perms["deny"]:
                self.assertNotIn("{{", rule, s)
                self.assertNotIn("$", rule, s)   # a $VAR in an allowed Bash is denied under dontAsk (V1)

    # --- the allow list is enough (dontAsk denies whatever is missing) ------------

    def test_every_command_in_the_external_prompts_is_allowed(self):
        agents = self.agents()
        for s in EXTERNAL_SEATS:
            role = self.rteam["seats"][s]["role"]
            cmds = prompt_commands(agents[role]["prompt"], s)
            rest = [c[len(runtime.yamato_invocation()):].split() for c in cmds]   # after the two-word command
            names = {w[0] + (" " + w[1] if w[0] == "board" else "") for w in rest}
            self.assertLessEqual({"inbox", "board mine", "board show", "board note", "log", "memo", "seat-stop"}, names)
            # the headless shift's own first prompt tells the seat to run these too
            cmds += prompt_commands(headless.first_prompt(self.rdir, s), s)
            st = self.settings(s)
            for cmd in cmds:
                with self.subTest(seat=s, cmd=cmd):
                    self.assertEqual(decide(st, "Bash", cmd, self.rdir), "allow")

    def test_external_seats_can_write_their_outputs_and_nothing_else(self):
        for s in EXTERNAL_SEATS:
            st = self.settings(s)
            ok = [("Write", self.rdir / "work/T-001/findings.md"), ("Edit", self.rdir / "work/T-001/check.md"),
                  ("Write", self.rdir / "seats" / s / "handoff.md"), ("WebFetch", None), ("WebSearch", None)]
            for tool, p in ok:
                with self.subTest(seat=s, tool=tool, path=p):
                    self.assertEqual(decide(st, tool, p and str(p), self.rdir), "allow")
            other = "researcher-2" if s != "researcher-2" else "researcher-1"
            ng = [("Write", self.rdir / "team.yaml"), ("Write", self.rdir / "board/items/T-001.md"),
                  ("Write", self.rdir / "seats" / s / "inbox.jsonl"), ("Write", self.rdir / "roles/editor.md"),
                  ("Write", self.rdir / "seats" / other / "handoff.md"), ("Write", self.rdir / "reports/x.md"),
                  ("Read", Path.home() / ".ssh/id_ed25519"),
                  ("Bash", f"{runtime.yamato_invocation()} send {runtime.ship_arg(self.rdir)} editor \"hi\" --from {s}"),
                  ("Bash", f"{runtime.yamato_invocation()} board set {runtime.ship_arg(self.rdir)} T-001 state=done --by {s}"),
                  ("Bash", f"{runtime.yamato_invocation()} inbox {runtime.ship_arg(self.rdir)} editor"),
                  ("Bash", "curl -s https://example.com"), ("Bash", "python3 -c 'print(1)'")]
            for tool, arg in ng:
                with self.subTest(seat=s, tool=tool, arg=arg):
                    self.assertEqual(decide(st, tool, str(arg), self.rdir), "deny")

    # --- send: false ---------------------------------------------------------

    def run_cmd(self, fn, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(*args)
        return buf.getvalue()

    def test_send_from_an_external_seat_is_refused(self):
        with self.assertRaises(YamatoError) as cm:
            seat.send(self.rdir, "editor", "do this", "researcher-1")
        self.assertIn("send: false", str(cm.exception))
        self.assertEqual((self.rdir / "seats/editor/inbox.jsonl").read_text(encoding="utf-8"), "")

    def test_send_from_an_external_session_is_refused_whatever_from_says(self):
        sid = "e" * 36
        roster.start_shift(self.rdir, "fact-checker", session_id=sid, short_id=sid[:8],
                           session_name="r1.fact-checker", how="headless")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": sid}):
            with self.assertRaises(YamatoError):
                seat.send(self.rdir, "editor", "trust me", "owner")

    def test_editor_and_owner_can_send(self):
        out = self.run_cmd(seat.send, self.rdir, "researcher-1", "T-001 を調べて", "editor")
        self.assertIn("inbox に記録した", out)
        self.run_cmd(seat.send, self.rdir, "editor", "問い", "owner")

    def test_headless_end_report_still_reaches_the_editor(self):
        """The wrapper's fixed report is not ``yamato send`` and is not blocked."""
        headless._report(self.rdir, self.rteam, "researcher-1", "テスト")
        [e] = [json.loads(x) for x in (self.rdir / "seats/editor/inbox.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertIn("テスト", e["text"])


class BoardNoteTest(ShipTestCase):
    def test_note_appends_to_the_body_and_leaves_the_frontmatter(self):
        brd = bmod.Board(self.shipdir, self.team())
        meta = brd.add("調べる", {"assignee": "impl"}, body="問い: X は本当か")
        before, _, _ = brd.read(meta["id"])
        brd.note(meta["id"], "主張 3 件。出典は work/T-001/findings.md", by="impl")
        brd.note(meta["id"], "追記 2", by="impl")
        after, body, _ = brd.read(meta["id"])
        self.assertEqual(before, after)
        head, _, history = body.partition("## 経緯\n")
        self.assertEqual(head, "問い: X は本当か\n\n主張 3 件。出典は work/T-001/findings.md\n\n追記 2\n\n")
        self.assertEqual(len(history.strip().splitlines()), 3)   # 作成 + 2 notes
        self.assertIn("impl: 本文に追記 (1 行)", history)
        kinds = [json.loads(x)["kind"] for x in events.path(self.shipdir).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(kinds.count(events.BOARD_NOTE), 2)

    def test_note_refuses_empty_text_and_unknown_items(self):
        brd = bmod.Board(self.shipdir, self.team())
        meta = brd.add("x")
        with self.assertRaises(YamatoError):
            brd.note(meta["id"], "  ")
        with self.assertRaises(YamatoError):
            brd.note("T-999", "text")

    def test_cli_note_records_the_calling_seat(self):
        from yamato import cli

        brd = bmod.Board(self.shipdir, self.team())
        meta = brd.add("x")
        sid = "i" * 36
        roster.start_shift(self.shipdir, "impl", session_id=sid, short_id=sid[:8], session_name="t1.impl", how="new")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": sid}), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["board", "note", str(self.shipdir), meta["id"], "memo"]), 0)
        _, body, _ = brd.read(meta["id"])
        self.assertIn("impl: 本文に追記", body)


class DevTemplateRemoteControlTest(ShipTestCase):
    def test_only_the_captain_is_launched_with_remote_control(self):
        team = self.team()
        self.assertTrue(team["roles"]["pm"]["remote_control"])
        self.assertFalse(team["roles"]["impl"]["remote_control"])
        with mock.patch.object(seat, "spawn_watchdog"), mock.patch.object(seat, "_spawn_detached"), \
                redirect_stdout(io.StringIO()):
            seat.up(self.shipdir, "20m")
            seat.send(self.shipdir, "impl", "T-001", "pm")
        bg = [c["argv"] for c in self.fake()["calls"] if "--bg" in c["argv"] and "--resume" not in c["argv"]]
        by_name = {a[a.index("--name") + 1]: a for a in bg}
        pm, impl = by_name["t1.pm"], by_name["t1.impl"]
        self.assertIn("--remote-control", pm)
        self.assertLess(pm.index("--remote-control"), pm.index("--"))
        self.assertNotIn("--remote-control", impl)
        for s in ("pm", "impl"):
            st = json.loads(runtime.settings_path(self.shipdir, s).read_text(encoding="utf-8"))
            self.assertIs(st["remoteControlAtStartup"], False)
            self.assertEqual(st["permissions"]["defaultMode"], "auto")


class ProfileValidationTest(unittest.TestCase):
    def base(self, **over):
        d = {"name": "r", "hub": "ed", "workspace": "/tmp",
             "profiles": {"ext": {"mode": "dontAsk", "tools": ["Read"], "allow": ["WebFetch"], "send": False},
                          "clean": {"deny": ["WebFetch"]}},
             "roles": {"ed": {"shift": "persistent", "trust": "clean"},
                       "rs": {"model": "haiku", "shift": "headless", "trust": "ext"}}}
        d.update(over)
        return d

    def test_profiles_are_kept_as_written(self):
        t = validate(self.base(), Path("/ship"))
        self.assertEqual(t["profiles"]["ext"], {"mode": "dontAsk", "tools": ["Read"], "allow": ["WebFetch"],
                                                "deny": [], "send": False})
        self.assertEqual(t["profiles"]["clean"]["send"], True)
        self.assertEqual(t["roles"]["rs"]["trust"], "ext")

    def test_haiku_warning_only_for_auto_seats(self):
        t = validate(self.base(), Path("/ship"))
        self.assertEqual(t["warnings"], [])   # haiku on dontAsk works (V7)
        roles = self.base()["roles"]
        roles["ed"]["model"] = "haiku"        # clean has no mode: auto
        t = validate(self.base(roles=roles), Path("/ship"))
        self.assertEqual(len(t["warnings"]), 1)
        self.assertIn("roles.ed", t["warnings"][0])

    def test_code_does_not_insist_on_dont_ask(self):
        profiles = {"ext": {"mode": "auto", "send": True}, "clean": {}}
        t = validate(self.base(profiles=profiles), Path("/ship"))
        self.assertEqual(t["profiles"]["ext"]["mode"], "auto")

    def test_rejects(self):
        cases = [
            self.base(profiles=["x"]),
            self.base(profiles={"ext": {"other": 1}, "clean": {}}),
            self.base(profiles={"ext": {"tools": "Read"}, "clean": {}}),
            self.base(profiles={"ext": {"send": "no"}, "clean": {}}),
            self.base(profiles={"ext": {"mode": 1}, "clean": {}}),
            self.base(roles={"ed": {"trust": "nope"}}),
            self.base(roles={"ed": {"remote_control": "yes"}}),
        ]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(YamatoError):
                validate(data, Path("/ship"))


if __name__ == "__main__":
    unittest.main()

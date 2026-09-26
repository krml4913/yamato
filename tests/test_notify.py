"""notify.py: slack / mac / windows / command, best-effort, failures into events.jsonl.

Nothing here sends anything: ``subprocess.run`` and ``urlopen`` are replaced
in every test (and fail the test if a call is not expected).
"""
import base64
import json
import os
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from tests.helpers import ShipTestCase
from yamato import events, notify
from yamato.team import validate

WEBHOOK = "https://hooks.example.test/services/T000/B000/SECRET"
ENV = "YAMATO_TEST_WEBHOOK"


def team(via, **extra):
    return {"name": "dev", "notify": {"via": via, "slack": {"webhook_env": ENV}, **extra}}


def completed(returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout="", stderr=stderr)


class NotifyTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.shipdir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        env = mock.patch.dict(os.environ, {ENV: WEBHOOK})
        env.start()
        self.addCleanup(env.stop)
        run = mock.patch("yamato.notify.subprocess.run", return_value=completed())
        self.run = run.start()
        self.addCleanup(run.stop)
        urlopen = mock.patch("yamato.notify.urllib.request.urlopen")
        self.urlopen = urlopen.start()
        self.addCleanup(urlopen.stop)

    def system(self, name):
        p = mock.patch("yamato.notify.platform.system", return_value=name)
        p.start()
        self.addCleanup(p.stop)

    def failures(self):
        return events.read(self.shipdir, kinds=notify.NOTIFY_FAILED)

    def sent_request(self):
        [call] = self.urlopen.call_args_list
        return call.args[0], call.kwargs


class SlackTest(NotifyTestCase):
    def test_posts_an_attachment_with_level_color_and_emoji(self):
        lines = notify.notify(team(["slack"]), "yamato dev: pm から", "判断ください", "waiting",
                              shipdir=self.shipdir)
        self.assertEqual(lines, ["通知 slack: 送った"])
        req, kw = self.sent_request()
        self.assertEqual(req.full_url, WEBHOOK)
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(kw["timeout"], notify.TIMEOUT)
        [att] = json.loads(req.data.decode("utf-8"))["attachments"]
        self.assertEqual(att["color"], "warning")
        self.assertEqual(att["title"], "🟡 yamato dev: pm から")
        self.assertEqual(att["text"], "判断ください")
        self.assertEqual(att["footer"], "yamato · dev")
        self.assertEqual(self.failures(), [])

    def test_each_level_has_its_own_color(self):
        colors = {}
        for level in notify.LEVELS:
            self.urlopen.reset_mock()
            notify.notify(team(["slack"]), "t", "m", level)
            req, _ = self.sent_request()
            colors[level] = json.loads(req.data)["attachments"][0]["color"]
        self.assertEqual(colors, {"success": "good", "waiting": "warning", "progress": "#439FE0",
                                  "error": "danger", "info": "#cccccc"})

    def test_unknown_level_falls_back_to_info(self):
        notify.notify(team(["slack"]), "t", "m", "loud")
        req, _ = self.sent_request()
        self.assertEqual(json.loads(req.data)["attachments"][0]["title"], "ℹ️ t")

    def test_missing_env_var_is_a_failure_and_sends_nothing(self):
        del os.environ[ENV]
        lines = notify.notify(team(["slack"]), "t", "m", shipdir=self.shipdir)
        self.assertEqual(lines, [f"通知 slack: 失敗 (環境変数 {ENV} が空)"])
        self.urlopen.assert_not_called()
        [ev] = self.failures()
        self.assertEqual(ev["data"], {"via": "slack", "level": "info", "reason": f"環境変数 {ENV} が空"})

    def test_missing_webhook_env_setting_is_a_failure(self):
        lines = notify.notify({"notify": {"via": ["slack"]}}, "t", "m", shipdir=self.shipdir)
        self.assertIn("notify.slack.webhook_env が未設定", lines[0])
        self.urlopen.assert_not_called()

    def test_only_http_urls_are_used(self):
        os.environ[ENV] = "file:///etc/passwd"
        lines = notify.notify(team(["slack"]), "t", "m")
        self.assertIn("失敗", lines[0])
        self.urlopen.assert_not_called()

    def test_http_error_is_a_failure_and_never_shows_the_url(self):
        self.urlopen.side_effect = urllib.error.URLError(f"cannot reach {WEBHOOK}")
        lines = notify.notify(team(["slack"]), "t", "m", shipdir=self.shipdir)
        self.assertIn("<webhook>", lines[0])
        [ev] = self.failures()
        self.assertNotIn("SECRET", json.dumps(ev))
        self.assertNotIn("SECRET", lines[0])

    def test_timeout_is_a_failure(self):
        self.urlopen.side_effect = TimeoutError("timed out")
        lines = notify.notify(team(["slack"]), "t", "m", shipdir=self.shipdir)
        self.assertEqual(lines, ["通知 slack: 失敗 (timed out)"])
        self.assertEqual(len(self.failures()), 1)


class MacTest(NotifyTestCase):
    def test_osascript_gets_emoji_body_and_escaped_strings(self):
        self.system("Darwin")
        lines = notify.notify(team(["mac"]), 'yamato "dev"', 'a\\b "q"\nline2', "error")
        self.assertEqual(lines, ["通知 mac: exit 0"])
        [call] = self.run.call_args_list
        self.assertEqual(call.args[0][:2], ["osascript", "-e"])
        self.assertEqual(call.args[0][2], 'display notification "❌ a\\\\b \\"q\\" line2" with title "yamato \\"dev\\""')
        self.assertEqual(call.kwargs["timeout"], notify.TIMEOUT)

    def test_body_is_cut(self):
        self.system("Darwin")
        notify.notify(team(["mac"]), "t", "あ" * 1000)
        script = self.run.call_args.args[0][2]
        self.assertLess(len(script), 400)

    def test_skipped_off_macos_without_a_failure(self):
        self.system("Linux")
        lines = notify.notify(team(["mac"]), "t", "m", shipdir=self.shipdir)
        self.assertEqual(lines, ["通知 mac: 送らない (macOS ではない)"])
        self.run.assert_not_called()
        self.assertEqual(self.failures(), [])

    def test_nonzero_exit_is_a_failure(self):
        self.system("Darwin")
        self.run.return_value = completed(1, "execution error")
        lines = notify.notify(team(["mac"]), "t", "m", shipdir=self.shipdir)
        self.assertEqual(lines, ["通知 mac: 失敗 (exit 1: execution error)"])
        self.assertEqual(self.failures()[0]["data"]["via"], "mac")


class WindowsTest(NotifyTestCase):
    def toast_xml(self):
        argv = self.run.call_args.args[0]
        script = base64.b64decode(argv[argv.index("-EncodedCommand") + 1]).decode("utf-16-le")
        return script

    def test_runs_powershell_with_an_encoded_toast(self):
        self.system("Windows")
        self.run.return_value = completed(0)
        lines = notify.notify(team(["windows"]), "yamato dev", "終わった", "success")
        self.assertEqual(lines, ["通知 windows: 送った"])
        argv = self.run.call_args.args[0]
        self.assertEqual(argv[:5], ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"])
        script = self.toast_xml()
        self.assertIn("ToastNotificationManager", script)
        self.assertIn("<text>yamato dev</text>", script)
        self.assertIn(f"<text>{notify._toast_text('✅ 終わった')}</text>", script)

    def test_text_cannot_break_out_of_the_xml_or_the_powershell_string(self):
        script = notify._windows_toast_script("a'b", "<b>&\"'</b>\nx\x00y", "info")
        xml = script.split("LoadXml('")[1].split("')\n")[0]
        self.assertNotIn("'", xml)                 # PowerShell single-quoted string
        self.assertNotIn("\n", xml)
        self.assertNotIn("\x00", xml)
        self.assertTrue(xml.isascii())
        self.assertIn("&lt;b&gt;&amp;&quot;&apos;&lt;/b&gt; x y", xml)

    def test_skipped_off_windows_without_a_failure(self):
        self.system("Darwin")
        lines = notify.notify(team(["windows"]), "t", "m", shipdir=self.shipdir)
        self.assertEqual(lines, ["通知 windows: 送らない (Windows ではない)"])
        self.run.assert_not_called()
        self.assertEqual(self.failures(), [])

    def test_nonzero_exit_is_a_failure(self):
        self.system("Windows")
        self.run.return_value = subprocess.CompletedProcess([], 1, stdout=b"", stderr="トースト失敗".encode())
        lines = notify.notify(team(["windows"]), "t", "m", shipdir=self.shipdir)
        self.assertEqual(lines, ["通知 windows: 失敗 (exit 1: トースト失敗)"])
        self.assertEqual(len(self.failures()), 1)


class CommandTest(NotifyTestCase):
    def test_message_goes_to_stdin_as_json_and_to_the_environment(self):
        lines = notify.notify(team(["command"], command="mail-me"), "件名", "本文\n2 行目", "waiting")
        self.assertEqual(lines, ["通知 command: exit 0"])
        [call] = self.run.call_args_list
        self.assertEqual(call.args[0], "mail-me")
        self.assertTrue(call.kwargs["shell"])
        self.assertEqual(json.loads(call.kwargs["input"]), {"title": "件名", "message": "本文\n2 行目", "level": "waiting"})
        env = call.kwargs["env"]
        self.assertEqual((env["YAMATO_TITLE"], env["YAMATO_MESSAGE"], env["YAMATO_LEVEL"]),
                         ("件名", "本文\n2 行目", "waiting"))

    def test_empty_command_is_a_failure(self):
        lines = notify.notify(team(["command"]), "t", "m", shipdir=self.shipdir)
        self.assertEqual(lines, ["通知 command: 失敗 (notify.command が空)"])
        self.run.assert_not_called()
        self.assertEqual(len(self.failures()), 1)

    def test_nonzero_exit_is_a_failure(self):
        self.run.return_value = completed(3, "boom")
        lines = notify.notify(team(["command"], command="x"), "t", "m", shipdir=self.shipdir)
        self.assertEqual(lines, ["通知 command: 失敗 (exit 3: boom)"])
        self.assertEqual(self.failures()[0]["data"]["reason"], "exit 3: boom")

    def test_command_is_not_run_unless_listed_in_via(self):
        notify.notify(team(["mac"], command="mail-me"), "t", "m")
        self.assertNotIn("mail-me", [c.args[0] for c in self.run.call_args_list])


class BestEffortTest(NotifyTestCase):
    def test_every_channel_is_tried_and_a_failure_stops_none(self):
        self.system("Darwin")
        self.urlopen.side_effect = urllib.error.URLError("down")
        self.run.side_effect = [subprocess.TimeoutExpired("osascript", 10), completed(0)]
        lines = notify.notify(team(["slack", "mac", "command"], command="x"), "t", "m", "error",
                              shipdir=self.shipdir)
        self.assertEqual(len(lines), 3)
        self.assertIn("通知 slack: 失敗", lines[0])
        self.assertIn("通知 mac: 失敗", lines[1])
        self.assertEqual(lines[2], "通知 command: exit 0")
        self.assertEqual([e["data"]["via"] for e in self.failures()], ["slack", "mac"])
        self.assertEqual({e["data"]["level"] for e in self.failures()}, {"error"})

    def test_an_os_error_from_the_subprocess_does_not_escape(self):
        self.system("Darwin")
        self.run.side_effect = FileNotFoundError("osascript")
        lines = notify.notify(team(["mac"]), "t", "m", shipdir=self.shipdir)
        self.assertIn("通知 mac: 失敗", lines[0])

    def test_unknown_channel_is_a_failure(self):
        lines = notify.notify(team(["pigeon"]), "t", "m", shipdir=self.shipdir)
        self.assertIn("通知 pigeon: 失敗 (知らない経路", lines[0])
        self.assertEqual(self.failures()[0]["data"]["via"], "pigeon")

    def test_push_notification_is_not_a_channel(self):
        self.assertNotIn("push", notify.CHANNELS)
        self.assertNotIn("PushNotification", notify.CHANNELS)

    def test_nothing_is_sent_without_via(self):
        for t in ({"name": "dev"}, {"name": "dev", "notify": {}}, team([])):
            self.assertEqual(notify.notify(t, "t", "m", shipdir=self.shipdir), [])
        self.run.assert_not_called()
        self.urlopen.assert_not_called()

    def test_without_shipdir_nothing_is_recorded(self):
        lines = notify.notify(team(["command"]), "t", "m")
        self.assertIn("失敗", lines[0])
        self.assertFalse(events.path(self.shipdir).exists())

    def test_a_shipdir_that_cannot_be_written_does_not_raise(self):
        lines = notify.notify(team(["command"]), "t", "m", shipdir=self.shipdir / "missing")
        self.assertIn("失敗", lines[0])


class TeamConfigTest(unittest.TestCase):
    def base(self, **notify_cfg):
        return {"name": "dev", "hub": "pm", "workspace": "/tmp",
                "roles": {"pm": {"model": "opus", "shift": "persistent"}}, "notify": notify_cfg}

    def test_slack_webhook_env_and_command_are_carried(self):
        t = validate(self.base(via=["slack", "mac"], slack={"webhook_env": "HOOK"}, command="x"), Path("/ship"))
        self.assertEqual(t["notify"], {"via": ["slack", "mac"], "command": "x", "slack": {"webhook_env": "HOOK"},
                                       "decisions": "digest"})
        self.assertEqual(t["warnings"], [])

    def test_absent_notify_means_no_channel(self):
        t = validate(self.base(), Path("/ship"))
        self.assertEqual(t["notify"]["via"], [])

    def test_unknown_channel_is_a_warning_not_an_error(self):
        t = validate(self.base(via=["pigeon"]), Path("/ship"))
        self.assertTrue(any("pigeon" in w for w in t["warnings"]))

    def test_bad_shapes_are_rejected(self):
        from yamato.util import YamatoError
        for bad in ({"via": "slack"}, {"slack": "url"}, {"slack": {"webhook_env": 3}}, {"command": ["a"]}):
            with self.subTest(bad=bad), self.assertRaises(YamatoError):
                validate(self.base(**bad), Path("/ship"))


class TemplateTest(ShipTestCase):
    def test_the_template_reads_the_webhook_from_an_environment_variable(self):
        n = self.team()["notify"]
        self.assertEqual(n["via"], [])
        self.assertEqual(n["slack"], {"webhook_env": "YAMATO_SLACK_WEBHOOK"})
        self.assertNotIn("https://", (self.shipdir / "team.yaml").read_text())


if __name__ == "__main__":
    unittest.main()

"""web コンソールの操作の共通部品と席の操作 (rotate・send・decide-close): CLI を subprocess で呼ぶだけ。"""
import http.client
import json
import subprocess
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests import api_schema
from tests.helpers import ShipTestCase
from yamato import dashboard, roster
from yamato.web import edit, ops

PATH = "/api/v1/ops/{op}"


class FakePopen:
    """spawn_detached の偽物が返す子。hang=True は wait が時間切れになる (終わらない CLI)。"""

    def __init__(self, rc, hang):
        self.returncode, self.hang = (None if hang else rc), hang
        self.kill = mock.Mock()
        self.terminate = mock.Mock()
        self.send_signal = mock.Mock()

    def wait(self, timeout=None):
        if self.hang and timeout is not None:
            raise subprocess.TimeoutExpired([], timeout)
        return self.returncode


def spawn(out=b"", rc=0, hang=False, side_effect=None):
    """``ops.procs.spawn_detached`` を偽物にする (本物の CLI は起こさない)。出力は渡された stdout に書く。"""
    box = {}

    def fake(argv, cwd=None, env=None, stdout=None, stderr=None):
        stdout.write(out)
        stdout.flush()
        box["popen"] = FakePopen(rc, hang)
        return box["popen"]

    m = mock.patch.object(ops.procs, "spawn_detached", side_effect=side_effect or fake)
    m.popen = lambda: box["popen"]
    return m


class OpsCommonTest(ShipTestCase):
    def setUp(self):
        super().setUp()
        p = mock.patch("yamato.admiral.all_ships", return_value={"t1": self.shipdir})
        p.start()
        self.addCleanup(p.stop)
        self.seat = next(n for n, s in self.team()["seats"].items() if s["shift"] == "persistent")
        roster.update(self.shipdir, self.seat, sessionId="sid-1234-abcd")

    def call(self, op, body):
        return dashboard.api_post(f"/api/v1/ops/{op}", body)

    def req(self, **kw):
        return {"ship": "t1", "params": {"seat": self.seat}, "confirmed": True, **kw}

    # --- 呼び方: list・shell なし・タイムアウト ---

    def test_runs_cli_as_argv_list_without_shell(self):
        m = spawn(b"ok\n")
        with m as run:
            status, body = self.call("rotate", self.req())
        self.assertEqual(status, 200, body)
        argv = run.call_args.args[0]
        self.assertIsInstance(argv, list)
        self.assertEqual(argv[2:], ["rotate", str(self.shipdir), self.seat, "--by", "owner"])
        self.assertFalse(run.call_args.kwargs.get("shell"))
        api_schema.assert_valid(self, body, PATH, method="post")
        self.assertEqual((body["exit_code"], body["output"], body["timed_out"]), (0, "ok\n", False))

    def test_a_seat_name_with_shell_characters_is_one_argument(self):
        status, body = self.call("rotate", self.req(params={"seat": "x; rm -rf /"}))
        self.assertEqual((status, body["error"]["code"]), (400, "bad_request"))

    def test_nonzero_exit_is_200_with_output_and_exit_code(self):
        with spawn("失敗した\n".encode(), 1):
            status, body = self.call("rotate", self.req())
        self.assertEqual((status, body["exit_code"], body["output"]), (200, 1, "失敗した\n"))

    def test_timeout_is_reported_and_the_cli_is_not_killed(self):
        m = spawn(b"partial", hang=True)
        with m, mock.patch.object(ops.threading, "Thread") as thread:
            status, body = self.call("rotate", self.req())
        self.assertEqual((status, body["timed_out"], body["exit_code"], body["output"]), (200, True, None, "partial"))
        api_schema.assert_valid(self, body, PATH, method="post")
        p = m.popen()
        for killer in (p.kill, p.terminate, p.send_signal):
            killer.assert_not_called()   # 時間切れでも CLI は殺さない (裏で最後まで走る)
        self.assertEqual(thread.call_args.kwargs["target"], p.wait)   # 終わったら回収する
        self.assertTrue(Path(body["log"]).is_file())
        self.assertIn(".runtime", Path(body["log"]).parts)

    def test_cli_is_started_in_its_own_session(self):
        with mock.patch.object(ops.procs.subprocess, "Popen") as popen, \
                mock.patch.object(ops.procs, "is_windows", return_value=False):
            popen.return_value.returncode = 0
            self.call("rotate", self.req())
        self.assertTrue(popen.call_args.kwargs["start_new_session"])   # dashboard の Ctrl-C を受けない
        self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_output_files_are_pruned(self):
        with spawn(b"x"):
            for _ in range(ops.LOG_KEEP + 3):
                self.call("rotate", self.req())
        logs = list((self.shipdir / ".runtime" / "web-ops").glob("*.log"))
        self.assertLessEqual(len(logs), ops.LOG_KEEP)

    def test_output_is_stripped_of_control_chars_and_capped(self):
        raw = b"\x1b[31mred\x1b[0m\x07\r\nline\n" + b"x" * (ops.MAX_OUTPUT + 5)
        with spawn(raw):
            _, body = self.call("rotate", self.req())
        self.assertTrue(body["output"].startswith("red\nline\n"))
        self.assertNotIn("\x1b", body["output"])
        self.assertTrue(body["truncated"])
        self.assertEqual(len(body["output"]), ops.MAX_OUTPUT)

    def test_html_in_output_is_returned_as_text_untouched(self):
        with spawn(b"<script>x</script>"):
            _, body = self.call("rotate", self.req())
        self.assertEqual(body["output"], "<script>x</script>")   # 画面は text として描く (HTML にしない)

    # --- 入口の検査 ---

    def test_needs_confirmation(self):
        with mock.patch.object(ops.procs, "spawn_detached") as run:
            for b in (self.req(confirmed=False), {k: v for k, v in self.req().items() if k != "confirmed"}):
                status, body = self.call("rotate", b)
                self.assertEqual((status, body["error"]["code"]), (400, "bad_request"))
        run.assert_not_called()

    def test_unknown_op_ship_seat_and_bad_body(self):
        with mock.patch.object(ops.procs, "spawn_detached") as run:
            self.assertEqual(self.call("nope", self.req())[0], 404)
            self.assertEqual(self.call("seat-stop", self.req())[0], 404)   # 外から席を止める入口は無い
            self.assertEqual(self.call("rotate", self.req(ship="zz"))[0], 404)
            self.assertEqual(self.call("rotate", self.req(params={"seat": "ghost"}))[0], 400)
            self.assertEqual(self.call("rotate", self.req(params={}))[0], 400)
            self.assertEqual(self.call("rotate", self.req(params={"seat": 1}))[0], 400)
            self.assertEqual(self.call("rotate", {"params": {}, "confirmed": True})[0], 400)
        run.assert_not_called()

    def test_a_failing_launch_is_500_json(self):
        with spawn(side_effect=OSError("boom")):
            status, body = self.call("rotate", self.req())
        self.assertEqual((status, body["error"]["code"]), (500, "internal"))

    # --- 本物の CLI (偽の claude で) ---

    def test_real_cli_rotate_marks_a_stopped_persistent_seat(self):
        status, body = self.call("rotate", self.req())
        self.assertEqual((status, body["exit_code"]), (200, 0), body)
        self.assertTrue(roster.seat(self.shipdir, self.seat).get("rotateRequested"))


class OpsHttpTest(unittest.TestCase):
    """POST の入口は edit と同じ検査 (トークン・Host・Origin) を通る。"""

    def setUp(self):
        with mock.patch.object(dashboard.static, "DIST", Path("/nonexistent/dist")):
            self.edit_srv = dashboard.make_server(0, edit_mode=True)
            self.ro_srv = dashboard.make_server(0, edit_mode=False)
        for s in (self.edit_srv, self.ro_srv):
            self.addCleanup(s.server_close)
            threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
            self.addCleanup(s.shutdown)

    def post(self, srv, token="ok", origin="ok"):
        port = srv.server_address[1]
        h = {"Content-Type": "application/json"}
        if token == "ok":
            token = srv.edit_token
        if token:
            h["X-Yamato-Token"] = token
        h["Origin"] = f"http://127.0.0.1:{port}" if origin == "ok" else origin
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("POST", "/api/v1/ops/rotate", json.dumps({"ship": "x", "params": {}, "confirmed": True}), h)
        r = c.getresponse()
        return r.status

    def test_checks_are_the_same_as_edit(self):
        with mock.patch.object(ops.procs, "spawn_detached") as run:
            self.assertEqual(self.post(self.edit_srv, token="bad"), 403)
            self.assertEqual(self.post(self.edit_srv, token=None), 403)
            self.assertEqual(self.post(self.edit_srv, origin="http://evil.example"), 403)
            self.assertEqual(self.post(self.ro_srv, token=None), 501)   # --edit なしでは受けない
        run.assert_not_called()
        self.assertEqual(self.post(self.edit_srv), 404)   # 通る (艦が無いので 404)


class ShipOpsTest(ShipTestCase):
    """up・down・extend・halt: 本物は走らせず subprocess を偽物にして、呼ぶ引数と環境を確かめる。"""

    def setUp(self):
        super().setUp()
        p = mock.patch("yamato.admiral.find_ship", side_effect=lambda n: self.shipdir if n == "t1" else None)
        p.start()
        self.addCleanup(p.stop)

    def run_op(self, op, params=None, **kw):
        body = {"ship": "t1", "params": params or {}, "confirmed": True, **kw}
        with spawn(b"ok\n") as run:
            status, resp = dashboard.api_post(f"/api/v1/ops/{op}", body)
        return status, resp, run

    def test_each_op_calls_the_regular_cli_entry(self):
        d = str(self.shipdir)
        cases = [
            ("up", {}, ["up", d, "--quiet"]),
            ("up", {"for": "3h", "seats": "impl,review"}, ["up", d, "--quiet", "--for=3h", "--seats=impl,review"]),
            ("down", {}, ["down", d]),
            ("down-force", {}, ["down", d, "--force"]),
            ("extend", {"span": "1h"}, ["extend", d, "1h"]),
            ("halt", {}, ["halt", d]),
        ]
        for op, params, expect in cases:
            with self.subTest(op=op, params=params):
                status, body, run = self.run_op(op, params)
                self.assertEqual(status, 200, body)
                argv = run.call_args.args[0]
                self.assertEqual(argv[2:], expect)
                self.assertFalse(run.call_args.kwargs.get("shell"))
                api_schema.assert_valid(self, body, PATH, method="post")

    def test_does_not_borrow_a_seat_or_admiral_identity(self):
        _, _, run = self.run_op("halt")
        added = set(run.call_args.kwargs["env"]) - set(__import__("os").environ)
        self.assertEqual(added, set())

    def test_every_ship_op_needs_confirmation(self):
        for op in ("up", "down", "down-force", "extend", "halt"):
            with self.subTest(op=op):
                status, body, run = self.run_op(op, {"span": "1h"}, confirmed=False)
                self.assertEqual((status, body["error"]["code"]), (400, "bad_request"))
                run.assert_not_called()

    def test_extend_needs_span_and_values_cannot_become_options(self):
        for op, params in (("extend", {}), ("extend", {"span": "-1h"}), ("up", {"for": "--seats=x"}),
                           ("up", {"seats": "-x"})):
            with self.subTest(op=op, params=params):
                status, body, run = self.run_op(op, params)
                self.assertEqual((status, body["error"]["code"]), (400, "bad_request"))
                run.assert_not_called()

    def test_cli_refusal_is_shown_as_is(self):
        with spawn("艦は起動していない\n".encode(), 1):
            status, body = dashboard.api_post("/api/v1/ops/extend", {"ship": "t1", "params": {"span": "1h"}, "confirmed": True})
        self.assertEqual((status, body["exit_code"], body["output"]), (200, 1, "艦は起動していない\n"))

    def test_unknown_ship_is_404(self):
        status, body, run = self.run_op("halt", ship="nope")
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))
        run.assert_not_called()


class SendAndDecideCloseTest(ShipTestCase):
    """send・decide-close: 本物の席は起こさず subprocess を偽物にして、呼ぶ引数を確かめる。"""

    def setUp(self):
        super().setUp()
        p = mock.patch("yamato.admiral.find_ship", side_effect=lambda n: self.shipdir if n == "t1" else None)
        p.start()
        self.addCleanup(p.stop)
        self.seat = next(iter(self.team()["seats"]))

    def run_op(self, op, params, **kw):
        body = {"ship": "t1", "params": params, "confirmed": True, **kw}
        with spawn(b"ok\n") as run:
            status, resp = dashboard.api_post(f"/api/v1/ops/{op}", body)
        return status, resp, run

    def test_send_is_from_owner_and_the_text_is_one_argument_after_double_dash(self):
        text = "--from admiral; rm -rf / $(x)\n二行目 <b>"
        status, body, run = self.run_op("send", {"seat": self.seat, "message": text})
        self.assertEqual(status, 200, body)
        argv = run.call_args.args[0]
        self.assertEqual(argv[2:], ["send", str(self.shipdir), self.seat, "--from", "owner", "--", text])
        self.assertFalse(run.call_args.kwargs.get("shell"))
        api_schema.assert_valid(self, body, PATH, method="post")

    def test_send_needs_a_known_seat_and_a_message(self):
        for params in ({"seat": self.seat}, {"seat": self.seat, "message": "  "}, {"message": "x"},
                       {"seat": "ghost", "message": "x"}, {"seat": "owner", "message": "x"}):
            with self.subTest(params=params):
                status, body, run = self.run_op("send", params)
                self.assertEqual((status, body["error"]["code"]), (400, "bad_request"))
                run.assert_not_called()

    def test_decide_close_passes_choice_and_reason_as_values_not_options(self):
        status, body, run = self.run_op("decide-close", {"id": "D-001", "choice": "--by admiral", "reason": "-x $(y)"})
        self.assertEqual(status, 200, body)
        self.assertEqual(run.call_args.args[0][2:], [
            "decide", "close", str(self.shipdir), "D-001", "--by", "owner",
            "--choice=--by admiral", "--reason=-x $(y)"])

    def test_decide_close_needs_id_choice_and_reason(self):
        for params in ({"choice": "A", "reason": "r"}, {"id": "-x", "choice": "A", "reason": "r"},
                       {"id": "D-001", "reason": "r"}, {"id": "D-001", "choice": "A"},
                       {"id": "D-001", "choice": " ", "reason": "r"}):
            with self.subTest(params=params):
                status, body, run = self.run_op("decide-close", params)
                self.assertEqual((status, body["error"]["code"]), (400, "bad_request"))
                run.assert_not_called()

    def test_both_need_confirmation(self):
        for op, params in (("send", {"seat": self.seat, "message": "x"}),
                           ("decide-close", {"id": "D-001", "choice": "A", "reason": "r"})):
            with self.subTest(op=op):
                status, body, run = self.run_op(op, params, confirmed=False)
                self.assertEqual((status, body["error"]["code"]), (400, "bad_request"))
                run.assert_not_called()

    def test_real_cli_closes_a_decision_as_owner(self):
        from yamato import decide
        meta = decide.open_decision(self.shipdir, self.team(), category="default", title="どれにする", by="owner")
        status, body = dashboard.api_post("/api/v1/ops/decide-close", {
            "ship": "t1", "confirmed": True,
            "params": {"id": meta["id"], "choice": "A", "reason": "安いから"}})
        self.assertEqual((status, body["exit_code"]), (200, 0), body)
        self.assertIn("閉じた", body["output"])
        log = (self.shipdir / "decisions" / "log.md").read_text(encoding="utf-8")
        self.assertRegex(log, r"## D-001 .*, owner\)")
        self.assertIn("安いから", log)


class OperationRegistryTest(unittest.TestCase):
    def test_only_registered_operations_exist(self):
        self.assertEqual(sorted(ops.OPERATIONS), ["decide-close", "down", "down-force", "extend", "halt", "rotate", "send", "up"])
        with self.assertRaises(edit.EditError) as cm:
            ops.handle("seat-stop", {"ship": "x", "params": {}, "confirmed": True})
        self.assertEqual(cm.exception.status, 404)


if __name__ == "__main__":
    unittest.main()

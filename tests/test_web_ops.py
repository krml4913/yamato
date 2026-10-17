"""web コンソールの操作の共通部品と席の操作 (rotate): CLI を subprocess で呼ぶだけ。"""
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


def completed(out=b"", rc=0):
    return subprocess.CompletedProcess([], rc, out, None)


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

    def test_runs_cli_as_argv_list_without_shell_with_timeout(self):
        with mock.patch.object(ops.subprocess, "run", return_value=completed(b"ok\n")) as run:
            status, body = self.call("rotate", self.req())
        self.assertEqual(status, 200, body)
        argv = run.call_args.args[0]
        self.assertIsInstance(argv, list)
        self.assertEqual(argv[2:], ["rotate", str(self.shipdir), self.seat, "--by", "owner"])
        self.assertFalse(run.call_args.kwargs.get("shell"))
        self.assertEqual(run.call_args.kwargs["timeout"], ops.TIMEOUT)
        api_schema.assert_valid(self, body, PATH, method="post")
        self.assertEqual((body["exit_code"], body["output"], body["timed_out"]), (0, "ok\n", False))

    def test_a_seat_name_with_shell_characters_is_one_argument(self):
        status, body = self.call("rotate", self.req(params={"seat": "x; rm -rf /"}))
        self.assertEqual((status, body["error"]["code"]), (400, "bad_request"))

    def test_nonzero_exit_is_200_with_output_and_exit_code(self):
        with mock.patch.object(ops.subprocess, "run", return_value=completed("失敗した\n".encode(), 1)):
            status, body = self.call("rotate", self.req())
        self.assertEqual((status, body["exit_code"], body["output"]), (200, 1, "失敗した\n"))

    def test_timeout_is_reported_not_raised(self):
        err = subprocess.TimeoutExpired([], 1, output=b"partial")
        with mock.patch.object(ops.subprocess, "run", side_effect=err):
            status, body = self.call("rotate", self.req())
        self.assertEqual((status, body["timed_out"], body["exit_code"], body["output"]), (200, True, None, "partial"))
        api_schema.assert_valid(self, body, PATH, method="post")

    def test_output_is_stripped_of_control_chars_and_capped(self):
        raw = b"\x1b[31mred\x1b[0m\x07\r\nline\n" + b"x" * (ops.MAX_OUTPUT + 5)
        with mock.patch.object(ops.subprocess, "run", return_value=completed(raw)):
            _, body = self.call("rotate", self.req())
        self.assertTrue(body["output"].startswith("red\nline\n"))
        self.assertNotIn("\x1b", body["output"])
        self.assertTrue(body["truncated"])
        self.assertEqual(len(body["output"]), ops.MAX_OUTPUT)

    def test_html_in_output_is_returned_as_text_untouched(self):
        with mock.patch.object(ops.subprocess, "run", return_value=completed(b"<script>x</script>")):
            _, body = self.call("rotate", self.req())
        self.assertEqual(body["output"], "<script>x</script>")   # 画面は text として描く (HTML にしない)

    # --- 入口の検査 ---

    def test_needs_confirmation(self):
        with mock.patch.object(ops.subprocess, "run") as run:
            for b in (self.req(confirmed=False), {k: v for k, v in self.req().items() if k != "confirmed"}):
                status, body = self.call("rotate", b)
                self.assertEqual((status, body["error"]["code"]), (400, "bad_request"))
        run.assert_not_called()

    def test_unknown_op_ship_seat_and_bad_body(self):
        with mock.patch.object(ops.subprocess, "run") as run:
            self.assertEqual(self.call("nope", self.req())[0], 404)
            self.assertEqual(self.call("seat-stop", self.req())[0], 404)   # 外から席を止める入口は無い
            self.assertEqual(self.call("rotate", self.req(ship="zz"))[0], 404)
            self.assertEqual(self.call("rotate", self.req(params={"seat": "ghost"}))[0], 400)
            self.assertEqual(self.call("rotate", self.req(params={}))[0], 400)
            self.assertEqual(self.call("rotate", self.req(params={"seat": 1}))[0], 400)
            self.assertEqual(self.call("rotate", {"params": {}, "confirmed": True})[0], 400)
        run.assert_not_called()

    def test_a_failing_launch_is_500_json(self):
        with mock.patch.object(ops.subprocess, "run", side_effect=OSError("boom")):
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
        with mock.patch.object(ops.subprocess, "run") as run:
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
        with mock.patch.object(ops.subprocess, "run", return_value=completed(b"ok\n", 0)) as run:
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
        with mock.patch.object(ops.subprocess, "run", return_value=completed("艦は起動していない\n".encode(), 1)):
            status, body = dashboard.api_post("/api/v1/ops/extend", {"ship": "t1", "params": {"span": "1h"}, "confirmed": True})
        self.assertEqual((status, body["exit_code"], body["output"]), (200, 1, "艦は起動していない\n"))

    def test_unknown_ship_is_404(self):
        status, body, run = self.run_op("halt", ship="nope")
        self.assertEqual((status, body["error"]["code"]), (404, "not_found"))
        run.assert_not_called()


class OperationRegistryTest(unittest.TestCase):
    def test_only_registered_operations_exist(self):
        self.assertEqual(sorted(ops.OPERATIONS), ["down", "down-force", "extend", "halt", "rotate", "up"])
        with self.assertRaises(edit.EditError) as cm:
            ops.handle("seat-stop", {"ship": "x", "params": {}, "confirmed": True})
        self.assertEqual(cm.exception.status, 404)


if __name__ == "__main__":
    unittest.main()

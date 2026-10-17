"""書き込みの土台: --edit のときだけ POST を受け、トークン・Host・Origin が全部合うときだけ通す。"""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tests import api_schema
from yamato import dashboard
from yamato.web import edit


class EditTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.ship = Path(tmp.name)
        self.file = self.ship / "note.txt"
        self.file.write_text("one\n", encoding="utf-8")
        self.problems: list[str] = []
        self.target = edit.Target(self.file, self.ship, "note.txt", lambda text: self.problems + (["空"] if not text else []))
        p = mock.patch.object(edit, "RESOLVERS", [lambda ship, f: self.target if (ship, f) == ("t1", "note.txt") else None])
        p.start()
        self.addCleanup(p.stop)
        with mock.patch.object(dashboard.static, "DIST", Path("/nonexistent/dist")):
            self.srv = dashboard.make_server(0, edit_mode=True)
        self.addCleanup(self.srv.server_close)
        threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.port = self.srv.server_address[1]

    def post(self, op, body, token="ok", host="ok", origin="ok", ctype="application/json", srv=None):
        srv = srv or self.srv
        port = srv.server_address[1]
        h = {"Content-Type": ctype}
        if token == "ok":
            token = srv.edit_token
        if token is not None:
            h["X-Yamato-Token"] = token
        if origin == "ok":
            origin = f"http://127.0.0.1:{port}"
        if origin is not None:
            h["Origin"] = origin
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.putrequest("POST", f"/api/v1/edit/{op}", skip_host=True)
        c.putheader("Host", f"127.0.0.1:{port}" if host == "ok" else host)
        for k, v in h.items():
            c.putheader(k, v)
        data = json.dumps(body).encode() if not isinstance(body, bytes) else body
        c.putheader("Content-Length", str(len(data)))
        c.endheaders(data)
        r = c.getresponse()
        raw = r.read()
        return r.status, json.loads(raw) if raw.startswith(b"{") else raw

    def req(self, content="two\n", **extra):
        return {"ship": "t1", "file": "note.txt", "content": content, **extra}

    def preview(self, content="two\n"):
        status, body = self.post("preview", self.req(content))
        self.assertEqual(status, 200, body)
        api_schema.assert_valid(self, body, "/api/v1/edit/preview", method="post")
        return body

    # --- 検査 (どれか 1 つ違えば通さない。ファイルは変わらない) ---

    def assert_refused(self, status, **kw):
        s, body = self.post("commit", self.req(base_hash=edit.file_hash(self.file)), **kw)
        self.assertEqual(s, status, body)
        api_schema.assert_valid(self, body, "/api/v1/edit/commit", str(s) if str(s) in ("200",) else "default", "post")
        self.assertEqual(self.file.read_text(encoding="utf-8"), "one\n")
        return body

    def test_wrong_token_refused(self):
        self.assertEqual(self.assert_refused(403, token="x" * 43)["error"]["code"], "forbidden")

    def test_missing_token_refused(self):
        self.assert_refused(403, token=None)

    def test_wrong_host_refused(self):
        self.assert_refused(403, host="evil.example:%d" % self.port)

    def test_wrong_origin_refused(self):
        self.assert_refused(403, origin="http://evil.example")
        self.assert_refused(403, origin=f"http://localhost:{self.port}")

    def test_missing_origin_refused(self):
        self.assert_refused(403, origin=None)

    def test_not_json_content_type_refused(self):
        self.assert_refused(400, ctype="text/plain")

    def test_without_edit_post_is_501(self):
        with mock.patch.object(dashboard.static, "DIST", Path("/nonexistent/dist")):
            srv = dashboard.make_server(0)
        self.addCleanup(srv.server_close)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.addCleanup(srv.shutdown)
        self.assertIsNone(srv.edit_token)
        for op in ("preview", "commit"):
            s, _ = self.post(op, self.req(base_hash="-"), token="anything", srv=srv)
            self.assertEqual(s, 501)
        self.assertEqual(self.file.read_text(encoding="utf-8"), "one\n")

    def test_edit_mode_still_501_for_put_and_delete(self):
        for m in ("PUT", "DELETE"):
            c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            c.request(m, "/api/v1/edit/commit")
            self.assertEqual(c.getresponse().status, 501)

    def test_edit_mode_get_checks_host(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.putrequest("GET", "/", skip_host=True)
        c.putheader("Host", "evil.example")
        c.endheaders()
        self.assertEqual(c.getresponse().status, 403)

    def test_token_is_embedded_in_index_only_in_edit_mode(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "index.html").write_text("<html><head><title>x</title></head><body></body></html>", encoding="utf-8")
            for mode in (True, False):
                with mock.patch.object(dashboard.static, "DIST", Path(d)):
                    srv = dashboard.make_server(0, edit_mode=mode)
                self.addCleanup(srv.server_close)
                threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
                self.addCleanup(srv.shutdown)
                c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
                c.request("GET", "/ship/x")
                html = c.getresponse().read().decode()
                meta = f'<meta name="yamato-token" content="{srv.edit_token}">'
                self.assertEqual(meta in html, mode)

    def test_tokens_differ_per_start(self):
        with mock.patch.object(dashboard.static, "DIST", Path("/nonexistent/dist")):
            other = dashboard.make_server(0, edit_mode=True)
        self.addCleanup(other.server_close)
        self.assertNotEqual(other.edit_token, self.srv.edit_token)

    # --- preview / commit ---

    def test_preview_returns_diff_and_does_not_write(self):
        body = self.preview("two\n")
        self.assertTrue(body["valid"] and body["changed"])
        self.assertIn("-one", body["diff"])
        self.assertIn("+two", body["diff"])
        self.assertEqual(body["base_hash"], edit.file_hash(self.file))
        self.assertEqual(self.file.read_text(encoding="utf-8"), "one\n")

    def test_preview_reports_validation_errors(self):
        self.problems = ["ここが違う"]
        body = self.preview("two\n")
        self.assertFalse(body["valid"])
        self.assertEqual(body["errors"], ["ここが違う"])

    def test_commit_writes_and_keeps_one_backup_generation(self):
        base = self.preview("two\n")["base_hash"]
        s, body = self.post("commit", self.req("two\n", base_hash=base))
        self.assertEqual(s, 200, body)
        api_schema.assert_valid(self, body, "/api/v1/edit/commit", method="post")
        self.assertEqual(self.file.read_text(encoding="utf-8"), "two\n")
        self.assertEqual(body["hash"], edit.file_hash(self.file))
        backup = self.ship / ".runtime" / edit.BACKUP_DIR / "note.txt"
        self.assertEqual(backup.read_text(encoding="utf-8"), "one\n")
        s, _ = self.post("commit", self.req("three\n", base_hash=body["hash"]))
        self.assertEqual(s, 200)
        self.assertEqual(backup.read_text(encoding="utf-8"), "two\n")   # 1 世代だけ
        self.assertEqual(len(list((self.ship / ".runtime" / edit.BACKUP_DIR).iterdir())), 1)
        self.assertEqual([p.name for p in self.ship.iterdir() if p.name.endswith(".tmp")], [])

    def test_commit_with_stale_hash_is_conflict_and_writes_nothing(self):
        base = self.preview("two\n")["base_hash"]
        self.file.write_text("changed elsewhere\n", encoding="utf-8")
        s, body = self.post("commit", self.req("two\n", base_hash=base))
        self.assertEqual((s, body["error"]["code"]), (409, "conflict"))
        api_schema.assert_valid(self, body, "/api/v1/edit/commit", "default", "post")
        self.assertEqual(self.file.read_text(encoding="utf-8"), "changed elsewhere\n")
        self.assertFalse((self.ship / ".runtime").exists())

    def test_commit_revalidates(self):
        base = self.preview("two\n")["base_hash"]
        self.problems = ["だめ"]
        s, body = self.post("commit", self.req("two\n", base_hash=base))
        self.assertEqual((s, body["error"]["code"]), (422, "invalid"))
        self.assertEqual(self.file.read_text(encoding="utf-8"), "one\n")

    def test_commit_creates_missing_file(self):
        self.file.unlink()
        base = self.preview("new\n")["base_hash"]
        self.assertEqual(base, edit.ABSENT)
        s, _ = self.post("commit", self.req("new\n", base_hash=base))
        self.assertEqual(s, 200)
        self.assertEqual(self.file.read_text(encoding="utf-8"), "new\n")
        self.assertFalse((self.ship / ".runtime").exists())

    def test_commit_keeps_file_mode(self):
        self.file.chmod(0o640)
        self.post("commit", self.req("two\n", base_hash=edit.file_hash(self.file)))
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o640)

    def test_atomic_write_failure_leaves_original(self):
        with mock.patch("os.replace", side_effect=OSError("boom")):
            s, body = self.post("commit", self.req("two\n", base_hash=edit.file_hash(self.file)))
        self.assertEqual(s, 500)
        self.assertEqual(self.file.read_text(encoding="utf-8"), "one\n")
        self.assertEqual([p.name for p in self.ship.iterdir() if p.name.endswith(".tmp")], [])

    def test_unknown_target_and_bad_body(self):
        s, _ = self.post("preview", {"ship": "t1", "file": "other.txt", "content": "x"})
        self.assertEqual(s, 404)
        s, _ = self.post("preview", {"ship": "t1"})
        self.assertEqual(s, 400)
        s, _ = self.post("commit", self.req("x"))   # base_hash 無し
        self.assertEqual(s, 400)
        s, _ = self.post("preview", b"not json")
        self.assertEqual(s, 400)
        s, _ = self.post("nope", self.req())
        self.assertEqual(s, 404)

    def test_default_has_no_writable_targets(self):
        with mock.patch.object(edit, "RESOLVERS", []):
            with self.assertRaises(edit.EditError):
                edit.resolve("t1", "note.txt")


if __name__ == "__main__":
    unittest.main()

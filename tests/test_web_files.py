"""web コンソールの md の閲覧 (files API): 列挙にあるファイルだけ返す。"""
import http.client
import json
import threading
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import api_schema
from yamato import dashboard

FILES_PATH = "/api/v1/ships/{name}/files/{file}"


class ShipFilesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.ship = self.root / "alpha"
        (self.ship / "roles").mkdir(parents=True)
        (self.ship / "roles" / "impl.md").write_text("# impl\n日本語\n", encoding="utf-8")
        (self.ship / "roles" / "pm.md").write_text("# pm\n", encoding="utf-8")
        (self.ship / "roles" / "notes.txt").write_text("x", encoding="utf-8")
        (self.ship / "charter.md").write_text("# charter\n", encoding="utf-8")
        (self.ship / "knowledge.md").write_text("# knowledge\n", encoding="utf-8")
        (self.ship / "team.yaml").write_text("name: alpha\n", encoding="utf-8")
        (self.ship / "secret.md").write_text("no", encoding="utf-8")
        patcher = mock.patch("yamato.admiral.all_ships", return_value={"alpha": self.ship})
        patcher.start()
        self.addCleanup(patcher.stop)

    def get(self, name, file):
        status, body = dashboard.api_get(f"/api/v1/ships/{name}/files/{file}")
        return status, json.loads(json.dumps(body))

    def test_role_charter_knowledge_are_served_and_match_the_schema(self):
        for file, kind in (("roles%2Fimpl.md", "role"), ("charter.md", "charter"), ("knowledge.md", "knowledge")):
            status, body = self.get("alpha", file)
            self.assertEqual(status, 200, file)
            self.assertEqual(body["kind"], kind)
            api_schema.assert_valid(self, body, FILES_PATH)
        self.assertEqual(self.get("alpha", "roles%2Fimpl.md")[1]["text"], "# impl\n日本語\n")

    def test_everything_else_is_404_json(self):
        outside = self.root / "outside.md"
        outside.write_text("no", encoding="utf-8")
        (self.ship / "roles" / "link.md").symlink_to(outside)
        for file in ("team.yaml", "secret.md", "roles%2Fnotes.txt", "roles%2Flink.md", "roles%2Fnone.md",
                     "roles", "roles%2F..%2Fsecret.md", "..%2Foutside.md", "%2e%2e%2Foutside.md",
                     "roles%2F..%2F..%2Foutside.md", "%2Fetc%2Fpasswd", str(outside).replace("/", "%2F"), "..", ""):
            status, body = self.get("alpha", file) if file else dashboard.api_get("/api/v1/ships/alpha/files/")
            self.assertEqual(status, 404, file)
            self.assertEqual(body["error"]["code"], "not_found", file)

    def test_encoded_slash_reaches_the_handler_over_http(self):
        srv = dashboard.make_server(0)
        self.addCleanup(srv.server_close)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.addCleanup(srv.shutdown)
        got = {}
        for file in ("roles%2Fimpl.md", "roles/impl.md"):
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
            c.request("GET", f"/api/v1/ships/alpha/files/{file}")
            got[file] = c.getresponse().status
        self.assertEqual(got, {"roles%2Fimpl.md": 200, "roles/impl.md": 404})

    def test_unknown_ship_is_404(self):
        for name in ("beta", "..", "alpha%2F..%2Falpha"):
            self.assertEqual(self.get(name, "charter.md")[0], 404, name)

    def test_symlinked_roles_dir_is_not_listed(self):
        real = self.root / "real"
        real.mkdir()
        (real / "x.md").write_text("x", encoding="utf-8")
        other = self.root / "beta"
        other.mkdir()
        (other / "roles").symlink_to(real)
        with mock.patch("yamato.admiral.all_ships", return_value={"beta": other}):
            self.assertEqual(self.get("beta", "roles%2Fx.md")[0], 404)

    def test_unreadable_file_is_a_json_error(self):
        with mock.patch("yamato.web.files.Path.read_bytes", side_effect=PermissionError("x")):
            status, body = self.get("alpha", "charter.md")
        self.assertEqual((status, body["error"]["code"]), (500, "unreadable"))


if __name__ == "__main__":
    unittest.main()

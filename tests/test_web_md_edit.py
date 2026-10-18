"""web コンソールの md の編集: roles/*.md・charter.md だけ書ける。knowledge.md・memory.md は読むだけ。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from yamato import dashboard  # noqa: F401  (md_edit を import して RESOLVERS に足す)
from yamato.web import edit


class MdEditTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.ship = self.root / "alpha"
        (self.ship / "roles" / "impl").mkdir(parents=True)
        (self.ship / "roles" / "impl.md").write_text("# impl\n", encoding="utf-8")
        (self.ship / "roles" / "impl" / "memory.md").write_text("m\n", encoding="utf-8")
        (self.ship / "charter.md").write_text("# charter\n", encoding="utf-8")
        (self.ship / "knowledge.md").write_text("k\n", encoding="utf-8")
        (self.ship / "team.yaml").write_text("name: alpha\n", encoding="utf-8")
        p = mock.patch("yamato.admiral.all_ships", return_value={"alpha": self.ship})
        p.start()
        self.addCleanup(p.stop)

    def body(self, file, content="new\n", **kw):
        return {"ship": "alpha", "file": file, "content": content, **kw}

    def test_role_and_charter_preview_then_commit(self):
        for file in ("roles/impl.md", "charter.md"):
            path = self.ship / file
            before = path.read_text(encoding="utf-8")
            pv = edit.handle("preview", self.body(file))
            self.assertTrue(pv["valid"] and pv["changed"])
            self.assertIn("+new", pv["diff"])
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            edit.handle("commit", self.body(file, base_hash=pv["base_hash"]))
            self.assertEqual(path.read_text(encoding="utf-8"), "new\n")
            self.assertEqual((self.ship / ".runtime" / edit.BACKUP_DIR / file.replace("/", "__")).read_text(encoding="utf-8"), before)

    def test_stale_base_hash_is_conflict(self):
        pv = edit.handle("preview", self.body("charter.md"))
        (self.ship / "charter.md").write_text("changed\n", encoding="utf-8")
        with self.assertRaises(edit.EditError) as cm:
            edit.handle("commit", self.body("charter.md", base_hash=pv["base_hash"]))
        self.assertEqual(cm.exception.status, 409)

    def test_empty_is_invalid(self):
        self.assertFalse(edit.handle("preview", self.body("charter.md", " \n"))["valid"])
        with self.assertRaises(edit.EditError) as cm:
            edit.handle("commit", self.body("charter.md", "", base_hash=edit.file_hash(self.ship / "charter.md")))
        self.assertEqual(cm.exception.status, 422)
        self.assertEqual((self.ship / "charter.md").read_text(encoding="utf-8"), "# charter\n")

    def test_read_only_and_other_files_are_404_and_untouched(self):
        outside = self.root / "outside.md"
        outside.write_text("out\n", encoding="utf-8")
        (self.ship / "roles" / "link.md").symlink_to(outside)
        files = ("knowledge.md", "roles/impl/memory.md", "memory.md", "roles/link.md",
                 "roles/none.md", "../outside.md", "roles/../charter.md", str(outside))
        for file in files:
            for op in ("preview", "commit"):
                with self.assertRaises(edit.EditError, msg=(file, op)) as cm:
                    edit.handle(op, self.body(file, base_hash="-"))
                self.assertEqual(cm.exception.status, 404, (file, op))
        self.assertEqual((self.ship / "knowledge.md").read_text(encoding="utf-8"), "k\n")
        self.assertEqual((self.ship / "roles" / "impl" / "memory.md").read_text(encoding="utf-8"), "m\n")
        self.assertEqual(outside.read_text(encoding="utf-8"), "out\n")

    def test_unknown_ship_is_404(self):
        with self.assertRaises(edit.EditError) as cm:
            edit.handle("preview", {"ship": "nope", "file": "charter.md", "content": "x"})
        self.assertEqual(cm.exception.status, 404)


if __name__ == "__main__":
    unittest.main()

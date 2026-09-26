import tempfile
import unittest
from pathlib import Path

from yamato.board import Board, dumps, loads, parse_assignments
from yamato.util import YamatoError

TEAM = {
    "seats": {"pm": {}, "impl-1": {}, "impl-2": {}},
    "board": {"kinds": ["task", "bug"], "columns": [], "fields": ["branch"]},
}


class BoardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ship = Path(self._tmp.name)
        self.b = Board(self.ship, TEAM)

    def tearDown(self):
        self._tmp.cleanup()

    def test_add_assigns_sequential_ids_and_defaults(self):
        a = self.b.add("one")
        b = self.b.add("two", {"assignee": "impl-1", "kind": "bug"})
        self.assertEqual((a["id"], b["id"]), ("T-001", "T-002"))
        self.assertEqual(a["state"], "open")
        self.assertEqual(a["kind"], "task")
        self.assertEqual(b["assignee"], "impl-1")
        self.assertTrue((self.ship / "board/items/T-002.md").is_file())

    def test_validation(self):
        self.b.add("one")
        bad = [
            {"state": "doing"}, {"assignee": "nobody"}, {"kind": ""}, {"id": "T-9"},
            {"parent": "T-404"}, {"blocked_on": "T-404"}, {"blocked_on": "T-001"},
            {"bad key": "x"}, {"title": ""},
        ]
        for fields in bad:
            with self.subTest(fields=fields), self.assertRaises(YamatoError):
                self.b.set("T-001", fields)

    def test_kinds_columns_and_extra_fields_are_free(self):
        self.b.add("one")
        m = self.b.set("T-001", {"kind": "epic", "column": "whatever", "priority": "high"})
        self.assertEqual((m["kind"], m["column"], m["priority"], m["state"]), ("epic", "whatever", "high", "open"))

    def test_set_fields_lists_and_clear(self):
        self.b.add("one")
        self.b.add("two")
        m = self.b.set("T-002", {"state": "blocked", "blocked_on": "T-001", "links": "PR#1, doc",
                                 "branch": "t-002", "assignee": "impl-2"})
        self.assertEqual(m["blocked_on"], ["T-001"])
        self.assertEqual(m["links"], ["PR#1", "doc"])
        self.assertEqual(m["branch"], "t-002")
        m = self.b.set("T-002", {"assignee": "", "blocked_on": ""})
        self.assertIsNone(m["assignee"])
        self.assertEqual(m["blocked_on"], [])

    def test_done_moves_to_archive_and_back(self):
        self.b.add("one", {"assignee": "impl-1"})
        self.b.set("T-001", {"state": "done"})
        self.assertFalse((self.ship / "board/items/T-001.md").exists())
        self.assertTrue((self.ship / "board/archive/T-001.md").exists())
        self.assertEqual(self.b.items(), [])
        self.assertEqual(len(self.b.items(include_archive=True)), 1)
        self.assertEqual(self.b.mine("impl-1"), [])
        self.assertEqual(self.b.add("two")["id"], "T-002")  # ids keep counting past archive
        self.b.set("T-001", {"state": "active"})
        self.assertTrue((self.ship / "board/items/T-001.md").exists())
        self.assertFalse((self.ship / "board/archive/T-001.md").exists())

    def test_archive_on_done_false_keeps_items_until_archived(self):
        team = dict(TEAM, board=dict(TEAM["board"], archive_on_done=False))
        b = Board(self.ship, team)
        b.add("one")
        b.add("two")
        b.set("T-001", {"state": "done"})
        self.assertTrue((self.ship / "board/items/T-001.md").exists())
        self.assertEqual(b.archive(), ["T-001"])
        self.assertTrue((self.ship / "board/archive/T-001.md").exists())
        with self.assertRaises(YamatoError):
            b.archive("T-002")  # not done

    def test_owner_can_be_assigned(self):
        self.b.add("decide", {"assignee": "owner"})

    def test_mine(self):
        self.b.add("a", {"assignee": "impl-1"})
        self.b.add("b", {"assignee": "impl-2"})
        self.b.add("c", {"assignee": "impl-1", "state": "active"})
        self.assertEqual([m["id"] for m in self.b.mine("impl-1")], ["T-001", "T-003"])

    def test_note_appends_and_body_survives_set(self):
        self.b.add("one", body="## 完了条件\n- add()", by="pm")
        self.b.set("T-001", {}, note="着手", by="impl-1")
        _, body, _ = self.b.read("T-001")
        self.assertIn("## 完了条件\n- add()", body)
        self.assertIn("pm: 作成", body)
        self.assertTrue(body.rstrip().endswith("impl-1: 着手"))
        with self.assertRaises(YamatoError):
            self.b.set("T-001", {})

    def test_columns_follow_state(self):
        team = dict(TEAM, board={"kinds": ["task"], "fields": [], "columns": [
            {"name": "backlog", "state": "open"}, {"name": "impl", "state": "active"},
            {"name": "review", "state": "active"}, {"name": "done", "state": "done"}]})
        b = Board(self.ship, team)
        m = b.add("x")
        self.assertEqual((m["column"], m["state"]), ("backlog", "open"))
        m = b.set("T-001", {"column": "review"})
        self.assertEqual(m["state"], "active")
        m = b.set("T-001", {"state": "done"})
        self.assertEqual(m["column"], "done")

    def test_frontmatter_roundtrip_with_awkward_values(self):
        meta = {"id": "T-001", "title": 'a: "b" #c', "parent": None, "links": ["x y", "PR#2"],
                "state": "open", "n": "123", "t": "true"}
        back, body = loads(dumps(meta, "本文\n"))
        self.assertEqual(back, meta)
        self.assertEqual(body, "本文\n")

    def test_parse_assignments(self):
        self.assertEqual(parse_assignments(["a=1", "b=x=y", "c="]), {"a": "1", "b": "x=y", "c": ""})
        with self.assertRaises(YamatoError):
            parse_assignments(["novalue"])


if __name__ == "__main__":
    unittest.main()

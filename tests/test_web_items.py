"""web コンソールの board 項目 1 件 (items API): 艦と id を検査し、読むだけ。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import api_schema
from yamato import dashboard

ITEMS_PATH = "/api/v1/ships/{name}/items/{id}"

DECISION = """---
id: D-001
kind: decision
title: "どちらにするか"
state: open
category: default
decider: owner
urgent: true
assignee: null
blocked_on: []
links: []
---
## 背景
日本語の本文
"""
TASK = """---
id: T-002
kind: task
title: 作業
state: done
assignee: impl
blocked_on: ["D-001"]
links: []
---
本文
"""


class ShipItemsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.ship = self.root / "alpha"
        (self.ship / "board" / "items").mkdir(parents=True)
        (self.ship / "board" / "archive").mkdir(parents=True)
        (self.ship / "board" / "items" / "D-001.md").write_text(DECISION, encoding="utf-8")
        (self.ship / "board" / "archive" / "T-002.md").write_text(TASK, encoding="utf-8")
        (self.root / "outside.md").write_text(DECISION, encoding="utf-8")
        patcher = mock.patch("yamato.admiral.all_ships", return_value={"alpha": self.ship})
        patcher.start()
        self.addCleanup(patcher.stop)

    def get(self, name, item_id):
        status, body = dashboard.api_get(f"/api/v1/ships/{name}/items/{item_id}")
        return status, json.loads(json.dumps(body))

    def test_decision_and_archived_task_have_the_same_shape(self):
        status, d = self.get("alpha", "D-001")
        self.assertEqual(status, 200)
        api_schema.assert_valid(self, d, ITEMS_PATH)
        self.assertEqual((d["kind"], d["category"], d["urgent"]), ("decision", "default", True))
        self.assertEqual(d["body"], "## 背景\n日本語の本文\n")
        status, t = self.get("alpha", "T-002")
        self.assertEqual(status, 200)
        api_schema.assert_valid(self, t, ITEMS_PATH)
        self.assertEqual((t["category"], t["urgent"], t["blocked_on"]), (None, None, ["D-001"]))
        self.assertEqual(set(d), set(t))

    def test_missing_ship_item_and_bad_ids_are_404(self):
        (self.ship / "board" / "items" / "link.md").symlink_to(self.root / "outside.md")
        (self.ship / "board" / "items" / "D-009.md").symlink_to(self.root / "outside.md")
        for name, item_id in (("beta", "D-001"), ("..", "D-001"), ("alpha", "D-777"), ("alpha", "D-009"),
                              ("alpha", "d-001"), ("alpha", "DD-001"), ("alpha", "D-"), ("alpha", "D-1x"),
                              ("alpha", "link"), ("alpha", ".."), ("alpha", "..%2Foutside"),
                              ("alpha", "D-001%2F..%2F..%2F..%2Foutside"), ("alpha", "%2Fetc%2Fpasswd"),
                              ("alpha", "D-001%0A"), ("alpha", "")):
            status, body = self.get(name, item_id) if item_id else dashboard.api_get("/api/v1/ships/alpha/items/")
            self.assertEqual(status, 404, (name, item_id))
            self.assertEqual(body["error"]["code"], "not_found", (name, item_id))


if __name__ == "__main__":
    unittest.main()

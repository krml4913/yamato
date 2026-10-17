"""team.yaml のフォーム編集: ops (yaml の部分置き換え) を preview → commit で書く。"""
import json
import unittest

from tests import api_schema
from tests.helpers import ShipTestCase
from yamato import dashboard, shipconfig, team
from yamato.web import edit


class TeamEditTest(ShipTestCase):
    def setUp(self):
        super().setUp()
        self.path = self.shipdir / "team.yaml"

    def body(self, ops, **extra):
        return {"ship": "t1", "file": "team.yaml", "ops": ops, **extra}

    def preview(self, ops):
        return edit.handle("preview", self.body(ops))

    def commit(self, ops, base):
        return edit.handle("commit", self.body(ops, base_hash=base))

    def test_set_value_keeps_everything_else_byte_for_byte(self):
        before = self.path.read_text(encoding="utf-8")
        p = self.preview([{"op": "set_value", "path": ["roles", "impl", "model"], "value": "opus"}])
        self.assertTrue(p["valid"], p["errors"])
        self.assertTrue(p["changed"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)   # preview は書かない
        self.commit([{"op": "set_value", "path": ["roles", "impl", "model"], "value": "opus"}], p["base_hash"])
        after = self.path.read_text(encoding="utf-8")
        self.assertEqual(self.team()["roles"]["impl"]["model"], "opus")
        self.assertEqual(len(after.splitlines()), len(before.splitlines()))
        changed = [a for a, b in zip(after.splitlines(), before.splitlines()) if a != b]
        self.assertEqual(len(changed), 1)
        self.assertTrue((self.shipdir / ".runtime" / "edit-backup" / "team.yaml").is_file())

    def test_add_and_remove_key_and_list_items(self):
        ops = [{"op": "add_key", "path": [], "key": "up_seats", "value": ["impl"]}]
        p = self.preview(ops)
        self.assertTrue(p["valid"], p["errors"])
        self.commit(ops, p["base_hash"])
        self.assertEqual(self.team()["up_seats"], ["impl"])
        ops = [{"op": "insert_item", "path": ["up_seats"], "value": "reviewer"},
               {"op": "remove_key", "path": ["grace"]}]
        p = self.preview(ops)
        self.commit(ops, p["base_hash"])
        self.assertEqual(self.team()["up_seats"], ["impl", "reviewer"])
        self.assertNotIn("grace", json.loads(json.dumps(__import__("yaml").safe_load(self.path.read_text(encoding="utf-8")))))

    def test_validation_failure_is_reported_and_not_written(self):
        ops = [{"op": "set_value", "path": ["roles", "impl", "count"], "value": 0}]
        p = self.preview(ops)
        self.assertFalse(p["valid"])
        self.assertTrue(p["errors"])
        before = self.path.read_text(encoding="utf-8")
        with self.assertRaises(edit.EditError) as cm:
            self.commit(ops, p["base_hash"])
        self.assertEqual((cm.exception.status, cm.exception.code), (422, "invalid"))
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_conflict_when_the_file_changed_after_preview(self):
        ops = [{"op": "set_value", "path": ["roles", "impl", "model"], "value": "opus"}]
        p = self.preview(ops)
        self.path.write_text(self.path.read_text(encoding="utf-8") + "\n# 別の人の変更\n", encoding="utf-8")
        with self.assertRaises(edit.EditError) as cm:
            self.commit(ops, p["base_hash"])
        self.assertEqual(cm.exception.status, 409)

    def test_ops_apply_to_the_text_that_was_hashed(self):
        # commit は今の中身に ops を当て直す: 別の人の変更 (ハッシュが合う前提では起きない) を巻き込まない
        p = self.preview([{"op": "set_value", "path": ["hub"], "value": "impl"}])
        self.commit([{"op": "set_value", "path": ["hub"], "value": "impl"}], p["base_hash"])
        self.assertEqual(self.team()["hub"], "impl")

    def test_unpatchable_shape_is_422_with_guidance(self):
        self.path.write_text("base: &a {model: opus}\nbase2: *a\n" + self.path.read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaises(edit.EditError) as cm:
            self.preview([{"op": "set_value", "path": ["hub"], "value": "impl"}])
        self.assertEqual((cm.exception.status, cm.exception.code), (422, "unpatchable"))
        self.assertIn("生の yaml", cm.exception.message)

    def test_bad_ops_are_400(self):
        for ops in ([{"op": "nope", "path": []}], [{"op": "set_value", "path": [True]}], ["x"],
                    [{"op": "add_key", "path": []}], [{"op": "insert_item", "path": ["up_seats"], "index": "1"}]):
            with self.assertRaises(edit.EditError) as cm:
                self.preview(ops)
            self.assertEqual(cm.exception.status, 400, ops)

    def test_exactly_one_of_content_and_ops(self):
        for extra in ({}, {"content": "x: 1\n"}):
            body = {"ship": "t1", "file": "team.yaml", **extra}
            if extra:
                body["ops"] = []
            with self.assertRaises(edit.EditError) as cm:
                edit.handle("preview", body)
            self.assertEqual(cm.exception.status, 400)

    def test_full_content_is_validated_like_team_validate(self):
        p = edit.handle("preview", {"ship": "t1", "file": "team.yaml", "content": "name: [unclosed\n"})
        self.assertFalse(p["valid"])
        p = edit.handle("preview", {"ship": "t1", "file": "team.yaml", "content": "- a\n"})
        self.assertFalse(p["valid"])
        p = edit.handle("preview", {"ship": "t1", "file": "team.yaml", "content": self.path.read_text(encoding="utf-8")})
        self.assertTrue(p["valid"], p["errors"])
        self.assertFalse(p["changed"])

    def raw(self, content):
        return edit.handle("preview", {"ship": "t1", "file": "team.yaml", "content": content})

    def test_raw_yaml_syntax_error_has_line_number(self):
        p = self.raw("name: t1\nroles:\n  impl: [a, b\nhub: x\n")
        self.assertFalse(p["valid"])
        self.assertRegex(p["errors"][0], r"^行 \d+: yaml を読めない")

    def test_raw_yaml_validation_error_points_at_the_line(self):
        text = self.path.read_text(encoding="utf-8")
        lines = text.splitlines(True)
        bad = "".join(lines) + "bogus_key: 1\n"
        p = self.raw(bad)
        self.assertFalse(p["valid"])
        self.assertTrue(p["errors"][0].startswith(f"行 {len(lines) + 1}: "), p["errors"])
        p = self.raw(text.replace("hub: impl", "hub: nobody") if "hub: impl" in text else text + "hub: nobody\n")
        self.assertFalse(p["valid"])
        self.assertRegex(p["errors"][0], r"^行 \d+: .*hub")

    def test_raw_yaml_commit_conflict_and_write(self):
        text = self.path.read_text(encoding="utf-8")
        p = self.raw(text + "\n# メモ\n")
        self.assertTrue(p["valid"], p["errors"])
        self.path.write_text(text + "# 別の変更\n", encoding="utf-8")
        with self.assertRaises(edit.EditError) as cm:
            edit.handle("commit", {"ship": "t1", "file": "team.yaml", "content": text + "\n# メモ\n",
                                   "base_hash": p["base_hash"]})
        self.assertEqual(cm.exception.status, 409)

    def test_only_registered_ships_and_team_yaml(self):
        for ship, file in (("nope", "team.yaml"), ("t1", "knowledge.md"), ("t1", "../team.yaml"), ("_admiral", "team.yaml")):
            with self.assertRaises(edit.EditError) as cm:
                edit.handle("preview", {"ship": ship, "file": file, "content": "x"})
            self.assertEqual(cm.exception.status, 404, (ship, file))

    def test_api_post_responses_match_openapi(self):
        status, body = dashboard.api_post("/api/v1/edit/preview", self.body(
            [{"op": "set_value", "path": ["hub"], "value": "impl"}]))
        self.assertEqual(status, 200)
        api_schema.assert_valid(self, body, "/api/v1/edit/preview", method="post")
        status, body = dashboard.api_post("/api/v1/edit/preview", self.body([{"op": "add_key", "path": ["hub"], "key": "k"}]))
        self.assertEqual((status, body["error"]["code"]), (422, "unpatchable"))

    def test_config_fields_carry_their_schema(self):
        cfg = json.loads(json.dumps(shipconfig.ship_config("t1", self.shipdir)))
        roles = next(f for f in cfg["fields"] if f["key"] == "roles")
        self.assertIn("additionalProperties", roles["schema"])
        self.assertEqual(roles["schema"]["type"], "object")
        self.assertEqual(next(f for f in cfg["fields"] if f["key"] == "hub")["schema"]["type"], "string")
        api_schema.assert_valid(self, cfg, "/api/v1/ships/{name}/config")


if __name__ == "__main__":
    unittest.main()

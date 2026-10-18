"""yamlpatch: コメントと並びを保った部分置き換え。"""
from __future__ import annotations

import difflib
import unittest

import yaml

from yamato import yamlpatch as yp
from yamato.yamlpatch import YamlPatchError

TEAM = """\
# yaml-language-server: $schema=file:///x/team.schema.json
# 艦の定義
name: demo
template: {name: dev, version: 1.3.0}   # 写しの元

hub: pm                      # captain
workspace: "/tmp/ws"   # 作業対象

# 最終受付
last_call:
  at_most: 30m
  ratio: 20%

roles:
  pm:
    model: opus
    description: captain の席   # 説明
    inject: [handoff, mine]   # 注入
    rotate:
      context: 30%
      hours: 8h
  impl:
    model: sonnet
    note: |
      一行目
      二行目
    deny:
      - "Bash(rm *)"   # 消さない
      - Write
      # 区切りのコメント
      - Edit
    tail: last   # 最後
deny: []
"""


def changed_lines(a: str, b: str) -> list[str]:
    return [l for l in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0)
            if l[:1] in "+-" and l[:3] not in ("+++", "---")]


class SetValueTest(unittest.TestCase):
    def test_noop_is_byte_identical(self):
        # 値を今と同じに変えても差分が出ない
        for path, v in [(("hub",), "pm"), (("roles", "pm", "model"), "opus"),
                        (("last_call", "ratio"), "20%")]:
            self.assertEqual(yp.set_value(TEAM, path, v), TEAM)

    def test_plain_changes_only_that_line(self):
        out = yp.set_value(TEAM, ("roles", "pm", "model"), "sonnet")
        self.assertEqual(changed_lines(TEAM, out), ["-    model: opus", "+    model: sonnet"])

    def test_keeps_trailing_comment_and_quote_style(self):
        out = yp.set_value(TEAM, ("workspace",), "/tmp/other")
        self.assertIn('workspace: "/tmp/other"   # 作業対象', out)
        out = yp.set_value(TEAM, ("hub",), "captain")
        self.assertIn("hub: captain                      # captain", out)

    def test_value_types(self):
        out = yp.set_value(TEAM, ("hub",), 3)
        self.assertEqual(yaml.safe_load(out)["hub"], 3)
        out = yp.set_value(TEAM, ("hub",), True)
        self.assertIs(yaml.safe_load(out)["hub"], True)
        out = yp.set_value(TEAM, ("hub",), None)
        self.assertIsNone(yaml.safe_load(out)["hub"])

    def test_strings_that_need_quotes(self):
        for s in ("yes", "123", "a: b", "# x", "", " pad", "x #y", "日本語 の文", "it's"):
            out = yp.set_value(TEAM, ("hub",), s)
            self.assertEqual(yaml.safe_load(out)["hub"], s, s)
            self.assertEqual(len(changed_lines(TEAM, out)), 2, s)

    def test_flow_map_value(self):
        out = yp.set_value(TEAM, ("template", "version"), "1.4.0")
        self.assertIn("template: {name: dev, version: 1.4.0}   # 写しの元", out)
        out = yp.set_value(TEAM, ("template", "name"), "a,b")   # flow 内の , は引用符
        self.assertEqual(yaml.safe_load(out)["template"]["name"], "a,b")
        self.assertEqual(len(changed_lines(TEAM, out)), 2)

    def test_flow_list_item(self):
        out = yp.set_value(TEAM, ("roles", "pm", "inject", 1), "log_tail")
        self.assertIn("inject: [handoff, log_tail]   # 注入", out)

    def test_multiline_block_scalar_stays_block(self):
        out = yp.set_value(TEAM, ("roles", "impl", "note"), "新しい\n三行\n")
        self.assertIn("    note: |\n      新しい\n      三行\n    deny:", out)
        self.assertEqual(yaml.safe_load(out)["roles"]["impl"]["note"], "新しい\n三行\n")
        self.assertIn("tail: last   # 最後", out)

    def test_block_scalar_to_single_line_and_back(self):
        out = yp.set_value(TEAM, ("roles", "impl", "note"), "短い")
        self.assertIn("    note: 短い\n    deny:", out)
        self.assertEqual(yaml.safe_load(out)["roles"]["impl"]["note"], "短い")

    def test_multiline_into_plain_uses_quoted(self):
        out = yp.set_value(TEAM, ("hub",), "a\nb")
        self.assertEqual(yaml.safe_load(out)["hub"], "a\nb")
        self.assertEqual(len(changed_lines(TEAM, out)), 2)
        self.assertIn("# captain", out)

    def test_only_block_scalar_at_end_of_file(self):
        t = "a: 1\nnote: |\n  x\n  y"
        out = yp.set_value(t, ("note",), "p\nq")
        self.assertEqual(yaml.safe_load(out)["note"], "p\nq")

    def test_empty_value(self):
        out = yp.set_value("a:   # c\nb: 1\n", ("a",), "x")
        self.assertEqual(out, "a: x   # c\nb: 1\n")

    def test_errors(self):
        for path in [("roles", "pm"), ("roles", "pm", "inject"), ("nope",), ("roles", "pm", "model", "x"), ()]:
            with self.assertRaises(YamlPatchError, msg=path):
                yp.set_value(TEAM, path, "x")
        with self.assertRaises(YamlPatchError):
            yp.set_value(TEAM, ("hub",), {"a": 1})
        with self.assertRaises(YamlPatchError):
            yp.set_value(TEAM, ("hub",), float("inf"))


class AddRemoveKeyTest(unittest.TestCase):
    def test_add_key_block_appends_after_last_line(self):
        out = yp.add_key(TEAM, ("last_call",), "extra", "v")
        self.assertEqual(changed_lines(TEAM, out), ["+  extra: v"])
        self.assertEqual(yaml.safe_load(out)["last_call"]["extra"], "v")

    def test_add_key_after_nested_value_and_comment_line(self):
        out = yp.add_key(TEAM, ("roles", "pm"), "trust", "clean")
        self.assertEqual(changed_lines(TEAM, out), ["+    trust: clean"])
        # rotate (入れ子) の直後に入る
        self.assertLess(out.index("hours: 8h"), out.index("trust: clean"))
        self.assertLess(out.index("trust: clean"), out.index("  impl:"))

    def test_add_key_after_block_scalar(self):
        out = yp.add_key(TEAM, ("roles", "impl"), "z", 1)
        self.assertEqual(yaml.safe_load(out)["roles"]["impl"]["z"], 1)
        self.assertEqual(len(changed_lines(TEAM, out)), 1)

    def test_add_key_nested_value(self):
        out = yp.add_key(TEAM, ("roles",), "qa", {"model": "haiku", "inject": ["a", "b"],
                                                  "text": "x\ny\n", "empty": {}})
        self.assertEqual(yaml.safe_load(out)["roles"]["qa"],
                         {"model": "haiku", "inject": ["a", "b"], "text": "x\ny\n", "empty": {}})
        self.assertEqual([l for l in changed_lines(TEAM, out) if l.startswith("-")], [])

    def test_add_key_at_top_level_and_eof_without_newline(self):
        out = yp.add_key("a: 1", (), "b", 2)
        self.assertEqual(out, "a: 1\nb: 2\n")
        out = yp.add_key(TEAM, (), "zz", [1, 2])
        self.assertTrue(out.startswith(TEAM))
        self.assertEqual(yaml.safe_load(out)["zz"], [1, 2])

    def test_add_key_flow(self):
        out = yp.add_key(TEAM, ("template",), "x", "y")
        self.assertIn("template: {name: dev, version: 1.3.0, x: y}   # 写しの元", out)
        out = yp.add_key("a: {}\n", ("a",), "k", [1])
        self.assertEqual(out, "a: {k: [1]}\n")

    def test_add_key_into_seq_item_map(self):
        t = "xs:\n  - a: 1\n    b: 2\n  - a: 3\n"
        out = yp.add_key(t, ("xs", 0), "c", 9)
        self.assertEqual(out, "xs:\n  - a: 1\n    b: 2\n    c: 9\n  - a: 3\n")

    def test_add_key_errors(self):
        with self.assertRaises(YamlPatchError):
            yp.add_key(TEAM, ("hub",), "k", 1)           # scalar の下
        with self.assertRaises(YamlPatchError):
            yp.add_key(TEAM, ("last_call",), "ratio", 1)  # 既にある

    def test_remove_key_block_removes_only_its_lines(self):
        out = yp.remove_key(TEAM, ("roles", "pm", "rotate"))
        self.assertEqual(changed_lines(TEAM, out),
                         ["-    rotate:", "-      context: 30%", "-      hours: 8h"])
        out = yp.remove_key(TEAM, ("hub",))
        self.assertEqual(changed_lines(TEAM, out), ["-hub: pm                      # captain"])

    def test_remove_key_with_block_scalar(self):
        out = yp.remove_key(TEAM, ("roles", "impl", "note"))
        self.assertEqual(changed_lines(TEAM, out), ["-    note: |", "-      一行目", "-      二行目"])

    def test_remove_key_leaves_preceding_comment(self):
        out = yp.remove_key(TEAM, ("last_call",))
        self.assertIn("# 最終受付\n", out)
        self.assertNotIn("at_most", out)

    def test_remove_key_flow(self):
        out = yp.remove_key(TEAM, ("template", "name"))
        self.assertIn("template: {version: 1.3.0}   # 写しの元", out)
        out = yp.remove_key("a: {x: 1, y: 2, z: 3}\n", ("a", "z"))
        self.assertEqual(out, "a: {x: 1, y: 2}\n")

    def test_remove_key_errors(self):
        for path in [("nope",), ("hub", "x"), ("template", "name", "x")]:
            with self.assertRaises(YamlPatchError, msg=path):
                yp.remove_key(TEAM, path)
        with self.assertRaises(YamlPatchError):
            yp.remove_key("a:\n  b: 1\n", ("a", "b"))     # 最後の 1 キー
        with self.assertRaises(YamlPatchError):
            yp.remove_key("xs:\n  - a: 1\n    b: 2\n", ("xs", 0, "a"))   # `- a:` の行の途中


class ListTest(unittest.TestCase):
    def test_append_block_item(self):
        out = yp.insert_item(TEAM, ("roles", "impl", "deny"), "Read")
        self.assertEqual(changed_lines(TEAM, out), ["+      - Read"])
        self.assertEqual(yaml.safe_load(out)["roles"]["impl"]["deny"][-1], "Read")

    def test_insert_block_item_in_middle_and_front(self):
        out = yp.insert_item(TEAM, ("roles", "impl", "deny"), "New", 2)
        self.assertEqual(yaml.safe_load(out)["roles"]["impl"]["deny"], ["Bash(rm *)", "Write", "New", "Edit"])
        self.assertIn("      - Write\n      - New\n      # 区切りのコメント\n", out)
        out = yp.insert_item(TEAM, ("roles", "impl", "deny"), "First", 0)
        self.assertEqual(yaml.safe_load(out)["roles"]["impl"]["deny"][0], "First")
        self.assertEqual(len(changed_lines(TEAM, out)), 1)

    def test_insert_map_item(self):
        t = "xs:\n  - a: 1\n    b: 2\n"
        out = yp.insert_item(t, ("xs",), {"a": 3, "b": [4]})
        self.assertEqual(out, t + "  - a: 3\n    b:\n      - 4\n")
        self.assertEqual(yaml.safe_load(out)["xs"][1], {"a": 3, "b": [4]})

    def test_remove_block_item(self):
        out = yp.remove_item(TEAM, ("roles", "impl", "deny", 0))
        self.assertEqual(changed_lines(TEAM, out), ['-      - "Bash(rm *)"   # 消さない'])
        out = yp.remove_item(TEAM, ("roles", "impl", "deny", -1))
        self.assertEqual(changed_lines(TEAM, out), ["-      - Edit"])

    def test_remove_map_item(self):
        t = "xs:\n  - a: 1\n    b: 2\n  - a: 3\n"
        self.assertEqual(yp.remove_item(t, ("xs", 0)), "xs:\n  - a: 3\n")

    def test_flow_list(self):
        out = yp.insert_item(TEAM, ("roles", "pm", "inject"), "log_tail")
        self.assertIn("inject: [handoff, mine, log_tail]   # 注入", out)
        out = yp.insert_item(TEAM, ("roles", "pm", "inject"), "first", 0)
        self.assertIn("inject: [first, handoff, mine]", out)
        out = yp.insert_item(TEAM, ("deny",), "x")
        self.assertIn("deny: [x]", out)
        out = yp.remove_item(TEAM, ("roles", "pm", "inject", 1))
        self.assertIn("inject: [handoff]   # 注入", out)
        out = yp.remove_item(TEAM, ("roles", "pm", "inject", 0))
        self.assertIn("inject: [mine]", out)

    def test_errors(self):
        with self.assertRaises(YamlPatchError):
            yp.insert_item(TEAM, ("hub",), "x")
        with self.assertRaises(YamlPatchError):
            yp.insert_item(TEAM, ("roles", "impl", "deny"), "x", 9)
        with self.assertRaises(YamlPatchError):
            yp.remove_item(TEAM, ("roles", "impl", "deny", 9))
        with self.assertRaises(YamlPatchError):
            yp.remove_item(TEAM, ("roles", "impl", "deny", "x"))
        with self.assertRaises(YamlPatchError):
            yp.remove_item("xs: [a]\n", ("xs", 0))   # 最後の 1 要素
        with self.assertRaises(YamlPatchError):
            yp.remove_item("xs:\n  - - a\n    - b\n", ("xs", 0, 0))   # `- - a`


class UnsupportedTest(unittest.TestCase):
    def test_anchor_alias_and_merge(self):
        for t in ("a: &x 1\nb: *x\n", "base: &b {k: 1}\nc:\n  <<: *b\n  z: 2\n"):
            with self.assertRaises(YamlPatchError):
                yp.set_value(t, ("a",) if t.startswith("a:") else ("c", "z"), "n")

    def test_broken_empty_and_multi_document(self):
        for t in ("a: [1\n", "", "# only comment\n", "a: 1\n---\nb: 2\n"):
            with self.assertRaises(YamlPatchError):
                yp.set_value(t, ("a",), 2)


class CrlfTest(unittest.TestCase):
    def test_crlf_insert_uses_crlf(self):
        t = "a:\r\n  b: 1\r\n"
        out = yp.add_key(t, ("a",), "c", 2)
        self.assertEqual(out, "a:\r\n  b: 1\r\n  c: 2\r\n")


if __name__ == "__main__":
    unittest.main()

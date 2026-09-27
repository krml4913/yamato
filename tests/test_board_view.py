"""board tree / board kanban / 注入の部品 board の描画 (design-drift #4 #14, T-030, D-018)."""
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout

from tests.helpers import ShipTestCase
from yamato import board_view, cli
from yamato.board import Board
from yamato.util import YamatoError


def _item(id, state="open", parent=None, assignee=None, title=None, kind="task"):
    return {"id": id, "state": state, "parent": parent, "assignee": assignee,
            "title": title or id, "kind": kind}


class TreeLinesTest(unittest.TestCase):
    def test_nests_by_parent_and_prunes_done_leaves(self):
        items = [
            _item("T-001", state="active"),
            _item("T-002", state="done", parent="T-001"),          # done, no kept children -> pruned
            _item("T-003", state="open", parent="T-001"),
            _item("T-004", state="done"),                          # lone done -> pruned entirely
        ]
        self.assertEqual(board_view.tree_lines(items), [
            "T-001 [active] -: T-001",
            "  T-003 [open] -: T-003  (parent=T-001)",
        ])

    def test_a_done_parent_with_a_kept_descendant_is_shown(self):
        items = [
            _item("T-001", state="active"),
            _item("T-002", state="done", parent="T-001"),
            _item("T-003", state="open", parent="T-002"),          # kept -> T-002 must show too
        ]
        lines = board_view.tree_lines(items)
        self.assertEqual(lines, [
            "T-001 [active] -: T-001",
            "  T-002 [done] -: T-002  (parent=T-001)",
            "    T-003 [open] -: T-003  (parent=T-002)",
        ])

    def test_item_with_a_missing_parent_lands_at_the_top(self):
        items = [_item("T-005", state="open", parent="T-999")]
        self.assertEqual(board_view.tree_lines(items), ["T-005 [open] -: T-005  (parent=T-999)"])

    def test_root_id_shows_only_its_subtree_and_itself_regardless_of_state(self):
        items = [
            _item("T-001", state="active"),
            _item("T-002", state="done", parent="T-001"),
            _item("T-003", state="open", parent="T-002"),
            _item("T-010", state="open"),   # unrelated top-level item
        ]
        lines = board_view.tree_lines(items, "T-002")
        self.assertEqual(lines, [
            "T-002 [done] -: T-002  (parent=T-001)",
            "  T-003 [open] -: T-003  (parent=T-002)",
        ])

    def test_unknown_root_raises(self):
        with self.assertRaises(YamatoError):
            board_view.tree_lines([_item("T-001")], "T-404")

    def test_decisions_are_never_shown(self):
        items = [_item("D-001", kind="decision"), _item("T-001")]
        lines = board_view.tree_lines(items)
        self.assertEqual(lines, ["T-001 [open] -: T-001"])
        with self.assertRaises(YamatoError):
            board_view.tree_lines(items, "D-001")   # not a valid tree root either

    def test_ids_sort_numerically_not_lexically(self):
        items = [_item("T-010"), _item("T-002"), _item("T-001", parent=None)]
        lines = board_view.tree_lines(items)
        self.assertEqual([l.split()[0] for l in lines], ["T-001", "T-002", "T-010"])


class KanbanTest(unittest.TestCase):
    def test_groups_by_state_when_no_columns_configured(self):
        items = [_item("T-001", state="open"), _item("T-002", state="active"),
                 _item("T-003", state="blocked"), _item("T-004", state="done")]
        groups = board_view.kanban_groups({}, items)
        self.assertEqual([name for name, _ in groups], ["open", "active", "blocked"])
        self.assertEqual([m["id"] for _, its in groups for m in its], ["T-001", "T-002", "T-003"])

    def test_all_includes_done_as_its_own_group(self):
        items = [_item("T-001", state="open"), _item("T-004", state="done")]
        groups = board_view.kanban_groups({}, items, include_done=True)
        self.assertEqual([name for name, _ in groups], ["open", "active", "blocked", "done"])
        self.assertEqual([m["id"] for m in dict(groups)["done"]], ["T-004"])

    def test_groups_by_configured_columns_in_order(self):
        team = {"board": {"columns": [{"name": "backlog", "state": "open"},
                                       {"name": "doing", "state": "active"},
                                       {"name": "review", "state": "active"}]}}
        items = [_item("T-001", state="open"), _item("T-002", state="active")]
        items[0]["column"] = "backlog"
        items[1]["column"] = "review"   # a second column also mapped to "active"
        groups = board_view.kanban_groups(team, items)
        self.assertEqual([name for name, _ in groups], ["backlog", "doing", "review"])
        self.assertEqual([m["id"] for m in dict(groups)["review"]], ["T-002"])
        self.assertEqual(dict(groups)["doing"], [])

    def test_items_within_a_group_sort_by_id(self):
        items = [_item("T-010", state="open"), _item("T-002", state="open")]
        groups = dict(board_view.kanban_groups({}, items))
        self.assertEqual([m["id"] for m in groups["open"]], ["T-002", "T-010"])

    def test_lines_include_headings_with_counts(self):
        items = [_item("T-001", state="open")]
        lines = board_view.kanban_lines({}, items)
        self.assertIn("## open (1)", lines)
        self.assertIn("## blocked (0)", lines)
        self.assertIn("(なし)", lines)

    def test_decisions_are_excluded(self):
        items = [_item("D-001", state="open", kind="decision")]
        groups = dict(board_view.kanban_groups({}, items))
        self.assertEqual(groups["open"], [])


class InjectLinesTest(unittest.TestCase):
    def test_header_line_counts_by_state_and_excludes_done(self):
        items = [_item("T-001", state="open"), _item("T-002", state="active"),
                 _item("T-003", state="blocked"), _item("T-004", state="done")]
        lines = board_view.inject_lines(items, 20, "yamato board kanban s")
        self.assertEqual(lines[0], "blocked 1 / active 1 / open 1")
        self.assertNotIn("T-004", "\n".join(lines))

    def test_order_is_blocked_then_active_then_open_ids_ascending(self):
        items = [_item("T-001", state="open"), _item("T-005", state="blocked"),
                 _item("T-002", state="blocked"), _item("T-003", state="active")]
        lines = board_view.inject_lines(items, 20, "yamato board kanban s")
        ids = [l.split()[0] for l in lines[1:]]
        self.assertEqual(ids, ["T-002", "T-005", "T-003", "T-001"])

    def test_over_the_limit_is_summarised_with_the_kanban_hint(self):
        items = [_item(f"T-{i:03d}", state="open") for i in range(1, 4)]
        lines = board_view.inject_lines(items, 2, "yamato board kanban /s")
        self.assertEqual(lines[-1], "…ほか 1 件 (`yamato board kanban /s`)")

    def test_decisions_never_count_or_show(self):
        items = [_item("D-001", state="open", kind="decision")]
        lines = board_view.inject_lines(items, 20, "yamato board kanban s")
        self.assertEqual(lines, ["blocked 0 / active 0 / open 0"])


class BoardTreeKanbanCliTest(ShipTestCase):
    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_tree_cli_shows_the_whole_board(self):
        b = Board(self.shipdir, self.team())
        b.add("root")
        b.add("child", {"parent": "T-001"})
        rc, out, _ = self.cli("board", "tree", str(self.shipdir))
        self.assertEqual(rc, 0)
        self.assertIn("T-001 [open] -: root", out)
        self.assertIn("  T-002 [open] -: child  (parent=T-001)", out)

    def test_tree_cli_with_an_id_scopes_to_the_subtree(self):
        b = Board(self.shipdir, self.team())
        b.add("root")
        b.add("child", {"parent": "T-001"})
        b.add("unrelated")
        rc, out, _ = self.cli("board", "tree", str(self.shipdir), "T-001")
        self.assertEqual(rc, 0)
        self.assertNotIn("unrelated", out)
        self.assertIn("child", out)

    def test_tree_cli_hints_all_for_an_archived_id(self):
        b = Board(self.shipdir, self.team())
        b.add("done one")
        b.set("T-001", {"state": "done"})
        rc, _, err = self.cli("board", "tree", str(self.shipdir), "T-001")
        self.assertEqual(rc, 1)
        self.assertIn("--all", err)

    def test_kanban_cli_lists_by_state_and_counts(self):
        b = Board(self.shipdir, self.team())
        b.add("a", {"state": "open"})
        b.add("b", {"state": "active"})
        rc, out, _ = self.cli("board", "kanban", str(self.shipdir))
        self.assertEqual(rc, 0)
        self.assertIn("## open (1)", out)
        self.assertIn("## active (1)", out)

    def test_kanban_cli_all_shows_done(self):
        b = Board(self.shipdir, self.team())
        b.add("done one")
        b.set("T-001", {"state": "done"})
        rc, out, _ = self.cli("board", "kanban", str(self.shipdir))
        self.assertNotIn("## done", out)
        rc, out, _ = self.cli("board", "kanban", str(self.shipdir), "--all")
        self.assertEqual(rc, 0)
        self.assertIn("## done (1)", out)


if __name__ == "__main__":
    unittest.main()

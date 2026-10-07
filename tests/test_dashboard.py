"""yamato dashboard (T-079): HTTP なしの描画関数で確かめる。実時間では待たない。"""
import http.client
import json
import os
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import ShipTestCase
from yamato import board, claude, cli, dashboard, events, feed, inbox, roster, seat
from yamato.util import YamatoError


def _tree_state(root: Path) -> dict:
    return {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(root.rglob("*"))}


class DashboardTest(ShipTestCase):
    def render(self, by=None, ships=None):
        return dashboard.render(ships if ships is not None else {"t1": self.shipdir}, by or {}, time.time())

    def put_item(self, name, text):
        (self.shipdir / "board" / "items" / f"{name}.md").write_text(text, encoding="utf-8")

    def test_empty_ship_renders_with_no_waiting(self):
        page = self.render()
        self.assertIn("元帥待ちは無い", page)
        self.assertIn("yamato dashboard", page)
        self.assertIn('http-equiv="refresh" content="60"', page)
        self.assertIn("最終更新", page)

    def test_waiting_human_decision_urgent_first_with_recommendation(self):
        self.put_item("D-1", "---\nid: D-1\ntitle: 認証\nkind: decision\nstate: open\ndecider: owner\n---\n"
                             "## 選択肢と推し\n- 推しは A\n")
        self.put_item("D-2", "---\nid: D-2\ntitle: 急ぎ\nkind: decision\nstate: open\ndecider: owner\nurgent: true\n---\n")
        self.put_item("D-3", "---\nid: D-3\ntitle: 内部\nkind: decision\nstate: open\ndecider: pm\n---\n")
        page = self.render()
        self.assertLess(page.index("D-2"), page.index("D-1"))
        self.assertIn("急ぎ", page)
        self.assertIn("推しは A", page)
        self.assertNotIn("D-3", page)   # decider が席なら元帥待ちではない

    def test_waiting_owner_unread_and_waiting_seat(self):
        inbox.append(self.shipdir, "owner", "pm", "確認を頼む <b>")
        rec = roster.seat(self.shipdir, "impl")
        roster.update(self.shipdir, "impl", sessionId="S1", pid=os.getpid())
        by = {"S1": {"sessionId": "S1", "pid": os.getpid(), "status": "waiting", "waitingFor": "permission prompt"}}
        page = self.render(by)
        self.assertIn("owner 宛て未読", page)
        self.assertIn("確認を頼む &lt;b&gt;", page)    # escape される
        self.assertNotIn("<b> ", page.split("元帥待ち")[1].split("</section>")[0].replace("<b>t1</b>", ""))
        self.assertIn("席 impl が待っている: permission prompt", page)

    def test_seat_table_shows_active_unread_and_log_line(self):
        board.Board(self.shipdir, self.team()).add("T-001", {"assignee": "impl", "state": "active"}, by="pm")
        inbox.append(self.shipdir, "impl", "pm", "go")
        from yamato.util import append_log

        append_log(self.shipdir, "impl", "テストを書いた <x>")
        page = self.render()
        self.assertIn("T-001", page)
        self.assertIn("テストを書いた &lt;x&gt;", page)
        self.assertNotIn("<x>", page)

    def test_anomaly_kinds_are_shared_with_feed(self):
        events.emit(self.shipdir, events.FORCE_STOP, seat="impl", summary="強制停止した")
        events.emit(self.shipdir, events.SEND, seat="pm", summary="ふつうの送信")
        page = self.render()
        self.assertIn("強制停止した", page)
        self.assertNotIn("ふつうの送信", page)
        self.assertIs(dashboard.feed.ABNORMAL_KINDS, feed.ABNORMAL_KINDS)

    def test_usage_per_seat_today(self):
        with open(self.shipdir / "usage.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "seat": "impl", "total_tokens": 12000}) + "\n")
            f.write(json.dumps({"ts": time.time() - 3 * 86400, "seat": "impl", "total_tokens": 99000}) + "\n")
        self.assertIn("12k", self.render())
        self.assertNotIn("99k", self.render())

    def test_prs_listed_with_link(self):
        board.Board(self.shipdir, self.team()).add("T-001", {"assignee": "impl", "state": "active", "pr": "12"}, by="pm")
        with mock.patch.object(dashboard, "_repo_url", return_value="https://github.com/o/r"):
            page = self.render()
        self.assertIn('href="https://github.com/o/r/pull/12"', page)

    def test_read_does_not_touch_the_ship_folder(self):
        board.Board(self.shipdir, self.team()).add("T-001", {"assignee": "impl", "state": "active"}, by="pm")
        events.emit(self.shipdir, events.FORCE_STOP, seat="impl", summary="x")
        inbox.append(self.shipdir, "owner", "pm", "hi")
        before = _tree_state(self.shipdir)
        with mock.patch.object(seat, "status", side_effect=AssertionError("seat.status を呼んだ")):
            self.render()
        self.assertEqual(_tree_state(self.shipdir), before)

    def test_a_broken_ship_does_not_stop_the_others(self):
        broken = self.tmp / "broken"
        broken.mkdir()
        (broken / "team.yaml").write_text("{{{ not yaml", encoding="utf-8")
        page = self.render(ships={"bad": broken, "t1": self.shipdir})
        self.assertIn("読めない", page)
        self.assertIn("<h2>t1</h2>", page)

    def test_render_page_calls_agents_once(self):
        with mock.patch.object(claude, "agents", return_value=[]) as ag, \
                mock.patch("yamato.admiral.all_ships", return_value={"t1": self.shipdir}):
            dashboard.render_page()
        self.assertEqual(ag.call_count, 1)

    def test_agents_failure_still_renders(self):
        with mock.patch.object(claude, "agents", side_effect=YamatoError("boom")), \
                mock.patch("yamato.admiral.all_ships", return_value={"t1": self.shipdir}):
            self.assertIn("生存は不明", dashboard.render_page())


class ServerTest(unittest.TestCase):
    def test_binds_loopback_only_and_get_only(self):
        srv = dashboard.make_server(0)
        self.addCleanup(srv.server_close)
        self.assertEqual(srv.server_address[0], "127.0.0.1")
        t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        t.start()
        self.addCleanup(srv.shutdown)
        port = srv.server_address[1]
        with mock.patch.object(dashboard, "render_page", return_value="<p>ok</p>"):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request("GET", "/")
            r = c.getresponse()
            self.assertEqual((r.status, r.read()), (200, b"<p>ok</p>"))
            for method in ("POST", "PUT", "DELETE"):
                c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                c.request(method, "/")
                self.assertEqual(c.getresponse().status, 501)
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request("GET", "/x")
            self.assertEqual(c.getresponse().status, 404)

    def test_default_port_falls_through_to_the_next_and_explicit_port_errors(self):
        first = dashboard.make_server(0)
        self.addCleanup(first.server_close)
        busy = first.server_address[1]
        with mock.patch.object(dashboard, "DEFAULT_PORT", busy):
            second = dashboard.make_server(None)
            self.addCleanup(second.server_close)
            self.assertNotEqual(second.server_address[1], busy)
        with self.assertRaises(YamatoError):
            dashboard.make_server(busy)

    def test_no_bind_address_option(self):
        import argparse

        p = argparse.ArgumentParser()
        dashboard.register(p.add_subparsers())
        flags = {a for act in p._subparsers._group_actions[0].choices["dashboard"]._actions for a in act.option_strings}
        self.assertEqual(flags - {"-h", "--help"}, {"--port", "--no-open"})


if __name__ == "__main__":
    unittest.main()

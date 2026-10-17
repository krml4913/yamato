"""yamato dashboard (T-079): HTTP なしの描画関数で確かめる。実時間では待たない。"""
import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests import api_schema
from tests.helpers import ShipTestCase
from yamato import board, claude, cli, dashboard, events, feed, inbox, report, roster, seat
from yamato.util import YamatoError


def _tree_state(root: Path) -> dict:
    return {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(root.rglob("*"))}


class DashboardTest(ShipTestCase):
    def overview(self, by=None, ships=None, check=True):
        body = dashboard.overview(ships if ships is not None else {"t1": self.shipdir}, by or {}, time.time())
        body = json.loads(json.dumps(body))   # 実際の応答と同じく JSON を通す
        if check:
            api_schema.assert_valid(self, body, "/api/v1/overview")
        return body

    def state(self, **kw):
        return self.overview(**kw)["ships"][0]["state"]

    def seat_of(self, name, **kw):
        return next(s for s in self.state(**kw)["seats"] if s["name"] == name)

    def put_item(self, name, text):
        (self.shipdir / "board" / "items" / f"{name}.md").write_text(text, encoding="utf-8")

    def test_empty_ship_has_no_waits_and_matches_the_schema(self):
        body = self.overview()
        self.assertEqual(body["waits"], [])
        self.assertIsNone(body["agents_error"])
        self.assertEqual([x["name"] for x in body["ships"]], ["t1"])
        self.assertIsNone(body["ships"][0]["error"])

    def test_waiting_human_decision_urgent_first_with_recommendation(self):
        self.put_item("D-1", "---\nid: D-1\ntitle: 認証\nkind: decision\nstate: open\ndecider: owner\n---\n"
                             "## 選択肢と推し\n- 推しは A\n")
        self.put_item("D-2", "---\nid: D-2\ntitle: 急ぎ\nkind: decision\nstate: open\ndecider: owner\nurgent: true\n---\n")
        self.put_item("D-3", "---\nid: D-3\ntitle: 内部\nkind: decision\nstate: open\ndecider: pm\n---\n")
        waits = self.overview()["waits"]
        self.assertEqual([w["id"] for w in waits], ["D-2", "D-1"])   # decider が席の D-3 は入らない
        self.assertEqual(waits[1]["recommendation"], "推しは A")
        self.assertIsNone(waits[0]["recommendation"])
        self.assertTrue(waits[0]["urgent"])
        self.assertEqual(waits[0]["ship"], "t1")

    def test_flags_split_red_and_warn(self):
        roster.seat(self.shipdir, "impl")
        roster.update(self.shipdir, "impl", sessionId="S1", pid=os.getpid())
        by = {"S1": {"sessionId": "S1", "pid": os.getpid(), "status": "waiting", "waitingFor": "permission prompt"}}
        impl = self.seat_of("impl", by=by)
        self.assertTrue(impl["alive"])
        self.assertEqual(impl["status"], "waiting")
        self.assertEqual(impl["waiting_for"], "permission prompt")
        flags = {f["level"]: f for f in impl["flags"]}
        self.assertEqual(flags["red"]["text"], "permission prompt 待ちで止まっている → attach して答える")
        self.assertEqual(flags["red"]["detail"], "status: waiting, waitingFor: permission prompt")
        # active の無い per_task の席は warn
        self.assertIn("per_task", flags["warn"]["detail"])

    def test_owner_unread_is_count_only_and_waiting_seat_is_not_a_wait(self):
        inbox.append(self.shipdir, "owner", "pm", "確認を頼む <b>")
        roster.seat(self.shipdir, "impl")
        roster.update(self.shipdir, "impl", sessionId="S1", pid=os.getpid())
        by = {"S1": {"sessionId": "S1", "pid": os.getpid(), "status": "waiting", "waitingFor": "permission prompt"}}
        body = self.overview(by)
        self.assertEqual(body["waits"], [])
        self.assertEqual(body["ships"][0]["state"]["owner_unread"], 1)
        self.assertNotIn("確認を頼む", json.dumps(body, ensure_ascii=False))

    def test_seat_shows_active_unread_and_log_line(self):
        board.Board(self.shipdir, self.team()).add("T-001", {"assignee": "impl", "state": "active"}, by="pm")
        inbox.append(self.shipdir, "impl", "pm", "go")
        from yamato.util import append_log

        append_log(self.shipdir, "impl", "テストを書いた <x>")
        impl = self.seat_of("impl")
        self.assertEqual(impl["active"], ["T-001"])
        self.assertEqual(impl["unread"], 1)
        self.assertTrue(impl["log"].endswith("テストを書いた <x>"))   # エスケープは画面の仕事。API は生の文字列
        self.assertEqual([b["id"] for b in self.state()["board"]], ["T-001"])
        self.assertEqual(self.state()["counts"], {"active": 1})

    def test_reviewing_item_is_listed_for_the_reviewer(self):
        board.Board(self.shipdir, self.team()).add(
            "T-001", {"assignee": "impl", "reviewer": "pm", "state": "active"}, by="pm")
        pm = self.seat_of("pm")
        self.assertEqual(pm["reviewing"], ["T-001"])
        self.assertEqual(pm["active"], [])

    def test_anomaly_kinds_are_shared_with_feed_newest_first(self):
        now = time.time()
        events.emit(self.shipdir, events.FORCE_STOP, seat="impl", summary="古い強制停止", now=now - 20)
        events.emit(self.shipdir, events.FORCE_STOP, seat="impl", summary="強制停止した", now=now - 10)
        events.emit(self.shipdir, events.SEND, seat="pm", summary="ふつうの送信", now=now - 5)
        an = self.state()["anomalies"]
        self.assertEqual([a["summary"] for a in an], ["強制停止した", "古い強制停止"])
        self.assertEqual(an[0]["kind"], events.FORCE_STOP)
        self.assertIs(dashboard.feed.ABNORMAL_KINDS, feed.ABNORMAL_KINDS)

    def test_usage_per_seat_today(self):
        with open(self.shipdir / "usage.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "seat": "impl", "total_tokens": 12000}) + "\n")
            f.write(json.dumps({"ts": time.time() - 3 * 86400, "seat": "impl", "total_tokens": 99000}) + "\n")
        self.assertEqual(self.seat_of("impl")["tokens"], 12000)
        self.assertEqual(self.state()["tokens"], 12000)

    def _budget_seat(self, shift, rows, budget=2):
        team = self.team()
        team["seats"]["impl"]["shift"] = shift
        team["roles"][team["seats"]["impl"]["role"]]["max_budget_usd"] = budget
        with open(self.shipdir / "usage.jsonl", "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps({"ts": time.time(), "seat": "impl", "total_tokens": 1, **r}) + "\n")
        with mock.patch.object(seat, "current_team", return_value=team):
            return self.seat_of("impl")

    def test_budget_headless_last_cost_and_limit(self):
        s = self._budget_seat("headless", [{"shift": "headless", "total_cost_usd": 0.9},
                                           {"shift": "headless", "total_cost_usd": 0.42}])
        self.assertEqual((s["last_cost"], s["budget"]), (0.42, 2))

    def test_budget_headless_null_cost(self):
        s = self._budget_seat("headless", [{"shift": "headless", "total_cost_usd": None}])
        self.assertEqual((s["last_cost"], s["budget"]), (None, 2))
        self.assertEqual(self._budget_seat("headless", [])["budget"], 2)

    def test_budget_not_shown_for_other_shifts(self):
        s = self._budget_seat("per_task", [{"shift": "headless", "total_cost_usd": 0.42}])
        self.assertIsNone(s["budget"])
        self.assertEqual(s["last_cost"], 0.42)   # 値は持つ。出すかどうかは画面が budget で決める

    def test_prs_listed_with_link(self):
        board.Board(self.shipdir, self.team()).add("T-001", {"assignee": "impl", "state": "active", "pr": "12"}, by="pm")
        with mock.patch.object(dashboard, "_repo_url", return_value="https://github.com/o/r"):
            prs = self.state()["prs"]
        self.assertEqual(prs[0]["number"], "12")
        self.assertEqual(prs[0]["link"], "https://github.com/o/r/pull/12")

    def test_read_does_not_touch_the_ship_folder(self):
        board.Board(self.shipdir, self.team()).add("T-001", {"assignee": "impl", "state": "active"}, by="pm")
        events.emit(self.shipdir, events.FORCE_STOP, seat="impl", summary="x")
        inbox.append(self.shipdir, "owner", "pm", "hi")
        before = _tree_state(self.shipdir)
        with mock.patch.object(seat, "status", side_effect=AssertionError("seat.status を呼んだ")):
            self.overview()
        self.assertEqual(_tree_state(self.shipdir), before)

    def test_a_broken_ship_does_not_stop_the_others(self):
        broken = self.tmp / "broken"
        broken.mkdir()
        (broken / "team.yaml").write_text("{{{ not yaml", encoding="utf-8")
        ships = {x["name"]: x for x in self.overview(ships={"bad": broken, "t1": self.shipdir})["ships"]}
        self.assertIsNone(ships["bad"]["state"])
        self.assertTrue(ships["bad"]["error"])
        self.assertIsNone(ships["t1"]["error"])
        self.assertIsNotNone(ships["t1"]["state"])

    def test_second_tier_report_orphans_memory_context(self):
        rd = report.reports_dir(self.shipdir)
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "2026-10-16.md").write_text("# 日報\n\n## 一言\n静かな一日 <b>\n\n## 明日\nT-9 を進める\n", encoding="utf-8")
        board.Board(self.shipdir, self.team()).add("T-001", {"assignee": "impl", "state": "active"}, by="pm")
        roster.seat(self.shipdir, "impl")
        roster.update(self.shipdir, "impl", state=roster.OFF, handoffWritten=False, sessionId="S9")
        tp = self.tmp / "S9.jsonl"
        tp.write_text(json.dumps({"type": "assistant", "message": {"model": "claude-sonnet-5", "usage": {
            "input_tokens": 10, "cache_read_input_tokens": 290000}}}) + "\n", encoding="utf-8")
        with mock.patch.object(claude, "transcript_paths", return_value=[tp]), \
                mock.patch.object(dashboard.rotate, "context_threshold", return_value=300000):
            st = self.state()
        self.assertEqual(st["report"], {"date": "2026-10-16", "word": "静かな一日 <b>", "tomorrow": "T-9 を進める"})
        self.assertEqual([o["id"] for o in st["orphans"]], ["T-001"])
        self.assertIn("引き継ぎなしで終了", st["orphans"][0]["why"])
        self.assertTrue(any(m["days"] is None for m in st["memory"]))
        self.assertEqual(next(s for s in st["seats"] if s["name"] == "impl")["context"],
                         {"tokens": 290010, "limit": 300000})

    def test_second_tier_empty_values(self):
        st = self.state()
        self.assertIsNone(st["report"])
        self.assertEqual(st["orphans"], [])
        self.assertTrue(all(s["context"] is None for s in st["seats"]))

    def test_overview_now_calls_agents_once(self):
        with mock.patch.object(claude, "agents", return_value=[]) as ag, \
                mock.patch("yamato.admiral.all_ships", return_value={"t1": self.shipdir}):
            body = dashboard.overview_now()
        self.assertEqual(ag.call_count, 1)
        api_schema.assert_valid(self, json.loads(json.dumps(body)), "/api/v1/overview")

    def test_agents_failure_is_reported_in_the_response(self):
        with mock.patch.object(claude, "agents", side_effect=YamatoError("boom")), \
                mock.patch("yamato.admiral.all_ships", return_value={"t1": self.shipdir}):
            body = dashboard.overview_now()
        self.assertEqual(body["agents_error"], "boom")
        api_schema.assert_valid(self, json.loads(json.dumps(body)), "/api/v1/overview")


class ServerTest(unittest.TestCase):
    def start(self, dist: Path | None = None):
        with mock.patch.object(dashboard.static, "DIST", dist or Path("/nonexistent/dist")):
            srv = dashboard.make_server(0)
        self.addCleanup(srv.server_close)
        t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        t.start()
        self.addCleanup(srv.shutdown)
        return srv

    def get(self, srv, path, method="GET"):
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
        c.request(method, path)
        r = c.getresponse()
        return r.status, r.getheader("Content-Type"), r.read()

    def test_binds_loopback_only_and_get_only(self):
        srv = self.start()
        self.assertEqual(srv.server_address[0], "127.0.0.1")
        for method in ("POST", "PUT", "DELETE"):
            self.assertEqual(self.get(srv, "/api/v1/overview", method)[0], 501)

    def test_overview_is_json_and_valid(self):
        srv = self.start()
        body = {"generated_at": 1.0, "agents_error": None, "waits": [], "ships": []}
        with mock.patch.object(dashboard, "overview_now", return_value=body):
            status, ctype, data = self.get(srv, "/api/v1/overview?x=1")
        self.assertEqual((status, ctype), (200, "application/json; charset=utf-8"))
        self.assertEqual(json.loads(data), body)

    def test_unknown_api_and_failures_are_json_errors(self):
        srv = self.start()
        status, ctype, data = self.get(srv, "/api/v1/nope")
        self.assertEqual((status, ctype), (404, "application/json; charset=utf-8"))
        self.assertEqual(api_schema.check(api_schema.load(), {"$ref": "#/components/schemas/Error"}, json.loads(data)), [])
        self.assertEqual(self.get(srv, "/api/")[0], 404)   # /api 以下は dist の index に落ちない
        with mock.patch.object(dashboard, "overview_now", side_effect=RuntimeError("x")):
            status, _, data = self.get(srv, "/api/v1/overview")
        self.assertEqual(status, 500)
        self.assertEqual(json.loads(data)["error"]["code"], "internal")

    def test_without_dist_the_root_says_not_built(self):
        srv = self.start()
        for p in ("/", "/ship/alpha"):
            status, ctype, data = self.get(srv, p)
            self.assertEqual((status, ctype), (503, "text/html; charset=utf-8"), p)
            self.assertIn("build されていない", data.decode("utf-8"))
        self.assertEqual(self.get(srv, "/assets/x.js")[0], 404)

    def test_serves_dist_files_and_spa_paths(self):
        with tempfile.TemporaryDirectory() as d:
            dist = Path(d) / "dist"
            (dist / "assets").mkdir(parents=True)
            (dist / "index.html").write_text("<p>app</p>", encoding="utf-8")
            (dist / "assets" / "a.js").write_text("1", encoding="utf-8")
            (dist / "assets" / "a.css").write_text("2", encoding="utf-8")
            (Path(d) / "secret.txt").write_text("s", encoding="utf-8")
            (dist / "link.txt").symlink_to(Path(d) / "secret.txt")
            srv = self.start(dist)
            self.assertEqual(self.get(srv, "/"), (200, "text/html; charset=utf-8", b"<p>app</p>"))
            self.assertEqual(self.get(srv, "/ship/alpha"), (200, "text/html; charset=utf-8", b"<p>app</p>"))
            self.assertEqual(self.get(srv, "/ship/v1.2")[0], 200)   # 知らない拡張子は画面の path
            self.assertEqual(self.get(srv, "/assets/a.js"), (200, "text/javascript; charset=utf-8", b"1"))
            self.assertEqual(self.get(srv, "/assets/a.css")[1], "text/css; charset=utf-8")
            for p in ("/assets/missing.js", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
                self.assertEqual(self.get(srv, p)[0], 404, p)
            self.assertEqual(self.get(srv, "/link.txt")[0], 404)      # 一覧に入れない
            # 起動後に増えたファイルは出ない (一覧は起動時)
            (dist / "assets" / "late.js").write_text("3", encoding="utf-8")
            self.assertEqual(self.get(srv, "/assets/late.js")[0], 404)

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


class ShipConfigTest(ShipTestCase):
    def get(self, name="t1"):
        status, body = dashboard.api_get(f"/api/v1/ships/{name}/config")
        return status, json.loads(json.dumps(body))

    def ok(self):
        status, body = self.get()
        self.assertEqual(status, 200)
        api_schema.assert_valid(self, body, "/api/v1/ships/{name}/config")
        return body

    def field(self, body, key):
        return next(f for f in body["fields"] if f["key"] == key)

    def test_fields_follow_the_schema_order_with_descriptions(self):
        body = self.ok()
        schema = json.loads(shipconfig_schema_path().read_text(encoding="utf-8"))
        self.assertEqual([f["key"] for f in body["fields"]][:len(schema["properties"])], list(schema["properties"]))
        hub = self.field(body, "hub")
        self.assertTrue(hub["set"] and hub["required"])
        self.assertEqual(hub["description"], schema["properties"]["hub"]["description"])
        self.assertEqual(body["raw"], (self.shipdir / "team.yaml").read_text(encoding="utf-8"))
        self.assertEqual(body["path"], str(self.shipdir / "team.yaml"))

    def test_unset_and_unknown_keys(self):
        path = self.shipdir / "team.yaml"
        path.write_text(path.read_text(encoding="utf-8") + "\nmystery: 1\n", encoding="utf-8")
        body = self.ok()
        schema = json.loads(shipconfig_schema_path().read_text(encoding="utf-8"))
        unset = [k for k in schema["properties"] if k not in yaml_keys(path)]
        self.assertTrue(unset)
        for k in unset:
            f = self.field(body, k)
            self.assertEqual((f["set"], f["value"]), (False, None))
        mystery = self.field(body, "mystery")
        self.assertEqual((mystery["set"], mystery["value"], mystery["description"]), (True, 1, None))

    def test_roles_are_a_table_value(self):
        roles = self.field(self.ok(), "roles")
        self.assertIn("impl", roles["value"])
        self.assertIsInstance(roles["value"]["impl"], dict)

    def test_files_listed(self):
        files = {f["file"]: f["kind"] for f in self.ok()["files"]}
        self.assertEqual(files["charter.md"], "charter")
        self.assertEqual(files["roles/impl.md"], "role")

    def test_pending_follows_the_mtimes(self):
        self.assertEqual(self.ok()["pending"], {"changed": False, "keys": []})   # up の前
        cache = self.shipdir / ".runtime" / "team.json"
        cache.parent.mkdir(exist_ok=True)
        yml = self.shipdir / "team.yaml"
        from yamato import team
        cache.write_text(json.dumps(team.load_team(self.shipdir), default=str), encoding="utf-8")
        os.utime(cache, (time.time() + 10, time.time() + 10))
        self.assertFalse(self.ok()["pending"]["changed"])
        yml.write_text(yml.read_text(encoding="utf-8").replace("hub: pm", "hub: impl"), encoding="utf-8")
        os.utime(cache, (time.time() - 10, time.time() - 10))
        self.assertEqual(self.ok()["pending"], {"changed": True, "keys": ["hub"]})

    def test_unregistered_ship_is_not_found(self):
        status, body = self.get("nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")
        # ships.json に無い (YAMATO_HOME 直下の folder だけ) 艦は受け付けない
        reg = Path(os.environ["YAMATO_HOME"]) / "ships.json"
        reg.write_text("{}", encoding="utf-8")
        self.assertEqual(self.get()[0], 404)

    def test_path_is_not_a_ship_name(self):
        for n in ("..", "%2e%2e", "t1%2F..", "_admiral"):
            self.assertEqual(self.get(n)[0], 404, n)

    def test_broken_yaml_is_an_error_json_not_a_crash(self):
        (self.shipdir / "team.yaml").write_text("name: [unclosed\n", encoding="utf-8")
        status, body = self.get()
        self.assertEqual(status, 500)
        self.assertEqual(body["error"]["code"], "unreadable")
        self.assertEqual(api_schema.check(api_schema.load(), {"$ref": "#/components/schemas/Error"}, body), [])
        (self.shipdir / "team.yaml").unlink()
        self.assertEqual(self.get()[1]["error"]["code"], "unreadable")

    def test_read_does_not_touch_the_ship_folder(self):
        before = _tree_state(self.shipdir)
        self.ok()
        self.assertEqual(_tree_state(self.shipdir), before)


def shipconfig_schema_path() -> Path:
    from yamato import shipconfig

    return shipconfig.SCHEMA


def yaml_keys(path: Path) -> set:
    import yaml

    return set(yaml.safe_load(path.read_text(encoding="utf-8")))


class FleetTest(ShipTestCase):
    def get(self):
        status, body = dashboard.api_get("/api/v1/fleet")
        body = json.loads(json.dumps(body))
        api_schema.assert_valid(self, body, "/api/v1/fleet")
        return status, body

    def test_registered_ship_and_no_admiral(self):
        status, body = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(body["home"], str(self.tmp / "home"))
        ship = next(s for s in body["ships"] if s["name"] == "t1")
        self.assertEqual((ship["path"], ship["exists"], ship["error"]), (str(self.shipdir), True, None))
        self.assertEqual(ship["team"], "t1")
        self.assertIsNone(body["admiral"])

    def test_missing_folder_and_broken_team_yaml_do_not_drop_the_ship(self):
        from yamato.util import register_ship

        gone = self.tmp / "gone"
        register_ship("gone", gone)
        (self.shipdir / "team.yaml").write_text("- not a mapping\n", encoding="utf-8")
        _, body = self.get()
        by = {s["name"]: s for s in body["ships"]}
        self.assertEqual((by["gone"]["exists"], by["gone"]["registered"], by["gone"]["team"]), (False, True, None))
        self.assertIn("gone", by["gone"]["error"])
        self.assertTrue(by["t1"]["exists"])
        self.assertIsNone(by["t1"]["team"])
        self.assertIn("mapping", by["t1"]["error"])

    def test_unregistered_folder_under_home_is_picked_up(self):
        home = self.tmp / "home"
        (home / "loose").mkdir()
        (home / "loose" / "team.yaml").write_text("name: loose\nroles: {}\n", encoding="utf-8")
        (home / "_hidden").mkdir()
        (home / "_hidden" / "team.yaml").write_text("name: h\n", encoding="utf-8")
        _, body = self.get()
        loose = next(s for s in body["ships"] if s["name"] == "loose")
        self.assertEqual((loose["registered"], loose["exists"], loose["team"]), (False, True, "loose"))
        self.assertNotIn("_hidden", [s["name"] for s in body["ships"]])

    def test_admiral_is_separate_with_roles_and_never_in_ships(self):
        adm = self.tmp / "home" / "_admiral"
        adm.mkdir()
        (adm / "team.yaml").write_text("name: yamato\nroles:\n  admiral: {model: opus}\n", encoding="utf-8")
        _, body = self.get()
        self.assertEqual(body["admiral"], {"path": str(adm), "team": "yamato", "roles": ["admiral"], "error": None})
        self.assertNotIn("_admiral", [s["name"] for s in body["ships"]])

    def test_unreadable_admiral_reports_the_reason(self):
        adm = self.tmp / "home" / "_admiral"
        adm.mkdir()
        _, body = self.get()
        self.assertEqual((body["admiral"]["team"], body["admiral"]["roles"]), (None, []))
        self.assertIn("team.yaml", body["admiral"]["error"])

    def test_writes_nothing(self):
        before = sorted(p for p in self.tmp.rglob("*"))
        self.get()
        self.assertEqual(sorted(p for p in self.tmp.rglob("*")), before)

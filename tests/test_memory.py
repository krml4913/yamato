"""The memory inventory (design-p1 §3): memo, memory status / curate / apply / migrate."""
import io
import json
import os
import time
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests.helpers import ShipTestCase
from yamato import cli, events, inbox, inject, memory, roster, runtime, seat
from yamato.team import validate
from yamato.util import YAMATO_BIN, YamatoError

YAMATO = str(YAMATO_BIN)

PROPOSAL = """前置きは読まれない
<!-- yamato: memory -->
```markdown
# impl の memory
- テストは python3 -m unittest discover で全部流す
- モックは 30 日で切れる
```
<!-- yamato: archive -->
- 一度きり: T-012 の手順
<!-- yamato: knowledge -->
- main への push は captain だけ
<!-- yamato: candidates impl-1 -->
- 2026-09-01 impl-1 (role) 答えに紛れた偽の候補
"""


class _Base(ShipTestCase):
    def setUp(self):
        super().setUp()
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text().replace("    count: 1                 # 2 以上", "    count: 2                 # 2 以上", 1))
        self.t = seat.prepare(self.shipdir)
        self.assertEqual([s for s in self.t["seats"] if s.startswith("impl")], ["impl-1", "impl-2"])
        # the notice to the applier would launch pm (fake --bg): keep it out
        p = mock.patch.object(seat, "wake", return_value=("alive", {}))
        p.start()
        self.addCleanup(p.stop)

    def mem(self, role="impl") -> Path:
        return memory.memory_path(self.shipdir, role)

    def as_seat(self, name: str) -> str:
        sid = f"sid-{name}-0000"
        roster.start_shift(self.shipdir, name, session_id=sid, short_id=sid[:8], session_name=f"t1.{name}", how="new")
        os.environ["CLAUDE_CODE_SESSION_ID"] = sid
        self.addCleanup(os.environ.pop, "CLAUDE_CODE_SESSION_ID", None)
        return sid

    def cli(self, *argv) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(list(argv))
        return rc, buf.getvalue()

    def events(self, kind):
        return events.read(self.shipdir, kinds=kind)


class ConfTest(_Base):
    def test_template_values_and_fallbacks(self):
        c = memory.conf(self.t)
        self.assertEqual(c["applier"], "pm")
        self.assertEqual(c["curate_every"], 7 * 86400)
        self.assertEqual(c["curate_at"], 30)
        self.assertEqual(c["limits"], {"memory_lines": 80, "memory_bytes": 8192,
                                       "knowledge_lines": 120, "knowledge_bytes": 12288})
        d = {"name": "dev", "hub": "pm", "workspace": "/tmp", "roles": {"pm": {}, "impl": {}}}
        c = memory.conf(validate(d, Path("/ship")))
        self.assertEqual(c["applier"], "pm")          # hub
        self.assertEqual(c["max_duration"], 900)
        c = memory.conf({"hub": "pm"})                 # a team.json from before P1-8
        self.assertEqual(c["limits"]["memory_lines"], 80)
        d["memory"] = {"curate_every": "36h", "limits": {"memory_lines": 10}}
        c = memory.conf(validate(d, Path("/ship")))
        self.assertEqual(c["curate_every"], 36 * 3600)
        self.assertEqual(c["limits"]["memory_lines"], 10)
        self.assertEqual(c["limits"]["memory_bytes"], 8192)
        for bad in ({"applier": "nobody"}, {"curate_at": 0}, {"limits": {"memory_lines": 0}},
                    {"limits": {"lines": 3}}, {"curate_every": "x"}, {"unknown": 1}):
            d["memory"] = bad
            with self.assertRaises(YamatoError, msg=bad):
                validate(d, Path("/ship"))


class MigrateTest(_Base):
    def test_seat_memory_moves_to_the_role_and_is_kept(self):
        (self.shipdir / "seats/impl-1/memory.md").write_text("- impl-1 の知見\n")
        (self.shipdir / "seats/impl-2/memory.md").write_text("- impl-2 の知見\n")
        (self.shipdir / "seats/pm/memory.md").write_text("")
        moved = memory.migrate(self.shipdir, self.t)
        self.assertEqual(len(moved), 2)
        text = self.mem().read_text()
        self.assertIn("<!-- seats/impl-1/memory.md から移した", text)
        self.assertIn("- impl-1 の知見", text)
        self.assertIn("- impl-2 の知見", text)
        self.assertEqual((self.shipdir / "seats/impl-1/memory.md.migrated").read_text(), "- impl-1 の知見\n")
        self.assertFalse((self.shipdir / "seats/impl-1/memory.md").exists())
        self.assertFalse((self.shipdir / "seats/pm/memory.md").exists())   # empty: nothing to keep
        self.assertFalse(self.mem("pm").exists())
        self.assertEqual(len(self.events(memory.MEMORY_MIGRATE)), 2)
        self.assertEqual(memory.migrate(self.shipdir, self.t), [])          # once

    def test_a_late_seat_file_is_appended_not_overwriting(self):
        self.mem().parent.mkdir(parents=True, exist_ok=True)
        self.mem().write_text("- もとの行\n")
        (self.shipdir / "seats/impl-1/memory.md").write_text("- あとから\n")
        (self.shipdir / "seats/impl-1/memory.md.migrated").write_text("前回")
        memory.migrate(self.shipdir, self.t)
        self.assertTrue(self.mem().read_text().startswith("- もとの行\n\n<!-- seats/impl-1"))
        self.assertEqual((self.shipdir / "seats/impl-1/memory.md.migrated-2").read_text(), "- あとから\n")

    def test_injection_migrates_and_reads_the_role_memory(self):
        (self.shipdir / "seats/impl-1/memory.md").write_text("- P0 の席の知見\n")
        text, _ = inject.build(self.shipdir, self.t, "impl-2")
        self.assertIn("## 役割の memory (roles/impl/memory.md)", text)
        self.assertIn("- P0 の席の知見", text)   # shared by the role: impl-2 reads impl-1's

    def test_new_ships_have_no_seat_memory_file(self):
        self.assertFalse((self.shipdir / "seats/impl-1/memory.md").exists())
        self.assertTrue((self.shipdir / "seats/impl-1/memory-inbox.md").exists())

    def test_cli(self):
        (self.shipdir / "seats/impl-1/memory.md").write_text("- x\n")
        rc, out = self.cli("memory", "migrate", str(self.shipdir))
        self.assertEqual(rc, 0)
        self.assertIn("roles/impl/memory.md", out)
        self.assertIn("移すものはありません", self.cli("memory", "migrate", str(self.shipdir))[1])


class MemoTest(_Base):
    def test_the_calling_seat_is_found_by_session(self):
        self.as_seat("impl-2")
        rc, out = self.cli("memo", "テストのモックは  30 日で\n切れる", "--item", "T-042")
        self.assertEqual(rc, 0)
        line = (self.shipdir / "seats/impl-2/memory-inbox.md").read_text()
        self.assertRegex(line, r"^- \d{4}-\d{2}-\d{2} impl-2 \[T-042\] \(role\) テストのモックは 30 日で 切れる\n$")
        self.assertIn(line.strip(), out)
        self.cli("memo", "艦の知見", "--scope", "ship", "--ship", str(self.shipdir))
        self.assertIn("impl-2 (ship) 艦の知見", (self.shipdir / "seats/impl-2/memory-inbox.md").read_text())

    def test_a_seat_writes_only_its_own_inbox(self):
        self.as_seat("impl-2")
        rc = self.cli("memo", "x", "--seat", "impl-1")[0]
        self.assertEqual(rc, 1)
        self.assertEqual((self.shipdir / "seats/impl-1/memory-inbox.md").read_text(), "")

    def test_outside_a_seat_ship_and_seat_are_needed(self):
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        self.assertEqual(self.cli("memo", "x")[0], 1)
        self.assertEqual(self.cli("memo", "x", "--ship", str(self.shipdir), "--seat", "nobody")[0], 1)
        self.assertEqual(self.cli("memo", "x", "--ship", str(self.shipdir), "--seat", "pm")[0], 0)
        self.assertIn(" pm (role) x", (self.shipdir / "seats/pm/memory-inbox.md").read_text())
        with self.assertRaises(YamatoError):
            memory.memo(self.shipdir, "pm", "   ")


class StatusTest(_Base):
    def test_counts_days_and_the_curate_signs(self):
        now = time.time()
        for i in range(3):
            memory.memo(self.shipdir, "impl-1", f"候補 {i}")
        memory.memo(self.shipdir, "impl-2", "候補 x")
        rows = {r["role"]: r for r in memory.status(self.shipdir, self.t, now)}
        self.assertEqual(rows["impl"]["count"], 4)
        self.assertEqual(rows["impl"]["seats"], {"impl-1": 3, "impl-2": 1})
        self.assertIsNone(rows["impl"]["days"])
        self.assertFalse(rows["impl"]["due"])
        events.emit(self.shipdir, memory.MEMORY_APPLY, data={"role": "impl"}, now=now - 8 * 86400)
        lines = memory.status_lines(self.shipdir, self.t, now)
        impl = next(x for x in lines if x.startswith("impl:"))
        self.assertIn("impl: 候補 4 件 / 前回の棚卸しから 8 日", impl)
        self.assertIn("棚卸しの目安 (7 日 / 30 件) に達した", impl)
        self.assertIn("pm: 候補 0 件 / 前回の棚卸しなし", lines)
        self.assertTrue(any(x.startswith("knowledge: 候補 0 件") for x in lines))
        rc, out = self.cli("memory", "status", str(self.shipdir))
        self.assertIn("impl: 候補 4 件", out)

    def test_never_applied_counts_from_the_oldest_candidate(self):
        now = time.time()
        memory.memo(self.shipdir, "impl-1", "古い", now=now - 9 * 86400)
        memory.memo(self.shipdir, "impl-2", "新しい", now=now)
        row = next(r for r in memory.status(self.shipdir, self.t, now) if r["role"] == "impl")
        self.assertTrue(row["due"])
        impl = next(x for x in memory.status_lines(self.shipdir, self.t, now) if x.startswith("impl:"))
        self.assertIn("前回の棚卸しなし (一番古い候補から 9 日) ← 棚卸しの目安", impl)

    def test_item_is_one_line(self):
        line = memory.memo(self.shipdir, "impl-1", "本文", item=" T-1\nx ")
        self.assertIn("impl-1 [T-1 x] (role) 本文", line)

    def test_count_alone_is_a_sign(self):
        with mock.patch.dict(self.t["memory"], curate_at=2):
            memory.memo(self.shipdir, "impl-1", "a")
            memory.memo(self.shipdir, "impl-1", "b")
            self.assertTrue(next(r for r in memory.status(self.shipdir, self.t) if r["role"] == "impl")["due"])

    def test_the_applier_sees_it_once_a_day(self):
        memory.memo(self.shipdir, "impl-1", "候補")
        text, _ = inject.build(self.shipdir, self.t, "pm")
        self.assertIn("## memory の棚卸し (memory status。今日の最初のシフトだけ)\n", text)
        self.assertIn("- impl: 候補 1 件", text)
        self.assertNotIn("memory の棚卸し (memory status", inject.build(self.shipdir, self.t, "pm")[0])
        self.assertNotIn("memory の棚卸し (memory status", inject.build(self.shipdir, self.t, "impl-1")[0])
        tomorrow = time.time() + 86400
        self.assertIsNotNone(memory.applier_notice(self.shipdir, self.t, "pm", tomorrow))

    def test_injection_cuts_an_over_limit_file_and_warns(self):
        self.mem().parent.mkdir(parents=True, exist_ok=True)
        self.mem().write_text("".join(f"- {i}\n" for i in range(12)))
        (self.shipdir / "knowledge.md").write_text("k" * 50)
        lim = {"memory_lines": 10, "memory_bytes": 8192, "knowledge_lines": 120, "knowledge_bytes": 20}
        with mock.patch.dict(self.t["memory"], limits=lim):
            text, _ = inject.build(self.shipdir, self.t, "impl-1")
        self.assertIn("- 9\n(memory.md が上限を超えている (12 行 (上限 10 行))。上限で切った。棚卸しが必要)", text)
        self.assertNotIn("- 10", text)
        self.assertIn("k" * 20 + "\n(knowledge.md が上限を超えている (50 bytes (上限 20 bytes))", text)


class _Curated(_Base):
    def setUp(self):
        super().setUp()
        self.mem().parent.mkdir(parents=True, exist_ok=True)
        self.mem().write_text("- モックは 30 日で切れる\n- 古い行\n")
        memory.memo(self.shipdir, "impl-1", "テストは python3 -m unittest discover で全部流す", item="T-1")
        memory.memo(self.shipdir, "impl-2", "T-012 の手順")
        memory.memo(self.shipdir, "impl-2", "main への push は captain だけ", scope="ship")

    def p_calls(self):
        return [c for c in self.fake()["calls"] if "-p" in c["argv"]]


class CurateTest(_Curated):
    def test_a_headless_shift_writes_the_proposal_and_leaves_memory_alone(self):
        before = self.mem().read_text()
        self.set_fake_mode(p_result=PROPOSAL)
        res = memory.run_curate(self.shipdir, self.t, "impl")
        self.assertEqual(res["outcome"], "正常", res)
        self.assertEqual(self.mem().read_text(), before)
        [call] = self.p_calls()
        a = call["argv"]
        self.assertEqual(call["cwd"], str(self.shipdir))
        self.assertIsNone(call["GH_TOKEN"])
        self.assertTrue(call["stdin_is_devnull"])
        self.assertEqual(a[a.index("--agent") + 1], memory.CURATOR)
        self.assertEqual(a[a.index("--model") + 1], "sonnet")        # the curated role's model
        self.assertEqual(a[a.index("--name") + 1], "t1.memory-curate-impl")
        agents = json.loads(a[a.index("--agents") + 1])
        self.assertEqual(agents[memory.CURATOR]["tools"], ["Read"])
        self.assertIn("棚卸し案を作る係", agents[memory.CURATOR]["prompt"])
        settings = json.loads(Path(a[a.index("--settings") + 1]).read_text())
        self.assertEqual(settings["permissions"]["defaultMode"], "dontAsk")
        self.assertEqual(settings["permissions"]["allow"], [])
        self.assertNotIn("hooks", settings)                          # not a seat
        prompt = a[-1]
        self.assertIn("- モックは 30 日で切れる", prompt)
        self.assertIn("impl-1 [T-1] (role) テストは", prompt)
        self.assertIn("80 行 / 8192 bytes", prompt)

        prop = memory.proposed_path(self.shipdir, "impl").read_text()
        sec = memory.sections(prop)
        self.assertEqual(sec["memory"], "# impl の memory\n- テストは python3 -m unittest discover で全部流す\n"
                                        "- モックは 30 日で切れる")                 # fences stripped
        self.assertEqual(sec["archive"], "- 一度きり: T-012 の手順")
        # only the candidates yamato gathered, never ones in the answer
        self.assertEqual(set(sec["candidates"]), {"impl-1", "impl-2"})
        self.assertNotIn("偽の候補", prop)
        self.assertEqual(len(sec["candidates"]["impl-2"]), 2)
        self.assertIn("候補: 3 件 (impl-1: 1, impl-2: 2)", prop)
        self.assertIn("(+2 / -1 行)", prop)

        [u] = [json.loads(x) for x in (self.shipdir / "usage.jsonl").read_text().splitlines()]
        self.assertEqual((u["seat"], u["role"], u["source"]), ("memory-curate-impl", "impl", "result"))
        [e] = self.events(memory.MEMORY_CURATE)
        self.assertEqual(e["data"]["outcome"], "正常")
        [note] = inbox.entries(self.shipdir, "pm")
        self.assertEqual(note["from"], "yamato")
        self.assertIn("役割 impl の memory の棚卸し案ができた (+2 / -1 行、候補 3 件)", note["text"])
        self.assertNotIn("テストは", note["text"])   # fixed form: none of the shift's output
        impl = next(x for x in memory.status_lines(self.shipdir, self.t) if x.startswith("impl:"))
        self.assertIn(f"棚卸し案あり (+2 / -1 行): `", impl)
        self.assertIn(f"memory apply {self.shipdir} impl` で反映", impl)

    def test_an_answer_without_the_markers_fails_without_a_proposal(self):
        self.set_fake_mode(p_result="すみません、できませんでした", p_no_hook=True)
        res = memory.run_curate(self.shipdir, self.t, "impl")
        self.assertEqual(res["outcome"], "異常")
        self.assertEqual([w for w, _ in res["failures"]], ["案の形になっていない"])   # no hook needed
        self.assertFalse(memory.proposed_path(self.shipdir, "impl").exists())
        self.assertEqual(self.events(memory.MEMORY_CURATE)[0]["data"]["outcome"], "異常")
        self.assertIn("棚卸しが失敗した (案の形になっていない)", inbox.entries(self.shipdir, "pm")[0]["text"])
        self.assertEqual(len(list((memory.role_dir(self.shipdir, "impl") / "curate").glob("*.result.md"))), 1)

    def test_an_api_error_fails(self):
        self.set_fake_mode(p_api_error=429)
        res = memory.run_curate(self.shipdir, self.t, "impl")
        self.assertEqual(res["outcome"], "異常")
        self.assertIn("枠切れの可能性", res["failures"][0][0])

    def test_the_time_limit_stops_the_shift(self):
        self.set_fake_mode(p_sleep=30, p_result=PROPOSAL)
        with mock.patch.dict(self.t["memory"], max_duration=1):
            t0 = time.time()
            res = memory.run_curate(self.shipdir, self.t, "impl")
        self.assertLess(time.time() - t0, 15)
        self.assertEqual(res["outcome"], "時間切れ")
        self.assertFalse(memory.proposed_path(self.shipdir, "impl").exists())
        self.assertIn("時間切れで止まった", inbox.entries(self.shipdir, "pm")[0]["text"])
        self.assertEqual(json.loads((self.shipdir / "usage.jsonl").read_text())["source"], "transcript")

    def test_one_curate_per_role_at_a_time(self):
        from yamato import headless

        lock = headless._try_lock(runtime.runtime_dir(self.shipdir) / "memory-curate-impl.lock")
        self.addCleanup(headless._release, lock)
        with self.assertRaises(YamatoError):
            memory.run_curate(self.shipdir, self.t, "impl")

    def test_without_a_role_the_roles_with_candidates_are_curated(self):
        self.set_fake_mode(p_result=PROPOSAL)
        out = []
        self.assertEqual(memory.curate(self.shipdir, self.t, wait=True, out=out.append), ["impl"])
        self.assertIn("impl: 案を作った", out[0])
        with self.assertRaises(YamatoError):
            memory.curate(self.shipdir, self.t, "nobody", wait=True, out=out.append)

    def test_detached_by_default(self):
        with mock.patch("subprocess.Popen") as popen:
            memory.curate(self.shipdir, self.t, "impl", out=lambda *_: None)
        argv = popen.call_args[0][0]
        self.assertEqual(argv[-3:], ["_memory-curate", str(self.shipdir), "impl"])
        self.assertTrue(popen.call_args[1]["start_new_session"])

    def test_nothing_to_curate(self):
        for s in ("impl-1", "impl-2"):
            (self.shipdir / f"seats/{s}/memory-inbox.md").write_text("")
        out = []
        self.assertEqual(memory.curate(self.shipdir, self.t, out=out.append), [])
        self.assertIn("棚卸しする役割がありません", out[0])


class ApplyTest(_Curated):
    def curate(self, result=PROPOSAL):
        self.set_fake_mode(p_result=result)
        self.assertEqual(memory.run_curate(self.shipdir, self.t, "impl")["outcome"], "正常")

    def test_apply_writes_memory_archives_and_moves_the_candidates(self):
        self.curate()
        memory.memo(self.shipdir, "impl-1", "curate のあとに来た候補")
        self.as_seat("pm")
        rc, out = self.cli("memory", "apply", str(self.shipdir), "impl")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.mem().read_text(), "# impl の memory\n- テストは python3 -m unittest discover で全部流す\n"
                                                 "- モックは 30 日で切れる\n")
        archive = memory.archive_path(self.shipdir, "impl").read_text()
        self.assertRegex(archive, r"## \d{4}-\d{2}-\d{2} \d{2}:\d{2} 反映: pm \(役割 impl\)")
        self.assertIn("### 案が外したもの\n- 一度きり: T-012 の手順", archive)
        self.assertIn("### memory.md から外れた行\n- 古い行", archive)
        # processed candidates -> done; the late one stays
        left = (self.shipdir / "seats/impl-1/memory-inbox.md").read_text()
        self.assertIn("curate のあとに来た候補", left)
        self.assertNotIn("unittest", left)
        self.assertEqual((self.shipdir / "seats/impl-2/memory-inbox.md").read_text(), "")
        done = list((self.shipdir / "seats/impl-2/memory-inbox.done").glob("*.md"))
        self.assertEqual(len(done), 1)
        self.assertIn("T-012 の手順", done[0].read_text())
        kin = memory.knowledge_inbox_path(self.shipdir).read_text()
        self.assertIn("impl-2 (ship) main への push は captain だけ", kin)   # (ship) memo, mechanically
        self.assertIn("impl の棚卸し: main への push は captain だけ", kin)   # the knowledge section
        self.assertFalse(memory.proposed_path(self.shipdir, "impl").exists())
        self.assertTrue((memory.role_dir(self.shipdir, "impl") / "memory.proposed.applied.md").exists())
        [e] = self.events(memory.MEMORY_APPLY)
        self.assertEqual(e["by"], "pm")                                     # recorded, not checked
        self.assertEqual((e["data"]["role"], e["data"]["candidates"]), ("impl", 3))
        self.assertIn("impl: 候補 1 件 / 前回の棚卸しから 0 日", memory.status_lines(self.shipdir, self.t))

    def test_anyone_may_apply(self):
        self.curate()
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        self.assertEqual(self.cli("memory", "apply", str(self.shipdir), "impl")[0], 0)
        self.assertEqual(self.events(memory.MEMORY_APPLY)[0]["by"], "owner")

    def test_over_the_limit_is_refused_and_nothing_moves(self):
        big = "<!-- yamato: memory -->\n" + "".join(f"- {i}\n" for i in range(81)) + "<!-- yamato: archive -->\n"
        self.curate(big)
        before = self.mem().read_text()
        with self.assertRaisesRegex(YamatoError, r"上限を超えているので反映しない: 81 行 \(上限 80 行\)"):
            memory.apply(self.shipdir, self.t, "impl", "pm")
        self.assertEqual(self.mem().read_text(), before)
        self.assertTrue(memory.proposed_path(self.shipdir, "impl").exists())
        self.assertIn("unittest", (self.shipdir / "seats/impl-1/memory-inbox.md").read_text())
        self.assertIn("上限を超えている", inbox.entries(self.shipdir, "pm")[0]["text"])
        # bytes too
        with mock.patch.dict(self.t["memory"], limits={**memory.conf(self.t)["limits"], "memory_lines": 500,
                                                       "memory_bytes": 100}):
            with self.assertRaisesRegex(YamatoError, "bytes"):
                memory.apply(self.shipdir, self.t, "impl", "pm")

    def test_a_hand_edited_proposal_is_what_gets_applied(self):
        self.curate()
        p = memory.proposed_path(self.shipdir, "impl")
        p.write_text(p.read_text().replace("- モックは 30 日で切れる\n", "- 手で直した行\n", 1))
        memory.apply(self.shipdir, self.t, "impl", "pm")
        self.assertIn("- 手で直した行", self.mem().read_text())

    def test_errors(self):
        with self.assertRaisesRegex(YamatoError, "棚卸し案がありません"):
            memory.apply(self.shipdir, self.t, "impl", "pm")
        memory.proposed_path(self.shipdir, "impl").write_text("印なし")
        with self.assertRaisesRegex(YamatoError, "節がありません"):
            memory.apply(self.shipdir, self.t, "impl", "pm")
        self.assertEqual(self.cli("memory", "apply", str(self.shipdir))[0], 1)

    def test_knowledge(self):
        (self.shipdir / "knowledge.md").write_text("# k\n- 古い知見\n")
        memory.knowledge_inbox_path(self.shipdir).write_text("- 候補 1\n- 候補 2\n")
        with self.assertRaisesRegex(YamatoError, "knowledge の案がありません"):
            memory.apply_knowledge(self.shipdir, self.t, "pm")
        prop = memory.knowledge_proposed_path(self.shipdir)
        prop.write_text("# k\n" + "".join(f"- {i}\n" for i in range(120)))
        with self.assertRaisesRegex(YamatoError, "121 行"):
            memory.apply_knowledge(self.shipdir, self.t, "pm")
        self.assertIn("knowledge: 候補 2 件", "\n".join(memory.status_lines(self.shipdir, self.t)))
        prop.write_text("# k\n- 候補 1 をまとめた\n")
        rc, out = self.cli("memory", "apply", str(self.shipdir), "--knowledge", "--by", "pm")
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.shipdir / "knowledge.md").read_text(), "# k\n- 候補 1 をまとめた\n")
        self.assertIn("反映: pm (knowledge)\n\n- 古い知見", memory.knowledge_archive_path(self.shipdir).read_text())
        self.assertEqual(memory.knowledge_inbox_path(self.shipdir).read_text(), "")
        done = list((self.shipdir / "knowledge-inbox.done").glob("*.md"))
        self.assertEqual(done[0].read_text(), "- 候補 1\n- 候補 2\n")
        self.assertFalse(prop.exists())
        [e] = self.events(memory.MEMORY_APPLY)
        self.assertIsNone(e["data"]["role"])
        self.assertIn("knowledge: 候補 0 件 / 前回の棚卸しから 0 日", memory.status_lines(self.shipdir, self.t))


class TemplateTest(_Base):
    def test_the_template_carries_the_curator_and_the_memo_guidance(self):
        self.assertTrue((self.shipdir / "roles" / memory.CURATOR_PROMPT).is_file())
        agents = json.loads(runtime.agents_path(self.shipdir).read_text())
        self.assertNotIn(memory.CURATOR, agents)                 # not a seat's role
        for role in ("pm", "impl"):
            text = agents[role]["prompt"]
            self.assertIn(f"memo \"<本文>\" --ship {self.shipdir}", text)
            self.assertNotIn("詰まり / memory 候補", text)
        self.assertIn(f"memory curate {self.shipdir}", agents["pm"]["prompt"])
        self.assertIn(f"memory apply {self.shipdir} --knowledge", agents["pm"]["prompt"])
        deny = json.loads(runtime.settings_path(self.shipdir, "impl-1").read_text())["permissions"]["deny"]
        self.assertIn(f"Write(/{self.shipdir}/roles/*/memory.md)", deny)


class ResearchTemplateTest(ShipTestCase):
    def setUp(self):
        super().setUp()
        from yamato import ship
        from yamato.team import load_team

        self.rdir, _ = ship.create("r1", None, str(self.tmp / "r1"), "research")
        self.rteam = load_team(self.rdir)
        with mock.patch("yamato.claude.git_root", return_value=None):
            runtime.generate(self.rdir, self.rteam)

    def test_editor_applies_and_the_curator_is_there(self):
        self.assertEqual(memory.conf(self.rteam)["applier"], "editor")
        self.assertTrue((self.rdir / "roles" / memory.CURATOR_PROMPT).is_file())
        memory.curator_agents(self.rdir, self.rteam, "researcher")   # does not raise
        agents = json.loads(runtime.agents_path(self.rdir).read_text())
        self.assertIn(f"memory curate {self.rdir}", agents["editor"]["prompt"])
        for role in ("researcher", "fact-checker", "editor"):
            self.assertIn(f'memo "<本文>" --ship {self.rdir}', agents[role]["prompt"])
            self.assertNotIn("詰まり / memory 候補", agents[role]["prompt"])

    def test_the_memo_in_the_prompt_passes_the_dont_ask_allow_list(self):
        from tests.test_research import decide

        st = json.loads(runtime.settings_path(self.rdir, "researcher-1").read_text())
        cmd = f'{YAMATO} memo "よい出典は公式の文書" --ship {self.rdir} --item T-1 --scope ship'
        self.assertEqual(decide(st, "Bash", cmd, self.rdir), "allow")

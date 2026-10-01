"""``shift: headless`` (design-p1 §4): one shift = one ``claude -p``, run by ``yamato run-headless``.

``send`` to a headless seat appends to its inbox and starts this wrapper
detached (``wake``). The wrapper lives for one shift (or a few in a row, when
messages came in meanwhile) and ends with the ``claude -p`` it runs; nothing
stays resident (design §2). What it holds (design-p1 §4.2):

- the session id is chosen first and written to the roster (``--session-id``)
- the time limit: SIGTERM at ``min(role max_duration, deadline + grace)``
- after the run: one ``usage.jsonl`` line (from the ``result`` line, or from the
  transcript when there is none), the failure checks (no SessionStart
  ``hook_response`` / ``is_error`` / ``api_error_status`` / ``terminal_reason``;
  never the exit code or ``subtype``, verify-p1-d V5), the last
  ``rate_limit_event``, and a fixed-form report to ``report_to`` (default hub)
- one shift at a time per seat (record integrity): a run lock per seat

The report is written by yamato and never carries the seat's output (§7.2);
where it goes is a setting, that it is fixed-form is the safety net.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import board as board_mod
from . import claude, deadline, events, inbox, notify, procs, roster, runtime, usage
from .team import seat_spec
from .runtime import ship_arg, yamato_invocation
from .util import YAMATO_BIN, YamatoError, append_log, lock_file, try_lock_file, unlock_file

REPORTER = "yamato"   # the `from` of the end-of-shift report: not a seat, so nobody owes a SendMessage
POLL = 1.0            # seconds between time-limit checks while claude -p runs
KILL_WAIT = 30        # SIGTERM -> SIGKILL (a -p ends ~0.4 s after SIGTERM, verify-p1-d V4)
IDLE_POLL = 0.5       # seconds between looks while waiting for a wrapper to let go of the seat
NOTE_CHARS = 500      # the last response pasted into the item on a no-handoff end

OK = "正常"
NO_HANDOFF = "引き継ぎなし"
TIMEOUT = "時間切れ"
FORCED = "強制停止"
FAILED = "異常"


# --- one shift at a time per seat ---------------------------------------------

def _lock_path(shipdir: Path, seat: str, kind: str) -> Path:
    return Path(shipdir) / ".runtime" / f"headless-{seat}.{kind}"


def _try_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a", encoding="utf-8")
    if try_lock_file(f):
        return f
    f.close()
    return None


def _lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a", encoding="utf-8")
    lock_file(f)
    return f


def _release(f) -> None:
    unlock_file(f)
    f.close()


def running(shipdir: Path, seat: str) -> bool:
    """A wrapper holds the seat's run lock (it is running or closing a shift)."""
    f = _try_lock(_lock_path(shipdir, seat, "lock"))
    if f is None:
        return True
    _release(f)
    return False


def wait_idle(shipdir: Path, seat: str, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if not running(shipdir, seat):
            return True
        time.sleep(IDLE_POLL)
    return False


# --- send -> wrapper -----------------------------------------------------------

def spawn(shipdir: Path, seat: str) -> None:
    log = inbox.seat_dir(shipdir, seat) / "headless" / "wrapper.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a", encoding="utf-8") as f:
        procs.spawn_detached([sys.executable, str(YAMATO_BIN), "run-headless", str(shipdir), seat],
                             env=claude.seat_env(), stdout=f, stderr=f)


def wake(shipdir: Path, team: dict, seat: str) -> tuple[str, dict]:
    """Start the wrapper and return at once. Always spawned, even while a shift runs:
    the running wrapper only looks at the inbox before it ends, so a message landing
    after that look is picked up by this one (it waits for the lock, §4.3)."""
    busy = running(shipdir, seat)
    spawn(shipdir, seat)
    return ("queued" if busy else "spawned"), roster.seat(shipdir, seat)


def terminate(shipdir: Path, seat: str, reason: str, timeout: float = 30) -> bool:
    """``down --force`` / grace exceeded: SIGTERM the seat's ``claude -p``. The wrapper
    records the end (with ``reason``); returns True once it has let go of the seat."""
    rec = roster.seat(shipdir, seat)
    pid = rec.get("pid")
    if pid and rec.get("state") in (roster.ON_SHIFT, roster.STOPPING):
        roster.update(shipdir, seat, forceStop=reason)
        # Windows の soft_stop は CTRL_BREAK。pid が再利用されていると同じコンソールの別プロセスを落とすので、
        # pidStart で確かめられた pid にしか送らない
        if not procs.is_windows() or live_pid(rec):
            procs.soft_stop(pid, group=_grouped(rec))
    return wait_idle(shipdir, seat, timeout)


def _grouped(rec: dict) -> bool:
    """起動のとき group_kwargs を使った (= pid が自分のグループの先頭) と roster に記録された pid か。
    印が無い pid に Windows で CTRL_BREAK を送るとコンソール全体に届くので、soft_stop は hard_kill に回す。"""
    return rec.get("group") is True


def live_pid(rec: dict) -> int | None:
    """The shift's ``claude -p`` if it is still alive. The pid alone may have been reused:
    it counts only if its command line carries the shift's ``--session-id``."""
    pid, sid = rec.get("pid"), rec.get("sessionId")
    if not pid or not sid:
        return None
    if procs.is_windows():
        # Git Bash の ps には -o が無い。pid + 起動時刻 (roster の pidStart) で pid の再利用を見分ける
        # 起動時刻の印 (pidStart) が無い pid は、yamato が CREATE_NEW_PROCESS_GROUP で起こしたと確かめられない。
        # 生きた claude -p とみなすと stop_orphan が CTRL_BREAK を送り、同じコンソールの全員 (owner のシェル) を落とす
        started = rec.get("pidStart")
        if started is None or not procs.pid_alive(pid) or procs.start_time(pid) != started:
            return None
        return int(pid)
    try:
        cp = subprocess.run(["ps", "-p", str(int(pid)), "-o", "command="], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return int(pid) if cp.returncode == 0 and sid in cp.stdout else None


def stop_orphan(shipdir: Path, seat: str, reason: str, timeout: float = 10) -> bool:
    """The wrapper is gone (killed, crashed) but its ``claude -p`` still runs: nothing holds
    its time limit any more (safety net). SIGTERM it (SIGKILL after ``timeout``). The caller
    closes the shift. Returns True if there was one."""
    rec = roster.seat(shipdir, seat)
    if rec.get("state") not in (roster.ON_SHIFT, roster.STOPPING) or running(shipdir, seat):
        return False
    pid = live_pid(rec)
    if pid is None:
        return False
    procs.terminate(pid, timeout, alive=lambda: live_pid(rec) is not None, group=_grouped(rec))
    append_log(shipdir, seat, f"headless: ラッパーが居ないまま claude -p (pid {pid}) が残っていたので止めた ({reason})")
    events.emit(shipdir, events.FORCE_STOP, seat=seat, summary=f"孤児の claude -p を停止 ({reason})",
                data={"reason": reason, "shiftNo": rec.get("shiftNo"), "sessionId": rec.get("sessionId"),
                      "pid": pid, "orphan": True})
    roster.update(shipdir, seat, pid=None)
    return True


# --- the wrapper -----------------------------------------------------------------

def run(shipdir: Path, seat: str) -> int:
    """``yamato run-headless <ship> <seat>``."""
    from .seat import current_team

    team = current_team(shipdir)
    if seat_spec(team, seat)["shift"] != "headless":
        raise YamatoError(f"席 {seat} は shift: headless ではありません")
    lock = _try_lock(_lock_path(shipdir, seat, "lock"))
    waited = lock is None
    if waited:
        # one waiter is enough: it looks at the inbox after taking the run lock
        wait = _try_lock(_lock_path(shipdir, seat, "wait"))
        if wait is None:
            return 0
        try:
            lock = _lock(_lock_path(shipdir, seat, "lock"))
        finally:
            _release(wait)
    try:
        if waited and not inbox.unread(shipdir, seat):
            return 0
        while True:
            if deadline.phase(deadline.read(shipdir)) != deadline.RUNNING:
                append_log(shipdir, seat, "headless: 艦が稼働中でないのでシフトを起こさない (inbox は次に起動したときに読まれる)")
                return 0
            res = run_shift(shipdir, team, seat)
            if res["outcome"] == FAILED:
                return 1   # no automatic retry (§4.4)
            late = [e for e in inbox.unread(shipdir, seat) if e.get("ts", 0) >= res["startedAt"]]
            if not late:
                return 0
            append_log(shipdir, seat, f"headless: シフト中に届いた未読 {len(late)} 件があるので次のシフトを起こす")
            team = current_team(shipdir)
    finally:
        _release(lock)


def first_prompt(shipdir: Path, seat: str) -> str:
    y = yamato_invocation()
    return (f"[yamato] headless のシフト開始 (この 1 回の実行で終わります)。SessionStart で注入された引き継ぎ・"
            f"自分の担当・未読 inbox を確認し、役割どおりに仕事を進めてください。未読の続きは "
            f"`{y} inbox {ship_arg(shipdir)} {seat}` で読めます。終える前に handoff.md "
            f"({inbox.seat_dir(shipdir, seat) / 'handoff.md'}) を上書きし、`{y} seat-stop {ship_arg(shipdir)} {seat}` を"
            f"実行してから、このターンを短く終えてください。")


class _Stream:
    """Reads the stream-json output line by line, saves it, and keeps what the wrapper needs."""

    def __init__(self, src, dest: Path):
        self.src, self.dest = src, dest
        self.hook_ok = False       # system/hook_response for SessionStart (verify-p1-d V1)
        self.init = False          # system/init: flows right after the SessionStart hook
        self.rate_limit = None     # the last rate_limit_event's rate_limit_info (§4.4)
        self.result = None         # the final `result` line (absent after SIGTERM, V4)
        self.message_ids: set = set()

    def __call__(self) -> None:
        with open(self.dest, "a", encoding="utf-8", newline="\n") as out:
            for line in self.src:
                out.write(line)
                out.flush()
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(e, dict):
                    continue
                t = e.get("type")
                if t == "system" and e.get("subtype") == "hook_response" and e.get("hook_event") == "SessionStart":
                    self.hook_ok = True
                elif t == "system" and e.get("subtype") == "init":
                    self.init = True
                elif t == "rate_limit_event":
                    self.rate_limit = e.get("rate_limit_info")
                elif t == "assistant" and isinstance(e.get("message"), dict) and e["message"].get("id"):
                    self.message_ids.add(e["message"]["id"])
                elif t == "result":
                    self.result = e


def _time_up(shipdir: Path, started: float, max_duration: int | None, now: float) -> str | None:
    """Re-read every poll: ``down`` moves the grace end earlier, ``up`` moves it later."""
    if max_duration and now >= started + max_duration:
        return "max-duration"
    dl = deadline.read(shipdir)
    if dl and now >= dl["graceUntil"]:
        return "grace-exceeded"
    return None


def run_shift(shipdir: Path, team: dict, seat: str) -> dict:
    from . import seat as seat_mod

    spec = seat_spec(team, seat)
    role = team["roles"][spec["role"]]
    sid = str(uuid.uuid4())
    name = seat_mod.session_name(team, seat)
    cwd = roster.seat(shipdir, seat).get("nextCwd")   # send --cwd (design-p1 §8.2 の 2), used up here
    if cwd:
        roster.update(shipdir, seat, nextCwd=None)
    rec = roster.start_shift(shipdir, seat, session_id=sid, short_id=sid[:8], session_name=name, how="headless",
                             cwd=cwd)
    started, no = rec["shiftStartedAt"], rec["shiftNo"]
    seat_mod.clear_pending(shipdir, seat)
    out_dir = inbox.seat_dir(shipdir, seat) / "headless"
    out_dir.mkdir(parents=True, exist_ok=True)
    argv = claude.headless_argv(
        session_id=sid, name=name, role=spec["role"],
        agents_json=runtime.agents_path(shipdir).read_text(encoding="utf-8"),
        model=spec["model"], settings=str(runtime.settings_path(shipdir, seat)),
        add_dir=str(shipdir), prompt=first_prompt(shipdir, seat), max_budget_usd=role.get("max_budget_usd"),
    )
    append_log(shipdir, seat, f"シフト開始 #{no} (headless) session={sid}" + (f" cwd={cwd}" if cwd else ""))
    stream = _Stream([], out_dir / f"shift-{no}.jsonl")
    killed, kill_reason, rc, launch_error = None, None, None, None
    try:
        with open(out_dir / f"shift-{no}.stderr", "a", encoding="utf-8") as err:
            env = claude.seat_env([*claude.PRINT_CALLER_ENV, *(team.get("env_unset") or ())])
            proc = subprocess.Popen(argv, cwd=cwd or team["workspace"], env=env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=err,
                                    text=True, encoding="utf-8", errors="replace", **procs.group_kwargs())
    except OSError as e:
        launch_error = f"claude -p を起動できない: {e}"
    else:
        roster.update(shipdir, seat, pid=proc.pid, pidStart=procs.start_time(proc.pid), group=True,
                      wrapperPid=os.getpid(), forceStop=None)
        stream.src = proc.stdout
        reader = threading.Thread(target=stream, daemon=True)
        reader.start()
        restore = _forward_signals(shipdir, seat, proc)
        try:
            killed, kill_reason = _watch(shipdir, seat, proc, role, started, no, sid)
        finally:
            restore()
        reader.join(timeout=10)
        proc.stdout.close()
        rc = proc.returncode
        forced = roster.seat(shipdir, seat).get("forceStop")
        if killed is None and forced:
            killed, kill_reason = time.time(), forced
            events.emit(shipdir, events.FORCE_STOP, seat=seat, summary=f"強制停止 ({forced})",
                        data={"reason": forced, "shiftNo": no, "sessionId": sid})
    return _close(shipdir, team, seat, sid=sid, no=no, started=started, stream=stream,
                  rc=rc, killed=kill_reason if killed else None, launch_error=launch_error)


def _forward_signals(shipdir: Path, seat: str, proc):
    """SIGTERM / SIGINT to the wrapper go on to its ``claude -p``, and the wrapper still
    closes the shift: a stopped wrapper must not leave a -p running without its time limit.
    (A SIGKILLed wrapper cannot forward; ``stop_orphan`` is the net for that.)"""
    if threading.current_thread() is not threading.main_thread():
        return lambda: None

    def forward(signum, _frame):
        roster.update(shipdir, seat, forceStop="wrapper-signal")
        if proc.poll() is None:
            procs.soft_stop(proc.pid)

    # Windows: 切り離したラッパーに届くのは CTRL_BREAK (= SIGBREAK)
    sigs = [signal.SIGTERM, signal.SIGINT, *([signal.SIGBREAK] if hasattr(signal, "SIGBREAK") else [])]
    old = {sig: signal.signal(sig, forward) for sig in sigs}

    def restore():
        for sig, h in old.items():
            signal.signal(sig, h)
    return restore


def _watch(shipdir: Path, seat: str, proc, role: dict, started: float, no: int, sid: str):
    """Wait for claude -p, sending SIGTERM at the time limit. Returns (killed at, reason)."""
    killed, kill_reason = None, None
    while proc.poll() is None:
        now = time.time()
        if killed is None:
            kill_reason = _time_up(shipdir, started, role.get("max_duration"), now)
            if kill_reason:
                procs.soft_stop(proc.pid)
                killed = now
                append_log(shipdir, seat, f"headless: 時間切れ ({kill_reason}) → SIGTERM")
                events.emit(shipdir, events.FORCE_STOP, seat=seat, summary=f"時間切れで停止 ({kill_reason})",
                            data={"reason": kill_reason, "shiftNo": no, "sessionId": sid})
        elif now - killed > KILL_WAIT:
            procs.hard_kill(proc.pid)
        try:
            proc.wait(timeout=POLL)   # a POLL-long look, cut short when claude -p ends
        except subprocess.TimeoutExpired:
            pass
    return killed, kill_reason


def _failures(stream: _Stream, rc, killed, launch_error, need_hook: bool = True) -> list[tuple[str, str]]:
    """[(what, detail)]: ``what`` goes into the report, ``detail`` (may quote Claude's text) only into records.
    ``need_hook=False``: a run without seat hooks (the memory curate shift, design-p1 §3.3)."""
    if launch_error:
        return [("起動できない", launch_error)]
    out = []
    # a run stopped before it got going says nothing about the hook
    if need_hook and not stream.hook_ok and (stream.init or not killed):
        out.append(("SessionStart hook なし",
                    "SessionStart hook が走った印 (hook_response) がない。--bare 化などで記録を読まずに働いた可能性"))
    res = stream.result
    if res:
        status = res.get("api_error_status")
        if res.get("is_error") or status is not None or res.get("terminal_reason") == "api_error":
            limit_like = status == 429 or "limit" in str(res.get("result") or "").lower()
            what = "API エラー" + (f" {status}" if status is not None else "") + (" (枠切れの可能性)" if limit_like else "")
            out.append((what, f"{what}: terminal_reason={res.get('terminal_reason')} "
                              f"result={str(res.get('result') or '')[:200]}"))
    elif not killed:
        out.append((f"結果なしで終了 exit {rc}", f"result の行がないまま終了した (exit {rc})"))
    return out


def _usage_line(seat: str, role: str, sid: str, no: int, started: float, ended: float,
                stream: _Stream, outcome: str) -> dict:
    res = stream.result
    if res and isinstance(res.get("usage"), dict):
        tokens = {k: int(res["usage"].get(k) or 0) for k in usage.KEYS}
        extra = {"messages": len(stream.message_ids), "models": sorted(res.get("modelUsage") or {}),
                 "source": "result"}
    else:
        # SIGTERM leaves no result line: count the transcript, deduplicated by message.id (V4, V9)
        t = usage.count(claude.transcript_paths(sid), started, ended)
        tokens = {k: t[k] for k in usage.KEYS}
        extra = {"messages": t["messages"], "models": t["models"], "source": "transcript",
                 **({} if t["read"] else {"unknown": True})}
    return {
        "ts": ended, "seat": seat, "shiftNo": no, "sessionId": sid, "startedAt": started, "endedAt": ended,
        **tokens, **extra, "total_tokens": sum(tokens.values()),
        "shift": "headless", "role": role, "outcome": outcome,
        "total_cost_usd": res.get("total_cost_usd") if res else None,
        "num_turns": res.get("num_turns") if res else None,
        "duration_ms": res.get("duration_ms") if res else None,
        "rateLimit": stream.rate_limit,
    }


def _close(shipdir: Path, team: dict, seat: str, *, sid: str, no: int, started: float, stream: _Stream,
           rc, killed: str | None, launch_error: str | None) -> dict:
    from . import seat as seat_mod

    spec = seat_spec(team, seat)
    failures = _failures(stream, rc, killed, launch_error)
    by_seat = roster.seat(shipdir, seat).get("state") == roster.STOPPING   # seat-stop was accepted
    written = seat_mod.handoff_written_since(shipdir, seat, started)
    if failures:
        outcome, reason = FAILED, "failed"
    elif killed:
        # max-duration / grace-exceeded: the wrapper's own limit; down-force: from outside
        outcome, reason = (FORCED if killed == "down-force" else TIMEOUT), killed
    elif by_seat:
        outcome, reason = OK, "seat-stop"
    else:
        outcome, reason = NO_HANDOFF, "exited"
    notes = [f"{FAILED}: " + " / ".join(w for w, _ in failures)] if failures else []
    if killed:
        notes.append(f"{FORCED if killed == 'down-force' else TIMEOUT} ({killed})")
    if not by_seat:
        notes.append(roster.NO_HANDOFF_NOTE)
    note = "、".join(notes) or None

    ended = time.time()
    line = _usage_line(seat, spec["role"], sid, no, started, ended, stream, outcome)
    usage.append(shipdir, line)
    if failures:
        res = stream.result or {}
        events.emit(shipdir, events.SHIFT_FAILED, seat=seat, summary=f"headless シフト #{no} の異常: "
                    + " / ".join(w for w, _ in failures),
                    data={"shiftNo": no, "sessionId": sid, "exitCode": rc, "failures": [d for _, d in failures],
                          "is_error": res.get("is_error"), "api_error_status": res.get("api_error_status"),
                          "terminal_reason": res.get("terminal_reason")})
        for _, detail in failures:
            append_log(shipdir, seat, f"headless: 異常: {detail}")
    roster.update(shipdir, seat, pid=None)
    roster.end_shift(shipdir, seat, reason=reason, handoff_written=written, note=note,
                     extra={"outcome": outcome, "exitCode": rc, "rateLimit": stream.rate_limit})
    append_log(shipdir, seat, f"シフト終了 #{no} (headless, {outcome}, exit {rc})" + (f" {note}" if note else ""))
    append_log(shipdir, seat, _summary(line))

    brd = board_mod.Board(shipdir, team)
    items = [m for m in brd.mine(seat) if m.get("state") == "active"]
    last = " ".join(str((stream.result or {}).get("result") or "").split())
    if not by_seat and last:
        for m in items:
            brd.set(m["id"], {}, by=seat,
                    note=f"headless シフト #{no} は引き継ぎなしで終了。最後の応答: {last[:NOTE_CHARS]}"
                         + ("…" if len(last) > NOTE_CHARS else ""))
    _report(shipdir, team, seat, _report_text(shipdir, brd, seat, no, outcome, failures, killed, items))
    return {"outcome": outcome, "startedAt": started, "sessionId": sid, "shiftNo": no}


def _summary(line: dict) -> str:
    cost = line.get("total_cost_usd")
    if line.get("unknown"):
        return f"使用量 shift#{line['shiftNo']}: 分からない (result の行が無く、transcript も読めなかった)"
    return (f"使用量 shift#{line['shiftNo']} ({line['source']}): in={line['input_tokens']} out={line['output_tokens']} "
            f"cache_write={line['cache_creation_input_tokens']} cache_read={line['cache_read_input_tokens']} "
            f"(計 {line['total_tokens']}, {line['messages']} messages, turns={line.get('num_turns')}, "
            f"cost={'-' if cost is None else f'${cost:.4f}'})")


def _report_text(shipdir: Path, brd, seat: str, no: int, outcome: str, failures, killed, items) -> str:
    """yamato's fixed form: nothing the seat wrote goes in (design-p1 §4.2, §7.2)."""
    ids = ", ".join(m["id"] for m in items) or "担当の active な項目なし"
    detail = ""
    if failures:
        detail = ": " + " / ".join(w for w, _ in failures)
    elif killed:
        detail = f": {killed}"
    paths = ", ".join(str(brd.read(m["id"])[2]) for m in items) or "-"
    return (f"[yamato] {seat} の headless シフト #{no} が終了 ({ids}, {outcome}{detail})。"
            f"項目ファイル: {paths}。作業ログ: {inbox.seat_dir(shipdir, seat) / 'log'}")


def _report(shipdir: Path, team: dict, seat: str, text: str) -> None:
    """Like ``send`` from yamato. Not ``seat.send`` itself: past the grace end it would
    force-stop seats, and wait on this very seat's run lock."""
    from . import seat as seat_mod

    to = team["roles"][seat_spec(team, seat)["role"]].get("report_to") or team["hub"]
    if to == seat:
        # a report into its own inbox would count as mail that came in during the shift
        append_log(shipdir, seat, "headless: 報告先がこの席自身なので終了報告は送らない")
        return
    try:
        entry = inbox.append(shipdir, to, REPORTER, text)
        seat_mod._send_event(shipdir, to, REPORTER, entry)
        append_log(shipdir, seat, f"headless: 終了報告を {to} に送った (inbox #{entry['n']})")
        if to == inbox.OWNER:
            for line in notify.notify(team, f"yamato {team['name']}: {seat}", text):
                append_log(shipdir, seat, f"headless: {line}")
        elif deadline.phase(deadline.read(shipdir)) == deadline.RUNNING:
            what, _ = seat_mod.wake(shipdir, team, to)
            append_log(shipdir, seat, f"headless: 報告先 {to}: {what}")
    except (YamatoError, OSError) as e:
        append_log(shipdir, seat, f"headless: 終了報告を {to} に送れなかった: {e}")

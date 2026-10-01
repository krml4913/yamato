"""Hook entry points (``yamato hook <event> <ship> <seat>``), called by the seats every turn.

Kept light: standard library plus yamato's small modules, no YAML.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import deadline, events, inbox, inject, procs, roster
from .team import runtime_team
from .runtime import ship_arg, yamato_invocation
from .util import YamatoError, append_log, read_json, ship_lock, write_json

MAX_WRAPUP_NOTICES = 3   # per shift, Stop and PreToolUse together; then the grace-period force stop takes over
WAIT_POLL = 5            # seconds between checks (deadline, inbox) in the async watcher
LIVENESS_EVERY = 12      # a watcher with no deadline asks ``claude agents`` whether its session lives every this many polls
FORCE_STOP_AFTER = 5     # seconds from the hook to ``claude stop``: the denied call and the turn end first
FORCE_STOP_RETRY = 60    # a seat still calling hooks this long after its forced stop gets another one

FORCE_DENY_MESSAGE = (
    "[yamato] 艦の稼働時間の上限と猶予を過ぎました。ツールはもう使えません。"
    "このセッションは間もなく yamato が止めます。短い一言でこのターンを終えてください。"
)

INBOX_WAKE_MESSAGE = (
    "[yamato] inbox に未読があります ({count} 件、{senders} から)。"
    "`{yamato} inbox {ship} {seat}` で読んで対応してください。"
)


def _stdin_json() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return {}


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()


def _touch(shipdir: Path, seat: str) -> None:
    """roster ``lastActive`` (design-p1 §5.1). A failure must not cost the hook its real job."""
    try:
        roster.touch(shipdir, seat)
    except OSError as e:
        sys.stderr.write(f"yamato: lastActive を更新できませんでした: {e}\n")


def session_start(shipdir: Path, seat: str) -> int:
    """The records (hook A). The role's memory and knowledge.md are the other hook
    (``session_start_knowledge``): Claude Code caps each hook at 10,000 characters (verify-p0-c Q1)."""
    data = _stdin_json()
    _touch(shipdir, seat)
    source = data.get("source") or "startup"
    team = runtime_team(shipdir)
    notice = _last_call_notice(shipdir, team, seat, "SessionStart")
    text, cursor_to = inject.build(shipdir, team, seat, source, notice=notice)
    inbox.mark_read(shipdir, seat, cursor_to)
    append_log(shipdir, seat, f"SessionStart ({source}) session={data.get('session_id', '?')}")
    _emit({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}})
    return 0


def session_start_knowledge(shipdir: Path, seat: str) -> int:
    """The role's memory and knowledge.md (hook B). Nothing on stdout when the seat reads neither."""
    _stdin_json()
    text = inject.build_knowledge(shipdir, runtime_team(shipdir), seat)
    if text:
        _emit({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}})
    return 0


def _notice_path(shipdir: Path, seat: str) -> Path:
    return Path(shipdir) / ".runtime" / f"wrapup-{seat}.json"


def take_wrapup_notice(shipdir: Path, seat: str, shift_no) -> bool:
    """Count wrap-up notices per shift so a seat that ignores them is not nudged forever."""
    path = _notice_path(shipdir, seat)
    with ship_lock(shipdir):   # the Stop hook and the async watcher may count at once
        rec = read_json(path, {}) or {}
        if rec.get("shiftNo") != shift_no:
            rec = {"shiftNo": shift_no, "count": 0}
        if rec["count"] >= MAX_WRAPUP_NOTICES:
            return False
        rec["count"] += 1
        write_json(path, rec)
        return True


def take_pre_tool_use_notice(shipdir: Path, seat: str, shift_no) -> bool:
    """PreToolUse's own share of the wrap-up budget (design-drift nit B): a turn that makes
    several tool calls (the seat's own wrap-up -- ``Write`` the handoff, then ``yamato
    seat-stop``) fires PreToolUse once per call, but should only be nudged once per turn --
    otherwise the whole shift's budget (``MAX_WRAPUP_NOTICES``) is spent on a single
    compliant turn before a genuinely idle/non-compliant one ever gets nudged. The Stop hook
    marks the end of every turn and clears ``turnNotified`` (``_reset_turn_notice``), so the
    next turn's first PreToolUse call can nudge again -- still inside the shared, per-shift
    count that ``take_wrapup_notice`` (Stop) also spends from."""
    path = _notice_path(shipdir, seat)
    with ship_lock(shipdir):
        rec = read_json(path, {}) or {}
        if rec.get("shiftNo") != shift_no:
            rec = {"shiftNo": shift_no, "count": 0}
        if rec.get("turnNotified") or rec["count"] >= MAX_WRAPUP_NOTICES:
            return False
        rec["count"] += 1
        rec["turnNotified"] = True
        write_json(path, rec)
        return True


def _reset_turn_notice(shipdir: Path, seat: str, shift_no) -> None:
    """Every Stop hook call marks the end of a turn: let the next turn's first
    PreToolUse call notify again (``take_pre_tool_use_notice``)."""
    path = _notice_path(shipdir, seat)
    with ship_lock(shipdir):
        rec = read_json(path, {}) or {}
        if rec.get("shiftNo") == shift_no and rec.get("turnNotified"):
            rec["turnNotified"] = False
            write_json(path, rec)


def _needs_wrapup(shipdir: Path, seat: str) -> tuple[bool, dict]:
    rec = roster.seat(shipdir, seat)
    if rec.get("state") in (roster.STOPPING, roster.OFF):
        return False, rec
    return deadline.phase(deadline.read(shipdir)) in (deadline.OVER, deadline.FORCE), rec


def _force_stop_path(shipdir: Path, seat: str) -> Path:
    return Path(shipdir) / ".runtime" / f"force-stop-{seat}.json"


def force_stop_self(shipdir: Path, seat: str, session_id: str | None, where: str) -> bool:
    """Past deadline + grace, the seat's own hooks stop it, whether or not the watchdog
    is still there (§0 B4): the delayed stop ``seat-stop`` uses, once per shift (a
    persistent seat resumes under the same session id), again if the stop did not take.

    A headless shift is left to its wrapper, which holds the time limit and ends
    ``claude -p`` itself (the hooks only deny its tools). Returns whether a stop was set."""
    team = runtime_team(shipdir)
    spec = team["seats"].get(seat)
    if spec is None or spec["shift"] == "headless":
        return False
    rec = roster.seat(shipdir, seat)
    sid = session_id or rec.get("sessionId")
    if not sid:
        return False
    path = _force_stop_path(shipdir, seat)
    mark = {"sessionId": sid, "shiftNo": rec.get("shiftNo")}
    with ship_lock(shipdir):   # parallel tool calls run their PreToolUse hooks at once
        last = read_json(path, {}) or {}
        if {k: last.get(k) for k in mark} == mark and time.time() - (last.get("at") or 0) < FORCE_STOP_RETRY:
            return False
        write_json(path, {**mark, "at": time.time()})
    from . import seat as seat_mod

    seat_mod.spawn_delayed_stop(shipdir, seat, sid, FORCE_STOP_AFTER, forced=True)
    append_log(shipdir, seat, f"{where}: 猶予を過ぎた → {FORCE_STOP_AFTER} 秒後に強制停止 session={sid}")
    return True


def _wrapup_message(shipdir: Path, seat: str) -> str:
    return deadline.WRAP_UP_MESSAGE.format(yamato=yamato_invocation(), ship=ship_arg(shipdir), seat=seat)


def _last_call_notice(shipdir: Path, team: dict, seat: str, where: str) -> str | None:
    """The captain's first turn past the last call gets the notice, once (design-p1 §9).
    Whichever hook sees that turn first (SessionStart / UserPromptSubmit / Stop) gives it."""
    if seat != team["hub"]:
        return None
    dl = deadline.take_last_call_notice(shipdir)
    if dl is None:
        return None
    left = deadline.left(dl)
    append_log(shipdir, seat, f"{where} hook: 最終受付を過ぎた (終了まで {left}) → 注意を注入")
    events.emit(shipdir, events.LAST_CALL, seat=seat, summary=f"最終受付の注意 (終了まで {left})",
                data={"lastCallAt": dl["lastCallAt"], "deadline": dl["deadline"], "hook": where})
    return deadline.LAST_CALL_NOTICE.format(left=left)


def _rotate_notice(shipdir: Path, team: dict, seat: str, rec: dict, data: dict) -> str | None:
    """A live persistent seat past its ``rotate:`` conditions is told once per shift to
    ``seat-stop --rotate`` at the next break (design-p1 §5.4). A nudge, never a stop."""
    from . import rotate

    if team["seats"][seat]["shift"] != "persistent" or rec.get("state") != roster.ON_SHIFT:
        return None
    if rec.get("rotateNoticeShift") == rec.get("shiftNo"):
        return None
    reasons = rotate.live_reasons(team, seat, rec, data.get("transcript_path"))
    if not reasons or not rotate.take_notice(shipdir, seat, rec.get("shiftNo")):
        return None
    append_log(shipdir, seat, f"Stop hook: 入れ替えの条件 ({', '.join(reasons)}) → seat-stop --rotate を促す")
    events.emit(shipdir, events.ROTATE_SUGGESTED, seat=seat, summary=f"入れ替えを促した: {', '.join(reasons)}",
                data={"shiftNo": rec.get("shiftNo"), "reasons": reasons})
    return rotate.MESSAGE.format(reasons=", ".join(reasons), yamato=yamato_invocation(), ship=ship_arg(shipdir), seat=seat)


def stop(shipdir: Path, seat: str) -> int:
    """Past the deadline, block the end of the turn with the wrap-up order (§0 B4);
    past the grace period too, let the turn end and stop the seat. Otherwise, once
    each: the captain's last-call notice (design-p1 §9) and the nudge to rotate
    (§5.4). At most one block per turn end. Also marks the turn's end for
    PreToolUse's own notice (``_reset_turn_notice``, design-drift nit B)."""
    data = _stdin_json()
    _touch(shipdir, seat)
    if deadline.phase(deadline.read(shipdir)) == deadline.FORCE:
        force_stop_self(shipdir, seat, data.get("session_id"), "Stop hook")
        return 0
    need, rec = _needs_wrapup(shipdir, seat)
    _reset_turn_notice(shipdir, seat, rec.get("shiftNo"))   # this turn is ending either way
    if data.get("stop_hook_active"):
        return 0
    if need:
        if take_wrapup_notice(shipdir, seat, rec.get("shiftNo")):
            append_log(shipdir, seat, "Stop hook: 稼働時間の上限 → 終業を指示")
            _emit({"decision": "block", "reason": _wrapup_message(shipdir, seat)})
        return 0
    if rec.get("state") != roster.ON_SHIFT:
        return 0
    team = runtime_team(shipdir)
    if seat not in team["seats"]:
        return 0
    reason = _last_call_notice(shipdir, team, seat, "Stop") or _rotate_notice(shipdir, team, seat, rec, data)
    if reason:
        _emit({"decision": "block", "reason": reason})
    return 0


def pre_tool_use(shipdir: Path, seat: str) -> int:
    """Before every tool call (§0 B4): a seat that keeps working inside one long turn never
    reaches its Stop hook. Past the deadline the tool runs with the wrap-up order beside it
    (counted with the Stop hook's); past the grace period it is denied and the seat stopped.

    At most one notice per turn (design-drift nit B): the seat's own wrap-up (``Write`` the
    handoff, then ``yamato seat-stop``) is several tool calls in the same turn, so only the
    first gets the notice (``take_pre_tool_use_notice``); once ``roster`` shows ``STOPPING``
    (``seat-stop`` accepted), ``_needs_wrapup`` is already false and nothing more is said.

    The ``yamato`` entry runs ``pretool.main`` first, which returns while the ship is running
    without importing this module."""
    data = _stdin_json()
    phase = deadline.phase(deadline.read(shipdir))
    if phase == deadline.FORCE:
        try:
            force_stop_self(shipdir, seat, data.get("session_id"), "PreToolUse hook")
        except Exception as e:  # noqa: BLE001 — the deny below must hold even so
            sys.stderr.write(f"yamato: 強制停止を仕掛けられませんでした: {e}\n")
        _emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                      "permissionDecisionReason": FORCE_DENY_MESSAGE}})
        return 0
    if phase != deadline.OVER:
        return 0
    need, rec = _needs_wrapup(shipdir, seat)
    if need and take_pre_tool_use_notice(shipdir, seat, rec.get("shiftNo")):
        append_log(shipdir, seat, "PreToolUse hook: 稼働時間の上限 → 終業を指示")
        _emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                      "additionalContext": _wrapup_message(shipdir, seat)}})
    return 0


def user_prompt_submit(shipdir: Path, seat: str) -> int:
    """A turn starts (a prompt, or a SendMessage delivered as one): the seat is moving.
    The captain's first turn past the last call gets the notice (design-p1 §9)."""
    _stdin_json()
    _touch(shipdir, seat)
    dl = deadline.read(shipdir)
    if not deadline.in_last_call(dl) or dl.get("lastCallNoticed") == dl.get("lastCallAt"):
        return 0   # the usual case: no team to read on every prompt
    team = runtime_team(shipdir)
    notice = _last_call_notice(shipdir, team, seat, "UserPromptSubmit") if seat in team["seats"] else None
    if notice:
        _emit({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": notice}})
    return 0


def pre_compact(shipdir: Path, seat: str) -> int:
    """The context is about to be summarised: mark the shift for rotation (design-p1 §5.4)."""
    from . import rotate

    data = _stdin_json()
    rotate.mark_compacted(shipdir, seat)
    append_log(shipdir, seat, f"PreCompact ({data.get('trigger') or '?'}): 入れ替えの印を付けた")
    return 0


def _pid_alive(pid: int) -> bool:
    return procs.pid_alive(pid)


def _inbox_wake_path(shipdir: Path, seat: str) -> Path:
    return Path(shipdir) / ".runtime" / f"inbox-wake-{seat}.json"


def _inbox_stat(path: Path) -> list | None:
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return [st.st_mtime_ns, st.st_size]


def take_inbox_wake(shipdir: Path, seat: str, seats) -> list[dict]:
    """Unread inbox entries from outside the seats (owner, yamato's fixed texts, ...),
    each returned once. A seat sender delivers by SendMessage itself (§0 B1), so its
    entries are left alone: waking for them too would wake the seat twice.

    Polled every ``WAIT_POLL`` seconds by the idle watcher (#9): most polls find
    inbox.jsonl untouched since the last one, so its mtime/size are compared without
    the ship lock first (shared with ``inbox.append``, so nothing can change it
    unseen while we look), and the lock is only taken when they differ. Once inside,
    only the bytes appended since the last poll are read (seek), not the whole file."""
    ipath = inbox.path(shipdir, seat)
    wake_path = _inbox_wake_path(shipdir, seat)
    sig = _inbox_stat(ipath)
    state = read_json(wake_path, {}) or {}
    if state.get("sig") == sig:
        return []   # inbox.jsonl はロックなしで見た限り前回と変わっていない
    with ship_lock(shipdir):
        sig = _inbox_stat(ipath)   # ロックの中で確定させる (append と競らない)
        offset = state.get("offset", 0)
        size = sig[1] if sig else 0
        if size < offset:
            offset = 0   # 想定外の縮小 → 読み直す
        new_text = ""
        if ipath.exists():
            with open(ipath, "r", encoding="utf-8") as f:
                f.seek(offset)
                new_text = f.read()
                offset = f.tell()
        cursor = inbox.cursor(shipdir, seat)
        pending = [e for e in state.get("pending", []) if e.get("n", 0) > cursor]
        for line in new_text.splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue  # a torn line never blocks the rest (inbox.entries does the same)
            if e.get("n", 0) > cursor:
                pending.append(e)
        woken = state.get("n", 0)
        news = [e for e in pending if e.get("n", 0) > woken and e.get("from") not in seats]
        if news:
            woken = max(woken, max(e["n"] for e in news))
        write_json(wake_path, {"n": woken, "sig": sig, "offset": offset, "pending": pending})
        return news


def _inbox_wake_message(shipdir: Path, seat: str, news: list[dict]) -> str:
    senders = "・".join(dict.fromkeys(str(e.get("from")) for e in news))
    return INBOX_WAKE_MESSAGE.format(count=len(news), senders=senders, yamato=yamato_invocation(), ship=ship_arg(shipdir), seat=seat)


def _pidfile_text(pidfile: Path) -> str | None:
    try:
        return pidfile.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None


def _session_gone(session_id: str | None) -> bool:
    """True only when ``claude agents`` answers and shows no live pid for the session.

    An answer we cannot get (claude missing, the listing failing) is "unknown", never
    "gone": a watcher that quit on a hiccup would leave the idle seat deaf."""
    if not session_id:
        return False
    from . import claude

    try:
        return not claude.is_alive(claude.find(session_id))
    except (OSError, YamatoError, subprocess.SubprocessError):
        return False


def wait_deadline(shipdir: Path, seat: str) -> int:
    """Async + asyncRewake Stop hook: wake the idle seat at the deadline, or when its
    inbox gets an entry nobody will SendMessage (the owner's, yamato's; e2e-p1 C).

    Exit 2 with the message on stderr wakes the seat (verify-p0-a Q4). A
    pidfile keeps one watcher per seat, since every turn end spawns another.

    A ship with ``time_limit: none`` (D-013, the admiral only, T-020) never has a
    deadline to read. This used to make the watcher return at once and never look at
    the inbox either, so an idle admiral could not be woken by ``yamato send`` /
    ``yamato admiral --stop`` (T-021). It now keeps polling for an outside sender's
    unread message forever when there is no deadline at all (nothing here ever force-stops
    such a ship: only a normal deadline does that).

    A watcher also leaves when it is stale (T-038): the seat's ``shiftNo`` moved on
    (the next shift has its own watcher) or the pidfile now names another watcher.
    With no deadline nothing else ends it, so it also checks every
    ``LIVENESS_EVERY`` polls that its own session is still in ``claude agents`` (a
    ``claude stop`` by hand, a crash: neither passes ``seat-stop`` nor
    ``force_stop_all``, so the roster still says on_shift). Without this a dead
    session's watcher kept polling and, pidfile held, made the next shift's watcher
    return at once, then woke the dead session and used up the wake."""
    data = _stdin_json()
    try:
        team = runtime_team(shipdir)
        seats = set(team["seats"])
        # a runtime team.json without the key is a normal ship (older json, hand-edited): only an explicit null is "none"
        no_limit = "time_limit" in team and team["time_limit"] is None
        headless = team["seats"].get(seat, {}).get("shift") == "headless"
    except (OSError, YamatoError, KeyError, TypeError):
        seats, no_limit, headless = set(), False, False   # every sender then counts as outside: a wake too many, never one too few
    rec0 = roster.seat(shipdir, seat)
    shift0 = rec0.get("shiftNo")
    sid0 = data.get("session_id") or rec0.get("sessionId")
    pidfile = Path(shipdir) / ".runtime" / f"wait-{seat}.pid"
    mine = f"{os.getpid()} {shift0}"
    try:
        other, _, other_shift = pidfile.read_text(encoding="utf-8").strip().partition(" ")
        # a live watcher of an older shift does not count: it leaves by itself at its next poll
        # (a pidfile without a shift, from before T-038, still counts)
        if int(other) != os.getpid() and _pid_alive(int(other)) and other_shift in ("", str(shift0)):
            return 0
    except (FileNotFoundError, ValueError):
        pass
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(mine + "\n", encoding="utf-8", newline="\n")
    polls = 0
    try:
        while True:
            dl = deadline.read(shipdir)
            if dl is None and not no_limit:
                return 0
            need, rec = _needs_wrapup(shipdir, seat)
            if rec.get("state") in (roster.STOPPING, roster.OFF):
                return 0
            if rec.get("shiftNo") != shift0 or _pidfile_text(pidfile) != mine:
                return 0   # a newer shift / watcher owns this seat now: not ours to wake
            if dl is None:
                polls += 1
                if not headless and polls % LIVENESS_EVERY == 0 and _session_gone(sid0):
                    append_log(shipdir, seat, "inbox watcher: セッションが消えている → 見張りをやめる")
                    return 0
            phase = deadline.phase(dl)
            if dl is not None:
                if phase == deadline.FORCE:
                    # an idle seat past the grace period: stopped here if the watchdog is gone (§0 B4)
                    force_stop_self(shipdir, seat, data.get("session_id"), "deadline watcher")
                    return 0
                if need and take_wrapup_notice(shipdir, seat, rec.get("shiftNo")):
                    append_log(shipdir, seat, "deadline watcher: 稼働時間の上限 → 終業を指示")
                    sys.stderr.write(_wrapup_message(shipdir, seat) + "\n")
                    return 2
            # notices used up (or no deadline at all): keep watching for an outside sender
            news = take_inbox_wake(shipdir, seat, seats) if (dl is None or phase == deadline.RUNNING) else []
            if news:
                append_log(shipdir, seat, f"inbox watcher: 席の外からの未読 {len(news)} 件 (#{news[-1]['n']} まで) → 起こす")
                sys.stderr.write(_inbox_wake_message(shipdir, seat, news) + "\n")
                return 2
            if dl is None:
                time.sleep(WAIT_POLL)
            else:
                until = dl["deadline"] if phase == deadline.RUNNING else dl["graceUntil"]
                time.sleep(max(1.0, min(WAIT_POLL, until - time.time())))
    finally:
        try:
            if _pidfile_text(pidfile) == mine:
                pidfile.unlink()
        except FileNotFoundError:
            pass


def deny_dialog(shipdir: Path, seat: str) -> int:
    """PermissionRequest: an unattended seat never waits on a dialog (verify-p0-b Q1)."""
    data = _stdin_json()
    tool = data.get("tool_name", "?")
    tool_input = json.dumps(data.get("tool_input", {}), ensure_ascii=False)[:300]
    append_log(shipdir, seat, f"権限ダイアログを自動で拒否: {tool} {tool_input}")
    _emit({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {
        "behavior": "deny",
        "message": "unattended seat: dialog auto-denied by yamato. 別のやり方を取るか、captain に報告してください",
    }}})
    events.emit(shipdir, events.PERMISSION_DENIED, seat=seat, summary=f"権限ダイアログを自動で拒否: {tool} {tool_input}",
                data={"source": "dialog", "tool": tool})
    return 0


def log_denied(shipdir: Path, seat: str) -> int:
    data = _stdin_json()
    tool = data.get("tool_name", "?")
    tool_input = json.dumps(data.get("tool_input", {}), ensure_ascii=False)[:300]
    reason = str(data.get("reason", ""))[:200]
    append_log(shipdir, seat, f"auto mode が拒否: {tool} {tool_input} reason={reason}")
    events.emit(shipdir, events.PERMISSION_DENIED, seat=seat, summary=f"auto mode が拒否: {tool} {tool_input}",
                data={"source": "auto", "tool": tool, "reason": reason})
    return 0


HOOKS = {
    "session-start": session_start,
    "session-start-knowledge": session_start_knowledge,
    "stop": stop,
    "user-prompt-submit": user_prompt_submit,
    "pre-tool-use": pre_tool_use,
    "pre-compact": pre_compact,
    "wait-deadline": wait_deadline,
    "deny-dialog": deny_dialog,
    "log-denied": log_denied,
}

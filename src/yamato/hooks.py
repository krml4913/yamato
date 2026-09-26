"""Hook entry points (``yamato hook <event> <ship> <seat>``), called by the seats every turn.

Kept light: standard library plus yamato's small modules, no YAML.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from . import deadline, events, inbox, inject, roster
from .team import runtime_team
from .util import YAMATO_BIN, append_log, read_json, ship_lock, write_json

MAX_WRAPUP_NOTICES = 3   # per shift; after that the grace-period force stop takes over
WAIT_POLL = 5            # seconds between deadline checks in the async watcher


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
    data = _stdin_json()
    _touch(shipdir, seat)
    source = data.get("source") or "startup"
    team = runtime_team(shipdir)
    text, cursor_to = inject.build(shipdir, team, seat, source)
    notice = _last_call_notice(shipdir, team, seat, "SessionStart")
    if notice:
        text = notice + "\n\n" + text
    inbox.mark_read(shipdir, seat, cursor_to)
    append_log(shipdir, seat, f"SessionStart ({source}) session={data.get('session_id', '?')}")
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


def _needs_wrapup(shipdir: Path, seat: str) -> tuple[bool, dict]:
    rec = roster.seat(shipdir, seat)
    if rec.get("state") in (roster.STOPPING, roster.OFF):
        return False, rec
    return deadline.phase(deadline.read(shipdir)) in (deadline.OVER, deadline.FORCE), rec


def _wrapup_message(shipdir: Path, seat: str) -> str:
    return deadline.WRAP_UP_MESSAGE.format(yamato=YAMATO_BIN, ship=shipdir, seat=seat)


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
    return rotate.MESSAGE.format(reasons=", ".join(reasons), yamato=YAMATO_BIN, ship=shipdir, seat=seat)


def stop(shipdir: Path, seat: str) -> int:
    """Past the deadline, block the end of the turn with the wrap-up order (§0 B4).
    Otherwise, once each: the captain's last-call notice (design-p1 §9) and the
    nudge to rotate (§5.4). At most one block per turn end."""
    data = _stdin_json()
    _touch(shipdir, seat)
    need, rec = _needs_wrapup(shipdir, seat)
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
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_deadline(shipdir: Path, seat: str) -> int:
    """Async + asyncRewake Stop hook: sleep until the deadline, then wake the idle seat.

    Exit 2 with the message on stderr wakes the seat (verify-p0-a Q4). A
    pidfile keeps one watcher per seat, since every turn end spawns another.
    """
    _stdin_json()
    pidfile = Path(shipdir) / ".runtime" / f"wait-{seat}.pid"
    try:
        other = int(pidfile.read_text().strip())
        if other != os.getpid() and _pid_alive(other):
            return 0
    except (FileNotFoundError, ValueError):
        pass
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(f"{os.getpid()}\n")
    try:
        while True:
            dl = deadline.read(shipdir)
            if dl is None:
                return 0
            need, rec = _needs_wrapup(shipdir, seat)
            if rec.get("state") in (roster.STOPPING, roster.OFF):
                return 0
            if need:
                if not take_wrapup_notice(shipdir, seat, rec.get("shiftNo")):
                    return 0
                append_log(shipdir, seat, "deadline watcher: 稼働時間の上限 → 終業を指示")
                sys.stderr.write(_wrapup_message(shipdir, seat) + "\n")
                return 2
            time.sleep(max(1.0, min(WAIT_POLL, dl["deadline"] - time.time())))
    finally:
        try:
            if pidfile.read_text().strip() == str(os.getpid()):
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
    "stop": stop,
    "user-prompt-submit": user_prompt_submit,
    "pre-compact": pre_compact,
    "wait-deadline": wait_deadline,
    "deny-dialog": deny_dialog,
    "log-denied": log_denied,
}

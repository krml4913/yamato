"""Seat lifecycle: up / send / seat-stop / down / status, and the time-limit enforcement.

No daemon (design §2): everything happens synchronously when a command is
called. The only background processes are the one-shot watchdog started by
``up``/``down`` and the delayed self-stop started by ``seat-stop``; the limit
still holds without them because ``send`` / ``status`` / the hooks re-check
``.runtime/deadline`` every time (§0 B4).
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import claude, deadline, events, inbox, monitor, notify, report, roster, rotate, runtime, usage
from .team import last_call_conf, load_team, profile_of, seat_spec
from .util import (YAMATO_BIN, YamatoError, append_log, fmt_span, fmt_time, read_json,
                   seat_lock, ship_lock, write_json)

SEAT_FILES = ("memory-inbox.md",)   # the memory itself is the role's: roles/<role>/memory.md (design-p1 §3)
OWNER = inbox.OWNER
WATCHDOG_POLL = 30
WATCHDOG_MIN_SLEEP = 1.0   # the shortest watchdog sleep (tests shorten it)


def out(msg: str = "") -> None:
    print(msg, flush=True)


# --- preparation -------------------------------------------------------------

def ensure_seat_dirs(shipdir: Path, team: dict) -> None:
    for d in ("board/items", "board/archive", ".runtime"):
        (shipdir / d).mkdir(parents=True, exist_ok=True)
    for seat in team["seats"]:
        sdir = inbox.seat_dir(shipdir, seat)
        (sdir / "log").mkdir(parents=True, exist_ok=True)
        for name in SEAT_FILES:
            (sdir / name).touch(exist_ok=True)
        (sdir / "inbox.jsonl").touch(exist_ok=True)


def prepare(shipdir: Path) -> dict:
    """Validate team.yaml and regenerate .runtime/ (settings are re-read on resume)."""
    team = load_team(shipdir)
    with ship_lock(shipdir):
        ensure_seat_dirs(shipdir, team)
        runtime.generate(shipdir, team)
    return team


def current_team(shipdir: Path) -> dict:
    return read_json(shipdir / ".runtime" / "team.json") or load_team(shipdir)


def session_name(team: dict, seat: str) -> str:
    return f"{team['name']}.{seat}"


def handoff_max_lines(team: dict) -> int:
    from .inject import LIMITS

    return ((team.get("inject") or {}).get("limits") or {}).get("handoff", LIMITS["handoff"])[0]


def _handoff(shipdir: Path, seat: str) -> Path:
    return inbox.seat_dir(shipdir, seat) / "handoff.md"


def handoff_written_since(shipdir: Path, seat: str, since: float | None) -> bool:
    try:
        return _handoff(shipdir, seat).stat().st_mtime >= (since or 0)
    except FileNotFoundError:
        return False


# --- deliveries the sender still owes -----------------------------------------
# `send` to a live seat leaves the delivery to the sender's SendMessage (§0 B1).
# E2E run 2: a seat recorded its report, then ended its shift without the
# SendMessage, and the idle captain never heard of it. seat-stop checks these.

def _pending_path(shipdir: Path, seat: str) -> Path:
    return shipdir / ".runtime" / f"pending-{seat}.json"


def add_pending(shipdir: Path, sender: str, to: str, n: int, name: str) -> None:
    with ship_lock(shipdir):
        items = read_json(_pending_path(shipdir, sender), []) or []
        items.append({"to": to, "n": n, "name": name})
        write_json(_pending_path(shipdir, sender), items)


def unresolved_pending(shipdir: Path, sender: str) -> list[dict]:
    """Pending deliveries the recipient has not read yet."""
    items = read_json(_pending_path(shipdir, sender), []) or []
    return [p for p in items if inbox.cursor(shipdir, p["to"]) < p["n"]]


def clear_pending(shipdir: Path, seat: str) -> None:
    _pending_path(shipdir, seat).unlink(missing_ok=True)


# --- shifts ------------------------------------------------------------------

def _first_prompt(shipdir: Path, seat: str) -> str:
    return (f"[yamato] シフト開始。SessionStart で注入された引き継ぎ・自分の担当・未読 inbox を確認し、"
            f"役割どおりに仕事を進めてください。未読の続きは `{YAMATO_BIN} inbox {shipdir} {seat}` で読めます。")


def _resume_prompt(shipdir: Path, seat: str, reason: str = "send") -> str:
    if reason == "up":
        # E2E run 6: resumed with the "new message" prompt, the captain kept its old
        # conclusion ("no time left") and stopped again at once. Say what changed.
        return (f"[yamato] 艦が起動された ({deadline.describe(deadline.read(shipdir))})。"
                f"前のシフトの時間の判断は忘れ、SessionStart で注入された引き継ぎ・担当・未読 inbox を確認して、"
                f"引き継ぎの「次にやること」から仕事を再開してください。")
    return (f"[yamato] inbox に新しいメッセージがあります。`{YAMATO_BIN} inbox {shipdir} {seat}` で読んで対応してください。")


def start_new_shift(shipdir: Path, team: dict, seat: str, rotated: list[str] | None = None) -> dict:
    """``rotated``: why a persistent seat gets this instead of a resume (design-p1 §5.3).
    A ``nextCwd`` left by ``send --cwd`` is used (and used up) here (§8.2 の 2)."""
    spec = seat_spec(team, seat)
    cwd = roster.seat(shipdir, seat).get("nextCwd")
    workspace = Path(cwd or team["workspace"])
    if not workspace.is_dir():
        raise YamatoError(f"{'--cwd の場所' if cwd else 'workspace'}がありません: {workspace}")
    if not claude.is_trusted(workspace):
        raise YamatoError(claude.untrusted_message(workspace))
    settings = runtime.settings_path(shipdir, seat)
    if cwd:
        # in a worktree already: Claude Code must not cut another one from it (bgIsolation: none)
        settings = runtime.write_cwd_settings(shipdir, team, seat)
    name = session_name(team, seat)
    short, full = claude.launch(
        cwd=str(workspace), name=name, role=spec["role"],
        agents_json=runtime.agents_path(shipdir).read_text(encoding="utf-8"),
        model=spec["model"], settings=str(settings),
        add_dir=str(shipdir), prompt=_first_prompt(shipdir, seat), env_unset=team.get("env_unset") or (),
        remote_control=bool(team["roles"][spec["role"]].get("remote_control")),
    )
    if cwd:
        roster.update(shipdir, seat, nextCwd=None)
    rec = roster.start_shift(shipdir, seat, session_id=full, short_id=short, session_name=name, how="new",
                             cwd=cwd, rotated=rotated)
    clear_pending(shipdir, seat)
    append_log(shipdir, seat, f"シフト開始 #{rec['shiftNo']} (new) session={full}"
               + (f" cwd={cwd}" if cwd else "") + (f" 入れ替え: {', '.join(rotated)}" if rotated else ""))
    return rec


def resume_shift(shipdir: Path, team: dict, seat: str, rec: dict, reason: str = "send") -> dict:
    sid = rec["sessionId"]
    # resume right after stop starts a flagless copy: wait for the pid to vanish (verify-p0-b Q2)
    if not claude.wait_gone(sid, timeout=30):
        raise YamatoError(f"席 {seat} の前のプロセスがまだ残っているので resume できません ({sid})")
    claude.resume(sid, _resume_prompt(shipdir, seat, reason), env_unset=team.get("env_unset") or (),
                  cwd=team["workspace"])
    new = roster.start_shift(shipdir, seat, session_id=sid, short_id=rec.get("shortId") or sid[:8],
                             session_name=session_name(team, seat), how="resume")
    clear_pending(shipdir, seat)
    append_log(shipdir, seat, f"シフト開始 #{new['shiftNo']} (resume) session={sid}")
    return new


def wake(shipdir: Path, team: dict, seat: str, reason: str = "send") -> tuple[str, dict]:
    """Bring a seat on shift. Returns (what happened, roster record).

    Under the seat's wake lock, liveness and the roster are read afresh: two
    concurrent sends to a stopped seat must not launch it twice, or one session
    would be missing from the roster and escape the time limit (review B1).
    """
    if seat_spec(team, seat)["shift"] == "headless":
        from . import headless

        return headless.wake(shipdir, team, seat)
    with seat_lock(shipdir, seat):
        listing = claude.agents()
        reconcile(shipdir, team, listing)
        rec = roster.seat(shipdir, seat)
        live = claude.by_session(listing).get(rec.get("sessionId"))
        if claude.is_alive(live) and rec.get("state") == roster.STOPPING:
            # E2E run 3: a message SendMessage'd into the delayed-stop window is lost
            # when the stop lands. Wait for the stop, then wake a fresh shift.
            if not claude.wait_gone(rec["sessionId"], timeout=60):
                raise YamatoError(f"席 {seat} は終業処理中ですが、止まるのを待てませんでした ({rec['sessionId']})")
            finish_shift(shipdir, seat, reason="seat-stop")
            rec = roster.seat(shipdir, seat)
            live = None
        if claude.is_alive(live):
            return "alive", rec
        spec = seat_spec(team, seat)
        if spec["shift"] == "persistent" and rec.get("sessionId"):
            # resume, or a new shift from the records (design-p1 §5.3, roles.<role>.rotate)
            reasons = rotate.resume_reasons(team, seat, rec)
            if not reasons:
                return "resumed", resume_shift(shipdir, team, seat, rec, reason)
            return "started", start_new_shift(shipdir, team, seat, rotated=reasons)
        return "started", start_new_shift(shipdir, team, seat)


def finish_shift(shipdir: Path, seat: str, *, reason: str, forced: bool = False) -> dict | None:
    """Close the seat's current shift in the roster once, with its usage line."""
    with ship_lock(shipdir):
        rec = roster.seat(shipdir, seat)
        if not rec or rec.get("state") == roster.OFF:
            return None
        written = handoff_written_since(shipdir, seat, rec.get("shiftStartedAt"))
        note = None
        if forced:
            note = roster.NO_HANDOFF_NOTE if not written else "強制停止 (引き継ぎは書かれていた)"
        elif not written:
            note = roster.NO_HANDOFF_NOTE
        line = None
        if rec.get("sessionId") and rec.get("shiftStartedAt"):
            line = usage.record(shipdir, seat, session_id=rec["sessionId"], shift_no=rec.get("shiftNo"),
                                since=rec["shiftStartedAt"])
        ended = roster.end_shift(shipdir, seat, reason=reason, handoff_written=written, note=note)
    append_log(shipdir, seat, f"シフト終了 #{ended.get('shiftNo')} ({reason})" + (f" {note}" if note else ""))
    if line:
        append_log(shipdir, seat, usage.summary(line))
    return ended


def reconcile(shipdir: Path, team: dict, listing: list[dict]) -> None:
    """Seats the roster thinks are on shift but whose process is gone get their shift closed."""
    by = claude.by_session(listing)
    for seat, rec in roster.load(shipdir)["seats"].items():
        if seat not in team["seats"] or rec.get("state") not in (roster.ON_SHIFT, roster.STOPPING):
            continue
        if team["seats"][seat]["shift"] == "headless":
            # the wrapper closes its own shift; the -p may be listed as alive, so the listing is not used
            if _headless_running(shipdir, team, seat):
                continue
            if _stop_headless_orphan(shipdir, seat, "wrapper-lost"):
                continue
        elif claude.is_alive(by.get(rec.get("sessionId"))):
            continue
        reason = "seat-stop" if rec["state"] == roster.STOPPING else "exited"
        finish_shift(shipdir, seat, reason=reason)


def _headless_running(shipdir: Path, team: dict, seat: str) -> bool:
    """A headless seat is on shift while its wrapper holds the seat (it closes the shift itself)."""
    if team["seats"][seat]["shift"] != "headless":
        return False
    from . import headless

    return headless.running(shipdir, seat)


def _stop_headless_orphan(shipdir: Path, seat: str, reason: str) -> bool:
    """A headless ``claude -p`` whose wrapper is gone: stop it and close the shift here."""
    from . import headless

    if not headless.stop_orphan(shipdir, seat, reason):
        return False
    finish_shift(shipdir, seat, reason=reason, forced=True)
    return True


def force_stop_all(shipdir: Path, team: dict, reason: str) -> list[str]:
    stopped = []
    for seat in team["seats"]:
        if team["seats"][seat]["shift"] == "headless":
            from . import headless

            if _headless_running(shipdir, team, seat):
                headless.terminate(shipdir, seat, reason)   # the wrapper records the forced end
                stopped.append(seat)
            elif _stop_headless_orphan(shipdir, seat, reason):
                stopped.append(seat)
            continue
        # a launch in flight holds the wake lock: wait for it, then look again
        with seat_lock(shipdir, seat):
            rec = roster.seat(shipdir, seat)
            live = claude.find(rec["sessionId"]) if rec.get("sessionId") else None
            if not claude.is_alive(live):
                continue
            claude.stop(rec.get("shortId") or rec["sessionId"][:8])
            claude.wait_gone(rec["sessionId"], timeout=30)
            events.emit(shipdir, events.FORCE_STOP, seat=seat, summary=f"強制停止 ({reason})",
                        data={"reason": reason, "shiftNo": rec.get("shiftNo"), "sessionId": rec["sessionId"]})
            finish_shift(shipdir, seat, reason=reason, forced=True)
            stopped.append(seat)
    if stopped:
        for line in report.safety_net(shipdir, team, f"強制停止 ({reason})"):
            out(line)
    return stopped


def enforce(shipdir: Path, team: dict) -> list[str]:
    """Past deadline + grace: stop every seat still alive (like ``down --force``)."""
    if deadline.phase(deadline.read(shipdir)) != deadline.FORCE:
        return []
    return force_stop_all(shipdir, team, reason="grace-exceeded")


def _spawn_detached(args: list[str]) -> None:
    subprocess.Popen(["nohup", *args], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, env=claude.seat_env())


def spawn_watchdog(shipdir: Path, token: str) -> None:
    _spawn_detached([sys.executable, str(YAMATO_BIN), "_watchdog", str(shipdir), token])


# --- commands ----------------------------------------------------------------

def up(shipdir: Path, for_: str | None) -> int:
    from .util import parse_duration

    team = prepare(shipdir)
    for w in team.get("warnings") or []:
        out(f"注意: {w}")
    workspace = Path(team["workspace"])
    if not workspace.is_dir():
        raise YamatoError(f"workspace がありません: {workspace}")
    if not claude.is_trusted(workspace):
        raise YamatoError(claude.untrusted_message(workspace))
    limit = parse_duration(for_) if for_ else team["time_limit"]
    listing = claude.agents()
    reconcile(shipdir, team, listing)
    token = uuid.uuid4().hex[:12]
    dl = deadline.write(shipdir, limit=limit, grace=team["grace"], token=token, last_call=last_call_conf(team))
    hub = team["hub"]
    what, rec = wake(shipdir, team, hub, reason="up")
    spawn_watchdog(shipdir, token)
    out(f"艦 {team['name']} を起動: deadline {fmt_time(dl['deadline'])} (稼働 {fmt_span(limit)}, 猶予 {fmt_span(team['grace'])})")
    label = {"alive": "すでに動いている", "resumed": "resume した", "started": "新しいシフトを起動した",
             "spawned": "headless のシフトを起動した (run-headless)", "queued": "headless のシフト中"}[what]
    out(f"  captain 席 {hub}: {label}" + (f" (session {rec['sessionId']})" if what != "spawned" else ""))
    return 0


def send(shipdir: Path, seat: str, text: str, sender: str, cwd: str | None = None) -> int:
    team = current_team(shipdir)
    if not text.strip():
        raise YamatoError("本文が空です")
    _check_may_send(shipdir, team, sender)
    if seat == OWNER:
        if cwd:
            raise YamatoError("--cwd は席への send でだけ使える")
        entry = inbox.append(shipdir, OWNER, sender, text)
        _send_event(shipdir, OWNER, sender, entry)
        out(f"owner の inbox に記録した: #{entry['n']} (`{YAMATO_BIN} inbox {shipdir} owner` で読む)")
        _watch_send(shipdir, team, sender, OWNER, entry, text)
        for line in notify.notify(team, f"yamato {team['name']}: {sender} から", text, shipdir=shipdir):
            out(line)
        return 0
    spec = seat_spec(team, seat)
    if cwd:
        cwd = _check_cwd(spec, seat, cwd)
    original = text
    dl = deadline.read(shipdir)
    if sender == team["hub"] and deadline.in_last_call(dl):
        # past the last call the captain's assignments carry the time left (design-p1 §9). Never refused
        text = deadline.LAST_CALL_PREFIX.format(left=deadline.left(dl)) + text
    entry = inbox.append(shipdir, seat, sender, text)
    _send_event(shipdir, seat, sender, entry, original)
    out(f"inbox に記録した: {seat} #{entry['n']}")
    if text != original:
        out(f"最終受付を過ぎているので、本文の先頭に「{text[:len(text) - len(original)].strip()}」を足した")
    _watch_send(shipdir, team, sender, seat, entry, original)
    if cwd:
        roster.update(shipdir, seat, nextCwd=cwd)
        out(f"次のシフトは {cwd} を cwd にして起動する (bgIsolation: none)")
    y = YAMATO_BIN
    ph = deadline.phase(dl)
    if ph == deadline.NOT_UP:
        out(f"艦は起動していないので宛先は起こさない。`{y} up {shipdir}` で起動すると、宛先の席が起きたときに読まれる。")
        return 0
    if ph in (deadline.OVER, deadline.FORCE):
        if ph == deadline.FORCE:
            stopped = enforce(shipdir, team)
            if stopped:
                out(f"猶予を過ぎても動いていた席を強制停止した: {', '.join(stopped)}")
        out("稼働時間の上限を過ぎているので宛先は起こさない (inbox には記録済み。次に起動したときに読まれる)。")
        if sender in team["seats"]:
            out(deadline.WRAP_UP_MESSAGE.format(yamato=y, ship=shipdir, seat=sender))
        return 0

    what, rec = wake(shipdir, team, seat)
    name = session_name(team, seat)
    if cwd and what in ("alive", "queued"):
        out(f"宛先 {seat} はシフト中なので、--cwd は次のシフトから効く")
    if what == "alive":
        if sender in team["seats"]:
            add_pending(shipdir, sender, seat, entry["n"], name)
            out(f"宛先 {seat} は生きている。yamato は配送しない。SendMessage ツールで to=\"{name}\" に次の本文を届けること:")
            out(f"  [yamato inbox #{entry['n']} from {sender}] {text}")
            out(f"  (SendMessage が success:false なら、もう一度 `{y} send` する)")
        else:
            out(f"宛先 {seat} は生きている (session {rec.get('shortId')})。inbox に記録済み。"
                f"idle なら席の watcher が数秒で起こす。直接話すなら `claude attach {rec.get('shortId')}`。")
    elif what == "spawned":
        out(f"headless の席 {seat} のシフトを起動した (run-headless。終わると {_report_to(team, seat)} に定型文で報告が届く)。SendMessage は不要。")
    elif what == "queued":
        out(f"headless の席 {seat} はシフト中。inbox に積んだので、今のシフトの終わりに未読として続けて読まれる。"
            f"すぐ伝えるなら SendMessage で to=\"{name}\" に送ってもよい (任意)。")
    elif what == "resumed":
        out(f"止まっていた persistent の席 {seat} を resume した (session {rec['sessionId']})。SendMessage は不要。")
    elif rec.get("rotated"):
        out(f"persistent の席 {seat} を resume せず、新しいシフトを起動した (入れ替え: {', '.join(rec['rotated'])}。"
            f"session {rec['sessionId']})。SendMessage は不要。")
    else:
        out(f"席 {seat} の新しいシフトを起動した (session {rec['sessionId']})。SendMessage は不要。")
    return 0


def _check_may_send(shipdir: Path, team: dict, sender: str) -> None:
    """A seat whose trust profile says ``send: false`` cannot send (design-p1 §7.2).

    Outside text read by such a seat must not reach another seat's conversation
    as an instruction; its end-of-shift report is the wrapper's fixed text. Both
    the claimed ``--from`` and the calling session (roster) are checked, so a
    wrong ``--from`` does not get round it."""
    caller = roster.seat_of_session(shipdir, os.environ.get("CLAUDE_CODE_SESSION_ID"))
    for who in dict.fromkeys((sender, caller)):
        spec = team["seats"].get(who) if who else None
        profile = profile_of(team, spec["role"]) if spec else None
        if profile and profile.get("send") is False:
            raise YamatoError(f"席 {who} (trust: {team['roles'][spec['role']]['trust']}) は send を使えない "
                              "(profiles の send: false)。成果はファイルと board note に残し、seat-stop で終える "
                              "(終わりの報告は yamato が定型文で送る)")


def _check_cwd(spec: dict, seat: str, cwd: str) -> str:
    """``send --cwd`` (design-p1 §8.2 の 2): the next shift starts in ``cwd``. A resume keeps
    its session's own directory, so a persistent seat cannot take it."""
    if spec["shift"] == "persistent":
        raise YamatoError(f"--cwd は per_task / headless の席で使える (席 {seat} は persistent で、resume は元の場所で起きる)")
    path = Path(cwd).expanduser().resolve()
    if not path.is_dir():
        raise YamatoError(f"--cwd の場所がありません: {path}")
    if spec["shift"] != "headless" and not claude.is_trusted(path):
        raise YamatoError(claude.untrusted_message(path))
    return str(path)


def _watch_send(shipdir: Path, team: dict, sender: str, to: str, entry: dict, text: str) -> None:
    """Looking where the work flows (design-p1 §5.2, §5.5): warnings, never refusals."""
    for w in monitor.check_send(shipdir, team, sender, to, entry, monitor.digest(text)):
        out(f"注意: {w}")
    if deadline.phase(deadline.read(shipdir)) == deadline.RUNNING:
        gap = monitor.check_captain_gap(shipdir, team)
        if gap:
            out(f"注意: {gap['summary']} (events に記録した)")


def _report_to(team: dict, seat: str) -> str:
    return team["roles"][team["seats"][seat]["role"]].get("report_to") or team["hub"]


def _send_event(shipdir: Path, to: str, sender: str, entry: dict, text: str | None = None) -> None:
    """``digest``: of the text as the sender wrote it, for the duplicate check (design-p1 §5.5)."""
    events.emit(shipdir, events.SEND, seat=to, by=sender, summary=entry["text"],
                data={"n": entry["n"], "chars": len(entry["text"]),
                      "digest": monitor.digest(entry["text"] if text is None else text)})


def seat_stop(shipdir: Path, seat: str, after: int, delivered: bool = False, rotate_: bool = False) -> int:
    team = current_team(shipdir)
    seat_spec(team, seat)
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        raise YamatoError("CLAUDE_CODE_SESSION_ID がありません。seat-stop は席のセッションの中から実行してください")
    rec = roster.seat(shipdir, seat)
    if rec.get("sessionId") != sid:
        raise YamatoError(f"このセッション ({sid[:8]}) は席 {seat} の今のシフト ({str(rec.get('sessionId'))[:8]}) ではありません")
    if rec.get("state") == roster.STOPPING:
        out("終業はすでに受け付けている。このターンは短い一言で終えてください。")
        return 0
    checks = team.get("seat_stop") or {}
    if checks.get("require_handoff", True) and not handoff_written_since(shipdir, seat, rec.get("shiftStartedAt")):
        raise YamatoError(f"handoff.md が今回のシフトで更新されていません。先に {_handoff(shipdir, seat)} を上書きしてから、もう一度 seat-stop してください")
    pending = unresolved_pending(shipdir, seat)
    if pending and not delivered and checks.get("require_delivery", True):
        listing = "\n".join(f"  - to=\"{p['name']}\" inbox #{p['n']}" for p in pending)
        raise YamatoError("生きている宛先に SendMessage で届けるはずのメッセージが、まだ読まれていません:\n"
                          f"{listing}\n"
                          "SendMessage で届けていなければ今届けてから、届けたなら `seat-stop --delivered` で終業してください")
    lines = len(_handoff(shipdir, seat).read_text(encoding="utf-8").splitlines()) if _handoff(shipdir, seat).exists() else 0
    max_lines = handoff_max_lines(team)
    if lines > max_lines:
        out(f"注意: handoff.md が {lines} 行ある (注入の上限 {max_lines} 行)。次のシフトでは上限で切られる。")
    roster.mark_stopping(shipdir, seat,
                         handoff_written=handoff_written_since(shipdir, seat, rec.get("shiftStartedAt")),
                         rotate=rotate_)
    if rotate_:
        # the next shift is not started here: the next send wakes a new one (design-p1 §5.3 の 1, §5.4)
        append_log(shipdir, seat, "seat-stop --rotate: 入れ替えの印を立てた (次の send で新しいシフト)")
        events.emit(shipdir, events.ROTATE_REQUESTED, seat=seat, summary=f"入れ替えの印 (シフト #{rec.get('shiftNo')})",
                    data={"shiftNo": rec.get("shiftNo")})
        out("入れ替えの印を立てた。次に誰かがこの席に send したとき、resume せず新しいシフトとして記録から起きる。")
    if seat != team["hub"] and deadline.phase(deadline.read(shipdir)) == deadline.RUNNING:
        gap = monitor.check_captain_gap(shipdir, team)   # design-p1 §5.2
        if gap:
            out(f"注意: {gap['summary']} (events に記録した)")
    if team["seats"][seat]["shift"] == "headless":
        # claude -p ends by itself after this turn; run-headless closes the shift
        append_log(shipdir, seat, "seat-stop: 終業を受け付けた (headless)")
        out("終業を受け付けた。headless のシフトはこのターンを終えれば終わる。短い一言で終えること (これ以上ツールを使わない)。")
        return 0
    append_log(shipdir, seat, f"seat-stop: 終業を受け付けた ({after} 秒後に停止)")
    short = sid[:8]
    # delayed stop (verify-p0-a Q3 b): the current turn and its Stop hook finish first
    script = (f"sleep {int(after)}; {shlex.quote(claude.claude_bin())} stop {short}; "
              f"{shlex.quote(sys.executable)} {shlex.quote(str(YAMATO_BIN))} _shift-ended "
              f"{shlex.quote(str(shipdir))} {shlex.quote(seat)} {sid}")
    _spawn_detached(["sh", "-c", script])
    out(f"終業を受け付けた。{after} 秒後にこのセッションは止まる。このターンは短い一言で終えること (これ以上ツールを使わない)。")
    return 0


def shift_ended(shipdir: Path, seat: str, sid: str) -> int:
    """Run by the delayed stop after ``claude stop``: close the shift and record usage."""
    claude.wait_gone(sid, timeout=60)
    if roster.seat(shipdir, seat).get("sessionId") == sid:
        finish_shift(shipdir, seat, reason="seat-stop")
    return 0


def watchdog(shipdir: Path, token: str) -> int:
    """One-shot timer: at deadline + grace, force-stop whatever is still alive."""
    pidfile = shipdir / ".runtime" / "watchdog.pid"
    try:
        other_pid, other_token = pidfile.read_text().split()
        if int(other_pid) != os.getpid() and other_token == token:
            os.kill(int(other_pid), 0)
            return 0  # the same deadline is already being watched
    except (FileNotFoundError, ValueError, ProcessLookupError):
        pass
    except PermissionError:
        return 0
    pidfile.write_text(f"{os.getpid()} {token}\n")
    try:
        while True:
            dl = deadline.read(shipdir)
            if not dl or dl.get("token") != token:
                return 0
            now = time.time()
            if now >= dl["graceUntil"]:
                team = current_team(shipdir)
                stopped = enforce(shipdir, team)
                for seat in stopped:
                    append_log(shipdir, seat, "watchdog: 猶予を過ぎたので強制停止")
                if not stopped:
                    report.safety_net(shipdir, team, "終業のときに日報がなかった")
                return 0
            if now >= dl["deadline"]:
                team = current_team(shipdir)
                listing = claude.agents()
                reconcile(shipdir, team, listing)
                if not any(claude.is_alive(claude.by_session(listing).get(r.get("sessionId")))
                           for r in roster.load(shipdir)["seats"].values()):
                    report.safety_net(shipdir, team, "終業のときに日報がなかった")
                    return 0
            time.sleep(max(WATCHDOG_MIN_SLEEP, min(WATCHDOG_POLL, dl["graceUntil"] - now)))
    finally:
        try:
            if pidfile.read_text().split()[0] == str(os.getpid()):
                pidfile.unlink()
        except (FileNotFoundError, IndexError):
            pass


def down(shipdir: Path, force: bool) -> int:
    team = current_team(shipdir)
    listing = claude.agents()
    reconcile(shipdir, team, listing)
    dl = deadline.end_now(shipdir)
    if dl is None:
        out("艦は起動していない (deadline なし)。")
    if force:
        if dl is not None:
            with ship_lock(shipdir):
                dl["graceUntil"] = min(dl["graceUntil"], time.time())
                deadline.write_raw(shipdir, dl)
        stopped = force_stop_all(shipdir, team, reason="down-force")
        out(f"強制停止した席: {', '.join(stopped) if stopped else '(なし)'}")
        if dl is not None and not stopped:
            for line in report.safety_net(shipdir, team, "down --force"):
                out(line)
        return 0
    by = claude.by_session(listing)
    alive = [s for s in team["seats"] if claude.is_alive(by.get(roster.seat(shipdir, s).get("sessionId")))]
    if dl is not None and not alive:
        # nobody is left to write it (design-p1 §2.2 の 2); with seats alive the watchdog does this
        for line in report.safety_net(shipdir, team, "down のとき captain が動いていなかった"):
            out(line)
    if dl is not None:
        spawn_watchdog(shipdir, dl["token"])
        out(f"終業を指示した。動いている席 ({', '.join(alive) if alive else 'なし'}) は hook 経由で引き継ぎを書いて止まる。")
        out(f"{fmt_time(dl['graceUntil'])} (猶予 {fmt_span(dl.get('grace', 0))}) を過ぎても残っている席は強制停止する。今すぐ止めるなら --force。")
    return 0


def status(shipdir: Path) -> int:
    from . import admiral

    team = current_team(shipdir)
    listing = claude.agents()
    reconcile(shipdir, team, listing)
    stopped = enforce(shipdir, team)
    if stopped:
        listing = claude.agents()
    by = claude.by_session(listing)
    dl = deadline.read(shipdir)
    now = time.time()
    out(f"艦 {team['name']} ({shipdir})")
    out(f"  {deadline.describe(dl, now)}")
    if stopped:
        out(f"  猶予を過ぎても動いていた席を強制停止した: {', '.join(stopped)}")
    for seat, spec in team["seats"].items():
        rec = roster.seat(shipdir, seat)
        live = by.get(rec.get("sessionId"))
        alive = claude.is_alive(live)
        if _headless_running(shipdir, team, seat):
            alive, live = True, {**(live or {}), "pid": rec.get("pid") or (live or {}).get("pid")}
        last = _last_active(rec)
        cols = [
            f"{seat:<10}",
            f"{spec['shift']:<10}",
            f"生存={'yes pid ' + str(live.get('pid')) if alive else 'no'}",
            f"status={(live or {}).get('status') or '-'}",
            f"最終={fmt_time(last)}" + (f" ({fmt_span(now - last)}前)" if last else ""),
            f"shift#{rec.get('shiftNo', 0)} {rec.get('state') or '未起動'}",
        ]
        waiting = (live or {}).get("waitingFor")
        if waiting:
            cols.append(f"waitingFor={waiting}")
        if rec.get("note"):
            cols.append(f"[{rec['note']}]")
        out("  " + "  ".join(cols))
        for flag in admiral.red_flags(team, rec, live, now):   # design-p1 §5.2
            out(f"  !!! {seat}: {flag} (claude attach {rec.get('shortId')} で確認)")
    unread = {s: len(inbox.unread(shipdir, s)) for s in team["seats"]}
    if any(unread.values()):
        out("  未読 inbox: " + ", ".join(f"{s}={n}" for s, n in unread.items() if n))
    return 0


def _last_active(rec: dict) -> float | None:
    """The hooks' ``lastActive`` (design-p1 §5.1), or anything later the transcript shows
    (a seat started before the UserPromptSubmit hook existed has no ``lastActive``)."""
    sid = rec.get("sessionId")
    times = [rec["lastActive"]] if rec.get("lastActive") else []
    if sid:
        for p in claude.transcript_paths(sid):
            try:
                times.append(p.stat().st_mtime)
            except FileNotFoundError:
                pass
    for k in ("endedAt", "shiftStartedAt"):
        if rec.get(k):
            times.append(rec[k])
    return max(times) if times else None

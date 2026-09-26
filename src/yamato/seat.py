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

from . import claude, deadline, inbox, roster, runtime, usage
from .team import load_team, seat_spec
from .util import (YAMATO_BIN, YamatoError, append_log, fmt_span, fmt_time, read_json,
                   ship_lock, write_json)

SEAT_FILES = ("memory.md", "memory-inbox.md")
WATCHDOG_POLL = 30
HANDOFF_MAX_LINES = 40


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


def _resume_prompt(shipdir: Path, seat: str) -> str:
    return (f"[yamato] inbox に新しいメッセージがあります。`{YAMATO_BIN} inbox {shipdir} {seat}` で読んで対応してください。")


def start_new_shift(shipdir: Path, team: dict, seat: str) -> dict:
    spec = seat_spec(team, seat)
    workspace = Path(team["workspace"])
    if not workspace.is_dir():
        raise YamatoError(f"workspace がありません: {workspace}")
    if not claude.is_trusted(workspace):
        raise YamatoError(claude.untrusted_message(workspace))
    name = session_name(team, seat)
    short, full = claude.launch(
        cwd=str(workspace), name=name, role=spec["role"],
        agents_json=runtime.agents_path(shipdir).read_text(encoding="utf-8"),
        model=spec["model"], settings=str(runtime.settings_path(shipdir, seat)),
        add_dir=str(shipdir), prompt=_first_prompt(shipdir, seat),
    )
    rec = roster.start_shift(shipdir, seat, session_id=full, short_id=short, session_name=name, how="new")
    clear_pending(shipdir, seat)
    append_log(shipdir, seat, f"シフト開始 #{rec['shiftNo']} (new) session={full}")
    return rec


def resume_shift(shipdir: Path, team: dict, seat: str, rec: dict) -> dict:
    sid = rec["sessionId"]
    # resume right after stop starts a flagless copy: wait for the pid to vanish (verify-p0-b Q2)
    if not claude.wait_gone(sid, timeout=30):
        raise YamatoError(f"席 {seat} の前のプロセスがまだ残っているので resume できません ({sid})")
    claude.resume(sid, _resume_prompt(shipdir, seat))
    new = roster.start_shift(shipdir, seat, session_id=sid, short_id=rec.get("shortId") or sid[:8],
                             session_name=session_name(team, seat), how="resume")
    clear_pending(shipdir, seat)
    append_log(shipdir, seat, f"シフト開始 #{new['shiftNo']} (resume) session={sid}")
    return new


def wake(shipdir: Path, team: dict, seat: str, listing: list[dict]) -> tuple[str, dict]:
    """Bring a seat on shift. Returns (what happened, roster record)."""
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
        return "resumed", resume_shift(shipdir, team, seat, rec)
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
        if claude.is_alive(by.get(rec.get("sessionId"))):
            continue
        reason = "seat-stop" if rec["state"] == roster.STOPPING else "exited"
        finish_shift(shipdir, seat, reason=reason)


def force_stop_all(shipdir: Path, team: dict, listing: list[dict], reason: str) -> list[str]:
    by = claude.by_session(listing)
    stopped = []
    for seat in team["seats"]:
        rec = roster.seat(shipdir, seat)
        live = by.get(rec.get("sessionId"))
        if not claude.is_alive(live):
            continue
        claude.stop(rec.get("shortId") or rec["sessionId"][:8])
        claude.wait_gone(rec["sessionId"], timeout=30)
        finish_shift(shipdir, seat, reason=reason, forced=True)
        stopped.append(seat)
    return stopped


def enforce(shipdir: Path, team: dict, listing: list[dict] | None = None) -> list[str]:
    """Past deadline + grace: stop every seat still alive (like ``down --force``)."""
    if deadline.phase(deadline.read(shipdir)) != deadline.FORCE:
        return []
    listing = claude.agents() if listing is None else listing
    return force_stop_all(shipdir, team, listing, reason="grace-exceeded")


def _spawn_detached(args: list[str]) -> None:
    subprocess.Popen(["nohup", *args], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, env=claude.seat_env())


def spawn_watchdog(shipdir: Path, token: str) -> None:
    _spawn_detached([sys.executable, str(YAMATO_BIN), "_watchdog", str(shipdir), token])


# --- commands ----------------------------------------------------------------

def up(shipdir: Path, for_: str | None) -> int:
    from .util import parse_duration

    team = prepare(shipdir)
    workspace = Path(team["workspace"])
    if not workspace.is_dir():
        raise YamatoError(f"workspace がありません: {workspace}")
    if not claude.is_trusted(workspace):
        raise YamatoError(claude.untrusted_message(workspace))
    limit = parse_duration(for_) if for_ else team["time_limit"]
    listing = claude.agents()
    reconcile(shipdir, team, listing)
    token = uuid.uuid4().hex[:12]
    dl = deadline.write(shipdir, limit=limit, grace=team["grace"], token=token)
    hub = team["hub"]
    what, rec = wake(shipdir, team, hub, listing)
    spawn_watchdog(shipdir, token)
    out(f"艦 {team['name']} を起動: deadline {fmt_time(dl['deadline'])} (稼働 {fmt_span(limit)}, 猶予 {fmt_span(team['grace'])})")
    label = {"alive": "すでに動いている", "resumed": "resume した", "started": "新しいシフトを起動した"}[what]
    out(f"  captain 席 {hub}: {label} (session {rec.get('sessionId')})")
    return 0


def send(shipdir: Path, seat: str, text: str, sender: str) -> int:
    team = current_team(shipdir)
    seat_spec(team, seat)
    if not text.strip():
        raise YamatoError("本文が空です")
    entry = inbox.append(shipdir, seat, sender, text)
    out(f"inbox に記録した: {seat} #{entry['n']}")
    y = YAMATO_BIN
    dl = deadline.read(shipdir)
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

    listing = claude.agents()
    reconcile(shipdir, team, listing)
    what, rec = wake(shipdir, team, seat, listing)
    name = session_name(team, seat)
    if what == "alive":
        if sender in team["seats"]:
            add_pending(shipdir, sender, seat, entry["n"], name)
            out(f"宛先 {seat} は生きている。yamato は配送しない。SendMessage ツールで to=\"{name}\" に次の本文を届けること:")
            out(f"  [yamato inbox #{entry['n']} from {sender}] {text}")
            out(f"  (SendMessage が success:false なら、もう一度 `{y} send` する)")
        else:
            out(f"宛先 {seat} は生きている (session {rec.get('shortId')})。inbox に記録済み。"
                f"すぐ伝えるなら `claude attach {rec.get('shortId')}` で直接話す。")
    elif what == "resumed":
        out(f"止まっていた persistent の席 {seat} を resume した (session {rec['sessionId']})。SendMessage は不要。")
    else:
        out(f"席 {seat} の新しいシフトを起動した (session {rec['sessionId']})。SendMessage は不要。")
    return 0


def seat_stop(shipdir: Path, seat: str, after: int, delivered: bool = False) -> int:
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
    if not handoff_written_since(shipdir, seat, rec.get("shiftStartedAt")):
        raise YamatoError(f"handoff.md が今回のシフトで更新されていません。先に {_handoff(shipdir, seat)} を上書きしてから、もう一度 seat-stop してください")
    pending = unresolved_pending(shipdir, seat)
    if pending and not delivered:
        listing = "\n".join(f"  - to=\"{p['name']}\" inbox #{p['n']}" for p in pending)
        raise YamatoError("生きている宛先に SendMessage で届けるはずのメッセージが、まだ読まれていません:\n"
                          f"{listing}\n"
                          "SendMessage で届けていなければ今届けてから、届けたなら `seat-stop --delivered` で終業してください")
    lines = len(_handoff(shipdir, seat).read_text(encoding="utf-8").splitlines())
    if lines > HANDOFF_MAX_LINES:
        out(f"注意: handoff.md が {lines} 行ある (目安 {HANDOFF_MAX_LINES} 行)。次のシフトでは上限で切られる。")
    roster.mark_stopping(shipdir, seat, handoff_written=True)
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
                return 0
            if now >= dl["deadline"]:
                team = current_team(shipdir)
                listing = claude.agents()
                reconcile(shipdir, team, listing)
                if not any(claude.is_alive(claude.by_session(listing).get(r.get("sessionId")))
                           for r in roster.load(shipdir)["seats"].values()):
                    return 0
            time.sleep(max(1.0, min(WATCHDOG_POLL, dl["graceUntil"] - now)))
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
        stopped = force_stop_all(shipdir, team, listing, reason="down-force")
        out(f"強制停止した席: {', '.join(stopped) if stopped else '(なし)'}")
        return 0
    by = claude.by_session(listing)
    alive = [s for s in team["seats"] if claude.is_alive(by.get(roster.seat(shipdir, s).get("sessionId")))]
    if dl is not None:
        spawn_watchdog(shipdir, dl["token"])
        out(f"終業を指示した。動いている席 ({', '.join(alive) if alive else 'なし'}) は hook 経由で引き継ぎを書いて止まる。")
        out(f"{fmt_time(dl['graceUntil'])} (猶予 {fmt_span(dl.get('grace', 0))}) を過ぎても残っている席は強制停止する。今すぐ止めるなら --force。")
    return 0


def status(shipdir: Path) -> int:
    team = current_team(shipdir)
    listing = claude.agents()
    reconcile(shipdir, team, listing)
    stopped = enforce(shipdir, team, listing)
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
        if waiting and "permission" in str(waiting):
            out(f"  !!! 詰まり: {seat} が権限の確認で止まっている (claude attach {rec.get('shortId')} で確認)")
    unread = {s: len(inbox.unread(shipdir, s)) for s in team["seats"]}
    if any(unread.values()):
        out("  未読 inbox: " + ", ".join(f"{s}={n}" for s, n in unread.items() if n))
    return 0


def _last_active(rec: dict) -> float | None:
    sid = rec.get("sessionId")
    times = []
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

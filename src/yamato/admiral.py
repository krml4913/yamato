"""admiral (窓口) の CLI (design-p1 §6): ``extend`` / ``halt`` / ``ships`` / ``talk`` and
``up --seats``. The rest of the admiral's commands are P0's ``ship create`` / ``up``
/ ``down`` / ``status``.

The admiral is not a seat: these are plain commands an owner (or the fleet
leader standing in for the admiral) types. That the admiral keeps out of a
ship's contents is a promise of ``docs/admiral.md``, not something checked here.
The ship list (``ships.json``) holds only name -> folder; every state shown is
read from the ship's folder each time.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import claude, deadline, inbox, procs, report, roster, seat
from .runtime import yamato_invocation
from .team import WATCH_FALLBACK, load_team, seat_spec
from .util import (YamatoError, fmt_span, fmt_time, load_registry, parse_duration, resolve_ship,
                   ship_lock, today, yamato_home)

TALK_NOTE = "[yamato talk] owner がこれから `claude attach` でこの席に来て直接話します。起きたら待っていてください。"

# --- yamato admiral (D-011, T-021) ---------------------------------------------------
# _admiral/ (T-020) is a ship folder like any other -- unregistered (all_ships() leaves it
# out) and with no deadline (time_limit: none, D-013) -- built once from the `admiral`
# template (T-023) the first time `yamato admiral` is used, then driven by the existing
# seat / talk / send / seat-stop / rotate machinery (Q5 of work/requirements/admiral.md).
ADMIRAL_DIRNAME = "_admiral"
ADMIRAL_TEMPLATE = "admiral"
# passed as ship.create's `name` (fills a template `{{name}}`), so `session_name()` (team["name"]
# + "." + seat) reads "yamato.admiral" for the (single, hub) seat "admiral" the template defines
ADMIRAL_SHIP_NAME = "yamato"
ADMIRAL_STOP_NOTE = ("[yamato admiral --stop] 引き継ぎ (handoff.md) を書いて "
                     f"`{yamato_invocation()} seat-stop` で終業してください。")
ADMIRAL_STOP_WAIT = 20   # --force が「待っても止まらなければ」で待つ秒数 (モジュール定数。テストで縮める)


def out(msg: str = "") -> None:
    print(msg, flush=True)


# --- the ship list ------------------------------------------------------------

def all_ships() -> dict[str, Path]:
    """Every ship: ``$YAMATO_HOME/ships.json``, then the folders under ``$YAMATO_HOME``.

    A name starting with ``_`` (``_admiral``, T-020) is never a normal ship's own name
    (``check_name`` forbids it) and is left out here: it is the admiral's special, unlisted
    folder. ``resolve_ship``/``status`` still find it directly by that name/path."""
    ships = {name: Path(p) for name, p in load_registry().items() if not name.startswith("_")}
    home = yamato_home()
    if home.is_dir():
        for d in sorted(home.iterdir()):
            if not d.name.startswith("_") and (d / "team.yaml").is_file():
                ships.setdefault(d.name, d)
    return {n: p for n, p in sorted(ships.items()) if (p / "team.yaml").is_file()}


# --- red seats (design-p1 §5.2) ---------------------------------------------------

def stale_after(team: dict) -> int:
    return (team.get("watch") or {}).get("stale_after") or parse_duration(WATCH_FALLBACK["stale_after"])


def red_flags(team: dict, seat_name: str, rec: dict, live: dict | None, now: float, *,
              has_active: bool = True) -> list[str]:
    """Why a seat shows red: a live seat idle for longer than ``watch.stale_after``, one
    waiting on an open prompt (``status: waiting``), or one whose API call failed
    (``state: failed``, verify-p1-d V5). A stopped seat is not red: liveness is the pid,
    never ``state`` (verify-p0-c Q5). ``state: blocked`` alone is not red (v1.2.0): it is
    Claude Code's label on the last words, so it misfires; ``status=idle`` already shows a
    seat is idle.

    ``has_active``: whether the seat has an ``active`` board item (design-drift #11, D-019).
    A live ``per_task`` seat with none is flagged -- it should have been ``seat-stop``'d when
    its task ended; a caller that does not track the board (or has none to check) can leave
    the default and this check is skipped."""
    if not claude.is_alive(live):
        return []
    flags = []
    waiting = str(live.get("waitingFor") or "")
    status, state = live.get("status"), live.get("state")
    if status == "waiting" or waiting:
        flags.append(f"詰まり: {waiting or '何か'} で止まっている (status: waiting, waitingFor: {waiting or '-'})")
    if state == "failed":
        flags.append("API エラー (state: failed)")
    last = seat._last_active(rec)
    limit = stale_after(team)
    if last and now - last > limit:
        flags.append(f"{fmt_span(now - last)} 動いていない (生きているのに最終が {fmt_span(limit)} より古い)")
    if team["seats"].get(seat_name, {}).get("shift") == "per_task" and not has_active:
        flags.append("per_task の席が生きているのに担当 (active) の項目が無い (前の task の会話が持ち込まれる疑い。#11)")
    return flags


# --- extend / halt ------------------------------------------------------------------

def extend(shipdir: Path, span: str) -> int:
    seconds = parse_duration(span)
    if seconds <= 0:
        raise YamatoError("延ばす時間は 0 より長くする")
    data, was_over = deadline.extend(shipdir, seconds)
    if data is None:
        raise YamatoError(f"艦は起動していない (deadline なし)。`yamato up {shipdir}` で起動する")
    team = seat.current_team(shipdir)
    if was_over:
        # the seats already told to wrap up are told again at the new deadline
        with ship_lock(shipdir):
            for s in team["seats"]:
                (shipdir / ".runtime" / f"wrapup-{s}.json").unlink(missing_ok=True)
    # the watchdog of this deadline may have ended (past the deadline with no seat alive);
    # a second one for the same token exits at once
    seat.spawn_watchdog(shipdir, data["token"])
    out(f"艦 {team['name']} の deadline を延ばした: {fmt_time(data['deadline'])} "
        f"(残り {fmt_span(data['deadline'] - time.time())}、強制停止は {fmt_time(data['graceUntil'])})")
    if was_over:
        out("  deadline は過ぎていたので、今から数えた。終業の指示を受けてもう止まった席は、次の send で起きる。")
    return 0


def halt(shipdir: Path) -> int:
    """Emergency stop: no grace, straight to the force stop (like ``down --force``)."""
    team = seat.current_team(shipdir)
    seat.reconcile(shipdir, team, claude.agents())
    dl = deadline.end_now(shipdir)
    if dl is not None:
        with ship_lock(shipdir):
            dl["graceUntil"] = min(dl["graceUntil"], time.time())
            deadline.write_raw(shipdir, dl)
    stopped = seat.force_stop_all(shipdir, team, reason="halt")
    out(f"艦 {team['name']} を緊急停止した。強制停止した席: {', '.join(stopped) if stopped else '(なし)'}")
    if dl is not None and not stopped:
        # force_stop_all runs the report's safety net only when it stopped something
        for line in report.safety_net(shipdir, team, "halt"):
            out(line)
    return 0


# --- up --seats ---------------------------------------------------------------------

def parse_seats(team: dict, text: str | None) -> list[str]:
    names = [s.strip() for s in (text or "").split(",") if s.strip()]
    for s in names:
        seat_spec(team, s)
    return [s for s in dict.fromkeys(names) if s != team["hub"]]


def up(shipdir: Path, for_: str | None, seats: str | None) -> int:
    team0 = load_team(shipdir)
    # team.yaml's up_seats first, then --seats; duplicates and the hub drop out
    extra = parse_seats(team0, ",".join([*(team0.get("up_seats") or []), seats or ""]))   # a wrong name fails before anything starts
    rc = seat.up(shipdir, for_)
    if rc or not extra:
        return rc
    team = seat.current_team(shipdir)
    label = {"alive": "すでに動いている", "resumed": "resume した", "started": "新しいシフトを起動した",
             "spawned": "headless のシフトを起動した (run-headless)", "queued": "headless のシフト中"}
    failed = []
    for s in extra:
        # one seat that does not come up must not keep the rest down: wake them all, then fail
        try:
            what, rec = seat.wake(shipdir, team, s, reason="up")
        except YamatoError as e:
            failed.append(s)
            out(f"  席 {s}: 起動に失敗 ({e})")
            continue
        out(f"  席 {s}: {label[what]}" + (f" (session {rec.get('sessionId')})" if what != "spawned" else ""))
    if failed:
        raise YamatoError(f"起動できなかった席があります: {', '.join(failed)} (ほかの席は起動した)")
    return 0


# --- ships --------------------------------------------------------------------------

def _usage_today(shipdir: Path) -> int:
    since = time.mktime(time.strptime(today(), "%Y-%m-%d"))
    total = 0
    try:
        text = (shipdir / "usage.jsonl").read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0
    for raw in text.splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and (row.get("ts") or 0) >= since:
            total += int(row.get("total_tokens") or 0)
    return total


def ship_line(name: str, shipdir: Path, by: dict, now: float) -> str:
    try:
        team = seat.current_team(shipdir)
    except YamatoError as e:
        return f"{name:<12} 読めない: {e}"
    dl = deadline.read(shipdir)
    ph = deadline.phase(dl, now)
    alive = [s for s in team["seats"] if claude.is_alive(by.get(roster.seat(shipdir, s).get("sessionId")))]
    if ph == deadline.NOT_UP:
        state = "停止中" + (f" (動いている席 {len(alive)})" if alive else "")
    elif ph == deadline.RUNNING:
        state = f"稼働中 残り {fmt_span(dl['deadline'] - now)}"
    elif ph == deadline.OVER:
        state = f"終業中 強制停止まで {fmt_span(dl['graceUntil'] - now)}"
    else:
        state = "上限切れ" + (f" (動いている席 {len(alive)})" if alive else "")
    hub = team["hub"]
    hub_rec = roster.seat(shipdir, hub)
    last = seat._last_active(hub_rec) if hub_rec else None
    hub_col = f"captain {hub}: {'生' if hub in alive else '止'} 最終 {fmt_time(last)}" + (
        f" ({fmt_span(now - last)}前)" if last else "")
    from . import board as board_mod

    active_assignees = {m.get("assignee") for m in board_mod.Board(shipdir, team).items()
                        if m.get("state") == "active"}
    red = []
    for s in team["seats"]:
        rec = roster.seat(shipdir, s)
        if red_flags(team, s, rec, by.get(rec.get("sessionId")), now, has_active=s in active_assignees):
            red.append(s)
    decisions = len(report.pending_decisions(shipdir, team, lambda d: report.is_human(team, d)))
    latest = report.latest(shipdir)
    cols = [
        f"{name:<12}",
        state,
        hub_col,
        f"赤 {len(red)}" + (f" ({','.join(red)})" if red else ""),
        f"判断待ち {decisions}",
        f"今日 {report._tokens(_usage_today(shipdir))} tok",
        f"日報 {latest.stem if latest else '-'}",
    ]
    return "  ".join(cols)


def ships() -> int:
    found = all_ships()
    if not found:
        out("艦がありません (yamato ship create で作る)")
        return 0
    by = claude.by_session(claude.agents())
    now = time.time()
    for name, path in found.items():
        out(ship_line(name, path, by, now))
    return 0


# --- talk ---------------------------------------------------------------------------

def _live_short(shipdir: Path, name: str) -> str | None:
    rec = roster.seat(shipdir, name)
    live = claude.find(rec["sessionId"]) if rec.get("sessionId") else None
    if not claude.is_alive(live):
        return None
    return live.get("id") or rec.get("shortId") or rec["sessionId"][:8]


def _wake_for_attach(shipdir: Path, name: str | None) -> tuple[dict, str, str]:
    """The seat to attach to, woken first by the same rule as ``send`` (a note in its inbox, then
    resume / a new shift) if it is stopped: (team, seat name, short id). Attaching to a stopped
    session would start a copy (design §10)."""
    team = seat.current_team(shipdir)
    name = name or team.get("talk_default") or team["hub"]
    if seat_spec(team, name)["shift"] == "headless":
        # claude -p has no session to attach to; send reaches it at its next shift
        raise YamatoError(f"席 {name} は headless (claude -p) なので attach できない。"
                          f"`yamato send {team['name']} {name} \"...\"` で頼む")
    short = _live_short(shipdir, name)
    if short is None:
        if team["time_limit"] is not None and deadline.phase(deadline.read(shipdir)) != deadline.RUNNING:
            # send would only record the note and not wake the seat: say so without the note.
            # A ship with no time limit at all (time_limit: none, D-013, the admiral) has no
            # "up" to wait for: it is always in bounds, so this gate does not apply to it.
            raise YamatoError(f"席 {name} は止まっていて、艦は稼働時間の外 ({deadline.describe(deadline.read(shipdir))})。"
                              f"`yamato up` か `yamato extend` のあとで talk する")
        out(f"席 {name} は止まっているので、send と同じ規則で起こしてから attach する。")
        seat.send(shipdir, name, TALK_NOTE, inbox.OWNER)
        short = _live_short(shipdir, name)
        if short is None:
            raise YamatoError(f"席 {name} を起こせなかったので attach しない (上の send の結果を参照)")
    return team, name, short


def talk(shipdir: Path, name: str | None, *, execvp=os.execvp) -> int:
    """Attach to a seat in the foreground (woken first if stopped, see ``_wake_for_attach``)."""
    team, name, short = _wake_for_attach(shipdir, name)
    out(f"claude attach {short} ({team['name']}.{name})")
    return procs.run_foreground([*claude.claude_cmd(), "attach", short], execvp=execvp)


# --- yamato admiral: bootstrap, attach, stop (D-011, T-021) ---------------------------

def admiral_dir() -> Path:
    return yamato_home() / ADMIRAL_DIRNAME


def ensure_admiral() -> Path:
    """``_admiral/`` built once, from the ``admiral`` template, the first time it is used
    (T-020's ``ship.create(..., register=False)``). Already there: left untouched (owner
    edits to team.yaml / charter.md / roles/admiral.md are never overwritten)."""
    from . import ship

    shipdir = admiral_dir()
    if (shipdir / "team.yaml").is_file():
        return shipdir
    created, warnings = ship.create(ADMIRAL_SHIP_NAME, None, str(shipdir), ADMIRAL_TEMPLATE, register=False)
    for w in warnings:
        out(f"注意: {w}")
    return created


def wake_admiral() -> None:
    """The admiral's pane in ``yamato view`` is only a ``view attach``, which never wakes a stopped
    seat; the admiral is who the owner opens the view to talk to, so wake it the way
    ``yamato admiral`` does (created on the first call, stopped -> send -> resume)."""
    shipdir = ensure_admiral()
    seat.prepare(shipdir)
    _wake_for_attach(shipdir, None)


def admiral_talk(*, execvp=os.execvp) -> int:
    """``yamato admiral``: 生きていれば attach、止まっていれば talk と同じ規則で起こしてから attach
    する (D-011)。``_admiral/`` は無ければここで初めて作る。settings / agents は毎回 ``prepare`` で
    作り直す (team.yaml を直したときに次の attach から効くのは他のどの艦とも同じ)。"""
    shipdir = ensure_admiral()
    seat.prepare(shipdir)
    return talk(shipdir, None, execvp=execvp)


def admiral_stop(force: bool = False) -> int:
    """``yamato admiral --stop`` (``--force`` つき): admiral は締切を持たない (D-013) ので、
    ``down`` のような一時的な締切は書かない。代わりに、既存の send の経路で「引き継ぎを書いて
    seat-stop しろ」を admiral の inbox に置く (自分で読んで自分を止める)。それでも
    ``ADMIRAL_STOP_WAIT`` 秒 (モジュール定数) 止まらなければ、``--force`` で ``down --force`` /
    ``halt`` と同じ強制停止に落ちる (黙って止めない: events と作業ログに残る、``force_stop_all`` 任せ)。"""
    shipdir = admiral_dir()
    if not (shipdir / "team.yaml").is_file():
        raise YamatoError("admiral はまだ一度も起こしていない (`yamato admiral` で起こす)")
    team = seat.current_team(shipdir)
    name = team["hub"]
    rec = roster.seat(shipdir, name)
    live = claude.find(rec["sessionId"]) if rec.get("sessionId") else None
    if not claude.is_alive(live):
        out("admiral はすでに止まっている。")
        return 0
    if rec.get("state") == roster.STOPPING:
        out("admiral はすでに終業を受け付けている (seat-stop 済み)。止まるのを待つ。")
    else:
        seat.send(shipdir, name, ADMIRAL_STOP_NOTE, inbox.OWNER)
        out(f"admiral (session {rec.get('shortId')}) に、引き継ぎを書いて seat-stop するよう伝えた。")
    if not force:
        out("自分で止まらなければ `yamato admiral --stop --force` で強制停止する。")
        return 0
    out(f"最大 {ADMIRAL_STOP_WAIT} 秒待って、それでも生きていれば強制停止する。")
    if claude.wait_gone(rec["sessionId"], timeout=ADMIRAL_STOP_WAIT):
        out("admiral は自分で終業した。")
        return 0
    out(f"{ADMIRAL_STOP_WAIT} 秒待っても止まらなかったので強制停止する (down --force と同じ扱い)。")
    stopped = seat.force_stop_all(shipdir, team, reason="admiral-stop-force")
    out(f"強制停止した: {', '.join(stopped) if stopped else '(なし)'}")
    return 0


# --- CLI ----------------------------------------------------------------------------

def register(sub) -> None:
    e = sub.add_parser("extend", help="(admiral) deadline を延ばす")
    e.add_argument("ship")
    e.add_argument("span", help="延ばす時間 (例 1h, 30m)")
    h = sub.add_parser("halt", help="(admiral) 緊急停止。猶予なしで強制停止する")
    h.add_argument("ship")
    sub.add_parser("ships", help="(admiral) 全艦を 1 行ずつ")
    t = sub.add_parser("talk", help="(admiral) 席に attach して直接話す。止まっていれば起こしてから")
    t.add_argument("ship")
    t.add_argument("seat", nargs="?", help="既定は team.yaml の talk_default (省略時 hub)")
    a = sub.add_parser("admiral", help="(owner) 常駐の admiral セッションに attach する。"
                                        "止まっていれば talk と同じ規則で起こしてから (D-011)")
    a.add_argument("--stop", action="store_true", help="引き継ぎを促して止める (seat-stop は艦の席と同じに使える)")
    a.add_argument("--force", action="store_true",
                   help="--stop と一緒に使う。待っても自分で止まらなければ強制停止する (down --force と同じ扱い)")


def run(args) -> int:
    if args.cmd == "ships":
        return ships()
    shipdir = resolve_ship(args.ship)
    if args.cmd == "extend":
        return extend(shipdir, args.span)
    if args.cmd == "halt":
        return halt(shipdir)
    if args.cmd == "talk":
        return talk(shipdir, args.seat)
    return 1

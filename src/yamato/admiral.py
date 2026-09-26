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

from . import claude, deadline, inbox, report, roster, seat
from .team import WATCH_FALLBACK, load_team, seat_spec
from .util import (YamatoError, fmt_span, fmt_time, load_registry, parse_duration, resolve_ship,
                   ship_lock, today, yamato_home)

TALK_NOTE = "[yamato talk] owner がこれから `claude attach` でこの席に来て直接話します。起きたら待っていてください。"


def out(msg: str = "") -> None:
    print(msg, flush=True)


# --- the ship list ------------------------------------------------------------

def all_ships() -> dict[str, Path]:
    """Every ship: ``$YAMATO_HOME/ships.json``, then the folders under ``$YAMATO_HOME``."""
    ships = {name: Path(p) for name, p in load_registry().items()}
    home = yamato_home()
    if home.is_dir():
        for d in sorted(home.iterdir()):
            if (d / "team.yaml").is_file():
                ships.setdefault(d.name, d)
    return {n: p for n, p in sorted(ships.items()) if (p / "team.yaml").is_file()}


# --- red seats (design-p1 §5.2) ---------------------------------------------------

def stale_after(team: dict) -> int:
    return (team.get("watch") or {}).get("stale_after") or parse_duration(WATCH_FALLBACK["stale_after"])


def red_flags(team: dict, rec: dict, live: dict | None, now: float) -> list[str]:
    """Why a seat shows red: a live seat idle for longer than ``watch.stale_after``,
    one waiting on a permission prompt, or one whose API call failed (``state: failed``,
    verify-p1-d V5). A stopped seat is not red."""
    if not claude.is_alive(live):
        return []
    flags = []
    waiting = str(live.get("waitingFor") or "")
    if "permission" in waiting:
        flags.append(f"詰まり: 権限の確認で止まっている (waitingFor: {waiting})")
    if live.get("state") == "failed":
        flags.append("API エラー (state: failed)")
    last = seat._last_active(rec)
    limit = stale_after(team)
    if last and now - last > limit:
        flags.append(f"{fmt_span(now - last)} 動いていない (生きているのに最終が {fmt_span(limit)} より古い)")
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
    extra = parse_seats(load_team(shipdir), seats)   # a wrong name fails before anything starts
    rc = seat.up(shipdir, for_)
    if rc or not extra:
        return rc
    team = seat.current_team(shipdir)
    label = {"alive": "すでに動いている", "resumed": "resume した", "started": "新しいシフトを起動した",
             "spawned": "headless のシフトを起動した (run-headless)", "queued": "headless のシフト中"}
    for s in extra:
        what, rec = seat.wake(shipdir, team, s, reason="up")
        out(f"  席 {s}: {label[what]}" + (f" (session {rec.get('sessionId')})" if what != "spawned" else ""))
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
    red = []
    for s in team["seats"]:
        rec = roster.seat(shipdir, s)
        if red_flags(team, rec, by.get(rec.get("sessionId")), now):
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


def talk(shipdir: Path, name: str | None, *, execvp=os.execvp) -> int:
    """Attach to a seat in the foreground. A stopped seat is woken first by the same
    rule as ``send`` (a note in its inbox, then resume / a new shift): attaching to a
    stopped session would start a copy (design §10)."""
    team = seat.current_team(shipdir)
    name = name or team.get("talk_default") or team["hub"]
    if seat_spec(team, name)["shift"] == "headless":
        # claude -p has no session to attach to; send reaches it at its next shift
        raise YamatoError(f"席 {name} は headless (claude -p) なので attach できない。"
                          f"`yamato send {team['name']} {name} \"...\"` で頼む")
    short = _live_short(shipdir, name)
    if short is None:
        if deadline.phase(deadline.read(shipdir)) != deadline.RUNNING:
            # send would only record the note and not wake the seat: say so without the note
            raise YamatoError(f"席 {name} は止まっていて、艦は稼働時間の外 ({deadline.describe(deadline.read(shipdir))})。"
                              f"`yamato up` か `yamato extend` のあとで talk する")
        out(f"席 {name} は止まっているので、send と同じ規則で起こしてから attach する。")
        seat.send(shipdir, name, TALK_NOTE, inbox.OWNER)
        short = _live_short(shipdir, name)
        if short is None:
            raise YamatoError(f"席 {name} を起こせなかったので attach しない (上の send の結果を参照)")
    out(f"claude attach {short} ({team['name']}.{name})")
    execvp(claude.claude_bin(), [claude.claude_bin(), "attach", short])
    return 0   # only reached when execvp is replaced (tests)


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

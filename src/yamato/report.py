"""captain の日報 (design-p1 §2): ``reports/daily/<日付>.md``.

``yamato report daily`` fills the fact sections mechanically (no LLM) from the
board, events.jsonl, usage.jsonl and the live seat listing. The captain only
writes 「一言」 and 「明日」 (role prompt, not checked here); ``report send``
then notifies the owner with the summary (一言 + 判断待ち + 異常, §2.4).

Whether a report is made at the end of the day is ``report.daily`` in
team.yaml (mechanism-not-policy). With ``on_down``, ``yamato down`` and the
forced stops call ``safety_net``: if the day's report is missing it makes a
``--facts-only`` one, and if it was never sent it sends it, so the owner
always gets something even when the captain is down (§2.2 の 2).
"""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import board as board_mod
from . import events, notify, roster
from .util import YamatoError, atomic_write, read_json, ship_lock, today

REPORT_MADE = "report_made"    # events.jsonl kinds (docs/events.md)
REPORT_SENT = "report_sent"

DAILY_MODES = ("on_down", "off")
DAILY_FALLBACK = "on_down"     # a ship whose team.json predates `report:`

MAX_LINES = 60                 # §2.1
NOTIFY_LINES = 20              # §2.4
SECTION_ITEMS = 8              # list items per section before 「…ほか N 件」

S_WORD = "一言"
S_DECISIONS = "owner の判断待ち"
S_DONE = "今日終わったもの"
S_MOVING = "動いているもの・止まっているもの"
S_ANOMALY = "異常"
S_USAGE = "使用量"
S_TOMORROW = "明日"
INJECT_SECTIONS = (S_WORD, S_DECISIONS, S_TOMORROW)   # §2.3
NOTIFY_SECTIONS = (S_WORD, S_DECISIONS, S_ANOMALY)    # §2.4

CAPTAIN_BLANK = "(captain が書く)"
FORCED_KINDS = ("down-force", "grace-exceeded")


def daily_mode(team: dict) -> str:
    return ((team.get("report") or {}).get("daily")) or DAILY_FALLBACK


def reports_dir(shipdir: Path) -> Path:
    return Path(shipdir) / "reports" / "daily"


def report_path(shipdir: Path, date: str) -> Path:
    return reports_dir(shipdir) / f"{date}.md"


def _check_date(date: str | None) -> str:
    date = date or today()
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise YamatoError(f"日付は YYYY-MM-DD で指定してください (今: {date!r})") from None
    return date


def day_span(date: str) -> tuple[float, float]:
    start = datetime.strptime(date, "%Y-%m-%d")
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def _hm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _capped(lines: list[str], limit: int = SECTION_ITEMS) -> list[str]:
    if len(lines) <= limit:
        return lines
    return lines[:limit] + [f"- …ほか {len(lines) - limit} 件"]


# --- facts ------------------------------------------------------------------

def _all_items(shipdir: Path) -> dict[str, tuple[dict, str, bool]]:
    """Every board item (tasks, decisions, ...) by id: (meta, body, archived)."""
    out = {}
    for sub, archived in (("items", False), ("archive", True)):
        d = Path(shipdir) / "board" / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            try:
                meta, body = board_mod.loads(p.read_text(encoding="utf-8"))
            except (YamatoError, OSError):
                continue
            if meta.get("id"):
                out[meta["id"]] = (meta, body, archived)
    return out


def is_human(team: dict, decider) -> bool:
    """A decider that is neither a role nor a seat of the ship is a human (``owner``)."""
    return bool(decider) and decider not in (team.get("roles") or {}) and decider not in (team.get("seats") or {})


def pending_decisions(shipdir: Path, team: dict, decider=None) -> list[tuple[dict, str]]:
    """Open ``kind: decision`` items, read straight from the board's fixed fields.

    ``decider``: None = anyone, a name, or a predicate on the decider. Kept
    apart so it can move onto ``decide list`` once decide.py exists (§1.2).
    """
    if isinstance(decider, str):
        name = decider
        decider = lambda d: d == name  # noqa: E731
    out = []
    for meta, body, _ in _all_items(shipdir).values():
        if meta.get("kind") != "decision" or meta.get("state") == "done":
            continue
        if decider is not None and not decider(meta.get("decider")):
            continue
        out.append((meta, body))
    return out


def _recommendation(body: str) -> str:
    """The first line under 「## 選択肢と推し」 (§2.2)."""
    m = re.search(r"^##\s*選択肢と推し\s*$\n(.*?)(?=^##\s|\Z)", body, re.M | re.S)
    if not m:
        return ""
    for line in m.group(1).splitlines():
        line = line.strip().lstrip("-*").strip()
        if line:
            return line[:80]
    return ""


def _age(seconds: float) -> str:
    if seconds >= 86400:
        return f"{int(seconds // 86400)} 日"
    if seconds >= 3600:
        return f"{int(seconds // 3600)} 時間"
    return f"{max(1, int(seconds // 60))} 分"


def decision_lines(shipdir: Path, team: dict, date: str, now: float) -> list[str]:
    opened = {e.get("item"): e["ts"] for e in events.read(shipdir, kinds=events.DECISION_OPEN)}
    lines = []
    for meta, body in pending_decisions(shipdir, team, lambda d: is_human(team, d)):
        words = [f"- {meta['id']} {meta.get('title') or ''}".rstrip()]
        info = []
        if meta["id"] in opened:
            info.append(f"待ち {_age(now - opened[meta['id']])}")
        due = meta.get("due")
        if due:
            info.append(f"期限 {due}" + (" 切れ" if str(due) < date else ""))
        if info:
            words.append(f"({', '.join(info)})")
        rec = _recommendation(body)
        if rec:
            words.append(f"… 推し: {rec}")
        lines.append(" ".join(words))
    return lines


def done_lines(shipdir: Path, items: dict, since: float, until: float) -> list[str]:
    lines, seen = [], set()
    for e in events.read(shipdir, since=since, until=until, kinds=(events.BOARD_SET, events.BOARD_ADD)):
        changes = (e.get("data") or {}).get("changes") or {}
        fields = (e.get("data") or {}).get("fields") or {}
        if (changes.get("state") or [None, None])[1] != "done" and fields.get("state") != "done":
            continue
        iid = e.get("item")
        if iid in seen:
            continue
        seen.add(iid)
        meta = items.get(iid, ({}, "", False))[0]
        if meta.get("kind") == "decision":
            continue   # closed decisions are not work done
        extra = [x for x in (meta.get("assignee") or e.get("seat"), meta.get("pr") and f"PR {meta['pr']}") if x]
        tail = f" ({', '.join(extra)})" if extra else ""
        lines.append(f"- {iid} {meta.get('title') or e.get('summary') or ''}{tail}")
    return lines


def moving_lines(shipdir: Path, items: dict) -> list[str]:
    seats = roster.load(shipdir)["seats"]
    active, blocked, waiting = [], [], 0
    for iid, (meta, _, _) in items.items():
        if meta.get("kind") == "decision":
            continue
        state = meta.get("state")
        who = meta.get("assignee") or "-"
        if state == "active":
            last = (seats.get(who) or {}).get("lastActive")
            when = f" (最後に動いた {_hm(last)})" if last else ""
            active.append(f"- {iid} {who} 実装中: {meta.get('title')}{when}")
        elif state == "blocked":
            on = ",".join(meta.get("blocked_on") or []) or "?"
            blocked.append(f"- {iid} blocked: {on} ({who}: {meta.get('title')})")
        elif state == "open":
            waiting += 1
    lines = _capped(active) + _capped(blocked)
    if waiting:
        lines.append(f"- 未着手 {waiting} 件")
    return lines


def _permission_prompt_seats(team: dict, shipdir: Path) -> list[str]:
    """Seats stuck on a permission dialog now (verify-p0-a Q1). Best-effort."""
    from . import claude

    try:
        by = claude.by_session(claude.agents())
    except Exception:  # noqa: BLE001 — the report must not fail on the listing
        return []
    out = []
    for seat in team.get("seats") or {}:
        live = by.get(roster.seat(shipdir, seat).get("sessionId")) or {}
        if "permission" in str(live.get("waitingFor") or ""):
            out.append(f"- {seat} が権限の確認で止まっている (waitingFor: {live['waitingFor']})")
    return out


def anomaly_lines(shipdir: Path, team: dict, since: float, until: float, live: bool) -> list[str]:
    evs = events.read(shipdir, since=since, until=until)
    lines = []
    forced = {}
    for e in evs:
        if e.get("kind") == events.FORCE_STOP:
            forced[(e.get("seat"), (e.get("data") or {}).get("shiftNo"))] = e
    for e in evs:
        kind, d = e.get("kind"), e.get("data") or {}
        if kind == events.FORCE_STOP:
            end = next((x for x in evs if x.get("kind") == events.SHIFT_END and x.get("seat") == e.get("seat")
                        and (x.get("data") or {}).get("shiftNo") == d.get("shiftNo")), None)
            no_handoff = end is not None and (end.get("data") or {}).get("handoffWritten") is False
            lines.append(f"- {e.get('seat')} が {_hm(e['ts'])} に強制停止 ({d.get('reason') or '?'})"
                         + (" (引き継ぎなし)。次のシフトは作業ログの末尾から再開する" if no_handoff else ""))
        elif kind == events.SHIFT_END and d.get("handoffWritten") is False \
                and d.get("reason") not in FORCED_KINDS and (e.get("seat"), d.get("shiftNo")) not in forced:
            lines.append(f"- {e.get('seat')} が {_hm(e['ts'])} に引き継ぎなしで終了 ({d.get('reason') or '?'})")
        elif kind == notify.NOTIFY_FAILED:
            lines.append(f"- {_hm(e['ts'])} {e.get('summary')}")
    denied: dict = {}
    for e in evs:
        if e.get("kind") == events.PERMISSION_DENIED:
            denied.setdefault(e.get("seat") or "?", []).append((e.get("data") or {}).get("tool") or "?")
    if denied:
        total = sum(len(v) for v in denied.values())
        per = []
        for seat, tools in denied.items():
            counts: dict = {}
            for t in tools:
                counts[t] = counts.get(t, 0) + 1
            per.append(f"{seat}: " + ", ".join(f"{t} ×{n}" for t, n in counts.items()))
        lines.append(f"- 権限の拒否 {total} 件 ({' / '.join(per)})")
    if live:
        lines += _permission_prompt_seats(team, shipdir)
    return lines


def _tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def usage_lines(shipdir: Path, since: float, until: float) -> list[str]:
    import json

    rows = []
    try:
        text = (Path(shipdir) / "usage.jsonl").read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    for raw in text.splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and since <= (row.get("ts") or 0) < until:
            rows.append(row)
    if not rows:
        return ["- シフト 0 回"]
    s = lambda k: sum(int(r.get(k) or 0) for r in rows)  # noqa: E731
    cache = s("cache_creation_input_tokens") + s("cache_read_input_tokens")
    return [f"- シフト {len(rows)} 回 / 合計 入力 {_tokens(s('input_tokens'))}・出力 {_tokens(s('output_tokens'))}"
            f"・cache {_tokens(cache)} トークン (席別は usage.jsonl)"]


def _span_label(shipdir: Path, since: float, until: float, now: float) -> str:
    shifts = sorted(events.read(shipdir, since=since, until=until, kinds=(events.SHIFT_START, events.SHIFT_END)),
                    key=lambda e: e["ts"])
    words = []
    if shifts:
        start = shifts[0]["ts"]
        end = shifts[-1]["ts"] if shifts[-1].get("kind") == events.SHIFT_END else min(now, until)
        words.append(f"稼働 {_hm(start)}–{_hm(end)}")
    dl = read_json(Path(shipdir) / ".runtime" / "deadline", None) or {}
    if dl.get("upAt") and since <= dl["upAt"] < until and dl.get("deadline"):
        words.append(f"{_span(dl['deadline'] - dl['upAt'])} 枠")
    return f" ({' / '.join(words)})" if words else ""


def _span(seconds: float) -> str:
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h}h{m:02d}m" if h and m else (f"{h}h" if h else f"{m}m")


# --- the report -------------------------------------------------------------

def build(shipdir: Path, team: dict, date: str, *, facts_only: str | None = None,
          now: float | None = None, live: bool = True) -> str:
    """The report text. ``facts_only``: the reason the captain could not write."""
    now = now or time.time()
    since, until = day_span(date)
    items = _all_items(shipdir)
    decisions = decision_lines(shipdir, team, date, now)
    blank = f"captain が書けなかった (理由: {facts_only})" if facts_only else CAPTAIN_BLANK
    sections = [
        (S_WORD, [blank]),
        (f"{S_DECISIONS} ({len(decisions)} 件)", _capped(decisions) or ["- なし"]),
        (S_DONE, _capped(done_lines(shipdir, items, since, until)) or ["- なし"]),
        (S_MOVING, moving_lines(shipdir, items) or ["- なし"]),
        (S_ANOMALY, _capped(anomaly_lines(shipdir, team, since, until, live)) or ["- なし"]),
        (S_USAGE, usage_lines(shipdir, since, until)),
        (S_TOMORROW, [blank]),
    ]
    out = [f"# {team['name']} 日報 {date}{_span_label(shipdir, since, until, now)}"]
    for title, body in sections:
        out += [f"## {title}", *body, ""]
    out = out[:-1]
    if len(out) > MAX_LINES:
        # the per-section caps keep us under 60 in practice; this is the last guard
        tail = out[-3:]   # 「明日」 always stays
        out = out[:MAX_LINES - 4] + ["…(上限 60 行で省略)"] + tail
    return "\n".join(out) + "\n"


def make(shipdir: Path, team: dict, date: str | None = None, *, facts_only: str | None = None,
         force: bool = False, live: bool = True) -> Path:
    date = _check_date(date)
    path = report_path(shipdir, date)
    text = build(shipdir, team, date, facts_only=facts_only, live=live)
    with ship_lock(shipdir):
        if path.exists() and not force:
            raise YamatoError(f"{path} はすでにある (captain の書いた欄を消さないため上書きしない。作り直すなら --force)")
        atomic_write(path, text)
    events.emit(shipdir, REPORT_MADE, summary=f"日報 {date} を作った" + (" (facts-only)" if facts_only else ""),
                data={"date": date, "factsOnly": bool(facts_only), **({"reason": facts_only} if facts_only else {})})
    return path


def sections(text: str) -> dict[str, str]:
    """``## 見出し`` -> body. A heading's 「(2 件)」 suffix is dropped from the key."""
    out: dict[str, str] = {}
    key = None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.*?)\s*(\(\d+ 件\))?\s*$", line)
        if m:
            key = m.group(1)
            out[key] = ""
        elif key is not None:
            out[key] += line + "\n"
    return {k: v.strip("\n") for k, v in out.items()}


def excerpt(text: str, names=INJECT_SECTIONS) -> str:
    """Only the named sections, with their original headings (§2.3, §2.4)."""
    kept, keep = [], False
    for line in text.splitlines():
        m = re.match(r"^##\s+(.*?)\s*(\(\d+ 件\))?\s*$", line)
        if m:
            keep = m.group(1) in names
        if keep:
            kept.append(line)
    return "\n".join(kept).strip("\n")


def latest(shipdir: Path) -> Path | None:
    files = sorted(reports_dir(shipdir).glob("????-??-??.md"))
    return files[-1] if files else None


def was_sent(shipdir: Path, date: str) -> bool:
    return any((e.get("data") or {}).get("date") == date for e in events.read(shipdir, kinds=REPORT_SENT))


def summary(text: str) -> tuple[str, str]:
    """(notify body of at most 20 lines, level)."""
    body = excerpt(text, NOTIFY_SECTIONS).splitlines()
    if len(body) > NOTIFY_LINES:
        body = body[:NOTIFY_LINES - 1] + ["…(続きは日報のファイル)"]
    secs = sections(text)
    anomalies = secs.get(S_ANOMALY, "- なし").strip() != "- なし"
    decisions = secs.get(S_DECISIONS, "- なし").strip() != "- なし"
    level = "error" if anomalies else ("waiting" if decisions else "info")
    return "\n".join(body), level


def send(shipdir: Path, team: dict, date: str | None = None) -> list[str]:
    """Notify the owner with the report's summary (§2.4). Returns notify's status lines."""
    date = _check_date(date)
    path = report_path(shipdir, date)
    if not path.exists():
        raise YamatoError(f"{path} がない (先に report daily で作る)")
    text = path.read_text(encoding="utf-8")
    body, level = summary(text)
    events.emit(shipdir, REPORT_SENT, summary=f"日報 {date} を通知した", data={"date": date, "level": level})
    return notify.notify(team, f"yamato {team['name']}: 日報 {date}", f"{body}\n\n全文: {path}", level,
                         shipdir=shipdir)


def safety_net(shipdir: Path, team: dict, reason: str, date: str | None = None) -> list[str]:
    """``down`` / forced stops (§2.2 の 2): make a facts-only report if the day has
    none, send it if it was never sent. Never raises: the stop must go on."""
    try:
        if daily_mode(team) == "off":
            return []
        date = _check_date(date)
        lines = []
        with ship_lock(shipdir):
            path = report_path(shipdir, date)
            if not path.exists():
                make(shipdir, team, date, facts_only=reason, live=False)
                lines.append(f"日報 {date} がなかったので事実だけで作った: {path}")
            if was_sent(shipdir, date):
                return lines
            # marked sent under the lock so two stopping processes do not both send
            events.emit(shipdir, REPORT_SENT, summary=f"日報 {date} を通知した ({reason})",
                        data={"date": date, "by": "safety_net"})
        text = path.read_text(encoding="utf-8")
        body, level = summary(text)
        lines += notify.notify(team, f"yamato {team['name']}: 日報 {date}", f"{body}\n\n全文: {path}", level,
                               shipdir=shipdir)
        return lines
    except Exception as e:  # noqa: BLE001 — a report never blocks a stop
        print(f"yamato: 日報の安全網に失敗した: {e}", file=sys.stderr)
        return []


# --- CLI --------------------------------------------------------------------

def register(sub) -> None:
    r = sub.add_parser("report", help="日報 (design-p1 §2)")
    rs = r.add_subparsers(dest="report_cmd", required=True)
    d = rs.add_parser("daily", help="reports/daily/<日付>.md に事実の節を埋めた下書きを作る (LLM を使わない)")
    d.add_argument("ship")
    d.add_argument("--date", help="YYYY-MM-DD (既定は今日)")
    d.add_argument("--facts-only", action="store_true",
                   help="「一言」「明日」を「captain が書けなかった」にして作り、すぐ通知する")
    d.add_argument("--reason", default="facts-only で作った", help="--facts-only の理由")
    d.add_argument("--force", action="store_true", help="すでにある日報を作り直す")
    s = rs.add_parser("send", help="日報の要約 (一言・判断待ち・異常) を notify.via で owner に送る")
    s.add_argument("ship")
    s.add_argument("--date", help="YYYY-MM-DD (既定は今日)")


def run(args) -> int:
    from .seat import current_team
    from .util import YAMATO_BIN, resolve_ship

    shipdir = resolve_ship(args.ship)
    team = current_team(shipdir)
    if args.report_cmd == "daily":
        path = make(shipdir, team, args.date, facts_only=args.reason if args.facts_only else None,
                    force=args.force)
        print(f"日報の下書きを作った: {path}")
        if args.facts_only:
            for line in send(shipdir, team, args.date):
                print(line)
        else:
            print(f"  「{S_WORD}」と「{S_TOMORROW}」だけを書き (他の節は直さない)、"
                  f"`{YAMATO_BIN} report send {shipdir}" + (f" --date {args.date}" if args.date else "")
                  + "` で owner に届ける")
        return 0
    if args.report_cmd == "send":
        for line in send(shipdir, team, args.date) or ["通知: notify.via が空なので送らない"]:
            print(line)
        return 0
    return 1

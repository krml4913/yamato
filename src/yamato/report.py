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

A second stop of the same day (e2e-p1 E): if something the report shows
happened after it was last made or sent, ``refresh`` rebuilds the fact
sections, keeping the captain's 「一言」「明日」, and it is sent again as an update.
"""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import board as board_mod
from . import events, notify, roster
from .pr import PR_CONFLICT, PR_MERGE, PR_OPEN
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
CAPTAIN_SECTIONS = (S_WORD, S_TOMORROW)
# what the fact sections are made of: one of these after the report was last made or
# sent means the report is out of date. Not shift starts/ends (the captain's own
# seat-stop after `report send`), nor notify_failed (the report's own notification)
FACT_KINDS = (events.BOARD_ADD, events.BOARD_SET, events.BOARD_ARCHIVE, events.DECISION_OPEN,
              events.DECISION_CLOSE, events.FORCE_STOP, events.SHIFT_FAILED, events.LAUNCH_FAILED,
              events.PERMISSION_DENIED,
              events.SPIN_SUSPECTED, events.DUPLICATE_SUSPECTED, events.CAPTAIN_GAP,
              PR_OPEN, PR_MERGE, PR_CONFLICT)
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
    for e in events.read(shipdir, since=since, until=until, kinds=(events.BOARD_SET, events.BOARD_ADD, PR_MERGE)):
        d = e.get("data") or {}
        merged = e.get("kind") == PR_MERGE   # a merge counts as done even while the item is still open
        changes, fields = d.get("changes") or {}, d.get("fields") or {}
        if not merged and (changes.get("state") or [None, None])[1] != "done" and fields.get("state") != "done":
            continue
        iid = e.get("item")
        if iid in seen:
            continue
        seen.add(iid)
        meta = items.get(iid, ({}, "", False))[0]
        if meta.get("kind") == "decision":
            continue   # closed decisions are not work done
        pr = f"PR {meta['pr']}" if meta.get("pr") else None
        if merged:
            pr = f"PR {d.get('pr') or meta.get('pr')} を {d.get('mergedBy') or e.get('by') or '?'} が merge"
        extra = [x for x in (meta.get("assignee") or e.get("seat"), pr) if x]
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
        elif kind in (notify.NOTIFY_FAILED, events.CAPTAIN_GAP, events.LAUNCH_FAILED):
            lines.append(f"- {_hm(e['ts'])} {e.get('summary')}")
        elif kind == events.DECISION_CLOSE and d.get("by_decider") is False:
            on = f"。--by は {d['on_behalf_of']}" if d.get("on_behalf_of") else ""
            lines.append(f"- {e.get('item')} の判断を decider ({d.get('decider') or '?'}) 以外の "
                         f"{d.get('closed_by') or e.get('by') or '?'} が {_hm(e['ts'])} に閉じた{on}")
        elif kind == PR_CONFLICT:
            to = f"。{d['notified']} に rebase を頼んだ" if d.get("notified") else ""
            lines.append(f"- {e.get('item')} の PR #{d.get('pr')} が {_hm(e['ts'])} に衝突 "
                         f"({d.get('mergedItem')} の PR #{d.get('mergedPr')} を merge){to}")
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
    # design-p1 §5.5: one line per sender -> recipient, however many sends tripped it
    loops: dict = {}
    for e in evs:
        if e.get("kind") in (events.SPIN_SUSPECTED, events.DUPLICATE_SUSPECTED):
            k = loops.setdefault((e.get("by") or "?", e.get("seat") or "?"), {"spin": 0, "dup": 0})
            k["spin" if e["kind"] == events.SPIN_SUSPECTED else "dup"] += 1
    for (by, to), n in loops.items():
        what = [f"送りすぎ {n['spin']} 回" if n["spin"] else "", f"同じ本文の連続 {n['dup']} 回" if n["dup"] else ""]
        lines.append(f"- 空回りの疑い {by} → {to} ({'、'.join(w for w in what if w)})")
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
        # a seat whose last shift event is a start is still on shift (e2e-p1: the captain
        # writes the report while the members' shifts have already ended)
        last = {}
        for e in shifts:
            last[e.get("seat")] = e
        on = any(e.get("kind") == events.SHIFT_START for e in last.values())
        end = min(now, until) if on else shifts[-1]["ts"]
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
          now: float | None = None, live: bool = True, keep: dict | None = None) -> str:
    """The report text. ``facts_only``: the reason the captain could not write.
    ``keep``: the captain's sections (「一言」「明日」) to carry over as they are."""
    now = now or time.time()
    since, until = day_span(date)
    items = _all_items(shipdir)
    decisions = decision_lines(shipdir, team, date, now)
    blank = f"captain が書けなかった (理由: {facts_only})" if facts_only else CAPTAIN_BLANK
    keep = keep or {}
    word = keep[S_WORD].splitlines() if keep.get(S_WORD) else [blank]
    tomorrow = keep[S_TOMORROW].splitlines() if keep.get(S_TOMORROW) else [blank]
    sections = [
        (S_WORD, word),
        (f"{S_DECISIONS} ({len(decisions)} 件)", _capped(decisions) or ["- なし"]),
        (S_DONE, _capped(done_lines(shipdir, items, since, until)) or ["- なし"]),
        (S_MOVING, moving_lines(shipdir, items) or ["- なし"]),
        (S_ANOMALY, _capped(anomaly_lines(shipdir, team, since, until, live)) or ["- なし"]),
        (S_USAGE, usage_lines(shipdir, since, until)),
        (S_TOMORROW, tomorrow),
    ]
    out = [f"# {team['name']} 日報 {date}{_span_label(shipdir, since, until, now)}"]
    for title, body in sections:
        out += [f"## {title}", *body, ""]
    out = out[:-1]
    if len(out) > MAX_LINES:
        # the per-section caps keep us under 60 in practice; this is the last guard
        tail = [f"## {S_TOMORROW}", *tomorrow]   # 「明日」 always stays
        out = out[:max(1, MAX_LINES - len(tail) - 1)] + ["…(上限 60 行で省略)"] + tail
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


def refresh(shipdir: Path, team: dict, date: str | None = None, *, live: bool = True) -> Path:
    """Rebuild the fact sections of the day's report, keeping the captain's 「一言」
    「明日」 as written (e2e-p1 E: the day's second stop)."""
    date = _check_date(date)
    path = report_path(shipdir, date)
    with ship_lock(shipdir):
        old = sections(path.read_text(encoding="utf-8"))
        atomic_write(path, build(shipdir, team, date, live=live,
                                 keep={k: old.get(k, "") for k in CAPTAIN_SECTIONS}))
    events.emit(shipdir, REPORT_MADE, summary=f"日報 {date} の事実の節を作り直した",
                data={"date": date, "factsOnly": False, "refreshed": True})
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


def _last(shipdir: Path, date: str, kinds) -> float | None:
    return max((e["ts"] for e in events.read(shipdir, kinds=kinds) if (e.get("data") or {}).get("date") == date),
               default=None)


def was_sent(shipdir: Path, date: str) -> bool:
    return _last(shipdir, date, REPORT_SENT) is not None


def _changed_after(shipdir: Path, date: str, mark: float | None) -> bool:
    if mark is None:
        return True
    _, until = day_span(date)
    return any(e["ts"] > mark for e in events.read(shipdir, since=mark, until=until, kinds=FACT_KINDS))


def changed_since_report(shipdir: Path, date: str) -> bool:
    """Something the report shows happened on ``date`` after the report was last made
    or sent: its fact sections are out of date (e2e-p1 E)."""
    return _changed_after(shipdir, date, _last(shipdir, date, (REPORT_MADE, REPORT_SENT)))


def needs_send(shipdir: Path, date: str) -> bool:
    """「最後に送ったあとに変化があるか」: never sent, or changed after the last send."""
    return _changed_after(shipdir, date, _last(shipdir, date, REPORT_SENT))


def _title(team: dict, date: str, update: bool) -> str:
    return f"yamato {team['name']}: 日報 {date}" + (" (更新)" if update else "")


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
    update = was_sent(shipdir, date)
    events.emit(shipdir, REPORT_SENT, summary=f"日報 {date} を通知した" + (" (更新)" if update else ""),
                data={"date": date, "level": level, **({"update": True} if update else {})})
    return notify.notify(team, _title(team, date, update), f"{body}\n\n全文: {path}", level, shipdir=shipdir)


def safety_net(shipdir: Path, team: dict, reason: str, date: str | None = None) -> list[str]:
    """``down`` / forced stops (§2.2 の 2): make a facts-only report if the day has
    none; if something happened after it was last made or sent (the day's second
    stop, e2e-p1 E), rebuild its fact sections keeping the captain's; send it unless
    it was sent and nothing changed since. Never raises: the stop must go on."""
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
            elif changed_since_report(shipdir, date):
                refresh(shipdir, team, date, live=False)
                lines.append(f"日報 {date} のあとに出来事があったので、事実の節を作り直した (一言・明日は残した): {path}")
            if not needs_send(shipdir, date):
                return lines
            update = was_sent(shipdir, date)
            # marked sent under the lock so two stopping processes do not both send
            events.emit(shipdir, REPORT_SENT, summary=f"日報 {date} を通知した ({reason})" + (" (更新)" if update else ""),
                        data={"date": date, "by": "safety_net", **({"update": True} if update else {})})
        text = path.read_text(encoding="utf-8")
        body, level = summary(text)
        lines += notify.notify(team, _title(team, date, update), f"{body}\n\n全文: {path}", level, shipdir=shipdir)
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
    d.add_argument("--force", action="store_true",
                   help="すでにある日報を一から作り直す (captain の書いた「一言」「明日」も消える。"
                        "付けなければ事実の節だけを作り直す)")
    s = rs.add_parser("send", help="日報の要約 (一言・判断待ち・異常) を notify.via で owner に送る")
    s.add_argument("ship")
    s.add_argument("--date", help="YYYY-MM-DD (既定は今日)")


def run(args) -> int:
    from .seat import current_team
    from .util import YAMATO_BIN, resolve_ship

    shipdir = resolve_ship(args.ship)
    team = current_team(shipdir)
    if args.report_cmd == "daily":
        date = _check_date(args.date)
        send_cmd = f"`{YAMATO_BIN} report send {shipdir}" + (f" --date {args.date}" if args.date else "") + "`"
        if report_path(shipdir, date).exists() and not (args.force or args.facts_only):
            # the day's second stop (e2e-p1 E): keep what the captain wrote, redo the facts
            if not needs_send(shipdir, date):
                print(f"日報 {date} は送信済みで、そのあと日報に載る出来事はない。作り直しも送り直しも要らない")
                return 0
            path = refresh(shipdir, team, date)
            print(f"すでにある日報の事実の節を作り直した (「{S_WORD}」「{S_TOMORROW}」は前のまま): {path}")
            print(f"  「{S_WORD}」と「{S_TOMORROW}」を今の状況に書き直し (他の節は直さない)、{send_cmd} で owner に届ける"
                  + (" (送信済みなので件名に「更新」が付く)" if was_sent(shipdir, date) else ""))
            return 0
        path = make(shipdir, team, args.date, facts_only=args.reason if args.facts_only else None,
                    force=args.force)
        print(f"日報の下書きを作った: {path}")
        if args.facts_only:
            for line in send(shipdir, team, args.date):
                print(line)
        else:
            print(f"  「{S_WORD}」と「{S_TOMORROW}」だけを書き (他の節は直さない)、{send_cmd} で owner に届ける")
        return 0
    if args.report_cmd == "send":
        for line in send(shipdir, team, args.date) or ["通知: notify.via が空なので送らない"]:
            print(line)
        return 0
    return 1

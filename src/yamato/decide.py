"""Decisions: ``yamato decide open / close / list`` and ``decisions/log.md`` (design-p1 §1).

A decision is a board item (``kind: decision``, id ``D-NNN``) kept next to the
tasks in ``board/items`` (``board/archive`` once closed). The code keeps only
the records consistent (mechanism-not-policy):

- the decider is resolved from team.yaml ``decisions`` when the item is opened
  and never changes afterwards;
- what a decision stops is held on the task side only (``blocked_on``); the
  decision has no ``blocks`` field, so the two sides cannot disagree;
- a closed decision is never rewritten (a new one ``--supersedes`` it);
- who opened and who closed (and on whose behalf) is always recorded.

Who may open or close is not checked: anyone may. A close by someone other
than the decider (or than ``--by <decider>``) is written to events.jsonl so
the daily report can show it.
"""
from __future__ import annotations

import argparse
import os
import re
import time
from datetime import date, datetime
from pathlib import Path

from . import board as bmod
from . import events, inbox, notify, roster
from .runtime import yamato_invocation
from .util import YamatoError, atomic_write, parse_duration, resolve_ship, ship_lock

OWNER = inbox.OWNER
ID_PREFIX = "D-"
ID_RE = re.compile(r"^D-(\d+)$")
DEFAULT_CATEGORY = "default"
PENDING = "(閉じるときに yamato が書く)"
LOG = Path("decisions") / "log.md"
_SECTION_RE = re.compile(r"^## 決定[ \t]*$", re.M)
_NEXT_SECTION_RE = re.compile(r"^## ", re.M)


# --- who is calling ----------------------------------------------------------

def caller(shipdir: Path) -> str:
    """Who runs this command, for the record only: the seat whose shift is
    ``$CLAUDE_CODE_SESSION_ID``, else the human (owner, at a terminal or in their
    own Claude Code session, which is not in the roster). Same rule as
    ``worktree.caller`` / ``pr``."""
    return roster.seat_of_session(shipdir, os.environ.get("CLAUDE_CODE_SESSION_ID")) or OWNER


# --- the table in team.yaml --------------------------------------------------

def resolve_decider(team: dict, category: str) -> str:
    """category -> decider. Unknown categories fall to ``default``, and without one to the hub."""
    table = team.get("decisions") or {}
    spec = table.get(category) or table.get(DEFAULT_CATEGORY)
    return spec["decider"] if spec else team["hub"]


def categories(team: dict) -> list[tuple[str, str, str | None]]:
    table = team.get("decisions") or {}
    rows = [(c, s["decider"], s.get("when")) for c, s in table.items()]
    if DEFAULT_CATEGORY not in table:
        rows.append((DEFAULT_CATEGORY, team["hub"], None))
    return rows


# --- storage -----------------------------------------------------------------

def _paths(brd: bmod.Board) -> list[Path]:
    out = []
    for d in (brd.items_dir, brd.archive_dir):
        if d.is_dir():
            out += list(d.glob(f"{ID_PREFIX}*.md"))
    return sorted(out, key=lambda p: _id_num(p.stem))


def _id_num(item_id: str) -> int:
    m = ID_RE.match(item_id)
    return int(m.group(1)) if m else 0


def _next_id(brd: bmod.Board) -> str:
    nums = [_id_num(p.stem) for p in _paths(brd)]
    return f"{ID_PREFIX}{(max(nums) if nums else 0) + 1:03d}"


def decisions(brd: bmod.Board, include_closed: bool = False) -> list[dict]:
    out = []
    for p in _paths(brd):
        meta, _ = bmod.loads(p.read_text(encoding="utf-8"))
        if meta.get("kind") == bmod.DECISION and (include_closed or meta.get("state") != "done"):
            out.append(meta)
    return out


def _read_decision(brd: bmod.Board, dec_id: str) -> tuple[dict, str, Path]:
    meta, body, path = brd.read(dec_id)
    if meta.get("kind") != bmod.DECISION:
        raise YamatoError(f"{dec_id} は判断の項目 (kind: decision) ではありません")
    return meta, body, path


def blocked_by(brd: bmod.Board, dec_id: str) -> list[dict]:
    """The tasks a decision stops: the reverse of their ``blocked_on`` (§1.1)."""
    return [m for m in brd.items() if dec_id in (m.get("blocked_on") or [])]


def _now_iso(now: float) -> str:
    return datetime.fromtimestamp(now).strftime("%Y-%m-%dT%H:%M:%S")


def _epoch(iso) -> float | None:
    try:
        return datetime.fromisoformat(str(iso)).timestamp()
    except (TypeError, ValueError):
        return None


# --- open --------------------------------------------------------------------

def open_decision(shipdir: Path, team: dict, *, category: str, title: str, blocks: list[str] = (),
                  links: list[str] = (), due: str | None = None, body: str = "", urgent: bool = False,
                  supersedes: str | None = None, by: str | None = None,
                  now: float | None = None) -> dict:
    """Create the D item and block the tasks. Returns what ``deliver_open`` needs.

    Delivery (send / notify) is left to the caller so that no seat is woken
    while the ship lock is held (the woken seat's hooks take the lock).
    """
    now = time.time() if now is None else now
    by = by or caller(shipdir)
    if not title.strip():
        raise YamatoError("--title が空です")
    if not str(category).strip():
        raise YamatoError("--category が空です")
    if due:
        try:
            date.fromisoformat(due)
        except ValueError:
            raise YamatoError(f"--due は YYYY-MM-DD (今: {due})") from None
    brd = bmod.Board(shipdir, team)
    with ship_lock(shipdir):
        tasks = []
        for tid in dict.fromkeys(blocks):
            meta, _, _ = brd.read(tid)
            if meta.get("kind") == bmod.DECISION:
                raise YamatoError(f"--blocks に判断の項目 ({tid}) は指定できません (止められるのはタスク)")
            if meta.get("state") == "done":
                raise YamatoError(f"{tid} は done なので止められません")
            tasks.append(meta)
        for lid in links:
            if not brd.exists(lid):
                raise YamatoError(f"--links={lid} が board にありません")
        if supersedes:
            _read_decision(brd, supersedes)
        dec_id = _next_id(brd)
        decider = resolve_decider(team, category)
        meta = {
            "id": dec_id,
            "kind": bmod.DECISION,
            "title": title.strip(),
            "category": category,
            "decider": decider,
            "opened_by": by,
            "opened_at": _now_iso(now),
            "state": "open",
            "due": due,
            "urgent": bool(urgent),
            "links": list(dict.fromkeys([t["id"] for t in tasks] + list(links))),
        }
        if supersedes:
            meta["supersedes"] = supersedes
        text = body.strip() + "\n\n" if body.strip() else "## 背景\n\n## 選択肢と推し\n\n"
        if not _SECTION_RE.search(text):
            text += f"## 決定\n{PENDING}\n\n"
        text += "## 経緯\n" + bmod._note_line(by, "判断を開いた")
        brd._write(meta, text)
        for t in tasks:
            fields = {"blocked_on": ",".join(list(t.get("blocked_on") or []) + [dec_id]), "state": "blocked"}
            # restored when blocked_on empties (§1.2). A task already blocked by hand (no earlier
            # decision saved a state) stays blocked when this decision closes.
            if t.get("state") != "blocked":
                fields["pre_blocked_state"] = t.get("state")
            elif t.get("pre_blocked_state") is None:
                fields["pre_blocked_state"] = "blocked"
            brd.set(t["id"], fields, note=f"{dec_id} の判断待ち: {meta['title']}", by=by)
        events.emit(shipdir, events.DECISION_OPEN, seat=decider, item=dec_id, by=by,
                    summary=f"判断を開いた ({category} → {decider}){' [急ぎ]' if urgent else ''} {meta['title']}",
                    data={"category": category, "decider": decider, "blocks": [t["id"] for t in tasks],
                          "links": meta["links"],
                          "urgent": bool(urgent), "due": due, "supersedes": supersedes})
    return meta


def deliver_open(shipdir: Path, team: dict, meta: dict, out=print) -> None:
    """Hand the new decision to its decider (§1.2 step 3)."""
    from . import seat as seatmod

    decider, by = meta["decider"], meta["opened_by"]
    _, _, path = bmod.Board(shipdir, team).read(meta["id"])
    stopped = [t["id"] for t in blocked_by(bmod.Board(shipdir, team), meta["id"])]
    blocks = f" 止まっているもの: {', '.join(stopped)}。" if stopped else ""
    text = (f"{meta['id']} の判断を頼む ({meta['category']}){' [急ぎ]' if meta.get('urgent') else ''}: "
            f"{meta['title']}。{blocks}項目ファイル: {path} 。"
            f"決めたら `{yamato_invocation()} decide close {shipdir} {meta['id']} --choice \"...\" --reason \"...\"`")
    if decider == by:
        out(f"decider ({decider}) は開いた本人なので送らない。")
        return
    if decider != OWNER:
        seatmod.send(shipdir, decider, text, by)
        return
    entry = inbox.append(shipdir, OWNER, by, text)
    events.emit(shipdir, events.SEND, seat=OWNER, by=by, summary=entry["text"],
                data={"n": entry["n"], "chars": len(entry["text"])})
    out(f"owner の inbox に記録した: #{entry['n']}")
    if meta.get("urgent") or (team.get("notify") or {}).get("decisions") == "each":
        for line in notify.notify(team, f"yamato {team['name']}: 判断待ち {meta['id']}", text, "waiting",
                                  shipdir=shipdir):
            out(line)
    else:
        out("通知は日報にまとめる (notify.decisions: digest)。すぐ知らせるなら --urgent を付けて開く。")


# --- close -------------------------------------------------------------------

def close_decision(shipdir: Path, team: dict, dec_id: str, *, choice: str, reason: str,
                   on_behalf_of: str | None = None, by: str | None = None,
                   now: float | None = None) -> dict:
    """Close the D item, append to decisions/log.md and release the tasks.

    Returns ``{meta, unblocked: [task meta], by_decider, choice}``; sending to the
    released tasks' seats is ``deliver_close``'s job (outside the lock).
    """
    now = time.time() if now is None else now
    by = by or caller(shipdir)
    on_behalf_of = on_behalf_of or by
    if not choice.strip():
        raise YamatoError("--choice が空です")
    if not reason.strip():
        raise YamatoError("--reason が空です")
    brd = bmod.Board(shipdir, team)
    with ship_lock(shipdir):
        meta, body, path = _read_decision(brd, dec_id)
        if meta.get("state") == "done":
            raise YamatoError(f"{dec_id} はもう閉じている (閉じた判断は書き換えない。覆すなら "
                              f"`decide open --supersedes {dec_id}` で新しい判断を開く)")
        decider = meta.get("decider")
        by_decider = decider in (by, on_behalf_of)
        stamp = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M")
        who = on_behalf_of if on_behalf_of == by else f"{on_behalf_of} (代筆: {by})"
        lines = [f"- 決定: {choice.strip()}", f"- 理由: {reason.strip()}", f"- 決めた人: {who}",
                 f"- 閉じた席: {by}", f"- 日時: {stamp}"]
        if not by_decider:
            lines.append(f"- 注意: decider ({decider}) 以外が閉じた")
        body = _write_section(body, "\n".join(lines))
        body = body.rstrip("\n") + "\n" + bmod._note_line(by, f"判断を閉じた: {choice.strip()}")
        meta.update({"state": "done", "closed_by": by, "on_behalf_of": on_behalf_of,
                     "closed_at": _now_iso(now)})
        new_path = brd._write(meta, body)
        if new_path != path:
            path.unlink()

        stopped = blocked_by(brd, dec_id)
        unblocked = []
        for t in stopped:
            rest = [x for x in t.get("blocked_on") or [] if x != dec_id]
            fields = {"blocked_on": ",".join(rest)}
            if not rest:
                if t.get("state") == "blocked":
                    fields["state"] = t.get("pre_blocked_state") or "open"
                if t.get("pre_blocked_state") is not None:
                    fields["pre_blocked_state"] = ""
            unblocked_meta = brd.set(t["id"], fields, note=f"{dec_id} が決まった: {choice.strip()}", by=by)
            if not rest:
                unblocked.append(unblocked_meta)

        _append_log(shipdir, meta, choice.strip(), reason.strip(), [t["id"] for t in stopped],
                    new_path, by_decider, now)
        events.emit(shipdir, events.DECISION_CLOSE, seat=decider, item=dec_id, by=by,
                    summary=(f"判断を閉じた: {choice.strip()} (決めた人 {who})"
                             + ("" if by_decider else f" / decider ({decider}) 以外が閉じた")),
                    data={"decider": decider, "closed_by": by, "on_behalf_of": on_behalf_of,
                          "by_decider": by_decider, "choice": choice.strip(), "reason": reason.strip(),
                          "unblocked": [t["id"] for t in unblocked], "was_blocking": [t["id"] for t in stopped]})
    return {"meta": meta, "unblocked": unblocked, "by_decider": by_decider, "choice": choice.strip()}


def _write_section(body: str, text: str) -> str:
    """Fill the ``## 決定`` section (replaced up to the next ``## `` heading), or add one."""
    m = None
    for m in _SECTION_RE.finditer(body):
        pass
    if m is None:
        head, _, tail = body.partition("## 経緯")
        if tail or body.startswith("## 経緯"):
            return head.rstrip("\n") + f"\n\n## 決定\n{text}\n\n## 経緯" + tail
        return body.rstrip("\n") + f"\n\n## 決定\n{text}\n"
    nxt = _NEXT_SECTION_RE.search(body, m.end())
    end = nxt.start() if nxt else len(body)
    return body[:m.end()] + "\n" + text + "\n\n" + body[end:].lstrip("\n")


def _append_log(shipdir: Path, meta: dict, choice: str, reason: str, stopped: list[str],
                item_path: Path, by_decider: bool, now: float) -> None:
    """decisions/log.md (§1.3): append only, one section per decision."""
    who = meta["on_behalf_of"] if meta["on_behalf_of"] == meta["closed_by"] \
        else f"{meta['on_behalf_of']} / 代筆 {meta['closed_by']}"
    lines = [f"## {meta['id']} {meta['title']} ({time.strftime('%Y-%m-%d', time.localtime(now))}, {who})",
             f"- 決定: {choice}", f"- 理由: {reason}",
             f"- 止まっていたもの: {', '.join(stopped) if stopped else 'なし'}"]
    if meta.get("supersedes"):
        lines.append(f"- 覆したもの: {meta['supersedes']}")
    if not by_decider:
        lines.append(f"- 注意: decider ({meta['decider']}) 以外が閉じた")
    lines.append(f"- 項目: {item_path.relative_to(shipdir)}")
    p = Path(shipdir) / LOG
    old = p.read_text(encoding="utf-8") if p.exists() else "# 判断の記録\n"
    atomic_write(p, old.rstrip("\n") + "\n\n" + "\n".join(lines) + "\n")


def deliver_close(shipdir: Path, team: dict, result: dict, out=print) -> None:
    """Tell each released task's assignee (the hub when there is none) (§1.2 step 4),
    and the seat that opened it: a merge decision holds nothing (``--links``), so
    without this nobody hears that the owner closed it from the terminal (e2e-p1)."""
    from . import seat as seatmod

    meta = result["meta"]
    told = set()
    for t in result["unblocked"]:
        to = t.get("assignee") or team["hub"]
        still = t.get("state") == "blocked"
        text = (f"{meta['id']} が決まった ({meta['title']}): {meta.get('on_behalf_of')} の決定。"
                + (f"{t['id']} は判断の前から blocked だったので blocked のまま。" if still
                   else f"{t['id']} の止まりが解けた (state={t.get('state')})。")
                + "決定と理由は "
                f"`{yamato_invocation()} board show {shipdir} {meta['id']}` で読める")
        if to == meta["closed_by"]:
            out(f"{t['id']} の担当 ({to}) は閉じた本人なので送らない。")
            continue
        seatmod.send(shipdir, to, text, meta["closed_by"])
        told.add(to)
    opener = meta.get("opened_by")
    if opener in team["seats"] and opener != meta["closed_by"] and opener not in told:
        links = f" 結んだ項目: {', '.join(meta['links'])}。" if meta.get("links") else ""
        text = (f"{meta['id']} が決まった ({meta['title']}): {meta.get('on_behalf_of')} の決定「{result['choice']}」。"
                f"{links}理由は `{yamato_invocation()} board show {shipdir} {meta['id']}` で読める")
        seatmod.send(shipdir, opener, text, meta["closed_by"])


# --- list --------------------------------------------------------------------

def _stale_seconds(text: str) -> int:
    s = str(text).strip().lower()
    if s.endswith("d") and s[:-1].isdigit():
        return int(s[:-1]) * 86400
    return parse_duration(s)


def waiting(brd: bmod.Board, *, decider: str | None = None, stale: str | None = None,
            now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    limit = _stale_seconds(stale) if stale else None
    out = []
    for m in decisions(brd):
        if decider and m.get("decider") != decider:
            continue
        opened = _epoch(m.get("opened_at"))
        if limit is not None and (opened is None or now - opened < limit):
            continue
        out.append(m)
    return out


def _age(seconds: float) -> str:
    if seconds >= 86400:
        return f"{int(seconds // 86400)}d"
    if seconds >= 3600:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 60)}m"


def format_decision(brd: bmod.Board, m: dict, now: float | None = None) -> str:
    now = time.time() if now is None else now
    opened = _epoch(m.get("opened_at"))
    extra = [f"{m.get('category')} → {m.get('decider')}"]
    if opened is not None and m.get("state") != "done":
        extra.append(f"{_age(now - opened)} 待ち")
    if m.get("due"):
        overdue = m.get("state") != "done" and str(m["due"]) < date.today().isoformat()
        extra.append(f"期限 {m['due']}{' 切れ' if overdue else ''}")
    stopped = [t["id"] for t in blocked_by(brd, m["id"])]
    if stopped:
        extra.append(f"止めている: {','.join(stopped)}")
    urgent = " [急ぎ]" if m.get("urgent") else ""
    return f"{m['id']} [{m.get('state')}]{urgent} {m.get('title')}  ({'; '.join(extra)})"


# --- CLI ---------------------------------------------------------------------

def add_parser(sub) -> None:
    d = sub.add_parser("decide", help="判断 (decision) を開く・閉じる・一覧する")
    ds = d.add_subparsers(dest="decide_cmd", required=True)
    o = ds.add_parser("open", help="判断の項目を開き、decider に届ける")
    o.add_argument("ship")
    o.add_argument("--category", required=True, help="team.yaml の decisions の鍵 (無ければ default)")
    o.add_argument("--title", required=True)
    o.add_argument("--blocks", default="", help="この判断で止まるタスク (T-001,T-002)")
    o.add_argument("--links", default="", help="止めずに結ぶ項目 (merge の判断とタスクなど)")
    o.add_argument("--due", help="期限 YYYY-MM-DD (日報で目立たせるだけ)")
    o.add_argument("--body-file", help="本文 (背景・選択肢と推し) のファイル")
    o.add_argument("--urgent", action="store_true", help="人間の decider にもすぐ通知する")
    o.add_argument("--supersedes", help="覆す判断の id")
    c = ds.add_parser("close", help="判断を閉じ、止まっていたタスクを戻す")
    c.add_argument("ship")
    c.add_argument("id")
    c.add_argument("--choice", required=True, help="決定")
    c.add_argument("--reason", required=True, help="理由")
    c.add_argument("--by", help="決めた人 (代筆するとき。既定は閉じた席自身)")
    ls = ds.add_parser("list", help="待ちの判断の一覧")
    ls.add_argument("ship")
    ls.add_argument("--decider")
    ls.add_argument("--stale", help="これより長く待っているものだけ (例 2d, 12h)")
    ls.add_argument("--all", action="store_true", help="閉じたものも含める")
    cat = ds.add_parser("categories", help="team.yaml の decisions (category・decider・いつ開くか)")
    cat.add_argument("ship")


def main(args: argparse.Namespace) -> int:
    from .seat import current_team

    shipdir = resolve_ship(args.ship)
    team = current_team(shipdir)
    if args.decide_cmd == "open":
        body = ""
        if args.body_file:
            try:
                body = Path(args.body_file).expanduser().read_text(encoding="utf-8")
            except OSError as e:
                raise YamatoError(f"--body-file を読めません: {e}") from None
        blocks = [b.strip() for b in args.blocks.split(",") if b.strip()]
        links = [x.strip() for x in args.links.split(",") if x.strip()]
        meta = open_decision(shipdir, team, category=args.category, title=args.title, blocks=blocks, links=links,
                             due=args.due, body=body, urgent=args.urgent, supersedes=args.supersedes)
        print(f"開いた: {meta['id']} ({meta['category']} → decider {meta['decider']}) {meta['title']}")
        if blocks:
            print(f"blocked にした: {', '.join(dict.fromkeys(blocks))}")
        deliver_open(shipdir, team, meta)
    elif args.decide_cmd == "close":
        result = close_decision(shipdir, team, args.id, choice=args.choice, reason=args.reason,
                                on_behalf_of=args.by)
        meta = result["meta"]
        print(f"閉じた: {meta['id']} 決定={args.choice.strip()} (決めた人 {meta['on_behalf_of']}, "
              f"閉じた席 {meta['closed_by']})")
        if not result["by_decider"]:
            print(f"注意: decider ({meta['decider']}) 以外が閉じた。events に記録した")
        for t in result["unblocked"]:
            head = "判断の前から blocked (そのまま)" if t.get("state") == "blocked" else "止まりが解けた"
            print(f"{head}: {bmod.format_item(t)}")
        deliver_close(shipdir, team, result)
    elif args.decide_cmd == "list":
        brd = bmod.Board(shipdir, team)
        items = decisions(brd, include_closed=True) if args.all else \
            waiting(brd, decider=args.decider, stale=args.stale)
        if args.all and args.decider:
            items = [m for m in items if m.get("decider") == args.decider]
        print("\n".join(format_decision(brd, m) for m in items) if items else "(待ちの判断なし)")
    elif args.decide_cmd == "categories":
        for cat, decider, when in categories(team):
            print(f"{cat}: decider={decider}" + (f" — {when}" if when else ""))
    return 0

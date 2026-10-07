"""What SessionStart injects into a seat, each part with its own cap (design §8.2, §0 I5).

However long the ship runs, what a seat reads at start stays bounded. Claude Code
takes up to 10,000 characters from one hook and swaps anything longer for a 2KB
preview (verify-p0-c Q1, counted per hook): the injection is two hooks, the records
(``build``) and the role's memory + knowledge.md (``build_knowledge``), each capped
at ``total_chars``. What is cut says which file to Read for the rest.
"""
from __future__ import annotations

import time
from pathlib import Path

from . import board as board_mod
from . import board_view, deadline, inbox, memory, roster
from .team import FILE_PREFIX, inject_parts
from .runtime import posix_path, ship_arg, yamato_invocation
from .util import YamatoError, today

# fallback only: the template's team.yaml spells these out (`inject.limits`). The role's
# memory and knowledge.md are cut at `inject.limits.memory` / `knowledge` (memory.py's defaults),
# the limits `memory apply` keeps to
LIMITS = {
    "handoff": (40, 2000),     # (lines, chars)
    "log_tail": (20, 1200),
    "mine_items": 15,
    "inbox_messages": 10,
    "inbox_chars": 400,        # per message
    "total_chars": 9500,       # per hook: Claude Code takes 10,000 chars from one (verify-p0-c Q1)
    "last_report": (30, 1500), # the previous daily report's 3 sections (design-p1 §2.3)
    "board_items": 20,         # the `board` part's overview (T-030); over this, "…ほか N 件"
    "fleet_items": 20,         # the `fleet` part (T-022); over this, "全文は `yamato ships`"
}
FILE_LIMIT = (60, 3000)        # a `file:<名前>` part without its own `inject.limits.files.<名前>` / `.default` (T-075)


def cap_text(text: str, max_lines: int, max_chars: int, source: str = "") -> str:
    lines = text.rstrip("\n").splitlines()
    cut_lines = len(lines) > max_lines
    lines = lines[:max_lines]
    out = "\n".join(lines)
    cut_chars = len(out) > max_chars
    if cut_chars:
        out = out[:max_chars]
    if cut_lines or cut_chars:
        out += f"\n…(上限で切った。全文は `{source}` を Read せよ)" if source else "\n…(上限で切った)"
    return out


def cap_total(shipdir: Path, seat: str, kind: str, text: str, total: int) -> str:
    """One hook's whole output within ``total``. What is over is kept in full in
    ``.runtime/inject-<seat>-<kind>.md`` for the seat to Read (verify-p0-c Q1 の 2)."""
    if len(text) <= total:
        return text
    path = Path(shipdir) / ".runtime" / f"inject-{seat}-{kind}.md"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        tail = f"\n…(注入の上限 {total} 文字で切った。全文は `{path}` を Read せよ)"
    except OSError:
        tail = f"\n…(注入の上限 {total} 文字で切った)"
    return text[:max(0, total - len(tail))] + tail


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _file_section(title: str, path: Path, limit: tuple[int, int]) -> str:
    text = _read(path).strip()
    if not text:
        return f"## {title}\n(なし)"
    return f"## {title}\n{cap_text(text, *limit, source=str(path))}"


def _memory_section(title: str, path: Path, team: dict, kind: str) -> str:
    """The role's memory / knowledge.md, cut at ``inject.limits.memory`` / ``knowledge``: the same limits ``memory apply``
    keeps to, so only a hand edit is ever cut, and says so (design-p1 §3.5)."""
    text = _read(path).strip()
    if not text:
        return f"## {title}\n(なし)"
    text, warning = memory.within_limits(text, team, kind)
    if warning:
        text += f"\n…({warning}。全文は `{path}` を Read せよ)"
    return f"## {title}\n{text}"


def _last_report(shipdir: Path, limit: tuple[int, int]) -> str:
    """Only 「一言」「owner の判断待ち」「明日」 of the latest daily report (design-p1 §2.3)."""
    from . import report

    path = report.latest(shipdir)
    if path is None:
        return "## 前回の日報\n(なし)"
    text = report.excerpt(_read(path)).replace("\n## ", "\n### ")
    text = text.replace("## ", "### ", 1) if text.startswith("## ") else (text or "(3 節が見つからない)")
    return f"## 前回の日報 ({path.stem}。全文: {path})\n{cap_text(text, *limit, source=str(path))}"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):   # missing, a directory, unreadable: one line, not an error
        return ""


def _file_part(name: str, team: dict, lim: dict) -> str:
    """A `file:<名前>` part (T-075, D-089): a file named under `inject.files`. Missing, empty or unreadable
    is one line, not an error; over the limit it is cut and points at the file."""
    inject = team.get("inject") or {}
    spec = (inject.get("files") or {}).get(name)
    if spec is None:   # a team.json from before this part, or hand-edited: say so, don't fall over
        return f"## {name}\n(inject.files に {name} がない)"
    path = Path(spec["abs"])
    title = f"## {name} (file: {spec['path']})"
    text = _read_text(path).strip()
    if not text:
        return f"{title}\n(なし: {path})"
    by_name = lim.get("files") or {}
    limit = by_name.get(name) or by_name.get("default") or FILE_LIMIT
    return f"{title}\n{cap_text(text, *limit, source=str(path))}"


def _board(shipdir: Path, team: dict, limit: int, y: str) -> str:
    """The `board` part (T-030, design-drift #4/#14): a whole-ship overview (opt-in), not
    just what the captain's own `mine` shows. Decisions are out (`decide list` covers those)."""
    items = board_mod.Board(shipdir, team).items()
    lines = board_view.inject_lines(items, limit, f"{y} board kanban {ship_arg(shipdir)}")
    return "## board (艦全体の進み具合)\n" + "\n".join(lines)


def _orphans(shipdir: Path, team: dict, max_items: int, y: str) -> str:
    """active items whose assignee fell over (design-p1 §5.6): reassign or wake the seat again."""
    from . import monitor

    found = monitor.orphans(shipdir, team)
    if not found:
        return "## 孤児の項目 (active のまま担当が止まっている)\n(なし)"
    lines = [f"- {board_mod.format_item(m)} ← {why}" for m, why in found[:max_items]]
    if len(found) > max_items:
        lines.append(f"…ほか {len(found) - max_items} 件 (`{y} board list {ship_arg(shipdir)} --state active`)")
    return ("## 孤児の項目 (active のまま担当が止まっている)\n" + "\n".join(lines)
            + "\n割り当て直すか、同じ席に send して起こし直す")


def _fleet(limit: int, y: str) -> str:
    """The `fleet` part (T-022, admiral's own): one line per ship, the same as `yamato
    ships`, capped at ``limit``; over that, the rest points at the uncapped command
    instead of being cut mid-line (design-drift D, D-013: admiral 用の全艦の要約).

    ``claude.agents()`` can fail or time out (up to 60s, ``YamatoError``): an extra part
    must never cost the hook its real job (``_touch`` と同じ考え), so only this section
    degrades to a pointer at `yamato ships` instead of taking session_start down with it."""
    from . import admiral, claude

    title = f"## fleet (全艦の様子。全文は `{y} ships`)"
    found = admiral.all_ships()
    if not found:
        return f"{title}\n(艦がありません)"
    try:
        by = claude.by_session(claude.agents())
    except YamatoError as e:
        return f"{title}\n(claude agents を読めない: {e}。`{y} ships` で見る)"
    now = time.time()
    names = list(found.items())
    lines = [admiral.ship_line(name, path, by, now) for name, path in names[:limit]]
    if len(names) > limit:
        lines.append(f"…ほか {len(names) - limit} 件 (`{y} ships` で見る)")
    return f"{title}\n" + "\n".join(lines)


def _limits(team: dict, limits: dict | None) -> dict:
    return {**LIMITS, **((team.get("inject") or {}).get("limits") or {}), **(limits or {})}


def build(shipdir: Path, team: dict, seat: str, source: str = "startup",
          limits: dict | None = None, notice: str | None = None) -> tuple[str, int]:
    """The records hook (hook A): the header, handoff, work log, board, report, the notices
    (``notice``: the captain's last call) and the inbox. Returns (context text, inbox cursor
    to advance to)."""
    lim = _limits(team, limits)
    want = set(inject_parts(team, seat))
    shipdir = Path(shipdir)
    spec = team["seats"][seat]
    sdir = inbox.seat_dir(shipdir, seat)
    y = yamato_invocation()
    parts = []

    dl = deadline.read(shipdir)
    head = [
        f"# yamato: シフト開始 ({source})",
        f"- 艦: {team['name']} / 艦フォルダ: {ship_arg(shipdir)}",
        f"- あなたの席: {seat} (役割 {spec['role']}, shift {spec['shift']}) / captain: {team['hub']}",
        f"- yamato コマンド: {y} (コマンドの <ship> には {ship_arg(shipdir)} を、<seat> には {seat} を渡す)",
        f"- 稼働時間: {deadline.describe(dl)}",
        f"- 引き継ぎ: {posix_path(sdir / 'handoff.md')} / 作業ログ: {posix_path(sdir / 'log' / (today() + '.md'))}",
    ]
    if deadline.phase(dl) in (deadline.OVER, deadline.FORCE):
        head.append(deadline.WRAP_UP_MESSAGE.format(yamato=y, ship=ship_arg(shipdir), seat=seat))
    if notice:
        parts.append(notice)
    parts.append("\n".join(head))

    if "handoff" in want:
        parts.append(_file_section("自分の引き継ぎ (handoff.md)", sdir / "handoff.md", lim["handoff"]))

    rec = roster.seat(shipdir, seat)
    if "log_tail" in want and rec.get("prevEndedWithoutHandoff"):
        logs = sorted((sdir / "log").glob("*.md"))
        tail = ""
        if logs:
            lines = _read(logs[-1]).rstrip("\n").splitlines()[-lim["log_tail"][0]:]
            tail = cap_text("\n".join(lines), *lim["log_tail"], source=str(logs[-1]))
        parts.append("## 前のシフトは引き継ぎなしで終わった: 作業ログの末尾\n" + (tail or "(作業ログなし)"))

    if "mine" in want:
        mine = board_mod.Board(shipdir, team).mine(seat)
        shown = [board_mod.format_item(m) for m in mine[:lim["mine_items"]]]
        if len(mine) > lim["mine_items"]:
            shown.append(f"…ほか {len(mine) - lim['mine_items']} 件 (上限で省略。`{y} board mine {ship_arg(shipdir)} {seat}`)")
        parts.append("## 自分の担当 (board mine)\n" + ("\n".join(shown) if shown else "(なし)"))

    if "orphans" in want:
        parts.append(_orphans(shipdir, team, lim["mine_items"], y))

    if "last_report" in want:
        parts.append(_last_report(shipdir, lim["last_report"]))

    if "board" in want:
        parts.append(_board(shipdir, team, lim["board_items"], y))

    if "fleet" in want:
        parts.append(_fleet(lim["fleet_items"], y))

    # the applier's role gets `memory status` on its first start of the day (design-p1 §3.4)
    notice = memory.applier_notice(shipdir, team, seat)
    if notice:
        parts.append(notice)

    cursor_to = inbox.cursor(shipdir, seat)
    if "inbox" in want:
        # the inbox comes last and gets what is left of the hook's budget; the cursor only
        # moves over what was shown, so nothing past it may be cut by the total cap
        unread = inbox.unread(shipdir, seat)
        title = f"## 未読の inbox ({len(unread)} 件)"
        more = f"…続きと省略された全文は `{y} inbox {ship_arg(shipdir)} {seat}` で読む"
        budget = lim["total_chars"] - len("\n\n".join([*parts, title])) - len(more) - 2
        lines = []
        contiguous = True
        for e in unread[:lim["inbox_messages"]]:
            full = len(e.get("text", "")) <= lim["inbox_chars"]
            line = inbox.format_entry(e, lim["inbox_chars"])
            budget -= len(line) + 1
            if budget < 0:
                break
            lines.append(line)
            # advance the cursor only over messages shown in full
            if full and contiguous:
                cursor_to = e["n"]
            else:
                contiguous = False
        rest = len(unread) - len(lines)
        if unread and (rest > 0 or not contiguous):
            lines.append(more)
        parts.append(f"{title}\n" + ("\n".join(lines) if lines else "(なし)"))

    return cap_total(shipdir, seat, "records", "\n\n".join(parts), lim["total_chars"]), cursor_to


def build_knowledge(shipdir: Path, team: dict, seat: str, limits: dict | None = None) -> str:
    """The knowledge hook (hook B): the role's memory and knowledge.md, "" when the seat's
    ``inject`` has neither."""
    lim = _limits(team, limits)
    # in the order the inject list names them: what is cut by `total_chars` is the end (T-075)
    order = [p for p in dict.fromkeys(inject_parts(team, seat))
             if p in ("memory", "knowledge") or p.startswith(FILE_PREFIX)]
    if not order:
        return ""
    shipdir = Path(shipdir)
    parts = [f"# yamato: 役割の memory と艦の knowledge (席 {seat})"]
    for p in order:
        if p == "memory":
            memory.migrate(shipdir, team)   # a P0 ship's seats/<seat>/memory.md moves in on first read
            role = team["seats"][seat]["role"]
            parts.append(_memory_section(f"役割の memory (roles/{role}/memory.md)", memory.memory_path(shipdir, role),
                                         team, "memory"))
        elif p == "knowledge":
            parts.append(_memory_section("チームの knowledge.md", memory.knowledge_path(shipdir), team, "knowledge"))
        else:
            parts.append(_file_part(p[len(FILE_PREFIX):], team, lim))
    return cap_total(shipdir, seat, "knowledge", "\n\n".join(parts), lim["total_chars"])

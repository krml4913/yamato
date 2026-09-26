"""What SessionStart injects into a seat, each part with its own cap (design §8.2, §0 I5).

However long the ship runs, what a seat reads at start stays bounded. Claude Code
takes up to 10,000 characters from one hook and swaps anything longer for a 2KB
preview (verify-p0-c Q1, counted per hook): the injection is two hooks, the records
(``build``) and the role's memory + knowledge.md (``build_knowledge``), each capped
at ``total_chars``. What is cut says which file to Read for the rest.
"""
from __future__ import annotations

from pathlib import Path

from . import board as board_mod
from . import deadline, inbox, memory, roster
from .team import inject_parts
from .util import YAMATO_BIN, today

# fallback only: the template's team.yaml spells these out (`inject.limits`). The role's
# memory and knowledge.md are cut at `memory.limits`, the limits `memory apply` keeps to
LIMITS = {
    "handoff": (40, 2000),     # (lines, chars)
    "log_tail": (20, 1200),
    "mine_items": 15,
    "inbox_messages": 10,
    "inbox_chars": 400,        # per message
    "total_chars": 9500,       # per hook: Claude Code takes 10,000 chars from one (verify-p0-c Q1)
    "last_report": (30, 1500), # the previous daily report's 3 sections (design-p1 §2.3)
}


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
        path.write_text(text, encoding="utf-8")
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
    """The role's memory / knowledge.md, cut at ``memory.limits``: the same limits ``memory apply``
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


def _orphans(shipdir: Path, team: dict, max_items: int, y: str) -> str:
    """active items whose assignee fell over (design-p1 §5.6): reassign or wake the seat again."""
    from . import monitor

    found = monitor.orphans(shipdir, team)
    if not found:
        return "## 孤児の項目 (active のまま担当が止まっている)\n(なし)"
    lines = [f"- {board_mod.format_item(m)} ← {why}" for m, why in found[:max_items]]
    if len(found) > max_items:
        lines.append(f"…ほか {len(found) - max_items} 件 (`{y} board list {shipdir} --state active`)")
    return ("## 孤児の項目 (active のまま担当が止まっている)\n" + "\n".join(lines)
            + "\n割り当て直すか、同じ席に send して起こし直す")


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
    y = str(YAMATO_BIN)
    parts = []

    dl = deadline.read(shipdir)
    head = [
        f"# yamato: シフト開始 ({source})",
        f"- 艦: {team['name']} / 艦フォルダ: {shipdir}",
        f"- あなたの席: {seat} (役割 {spec['role']}, shift {spec['shift']}) / captain: {team['hub']}",
        f"- yamato コマンド: {y} (コマンドの <ship> には {shipdir} を、<seat> には {seat} を渡す)",
        f"- 稼働時間: {deadline.describe(dl)}",
        f"- 引き継ぎ: {sdir / 'handoff.md'} / 作業ログ: {sdir / 'log' / (today() + '.md')}",
    ]
    if deadline.phase(dl) in (deadline.OVER, deadline.FORCE):
        head.append(deadline.WRAP_UP_MESSAGE.format(yamato=y, ship=shipdir, seat=seat))
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
            shown.append(f"…ほか {len(mine) - lim['mine_items']} 件 (上限で省略。`{y} board mine {shipdir} {seat}`)")
        parts.append("## 自分の担当 (board mine)\n" + ("\n".join(shown) if shown else "(なし)"))

    if "orphans" in want:
        parts.append(_orphans(shipdir, team, lim["mine_items"], y))

    if "last_report" in want:
        parts.append(_last_report(shipdir, lim["last_report"]))

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
        more = f"…続きと省略された全文は `{y} inbox {shipdir} {seat}` で読む"
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
    want = set(inject_parts(team, seat))
    if not want & {"memory", "knowledge"}:
        return ""
    shipdir = Path(shipdir)
    parts = [f"# yamato: 役割の memory と艦の knowledge (席 {seat})"]
    if "memory" in want:
        memory.migrate(shipdir, team)   # a P0 ship's seats/<seat>/memory.md moves in on first read
        role = team["seats"][seat]["role"]
        parts.append(_memory_section(f"役割の memory (roles/{role}/memory.md)", memory.memory_path(shipdir, role),
                                     team, "memory"))
    if "knowledge" in want:
        parts.append(_memory_section("チームの knowledge.md", memory.knowledge_path(shipdir), team, "knowledge"))
    return cap_total(shipdir, seat, "knowledge", "\n\n".join(parts), lim["total_chars"])

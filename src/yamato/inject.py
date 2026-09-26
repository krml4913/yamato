"""What SessionStart injects into a seat, each part with its own cap (design §8.2, §0 I5).

However long the ship runs, what a seat reads at start stays bounded.
"""
from __future__ import annotations

from pathlib import Path

from . import board as board_mod
from . import deadline, inbox, roster
from .team import inject_parts
from .util import YAMATO_BIN, today

# fallback only: the template's team.yaml spells these out (`inject.limits`)
LIMITS = {
    "handoff": (40, 2000),     # (lines, chars)
    "memory": (40, 1500),
    "knowledge": (60, 1500),
    "log_tail": (20, 1200),
    "mine_items": 15,
    "inbox_messages": 10,
    "inbox_chars": 400,        # per message
    "total_chars": 9500,       # hook output is capped by Claude Code around 10k
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
        out += f"\n…(上限で省略。全文: {source})" if source else "\n…(上限で省略)"
    return out


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


def build(shipdir: Path, team: dict, seat: str, source: str = "startup",
          limits: dict | None = None) -> tuple[str, int]:
    """Returns (context text, inbox cursor to advance to)."""
    lim = {**LIMITS, **((team.get("inject") or {}).get("limits") or {}), **(limits or {})}
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

    cursor_to = inbox.cursor(shipdir, seat)
    if "inbox" in want:
        # the inbox gets what is left of the total budget after the parts above,
        # keeping room for memory / knowledge; the cursor only moves over what was shown
        budget = lim["total_chars"] - len("\n\n".join(parts)) - 800
        unread = inbox.unread(shipdir, seat)
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
            lines.append(f"…続きと省略された全文は `{y} inbox {shipdir} {seat}` で読む")
        parts.append(f"## 未読の inbox ({len(unread)} 件)\n" + ("\n".join(lines) if lines else "(なし)"))
    if "memory" in want:
        parts.append(_file_section("席の memory", sdir / "memory.md", lim["memory"]))
    if "knowledge" in want:
        parts.append(_file_section("チームの knowledge.md", shipdir / "knowledge.md", lim["knowledge"]))

    text = "\n\n".join(parts)
    if len(text) > lim["total_chars"]:
        text = text[:lim["total_chars"]] + "\n…(注入の全体上限で省略)"
    return text, cursor_to

"""Per-seat inbox: an append-only ``inbox.jsonl`` plus a separate read cursor (design §0 I1)."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .util import atomic_write, ship_lock


OWNER = "owner"   # the human: its inbox lives at <ship>/owner/, not under seats/


def seat_dir(shipdir: Path, seat: str) -> Path:
    return Path(shipdir) / (OWNER if seat == OWNER else f"seats/{seat}")


def _inbox(shipdir: Path, seat: str) -> Path:
    return seat_dir(shipdir, seat) / "inbox.jsonl"


def _cursor(shipdir: Path, seat: str) -> Path:
    return seat_dir(shipdir, seat) / "inbox.cursor"


def entries(shipdir: Path, seat: str) -> list[dict]:
    try:
        lines = _inbox(shipdir, seat).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in lines:
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue  # a torn line never blocks the rest
    return out


def append(shipdir: Path, seat: str, sender: str, text: str) -> dict:
    with ship_lock(shipdir):
        # the last valid number + 1: a torn line must not reuse a number
        n = max((e.get("n", 0) for e in entries(shipdir, seat)), default=0) + 1
        entry = {"n": n, "ts": time.time(), "from": sender, "text": text}
        path = _inbox(shipdir, seat)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry


def cursor(shipdir: Path, seat: str) -> int:
    try:
        return int(_cursor(shipdir, seat).read_text().strip() or 0)
    except (FileNotFoundError, ValueError):
        return 0


def unread(shipdir: Path, seat: str) -> list[dict]:
    c = cursor(shipdir, seat)
    return [e for e in entries(shipdir, seat) if e.get("n", 0) > c]


def mark_read(shipdir: Path, seat: str, upto: int) -> None:
    with ship_lock(shipdir):
        if upto > cursor(shipdir, seat):
            atomic_write(_cursor(shipdir, seat), f"{upto}\n")


def format_entry(e: dict, max_chars: int | None = None) -> str:
    text = e.get("text", "")
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + f" …(以下 {len(text) - max_chars} 文字省略)"
    stamp = time.strftime("%m-%d %H:%M", time.localtime(e.get("ts", 0)))
    return f"[#{e.get('n')} {stamp} from {e.get('from')}] {text}"

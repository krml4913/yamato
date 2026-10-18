"""web コンソールで読める board の項目 1 件 (判断も task も同じ形)。HTTP は持たない。

リクエストの ``id`` は ``[A-Z]-[0-9]+`` の形だけ受け、path に使う前に検査する。通らなければ
見つからない (404) ものとして扱う。読むだけで、何も書かない。
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import board

ID_RE = re.compile(r"[A-Z]-[0-9]+")


def read(shipdir: Path, item_id: str) -> dict | None:
    """項目があれば応答の形にして返す。id の形が違う・項目が無い・通常ファイルでないときは None。"""
    if not ID_RE.fullmatch(item_id):
        return None
    for d in ("items", "archive"):
        p = Path(shipdir) / "board" / d / f"{item_id}.md"
        if p.is_file() and not p.is_symlink():
            meta, body = board.loads(p.read_bytes().decode("utf-8", errors="replace"))
            break
    else:
        return None
    is_decision = meta.get("kind") == board.DECISION
    return {
        "id": str(meta.get("id") or item_id),
        "kind": meta.get("kind"),
        "title": str(meta.get("title") or ""),
        "state": meta.get("state"),
        "category": meta.get("category") if is_decision else None,
        "urgent": bool(meta.get("urgent")) if is_decision else None,
        "assignee": meta.get("assignee"),
        "blocked_on": list(meta.get("blocked_on") or []),
        "links": list(meta.get("links") or []),
        "body": body,
    }

"""web コンソールで読める md (roles/*.md・charter.md・knowledge.md)。HTTP は持たない。

読めるのは、艦フォルダの中で列挙した実在の通常ファイルだけ。リクエストの ``file`` は列挙の結果を
dict で引く鍵として使い、path を組み立てない (``..``・絶対パス・シンボリックリンク・列挙に無い
ファイルは見つからない = 404)。
"""
from __future__ import annotations

from pathlib import Path

CHARTER = "charter.md"
KNOWLEDGE = "knowledge.md"


def _regular(p: Path) -> bool:
    return p.is_file() and not p.is_symlink()


def listing(shipdir: Path) -> dict[str, dict]:
    """``{艦フォルダからの相対 path: {"file", "kind", "bytes"}}``。roles は名前順、charter・knowledge が続く。"""
    out: dict[str, dict] = {}

    def add(rel: str, kind: str, p: Path) -> None:
        out[rel] = {"file": rel, "kind": kind, "bytes": p.stat().st_size, "_path": p}

    roles = shipdir / "roles"
    if roles.is_dir() and not roles.is_symlink():
        for p in sorted(roles.glob("*.md")):
            if _regular(p):
                add(f"roles/{p.name}", "role", p)
    for rel, kind in ((CHARTER, "charter"), (KNOWLEDGE, "knowledge")):
        p = shipdir / rel
        if _regular(p):
            add(rel, kind, p)
    return out


def read(shipdir: Path, name: str, file: str) -> dict | None:
    """列挙にある file だけ返す。無ければ None。"""
    ent = listing(shipdir).get(file)
    if ent is None:
        return None
    text = ent["_path"].read_bytes().decode("utf-8", errors="replace")
    return {"name": name, "file": ent["file"], "kind": ent["kind"], "text": text, "bytes": ent["bytes"]}

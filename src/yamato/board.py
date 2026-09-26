"""The board: one markdown file per item, frontmatter changed only through commands.

P0 scope (design §0): a single level of ``task`` items and the fixed fields
plus the team-defined ``column`` and extra fields. The body is free text.
Items that reach ``state: done`` move to ``board/archive/``.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .util import YamatoError, atomic_write, ship_lock

STATES = ("open", "active", "blocked", "done")
FIXED = ("id", "title", "kind", "parent", "assignee", "state", "blocked_on", "links")
LIST_FIELDS = ("blocked_on", "links")
ID_PREFIX = "T-"
ID_RE = re.compile(r"^T-(\d+)$")
_PLAIN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_./#-]*$")


# --- frontmatter -----------------------------------------------------------

def _dump_value(v) -> str:
    if isinstance(v, str) and _PLAIN_RE.match(v) and v not in ("null", "true", "false"):
        return v
    return json.dumps(v, ensure_ascii=False)


def _load_value(raw: str):
    raw = raw.strip()
    if raw == "":
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def dumps(meta: dict, body: str) -> str:
    lines = ["---"] + [f"{k}: {_dump_value(v)}" for k, v in meta.items()] + ["---", ""]
    return "\n".join(lines) + body.lstrip("\n")


def loads(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise YamatoError("board の項目に frontmatter がありません")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise YamatoError("board の項目の frontmatter が閉じていません")
    meta = {}
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        key, sep, raw = line.partition(":")
        if not sep:
            raise YamatoError(f"frontmatter の行が不正です: {line!r}")
        meta[key.strip()] = _load_value(raw)
    return meta, text[end + 5:].lstrip("\n")


# --- storage ---------------------------------------------------------------

class Board:
    def __init__(self, shipdir: Path, team: dict):
        self.shipdir = Path(shipdir)
        self.team = team
        self.items_dir = self.shipdir / "board" / "items"
        self.archive_dir = self.shipdir / "board" / "archive"

    # paths
    def _find(self, item_id: str) -> Path | None:
        for d in (self.items_dir, self.archive_dir):
            p = d / f"{item_id}.md"
            if p.is_file():
                return p
        return None

    def exists(self, item_id: str) -> bool:
        return self._find(item_id) is not None

    def read(self, item_id: str) -> tuple[dict, str, Path]:
        p = self._find(item_id)
        if p is None:
            raise YamatoError(f"board に {item_id} がありません")
        meta, body = loads(p.read_text(encoding="utf-8"))
        return meta, body, p

    def _all_paths(self, include_archive: bool) -> list[Path]:
        dirs = [self.items_dir] + ([self.archive_dir] if include_archive else [])
        out = []
        for d in dirs:
            if d.is_dir():
                out += [p for p in d.glob("T-*.md")]
        return sorted(out, key=lambda p: _id_num(p.stem))

    def items(self, include_archive: bool = False) -> list[dict]:
        out = []
        for p in self._all_paths(include_archive):
            meta, _ = loads(p.read_text(encoding="utf-8"))
            out.append(meta)
        return out

    def _next_id(self) -> str:
        nums = [_id_num(p.stem) for p in self._all_paths(True)]
        return f"{ID_PREFIX}{(max(nums) if nums else 0) + 1:03d}"

    # validation
    def _check(self, key: str, value, item_id: str | None):
        board = self.team.get("board", {})
        columns = board.get("columns") or []
        if key == "id":
            raise YamatoError("id は変更できません")
        if key == "title":
            if not value:
                raise YamatoError("title は空にできません")
            return str(value)
        if key == "kind":
            kinds = board.get("kinds") or ["task"]
            if value not in kinds:
                raise YamatoError(f"kind は {' / '.join(kinds)} のどれか (今: {value})")
            return value
        if key == "state":
            if value not in STATES:
                raise YamatoError(f"state は {' / '.join(STATES)} のどれか (今: {value})")
            return value
        if key == "assignee":
            if value in (None, "", "-"):
                return None
            if value not in self.team["seats"]:
                raise YamatoError(f"assignee は席の名前 ({', '.join(self.team['seats'])}) (今: {value})")
            return value
        if key == "parent":
            if value in (None, "", "-"):
                return None
            if value == item_id or not self.exists(value):
                raise YamatoError(f"parent={value} が board にありません")
            return value
        if key in LIST_FIELDS:
            vals = value if isinstance(value, list) else [v.strip() for v in str(value or "").split(",") if v.strip()]
            if key == "blocked_on":
                for v in vals:
                    if v == item_id or not self.exists(v):
                        raise YamatoError(f"blocked_on={v} が board にありません")
            return vals
        if key == "column":
            names = [c["name"] for c in columns]
            if not names:
                raise YamatoError("このチームは board.columns を定義していません")
            if value not in names:
                raise YamatoError(f"column は {' / '.join(names)} のどれか (今: {value})")
            return value
        if key in (board.get("fields") or []):
            return None if value in (None, "") else str(value)
        allowed = list(FIXED[1:]) + (["column"] if columns else []) + list(board.get("fields") or [])
        raise YamatoError(f"{key} は変更できる項目ではありません (項目: {', '.join(allowed)})")

    def _sync_column(self, meta: dict, changed: dict) -> None:
        columns = self.team.get("board", {}).get("columns") or []
        if not columns:
            return
        by_name = {c["name"]: c["state"] for c in columns}
        if "column" in changed and meta.get("column"):
            meta["state"] = by_name[meta["column"]]
        elif "state" in changed and by_name.get(meta.get("column")) != meta["state"]:
            meta["column"] = next((c["name"] for c in columns if c["state"] == meta["state"]), meta.get("column"))

    # commands
    def add(self, title: str, fields: dict | None = None, body: str = "", by: str | None = None) -> dict:
        with ship_lock(self.shipdir):
            item_id = self._next_id()
            board = self.team.get("board", {})
            meta = {
                "id": item_id,
                "title": self._check("title", title, item_id),
                "kind": (board.get("kinds") or ["task"])[0],
                "parent": None,
                "assignee": None,
                "state": "open",
                "blocked_on": [],
                "links": [],
            }
            columns = board.get("columns") or []
            if columns:
                meta["column"] = columns[0]["name"]
                meta["state"] = columns[0]["state"]
            changed = {}
            for k, v in (fields or {}).items():
                meta[k] = changed[k] = self._check(k, v, item_id)
            self._sync_column(meta, changed)
            text = (body.rstrip() + "\n\n" if body.strip() else "") + "## 経緯\n" + _note_line(by, "作成")
            self._write(meta, text)
            return meta

    def set(self, item_id: str, fields: dict, note: str | None = None, by: str | None = None) -> dict:
        if not fields and not note:
            raise YamatoError("変更する項目 (key=value) か --note を指定してください")
        with ship_lock(self.shipdir):
            meta, body, path = self.read(item_id)
            changed = {}
            for k, v in fields.items():
                meta[k] = changed[k] = self._check(k, v, item_id)
            self._sync_column(meta, changed)
            if note:
                body = body.rstrip("\n") + "\n" + _note_line(by, note)
            new_path = self._write(meta, body)
            if new_path != path:
                path.unlink()
            return meta

    def _write(self, meta: dict, body: str) -> Path:
        d = self.archive_dir if meta.get("state") == "done" else self.items_dir
        p = d / f"{meta['id']}.md"
        atomic_write(p, dumps(meta, body))
        return p

    def mine(self, seat: str) -> list[dict]:
        return [m for m in self.items() if m.get("assignee") == seat and m.get("state") != "done"]


def _id_num(item_id: str) -> int:
    m = ID_RE.match(item_id)
    return int(m.group(1)) if m else 0


def _note_line(by: str | None, text: str) -> str:
    stamp = time.strftime("%m-%d %H:%M")
    return f"- {stamp} {by or '?'}: {text}\n"


def format_item(meta: dict) -> str:
    extra = []
    if meta.get("column"):
        extra.append(f"column={meta['column']}")
    if meta.get("blocked_on"):
        extra.append(f"blocked_on={','.join(meta['blocked_on'])}")
    if meta.get("parent"):
        extra.append(f"parent={meta['parent']}")
    tail = f"  ({'; '.join(extra)})" if extra else ""
    return f"{meta['id']} [{meta.get('state')}] {meta.get('assignee') or '-'}: {meta.get('title')}{tail}"


def parse_assignments(pairs: list[str]) -> dict:
    out = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise YamatoError(f"key=value の形で指定してください: {pair!r}")
        out[key.strip()] = value.strip()
    return out

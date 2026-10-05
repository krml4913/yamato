"""The board: one markdown file per item, frontmatter changed only through commands.

P0 scope (design §0): a single level of items. The commands check only the
integrity of the fixed fields (state, assignee, parent / blocked_on
references, the id); kinds, columns and any extra fields are the team's own
free text. When the team defines ``board.columns``, a known column and the
state are kept in step. The body is free. ``state: done`` moves an item to
``board/archive/``.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import events
from .util import YamatoError, atomic_write, ship_lock

STATES = ("open", "active", "blocked", "done")
FIXED = ("id", "title", "kind", "parent", "assignee", "state", "blocked_on", "links")
LIST_FIELDS = ("blocked_on", "links")
REPO_FIELDS = ("worktree", "branch", "pr", "merged_by")   # {repo: value} maps in a several-repo ship
ID_PREFIX = "T-"
ID_RE = re.compile(r"^T-(\d+)$")
KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_PLAIN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_./#-]*$")
HISTORY = "## 経緯\n"   # the item's history section; `board set --note` appends to it
DECISION = "decision"   # kind of the D-NNN items `yamato decide` owns (design-p1 §1)
# fields of a decision only `decide open/close` writes (the decider is fixed when opened)
DECISION_FIXED = ("id", "kind", "state", "category", "decider", "opened_by", "opened_at",
                  "closed_by", "on_behalf_of", "closed_at")


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
        if key == "id":
            raise YamatoError("id は変更できません")
        if key == "title":
            if not value:
                raise YamatoError("title は空にできません")
            return str(value)
        if key == "kind":
            # the kinds a team uses are its own business; only a value is required
            if not value:
                raise YamatoError("kind は空にできません")
            return str(value)
        if key == "state":
            if value not in STATES:
                raise YamatoError(f"state は {' / '.join(STATES)} のどれか (今: {value})")
            return value
        if key == "assignee":
            if value in (None, "", "-"):
                return None
            if value not in self.team["seats"] and value != "owner":
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
        if key in REPO_FIELDS and isinstance(value, dict):
            # several-repo ships: {repo: value} (design.md §0 の workspace 配列)
            value = {str(k): str(v) for k, v in value.items() if v not in (None, "")}
            return value or None
        # column and team-defined fields are free text (mechanism-not-policy)
        if not KEY_RE.match(key):
            raise YamatoError(f"項目名が不正です: {key!r} (英数字・_・-)")
        return None if value in (None, "") else str(value)

    def _sync_column(self, meta: dict, changed: dict) -> None:
        columns = self.team.get("board", {}).get("columns") or []
        if not columns:
            return
        by_name = {c["name"]: c["state"] for c in columns}
        if "column" in changed and meta.get("column") in by_name:
            meta["state"] = by_name[meta["column"]]
        elif "column" in changed:
            return  # a column the team did not define: free text, state untouched
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
            text = (body.rstrip() + "\n\n" if body.strip() else "") + HISTORY + _note_line(by, "作成")
            self._write(meta, text)
            events.emit(self.shipdir, events.BOARD_ADD, seat=meta.get("assignee"), item=item_id, by=by,
                        summary=f"作成 [{meta['state']}] {meta['title']}", data={"fields": changed})
            return meta

    def set(self, item_id: str, fields: dict, note: str | None = None, by: str | None = None) -> dict:
        if not fields and not note:
            raise YamatoError("変更する項目 (key=value) か --note を指定してください")
        with ship_lock(self.shipdir):
            meta, body, path = self.read(item_id)
            if meta.get("kind") == DECISION and item_id.startswith("D-"):
                _check_decision_set(meta, fields)
            before = dict(meta)
            changed = {}
            for k, v in fields.items():
                meta[k] = changed[k] = self._check(k, v, item_id)
            self._sync_column(meta, changed)
            if note:
                body = body.rstrip("\n") + "\n" + _note_line(by, note)
            new_path = self._write(meta, body)
            if new_path != path:
                path.unlink()
            diff = {k: [before.get(k), meta.get(k)] for k in meta if before.get(k) != meta.get(k)}
            words = [f"{k}: {_show(a)}→{_show(b)}" for k, (a, b) in diff.items()]
            if note:
                words.append(f"経緯: {note}")
            events.emit(self.shipdir, events.BOARD_SET, seat=meta.get("assignee"), item=item_id, by=by,
                        summary=", ".join(words) or "変更なし",
                        data={"changes": diff, **({"note": note} if note else {})})
            return meta

    def note(self, item_id: str, text: str, by: str | None = None) -> dict:
        """Append ``text`` to the item's body (design-p1 §7.2): the frontmatter is not touched.

        For seats that may not change the board's structure (a ``trust: external``
        researcher). The text goes above ``## 経緯``, which gets one line saying so."""
        text = text.strip()
        if not text:
            raise YamatoError("追記する本文が空です")
        with ship_lock(self.shipdir):
            meta, body, path = self.read(item_id)
            if meta.get("kind") == DECISION and meta.get("state") == "done":
                raise YamatoError(f"{item_id} は閉じた判断なので書き換えない")
            head, sep, history = body.partition(HISTORY)
            if not sep:
                head, history = body, ""
            head, history = head.rstrip("\n"), history.strip("\n")
            body = ((head + "\n\n" if head else "") + text + "\n\n" + HISTORY
                    + (history + "\n" if history else "") + _note_line(by, f"本文に追記 ({len(text.splitlines())} 行)"))
            atomic_write(path, dumps(meta, body))
            events.emit(self.shipdir, events.BOARD_NOTE, seat=meta.get("assignee"), item=item_id, by=by,
                        summary=text, data={"chars": len(text)})
            return meta

    def _write(self, meta: dict, body: str) -> Path:
        archive = self.team.get("board", {}).get("archive_on_done", True)
        d = self.archive_dir if meta.get("state") == "done" and archive else self.items_dir
        p = d / f"{meta['id']}.md"
        atomic_write(p, dumps(meta, body))
        return p

    def archive(self, item_id: str | None = None) -> list[str]:
        """Move done items (all, or one) to archive/ (for ships with archive_on_done: false)."""
        moved = []
        with ship_lock(self.shipdir):
            closed_decisions = sorted(self.items_dir.glob("D-*.md")) if self.items_dir.is_dir() else []
            for p in self._all_paths(False) + closed_decisions:
                meta, body = loads(p.read_text(encoding="utf-8"))
                if (item_id and meta["id"] != item_id) or meta.get("state") != "done":
                    continue
                atomic_write(self.archive_dir / p.name, dumps(meta, body))
                p.unlink()
                moved.append(meta["id"])
                events.emit(self.shipdir, events.BOARD_ARCHIVE, seat=meta.get("assignee"), item=meta["id"],
                            summary=f"archive へ移した: {meta.get('title')}")
        if item_id and not moved:
            raise YamatoError(f"{item_id} は done の項目として board/items にありません")
        return moved

    def mine(self, seat: str) -> list[dict]:
        return [m for m in self.items() if m.get("assignee") == seat and m.get("state") != "done"]


def _check_decision_set(meta: dict, fields: dict) -> None:
    """Integrity of a decision item (design-p1 §0.3): closed ones are never rewritten."""
    if meta.get("state") == "done":
        raise YamatoError(f"{meta['id']} は閉じた判断なので書き換えない (覆すなら "
                          f"`decide open --supersedes {meta['id']}` で新しい判断を開く)")
    fixed = sorted(set(fields) & set(DECISION_FIXED))
    if fixed:
        raise YamatoError(f"{meta['id']} の {', '.join(fixed)} は decide open/close だけが書く "
                          "(閉じるのは `decide close`)")


def _id_num(item_id: str) -> int:
    m = ID_RE.match(item_id)
    return int(m.group(1)) if m else 0


def _show(v) -> str:
    if v in (None, "", []):
        return "-"
    return ",".join(v) if isinstance(v, list) else str(v)


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

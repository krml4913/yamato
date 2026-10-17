"""web コンソールの書き込みの共通部品 (HTTP は持たない)。

書き込みは 2 段: ``preview`` (検証して差分とハッシュを返す。何も書かない) と ``commit``
(preview のときのファイルのハッシュと今のハッシュが同じときだけ書く)。commit は検証をやり直し、
上書き前の版を ``<艦>/.runtime/edit-backup/`` に 1 世代残し、一時ファイル + rename で書く。

どのファイルを書けるかはここでは決めない。``RESOLVERS`` に (艦, ファイル名) から ``Target`` を返す関数を
足した分だけが書ける対象になる (何も足さなければ、どれも 404)。
"""
from __future__ import annotations

import difflib
import hashlib
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ABSENT = "-"            # ファイルがまだ無いときのハッシュ
BACKUP_DIR = "edit-backup"
_lock = threading.Lock()  # commit の「ハッシュを見る → 書く」を直列にする


class EditError(Exception):
    """API のエラー応答にする失敗。"""

    def __init__(self, status: int, code: str, message: str, detail: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.detail = status, code, message, detail


@dataclass
class Target:
    path: Path                                  # 書き込み先
    shipdir: Path                               # 艦フォルダ (.runtime/ の場所)
    key: str                                    # 退避ファイルの名前に使う (ファイル名)
    validate: Callable[[str], list[str]]        # 新しい中身の問題の一覧 (空なら通る)


Resolver = Callable[[str, str], "Target | None"]
RESOLVERS: list[Resolver] = []


def resolve(ship: str, file: str) -> Target:
    for r in RESOLVERS:
        t = r(ship, file)
        if t is not None:
            return t
    raise EditError(404, "not_found", "その艦・ファイルは書き換えられない")


def _digest(data: bytes | None) -> str:
    return ABSENT if data is None else hashlib.sha256(data).hexdigest()


def _snapshot(path: Path) -> bytes | None:
    """ファイルのバイト列を 1 回だけ読む (無ければ None)。ハッシュも差分もこの 1 回から作る。"""
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return None
    except OSError as ex:
        raise EditError(500, "unreadable", "今のファイルを読めない", type(ex).__name__) from ex


def file_hash(path: Path) -> str:
    return _digest(_snapshot(path))


def _decode(data: bytes | None) -> str:
    """改行を変換せずに文字列にする (CRLF のファイルの差分を取りこぼさない)。"""
    try:
        return "" if data is None else data.decode("utf-8")
    except UnicodeDecodeError as ex:
        raise EditError(500, "unreadable", "今のファイルを UTF-8 として読めない", type(ex).__name__) from ex


def preview(target: Target, content: str) -> dict:
    """検証して差分を返す。何も書かない。``base_hash`` は commit に渡す。"""
    snap = _snapshot(target.path)
    old = _decode(snap)
    errs = list(target.validate(content))
    diff = "".join(difflib.unified_diff(old.splitlines(True), content.splitlines(True),
                                        f"a/{target.key}", f"b/{target.key}"))
    return {"valid": not errs, "errors": errs, "changed": old != content, "diff": diff,
            "base_hash": _digest(snap)}


def _backup(target: Target, old: bytes) -> None:
    d = target.shipdir / ".runtime" / BACKUP_DIR
    d.mkdir(parents=True, exist_ok=True)
    _atomic_write(d / target.key.replace("/", "__"), old)


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, path.stat().st_mode & 0o7777)
        except FileNotFoundError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def commit(target: Target, content: str, base_hash: str) -> dict:
    """preview 以降にファイルが変わっていなければ書く。変わっていれば 409、検証に落ちれば 422。"""
    errs = list(target.validate(content))
    if errs:
        raise EditError(422, "invalid", "検証に通らない", "; ".join(errs))
    with _lock:
        snap = _snapshot(target.path)
        if _digest(snap) != base_hash:
            raise EditError(409, "conflict", "preview のあとにファイルが変わっている。やり直す")
        try:
            target.path.parent.mkdir(parents=True, exist_ok=True)
            if snap is not None:
                _backup(target, snap)   # ハッシュを見たバイト列をそのまま退避する
            _atomic_write(target.path, content.encode("utf-8"))
        except OSError as ex:
            raise EditError(500, "internal", "書き込みに失敗した", type(ex).__name__) from ex
        return {"written": True, "hash": file_hash(target.path)}


def handle(op: str, body: dict) -> dict:
    """``POST /api/v1/edit/{preview,commit}`` の本体 (トークンなどの検査は済んでいる前提)。"""
    ship, file, content = body.get("ship"), body.get("file"), body.get("content")
    if not (isinstance(ship, str) and isinstance(file, str) and isinstance(content, str)):
        raise EditError(400, "bad_request", "ship・file・content (文字列) が要る")
    target = resolve(ship, file)
    if op == "preview":
        return preview(target, content)
    base = body.get("base_hash")
    if not isinstance(base, str):
        raise EditError(400, "bad_request", "commit には preview が返した base_hash が要る")
    return commit(target, content, base)

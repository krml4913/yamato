"""web コンソールで team.yaml を直す対象 (``edit.RESOLVERS`` に足す関数と、検証・操作の適用)。

検証は ``team.validate`` と同じ (yaml を読み、艦フォルダを見て検証する)。直し方は 2 通り:
全文 (``content``) か、yaml の部分置き換え (``ops``。コメントと並びを保つ。``yamlpatch``)。
部分置き換えで扱えない形の team.yaml (anchor・複数文書など) は ``unpatchable`` で断り、生の yaml で直してもらう。
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import admiral, shipconfig, team as team_mod, yamlpatch
from ..util import YamatoError
from . import edit

FILE = "team.yaml"
OPS = {"set_value", "add_key", "remove_key", "insert_item", "remove_item"}


_PATH = re.compile(r"team\.yaml: ([A-Za-z_][\w-]*(?:\.[\w-]+)*)")
_NAMES = re.compile(r"(?:知らない項目があります|知らない項目): ([\w, .-]+)")


def _line_of(text: str, message: str) -> int | None:
    """検証のエラーが指す項目の行 (1 始まり)。メッセージの ``roles.dev.trust`` のような道筋を辿れた所までの行。"""
    import yaml
    m = _PATH.search(message)
    names = _NAMES.search(message)
    path = m.group(1).split(".") if m else []
    if names:
        path += [names.group(1).split(",")[0].strip()]
    try:
        node = yaml.compose(text)
    except yaml.YAMLError:
        return None
    line = None
    for part in path:
        if not isinstance(node, yaml.MappingNode):
            break
        for k, v in node.value:
            if k.value == part:
                node, line = v, k.start_mark.line + 1
                break
        else:
            break
    return line


def validate(shipdir: Path, content: str) -> list[str]:
    """問題の一覧。yaml の構文エラーと、項目を指せるエラーには ``行 N:`` を付ける。"""
    import yaml   # vendored; 使うときまで import しない
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as ex:
        mark = getattr(ex, "problem_mark", None) or getattr(ex, "context_mark", None)
        what = getattr(ex, "problem", None) or str(ex)
        where = f"行 {mark.line + 1}: " if mark is not None else ""
        return [f"{where}yaml を読めない: {what}"]
    if not isinstance(data, dict):
        return ["team.yaml の中身が mapping ではない"]
    try:
        team_mod.validate(data, shipdir)
    except YamatoError as ex:
        line = _line_of(content, str(ex))
        return [f"行 {line}: {ex}" if line else str(ex)]
    except Exception as ex:   # noqa: BLE001 — 検証の想定外の落ち方でサーバーを落とさない
        return [f"検証に失敗した: {type(ex).__name__}"]
    return []


def _path(op: dict) -> list:
    path = op.get("path")
    if not isinstance(path, list) or not all(isinstance(p, (str, int)) and not isinstance(p, bool) for p in path):
        raise edit.EditError(400, "bad_request", "ops の path は str / int の配列")
    return path


def apply_ops(text: str, ops: list) -> str:
    """操作を順に当てる。形が不正なら 400、yamlpatch が扱えない形・操作なら 422 (unpatchable)。"""
    for i, op in enumerate(ops):
        if not isinstance(op, dict) or op.get("op") not in OPS:
            raise edit.EditError(400, "bad_request", f"ops[{i}]: op は {' / '.join(sorted(OPS))} のどれか")
        kind, path = op["op"], _path(op)
        try:
            if kind == "set_value":
                text = yamlpatch.set_value(text, path, op.get("value"))
            elif kind == "add_key":
                if not isinstance(op.get("key"), str):
                    raise edit.EditError(400, "bad_request", f"ops[{i}]: add_key には key (文字列) が要る")
                text = yamlpatch.add_key(text, path, op["key"], op.get("value"))
            elif kind == "remove_key":
                text = yamlpatch.remove_key(text, path)
            elif kind == "insert_item":
                index = op.get("index")
                if not (index is None or (isinstance(index, int) and not isinstance(index, bool))):
                    raise edit.EditError(400, "bad_request", f"ops[{i}]: index は整数")
                text = yamlpatch.insert_item(text, path, op.get("value"), index)
            else:
                text = yamlpatch.remove_item(text, path)
        except yamlpatch.YamlPatchError as ex:
            raise edit.EditError(422, "unpatchable", "この形はフォームで直せない。生の yaml で直す", str(ex)) from ex
    return text


def _locked_values(text: str):
    import yaml
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return {k: data.get(k) for k in shipconfig.LOCKED_KEYS} if isinstance(data, dict) else None


def refuse_locked(path: Path, content: str) -> None:
    """``shipconfig.LOCKED_KEYS`` の項目は画面から変えさせない。今のファイルと値が違えば 400。"""
    try:
        old = path.read_text(encoding="utf-8")
    except OSError:
        return
    before, after = _locked_values(old), _locked_values(content)
    if before is None or after is None:
        return
    for key in shipconfig.LOCKED_KEYS:
        if before[key] != after[key]:
            raise edit.EditError(400, "locked_key", f"{key} は dashboard からは変えられない",
                                 "艦の名前は艦フォルダと席の宛先に関わる" if key == "name" else None)


def resolve(ship: str, file: str) -> edit.Target | None:
    if file != FILE:
        return None
    shipdir = admiral.find_ship(ship)
    if shipdir is None:
        return None
    path = shipdir / FILE

    def check(text: str) -> list[str]:
        refuse_locked(path, text)
        return validate(shipdir, text)

    return edit.Target(path, shipdir, FILE, check, apply_ops)

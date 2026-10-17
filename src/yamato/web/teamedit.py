"""web コンソールで team.yaml を直す対象 (``edit.RESOLVERS`` に足す関数と、検証・操作の適用)。

検証は ``team.validate`` と同じ (yaml を読み、艦フォルダを見て検証する)。直し方は 2 通り:
全文 (``content``) か、yaml の部分置き換え (``ops``。コメントと並びを保つ。``yamlpatch``)。
部分置き換えで扱えない形の team.yaml (anchor・複数文書など) は ``unpatchable`` で断り、生の yaml で直してもらう。
"""
from __future__ import annotations

from pathlib import Path

from .. import shipconfig, team as team_mod, yamlpatch
from ..util import YamatoError
from . import edit

FILE = "team.yaml"
OPS = {"set_value", "add_key", "remove_key", "insert_item", "remove_item"}


def validate(shipdir: Path, content: str) -> list[str]:
    import yaml   # vendored; 使うときまで import しない
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as ex:
        return [f"yaml を読めない: {ex}"]
    if not isinstance(data, dict):
        return ["team.yaml の中身が mapping ではない"]
    try:
        team_mod.validate(data, shipdir)
    except YamatoError as ex:
        return [str(ex)]
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


def resolve(ship: str, file: str) -> edit.Target | None:
    if file != FILE:
        return None
    shipdir = shipconfig.registered(ship)
    if shipdir is None:
        return None
    return edit.Target(shipdir / FILE, shipdir, FILE, lambda text: validate(shipdir, text), apply_ops)

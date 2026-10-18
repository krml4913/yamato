"""艦の設定 (team.yaml) を項目ごとに読む。``GET /api/v1/ships/{name}/config`` の中身 (HTTP なし)。

読むだけ: 艦フォルダに何も書かない。項目の一覧は ``team.schema.json`` の最上位の properties と
team.yaml のキーから組み立てる (項目名はここに固定しない)。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import team as team_mod
from .util import YamatoError, read_json

SCHEMA = Path(__file__).parent / "schema" / "team.schema.json"


def _jsonable(value):
    """YAML の値 (日付など) を JSON にできる形に写す。"""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _type_of(schema: dict) -> str | None:
    t = schema.get("type")
    if isinstance(t, list):
        return "|".join(str(x) for x in t)
    return t if isinstance(t, str) else None


# dashboard から書き換えさせない最上位の項目 (サーバーが断り、フォームは読むだけの表示にする)。
# name は艦フォルダと席の宛先 <name>.<席> に関わる。
LOCKED_KEYS = ("name",)


def fields(data: dict, schema: dict) -> list[dict]:
    """schema の最上位の並びで項目を出す。schema にあって yaml に無い項目は set=False、
    yaml にあって schema に無い項目は末尾に (説明なし) 出す。"""
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    out = []
    for key, spec in props.items():
        spec = spec if isinstance(spec, dict) else {}
        out.append({
            "key": key, "set": key in data, "required": key in required, "locked": key in LOCKED_KEYS,
            "value": _jsonable(data[key]) if key in data else None,
            "type": _type_of(spec), "description": spec.get("description"),
            "enum": spec.get("enum") if isinstance(spec.get("enum"), list) else None,
            "schema": spec or None,
        })
    for key in data:
        if key not in props:
            out.append({"key": str(key), "set": True, "required": False, "locked": key in LOCKED_KEYS, "value": _jsonable(data[key]),
                        "type": None, "description": None, "enum": None, "schema": None})
    return out


def _files(shipdir: Path) -> list[dict]:
    found = []
    for p in sorted((shipdir / "roles").glob("*.md")):
        found.append((p, "role"))
    for name, kind in (("charter.md", "charter"), ("knowledge.md", "knowledge")):
        found.append((shipdir / name, kind))
    out = []
    for p, kind in found:
        try:
            if p.is_file():
                out.append({"file": p.relative_to(shipdir).as_posix(), "kind": kind, "bytes": p.stat().st_size})
        except OSError:
            continue
    return out


def pending(shipdir: Path) -> dict:
    """team.yaml が ``.runtime/team.json`` より新しければ changed (次の up で効く変更がある)。
    keys は、検証した team.yaml と team.json で値の違う最上位の項目 (検証できなければ空)。"""
    yml, cache = shipdir / "team.yaml", shipdir / ".runtime" / "team.json"
    try:
        changed = yml.stat().st_mtime > cache.stat().st_mtime
    except OSError:
        return {"changed": False, "keys": []}   # まだ up していない艦は比べるものが無い
    keys: list[str] = []
    if changed:
        try:
            now, before = _jsonable(team_mod.load_team(shipdir)), read_json(cache) or {}
            keys = [k for k in dict.fromkeys([*now, *before]) if now.get(k) != before.get(k)]
        except (YamatoError, OSError, ValueError):
            keys = []
    return {"changed": changed, "keys": keys}


def ship_config(name: str, shipdir: Path) -> dict:
    """``ShipConfig`` の形。team.yaml を読めなければ ``YamatoError``。"""
    path = shipdir / "team.yaml"
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as ex:
        raise YamatoError(f"{path} を読めません: {ex}") from None
    data = team_mod.load_yaml(path)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return {"name": name, "path": str(path), "fields": fields(data, schema), "raw": raw,
            "files": _files(shipdir), "pending": pending(shipdir)}

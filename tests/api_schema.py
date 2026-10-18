"""API の応答を docs/api/openapi.yaml のスキーマで確かめる小さな検査器。

jsonschema は vendor しない。扱う範囲は type (配列で "null" も可)・nullable・properties・required・
additionalProperties (bool か schema)・items・enum・const・$ref (``#/…`` のこの文書の中だけ)。
知らないキーワードは黙って通さず ``UnsupportedSchema`` にする (検査したつもりになるのを防ぐ)。
"""
from __future__ import annotations

from pathlib import Path

import yaml

OPENAPI = Path(__file__).resolve().parent.parent / "docs" / "api" / "openapi.yaml"

# 検査に使うキーワード
CHECKED = {"type", "nullable", "properties", "required", "additionalProperties", "items", "enum", "const", "$ref"}
# 検査しない注釈
ANNOTATIONS = {"description", "title", "example", "examples", "default", "deprecated", "format"}
TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


class UnsupportedSchema(Exception):
    """この検査器が解けないキーワード・$ref を見つけた。"""


def load(path: Path = OPENAPI) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def resolve(doc: dict, ref: str) -> dict:
    if not ref.startswith("#/"):
        raise UnsupportedSchema(f"文書の外の $ref は解けない: {ref}")
    node = doc
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise UnsupportedSchema(f"$ref が解けない: {ref}")
        node = node[part]
    return node


def _is(value, t: str) -> bool:
    if t == "null":
        return value is None
    if t == "boolean":
        return isinstance(value, bool)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "string":
        return isinstance(value, str)
    if t == "array":
        return isinstance(value, list)
    if t == "object":
        return isinstance(value, dict)
    raise UnsupportedSchema(f"知らない type: {t!r}")


def _names(schema: dict) -> list[str]:
    t = schema.get("type")
    return [] if t is None else list(t) if isinstance(t, list) else [t]


def errors(doc: dict, schema: dict, value, path: str = "$") -> list[str]:
    """``value`` が ``schema`` に合わないところを ``"<path>: <理由>"`` で返す (合えば空)。"""
    if not isinstance(schema, dict):
        raise UnsupportedSchema(f"{path}: schema が object でない: {schema!r}")
    if "$ref" in schema:
        return errors(doc, resolve(doc, schema["$ref"]), value, path)
    unknown = set(schema) - CHECKED - ANNOTATIONS
    if unknown:
        raise UnsupportedSchema(f"{path}: 解けないキーワード {sorted(unknown)}")
    names = _names(schema)
    for t in names:
        if t not in TYPES:
            raise UnsupportedSchema(f"{path}: 知らない type {t!r}")
    if value is None and (schema.get("nullable") is True or "null" in names):
        return []
    out: list[str] = []
    if names and not any(_is(value, t) for t in names):
        return [f"{path}: type が {'|'.join(names)} でない ({type(value).__name__}: {value!r:.60})"]
    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: {schema['const']!r} でない ({value!r:.60})")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: enum {schema['enum']} にない ({value!r:.60})")
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for k in schema.get("required") or []:
            if k not in value:
                out.append(f"{path}: 必須の {k!r} がない")
        extra = schema.get("additionalProperties", True)
        for k, v in value.items():
            if k in props:
                out += errors(doc, props[k], v, f"{path}.{k}")
            elif extra is False:
                out.append(f"{path}: 宣言にない {k!r}")
            elif isinstance(extra, dict):
                out += errors(doc, extra, v, f"{path}.{k}")
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            out += errors(doc, schema["items"], v, f"{path}[{i}]")
    return out


def response_schema(doc: dict, path: str, status: str = "200", method: str = "get") -> dict:
    """path の operation の、status の応答の schema。"""
    resp = doc["paths"][path][method]["responses"][status]
    if "$ref" in resp:
        resp = resolve(doc, resp["$ref"])
    return resp["content"]["application/json"]["schema"]


def check(doc: dict, schema: dict, value) -> list[str]:
    return errors(doc, schema, value)


def check_response(value, path: str, status: str = "200", method: str = "get", doc: dict | None = None) -> list[str]:
    """API の応答 (JSON をデコードした値) を openapi.yaml の path / status の schema で検査する。"""
    doc = doc or load()
    return errors(doc, response_schema(doc, path, status, method), value)


def assert_valid(test, value, path: str, status: str = "200", method: str = "get") -> None:
    """unittest から: 合わなければ理由を全部出して失敗にする。"""
    errs = check_response(value, path, status, method)
    test.assertEqual(errs, [], "\n".join(errs))

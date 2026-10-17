"""docs/api/openapi.yaml と応答検査器 (tests/api_schema.py) の単体テスト (T-106)。"""
import copy
import unittest

from tests import api_schema as A

DOC = A.load()
S = DOC["components"]["schemas"]


def walk(node, path="$"):
    """文書の中の schema らしい dict を全部たどる (キーワードの検査用)。"""
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}[{i}]")


class ValidatorTest(unittest.TestCase):
    def e(self, schema, value):
        return A.errors({}, schema, value)

    def test_types(self):
        ok = {"string": "a", "integer": 3, "number": 1.5, "boolean": True, "null": None, "array": [], "object": {}}
        for t, v in ok.items():
            self.assertEqual(self.e({"type": t}, v), [], t)
        self.assertEqual(self.e({"type": "number"}, 2), [])           # integer は number でもある
        self.assertTrue(self.e({"type": "integer"}, 1.5))
        self.assertTrue(self.e({"type": "integer"}, True))            # bool は数でない
        self.assertTrue(self.e({"type": "number"}, False))
        self.assertTrue(self.e({"type": "string"}, None))

    def test_type_list_and_nullable(self):
        self.assertEqual(self.e({"type": ["string", "null"]}, None), [])
        self.assertEqual(self.e({"type": ["string", "null"]}, "x"), [])
        self.assertTrue(self.e({"type": ["string", "null"]}, 1))
        self.assertEqual(self.e({"type": "string", "nullable": True}, None), [])
        self.assertTrue(self.e({"type": "string"}, None))

    def test_no_type_accepts_anything(self):
        for v in (None, 1, "a", [1], {"a": 1}):
            self.assertEqual(self.e({"description": "any"}, v), [])

    def test_required_properties_additional(self):
        sc = {"type": "object", "required": ["a"], "properties": {"a": {"type": "integer"}, "b": {"type": "string"}}}
        self.assertEqual(self.e(sc, {"a": 1}), [])
        self.assertEqual(self.e(sc, {"a": 1, "b": "x", "extra": [1]}), [])           # 既定は余分を許す
        self.assertIn("必須の 'a'", self.e(sc, {})[0])
        self.assertIn("$.b", self.e(sc, {"a": 1, "b": 2})[0])
        self.assertIn("'extra'", self.e({**sc, "additionalProperties": False}, {"a": 1, "extra": 1})[0])
        m = {"type": "object", "additionalProperties": {"type": "integer"}}
        self.assertEqual(self.e(m, {"x": 1, "y": 2}), [])
        self.assertIn("$.y", self.e(m, {"x": 1, "y": "z"})[0])

    def test_items_enum_const(self):
        self.assertEqual(self.e({"type": "array", "items": {"type": "string"}}, ["a", "b"]), [])
        self.assertIn("[1]", self.e({"type": "array", "items": {"type": "string"}}, ["a", 2])[0])
        self.assertEqual(self.e({"enum": ["red", "warn"]}, "red"), [])
        self.assertTrue(self.e({"enum": ["red", "warn"]}, "blue"))
        self.assertTrue(self.e({"const": 1}, 2))
        self.assertEqual(self.e({"type": ["array", "null"], "items": {}}, None), [])

    def test_ref_resolution(self):
        doc = {"components": {"schemas": {"N": {"type": "integer"}}}}
        sc = {"type": "array", "items": {"$ref": "#/components/schemas/N"}}
        self.assertEqual(A.errors(doc, sc, [1, 2]), [])
        self.assertTrue(A.errors(doc, sc, ["x"]))
        with self.assertRaises(A.UnsupportedSchema):
            A.errors(doc, {"$ref": "#/components/schemas/Nope"}, 1)
        with self.assertRaises(A.UnsupportedSchema):
            A.errors(doc, {"$ref": "other.yaml#/x"}, 1)

    def test_unknown_keyword_is_not_silently_ignored(self):
        for kw in ({"oneOf": []}, {"allOf": []}, {"pattern": "x"}, {"minimum": 1}):
            with self.assertRaises(A.UnsupportedSchema):
                self.e(kw, 1)
        with self.assertRaises(A.UnsupportedSchema):
            self.e({"type": "str"}, "a")

    def test_reports_every_error_with_path(self):
        sc = {"type": "object", "required": ["a", "b"], "properties": {"c": {"type": "integer"}}}
        errs = self.e(sc, {"c": "x"})
        self.assertEqual(len(errs), 3)


class OpenApiDocTest(unittest.TestCase):
    PATHS = ["/api/v1/overview", "/api/v1/fleet", "/api/v1/ships/{name}/config", "/api/v1/ships/{name}/files/{file}",
             "/api/v1/edit/preview", "/api/v1/edit/commit"]

    def test_version_and_paths(self):
        self.assertTrue(DOC["openapi"].startswith("3.1"))
        self.assertEqual(sorted(DOC["paths"]), sorted(self.PATHS))

    def test_every_operation_has_200_json_and_shared_error(self):
        for p, item in DOC["paths"].items():
            method = "post" if p.startswith("/api/v1/edit/") else "get"   # 書くのは edit だけ。ほかは読むだけ
            self.assertEqual(list(k for k in item if k != "parameters"), [method], p)
            op = item[method]
            self.assertIn("operationId", op)
            self.assertIn("schema", op["responses"]["200"]["content"]["application/json"], p)
            self.assertEqual(op["responses"]["default"], {"$ref": "#/components/responses/Error"}, p)
            names = [x["name"] for x in op.get("parameters", [])
                     if "$ref" not in x] + [A.resolve(DOC, x["$ref"])["name"] for x in op.get("parameters", []) if "$ref" in x]
            for seg in ("name", "file"):
                self.assertEqual("{%s}" % seg in p, seg in names, (p, seg))

    def test_all_refs_resolve_and_only_supported_keywords(self):
        refs = 0
        for where, node in walk(DOC["components"]["schemas"]):
            if "$ref" in node:
                refs += 1
                A.resolve(DOC, node["$ref"])
            if "type" in node or "properties" in node or "$ref" in node:
                unknown = set(node) - A.CHECKED - A.ANNOTATIONS
                # properties の値の dict には任意の項目名が入るので、schema の位置だけ見る
                if "properties" in node and "type" in node or "$ref" in node:
                    self.assertEqual(unknown, set(), where)
        self.assertGreater(refs, 5)
        # 全 schema は検査器が実際に解ける (未対応キーワードがあれば UnsupportedSchema)
        for name, sc in S.items():
            A.errors(DOC, sc, {})

    def test_required_are_declared_properties(self):
        for where, node in walk(DOC["components"]["schemas"]):
            if isinstance(node.get("required"), list) and isinstance(node.get("properties"), dict):
                self.assertEqual(set(node["required"]) - set(node["properties"]), set(), where)

    def test_error_is_the_same_shape_everywhere(self):
        self.assertEqual(S["Error"]["required"], ["error"])
        err = {"error": {"code": "not_found", "message": "艦 x が無い", "detail": None}}
        self.assertEqual(A.errors(DOC, S["Error"], err), [])
        self.assertTrue(A.errors(DOC, S["Error"], {"error": {"code": "boom", "message": "x"}}))   # enum 外
        self.assertTrue(A.errors(DOC, S["Error"], {"error": {"code": "internal"}}))               # message 無し
        self.assertTrue(A.errors(DOC, S["Error"], {"message": "x"}))


def seat(**kw):
    s = {"name": "impl-1", "role": "impl", "shift": "per_task", "alive": True, "pid": 123, "status": "busy",
         "waiting_for": None, "last": 1.7e9, "active": ["T-1"], "reviewing": [], "unread": 0,
         "flags": [{"level": "warn", "text": "動いていない → 見る", "detail": "idle"}], "log": "", "tokens": 10,
         "budget": None, "last_cost": None, "context": {"tokens": 1000, "limit": None}}
    s.update(kw)
    return s


def state(**kw):
    s = {"team": "t", "deadline": "deadline 10-17 17:23 (残り 1h)", "hub": "pm", "hub_last": None, "tokens": 5,
         "owner_unread": 0, "multi_repo": False, "counts": {"open": 1, "active": 2}, "seats": [seat()],
         "board": [{"id": "T-1", "title": "x", "state": "active", "assignee": "impl-1", "blocked_on": []}],
         "prs": [{"item": "T-1", "title": "x", "state": "active", "repo": "yamato", "number": "123", "link": None}],
         "anomalies": [{"kind": "api_error", "ts": 1.7e9, "summary": "s"}],
         "report": None, "orphans": [{"id": "T-2", "title": "y", "seat": None, "why": "落ちた"}],
         "memory": [{"role": None, "count": 2, "days": None, "due": False}]}
    s.update(kw)
    return s


OVERVIEW = {"generated_at": 1.7e9, "agents_error": None,
            "waits": [{"ship": "a", "id": "D-1", "title": "t", "urgent": True, "age": "1h", "recommendation": None}],
            "ships": [{"name": "a", "error": None, "state": state()},
                      {"name": "b", "error": "ValueError: 壊れ", "state": None}]}
FLEET = {"home": "/h", "ships": [{"name": "a", "path": "/h/a", "registered": True, "team": "t", "error": None},
                                 {"name": "b", "path": "/h/b", "registered": False, "team": None, "error": "x"}],
         "admiral": {"path": "/h/_admiral", "team": None}}
CONFIG = {"name": "a", "path": "/h/a/team.yaml", "raw": "name: a\n# c\n", "pending": {"changed": True, "keys": ["roles"]},
          "files": [{"file": "roles/impl.md", "kind": "role", "bytes": 10}, {"file": "charter.md", "kind": "charter", "bytes": 0}],
          "fields": [{"key": "name", "set": True, "required": True, "value": "a", "type": "string",
                      "description": "艦の名前。", "enum": None, "schema": {"type": "string"}},
                     {"key": "workspace", "set": True, "required": True, "value": ["/x", "/y"], "type": None,
                      "description": None, "enum": None, "schema": None},
                     {"key": "x", "set": False, "required": False, "value": None, "type": "string",
                      "description": "d", "enum": ["a", "b"], "schema": {"enum": ["a", "b"]}}]}
FILE = {"name": "a", "file": "roles/impl.md", "kind": "role", "text": "# impl\n", "bytes": 7}

CASES = [("/api/v1/overview", OVERVIEW), ("/api/v1/fleet", FLEET),
         ("/api/v1/ships/{name}/config", CONFIG), ("/api/v1/ships/{name}/files/{file}", FILE)]


class ResponseCheckTest(unittest.TestCase):
    def test_sample_responses_are_valid(self):
        for path, body in CASES:
            with self.subTest(path):
                A.assert_valid(self, body, path)

    def test_missing_required_key_is_caught(self):
        for path, body in CASES:
            for key in list(body):
                bad = {k: v for k, v in body.items() if k != key}
                with self.subTest(path=path, key=key):
                    self.assertTrue(A.check_response(bad, path), "必須の欠けを見逃した")

    def test_wrong_types_are_caught(self):
        bad = copy.deepcopy(OVERVIEW)
        bad["ships"][0]["state"]["seats"][0]["pid"] = "123"
        self.assertIn("pid", A.check_response(bad, "/api/v1/overview")[0])
        bad = copy.deepcopy(OVERVIEW)
        bad["ships"][0]["state"]["counts"]["open"] = "1"
        self.assertTrue(A.check_response(bad, "/api/v1/overview"))
        bad = copy.deepcopy(OVERVIEW)
        bad["ships"][0]["state"]["seats"][0]["flags"][0]["level"] = "yellow"
        self.assertTrue(A.check_response(bad, "/api/v1/overview"))
        bad = copy.deepcopy(CONFIG)
        bad["files"][0]["kind"] = "memory"
        self.assertTrue(A.check_response(bad, "/api/v1/ships/{name}/config"))
        bad = copy.deepcopy(FILE)
        bad["bytes"] = None
        self.assertTrue(A.check_response(bad, "/api/v1/ships/{name}/files/{file}"))

    def test_error_response(self):
        err = {"error": {"code": "not_found", "message": "m"}}
        self.assertEqual(A.check_response(err, "/api/v1/fleet", "default"), [])
        self.assertTrue(A.check_response({"error": "m"}, "/api/v1/fleet", "default"))


if __name__ == "__main__":
    unittest.main()

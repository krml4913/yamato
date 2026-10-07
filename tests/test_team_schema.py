"""team.yaml's JSON Schema (T-057): the editor's completion / hover / warnings.

team.py's validate stays the authority; the schema is a second copy for the editor, so these
tests keep the two from drifting: the key sets must match, every property needs a description,
and the three templates must pass a minimal checker (no jsonschema in vendor/: types, enum,
const, anyOf, properties, additionalProperties, items, required, minimum)."""
import json
import re
import unittest
from pathlib import Path

from tests.helpers import ShipTestCase
from yamato import memory, ship, team
from yamato.team import load_yaml

SCHEMA_PATH = Path(ship.SCHEMA)
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool,
         "integer": int, "number": (int, float), "null": type(None)}


def check(value, schema, path="$"):
    """The first problem as a string, or None."""
    if "anyOf" in schema:
        errs = [check(value, s, path) for s in schema["anyOf"]]
        return None if None in errs else f"{path}: anyOf のどれにも合わない ({errs[0]})"
    if "const" in schema and (value != schema["const"] or type(value) is not type(schema["const"])):
        return f"{path}: {schema['const']!r} のはず"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path}: {value!r} は enum {schema['enum']} にない"
    t = schema.get("type")
    if t:
        ok = isinstance(value, TYPES[t]) and not (t in ("integer", "number") and isinstance(value, bool)) \
            and not (t == "integer" and isinstance(value, float))
        if not ok:
            return f"{path}: {t} のはず ({value!r})"
    if "minimum" in schema and isinstance(value, (int, float)) and value < schema["minimum"]:
        return f"{path}: {schema['minimum']} 以上のはず"
    if "pattern" in schema and isinstance(value, str) and not re.search(schema["pattern"], value):
        return f"{path}: {schema['pattern']} に合わない ({value!r})"
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in value:
                return f"{path}: {k} がない"
        for k, v in value.items():
            if k in props:
                err = check(v, props[k], f"{path}.{k}")
            else:
                extra = schema.get("additionalProperties", True)
                if extra is False:
                    return f"{path}: 知らない項目 {k}"
                err = check(v, extra, f"{path}.{k}") if isinstance(extra, dict) else None
            if err:
                return err
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            err = check(v, schema["items"], f"{path}[{i}]")
            if err:
                return err
    return None


def walk(schema, path="$"):
    """Every (path, subschema) that is a named property."""
    for k, sub in schema.get("properties", {}).items():
        yield f"{path}.{k}", sub
        yield from walk(sub, f"{path}.{k}")
    for sub in ([schema["additionalProperties"]] if isinstance(schema.get("additionalProperties"), dict) else []):
        yield from walk(sub, path + ".*")
    if isinstance(schema.get("items"), dict):
        yield from walk(schema["items"], path + "[]")
    for sub in schema.get("anyOf", []):
        yield from walk(sub, path)


def find(schema, *keys):
    """The object schema at team.<keys...> (through additionalProperties / anyOf)."""
    for k in keys:
        if k == "*":
            schema = schema["additionalProperties"]
        else:
            schema = schema["properties"][k]
        for alt in schema.get("anyOf", []):
            if "properties" in alt:
                schema = alt
    return schema


class TeamSchemaTest(ShipTestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def keys(self, *path):
        return set(find(self.schema, *path)["properties"])

    def test_the_keys_match_team_py(self):
        s = self.schema
        self.assertEqual(set(s["properties"]), team.TOP_KEYS)
        self.assertEqual(self.keys("roles", "*"), team.ROLE_KEYS)
        self.assertEqual(self.keys("roles", "*", "rotate"), set(team.ROTATE_KEYS))
        self.assertEqual(self.keys("profiles", "*"), team.PROFILE_KEYS)
        self.assertEqual(self.keys("git"), team.GIT_KEYS | team.GIT_REMOVED_KEYS)
        self.assertEqual(self.keys("inject"), {"parts", "limits", "files"})
        self.assertEqual(self.keys("inject", "limits"), {*team.INJECT_LIMIT_KEYS, "files", "charter"})   # charter: 古い書き方 (deprecated)
        self.assertEqual(self.keys("memory"), memory.MEMORY_KEYS)
        self.assertEqual(self.keys("memory", "limits"), set(memory.LIMIT_KEYS))
        self.assertEqual(self.keys("seat_stop"), set(team.SEAT_STOP_DEFAULTS))
        self.assertEqual(self.keys("last_call"), set(team.LAST_CALL_FALLBACK))
        self.assertEqual(self.keys("watch"), {*team.WATCH_FALLBACK, *team.WATCH_LIFECYCLE_FALLBACK})
        self.assertEqual(self.keys("watch", "spin"), set(team.WATCH_LIFECYCLE_FALLBACK["spin"]))
        self.assertEqual(self.keys("notify"), {"via", "slack", "command", "decisions"})
        self.assertEqual(self.keys("decisions", "*"), {"decider", "when"})
        self.assertEqual(self.keys("report"), {"daily"})
        self.assertEqual(self.keys("board"), {"archive_on_done", "kinds", "fields", "columns"})

    def test_inject_limits_shape_matches_team_py(self):
        """T-073: the pair keys are [lines, chars] only in the schema too (no bare int)."""
        props = find(self.schema, "inject", "limits")["properties"]
        for k in team.INJECT_LIMIT_KEYS:
            good, bad = ([10, 500], 500) if k in team.INJECT_PAIR_LIMIT_KEYS else (10, [10, 500])
            with self.subTest(key=k):
                self.assertIsNone(check(good, props[k]))
                self.assertIsNotNone(check(bad, props[k]))
        # T-075: limits.files is {名前 or default: [行数, 文字数]}
        self.assertIsNone(check({"default": [60, 3000], "design": [80, 4000]}, props["files"]))
        for bad in ({"design": 500}, {"design": [1, "x"]}, {"default": "x"}):
            self.assertIsNotNone(check(bad, props["files"]), bad)

    def test_inject_parts_accept_file_references(self):
        """T-075: the lists take the fixed parts and `file:<名前>`; inject.files is {名前: パス}."""
        for node in (find(self.schema, "inject")["properties"]["parts"],
                     find(self.schema, "roles", "*")["properties"]["inject"]):
            self.assertIsNone(check(["handoff", "file:charter"], node))
            self.assertIsNone(check(["charter"], node))   # the old spelling stays valid for one version (deprecated)
            self.assertIsNotNone(check(["file:"], node))
        files = find(self.schema, "inject")["properties"]["files"]
        self.assertIsNone(check({"charter": "charter.md"}, files))
        self.assertIsNotNone(check({"charter": 1}, files))

    def test_the_values_match_team_py(self):
        s = self.schema
        self.assertEqual(find(s, "roles", "*")["properties"]["shift"]["enum"], list(team.SHIFTS))
        inject_item = find(s, "inject")["properties"]["parts"]["items"]["anyOf"][0]["enum"]
        self.assertEqual(inject_item, list(team.INJECT_PARTS))
        self.assertEqual(find(s, "git")["properties"]["strategy"]["enum"], list(team.GIT_STRATEGIES))
        self.assertEqual(find(s, "git")["properties"]["merge_requires"]["items"]["enum"], list(team.GIT_REQUIRES))
        self.assertEqual(find(s, "notify")["properties"]["via"]["items"]["enum"], list(team.NOTIFY_CHANNELS))
        self.assertEqual(find(s, "notify")["properties"]["decisions"]["enum"], list(team.NOTIFY_DECISIONS))
        self.assertEqual(find(s, "board", "columns")["items"]["properties"]["state"]["enum"], list(team.STATES))

    def test_every_property_has_a_japanese_description(self):
        for path, sub in walk(self.schema):
            self.assertTrue(sub.get("description") or sub.get("deprecationMessage")
                            or any(a.get("description") for a in sub.get("anyOf", [])),
                            f"{path} に description がない")
        self.assertTrue(self.schema["description"])

    def test_the_old_memory_limits_is_deprecated_but_still_valid(self):
        """T-076: the four flat keys stay valid for one version (read over to inject.limits), marked deprecated."""
        node = find(self.schema, "memory", "limits")
        self.assertTrue(node["deprecated"])
        self.assertIsNone(check({"memory_lines": 80, "knowledge_chars": 5000}, node))
        self.assertIsNotNone(check({"memory_lines": 0}, node))

    def test_the_removed_git_key_is_deprecated(self):
        self.assertIn("deprecationMessage", self.schema["properties"]["git"]["properties"]["merge_decision"])

    def test_the_templates_pass_the_schema(self):
        dirs = {"dev": self.shipdir}
        for t in ("research", "admiral"):
            dirs[t], _ = ship.create(f"s-{t}", None, str(self.tmp / f"s-{t}"), t, register=False)
        for t, d in dirs.items():
            with self.subTest(template=t):
                self.assertIsNone(check(load_yaml(d / "team.yaml"), self.schema, t))

    def test_the_checker_catches_a_stray_key_and_a_bad_value(self):
        good = load_yaml(self.shipdir / "team.yaml")
        self.assertIsNotNone(check({**good, "nope": 1}, self.schema))
        self.assertIsNotNone(check({**good, "git": {"strategy": "fast"}}, self.schema))
        self.assertIsNotNone(check({**good, "roles": {"pm": {"shift": "sometimes"}}}, self.schema))

    def test_a_new_ship_points_at_the_schema_by_absolute_path(self):
        for t, d in (("dev", self.shipdir),
                     *((t, ship.create(f"p-{t}", None, str(self.tmp / f"p-{t}"), t, register=False)[0])
                       for t in ("research", "admiral"))):
            with self.subTest(template=t):
                first = (d / "team.yaml").read_text(encoding="utf-8").splitlines()[0]
                self.assertEqual(first, f"# yaml-language-server: $schema={SCHEMA_PATH.resolve().as_uri()}")
                self.assertNotIn("{{schema}}", first)


if __name__ == "__main__":
    unittest.main()

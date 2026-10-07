import unittest
from pathlib import Path

from yamato.team import expand_seats, validate
from yamato.util import YamatoError, parse_duration


def base(**over):
    d = {
        "name": "dev", "hub": "pm", "workspace": "/tmp",
        "roles": {"pm": {"model": "opus", "shift": "persistent"},
                  "impl": {"model": "sonnet", "shift": "per_task", "count": 2}},
    }
    d.update(over)
    return d


class TeamTest(unittest.TestCase):
    def test_count_two_expands_to_numbered_seats(self):
        t = validate(base(), Path("/ship"))
        self.assertEqual(list(t["seats"]), ["pm", "impl-1", "impl-2"])
        self.assertEqual(t["seats"]["impl-2"], {"role": "impl", "model": "sonnet", "shift": "per_task"})

    def test_count_one_keeps_role_name(self):
        seats = expand_seats({"impl": {"model": "sonnet", "shift": "per_task", "count": 1}})
        self.assertEqual(list(seats), ["impl"])

    def test_defaults(self):
        t = validate(base(), Path("/ship"))
        self.assertEqual(t["time_limit"], 3 * 3600)
        self.assertEqual(t["grace"], 20 * 60)
        self.assertEqual(t["board"]["kinds"], ["task"])
        self.assertEqual(t["deny"], [])
        self.assertEqual(t["settings"], {})
        self.assertEqual(t["seat_stop"], {"require_handoff": True, "require_delivery": True})

    def test_setting_sources_default_optin_and_invalid(self):
        self.assertEqual(validate(base(), Path("/ship"))["setting_sources"], ["project", "local"])
        t = validate(base(setting_sources=["project", "local", "user"]), Path("/ship"))
        self.assertEqual(t["setting_sources"], ["user", "project", "local"])
        for bad in (["global"], [], "user", [1]):
            with self.assertRaises(YamatoError, msg=repr(bad)):
                validate(base(setting_sources=bad), Path("/ship"))

    def test_relative_workspace_is_under_ship(self):
        t = validate(base(workspace="work"), Path("/ship"))
        self.assertEqual(Path(t["workspace"]), Path("/ship").resolve() / "work")

    def test_workspace_string_and_list(self):
        one = validate(base(workspace="/r/app"), Path("/ship"))
        self.assertEqual(one["workspaces"], [{"name": "app", "path": str(Path("/r/app").resolve())}])
        many = validate(base(workspace=["/r/app", "lib"]), Path("/ship"))
        self.assertEqual([w["name"] for w in many["workspaces"]], ["app", "lib"])
        self.assertEqual(many["workspace"], many["workspaces"][0]["path"])
        self.assertEqual(Path(many["workspaces"][1]["path"]), Path("/ship").resolve() / "lib")

    def test_workspace_list_rejects_duplicate_names_and_junk(self):
        for bad in (["/a/app", "/b/app"], [], [1], ["/a", ""]):
            with self.assertRaises(YamatoError, msg=repr(bad)):
                validate(base(workspace=bad), Path("/ship"))

    def test_time_limit_and_grace(self):
        t = validate(base(time_limit="20m", grace="90s"), Path("/ship"))
        self.assertEqual((t["time_limit"], t["grace"]), (1200, 90))

    def test_rejects(self):
        cases = [
            base(hub="nope"),
            base(extra=1),
            base(roles={"pm": {"shift": "sometimes"}}),
            base(roles={"pm": {"count": 0}}),
            base(roles={"pm": {"count": 2}}),          # hub must be a single seat
            base(roles={"pm": {"agent": "claude"}}),   # unknown role key
            base(name="Bad Name"),
            base(deny="Bash(x)"),
            base(board={"columns": [{"name": "x", "state": "weird"}]}),
            base(settings=["x"]),
            base(seat_stop={"require_handoff": "no"}),
            base(seat_stop={"other": True}),
        ]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(YamatoError):
                validate(data, Path("/ship"))

    def test_haiku_is_a_warning_not_an_error(self):
        t = validate(base(roles={"pm": {"model": "haiku", "shift": "persistent"}}), Path("/ship"))
        self.assertEqual(len(t["warnings"]), 1)
        self.assertIn("auto", t["warnings"][0])

    def test_owner_is_reserved(self):
        with self.assertRaises(YamatoError):
            validate(base(hub="owner", roles={"owner": {}}), Path("/ship"))

    def test_inject_settings(self):
        from yamato.team import inject_parts

        t = validate(base(inject={"parts": ["handoff", "inbox"], "limits": {"handoff": [10, 500], "mine_items": 3}},
                          roles={"pm": {"shift": "persistent", "inject": ["inbox"]}, "impl": {}}), Path("/ship"))
        self.assertEqual(inject_parts(t, "pm"), ["inbox"])
        self.assertEqual(inject_parts(t, "impl"), ["handoff", "inbox"])
        self.assertEqual(t["inject"]["limits"], {"handoff": (10, 500), "mine_items": 3})
        t = validate(base(inject={"limits": {"memory": [40, 1500], "knowledge": [60, 2000]}}), Path("/ship"))
        self.assertEqual(t["inject"]["limits"], {"memory": (40, 1500), "knowledge": (60, 2000)})
        for bad in ({"parts": ["nope"]}, {"limits": {"x": 1}}, {"limits": {"handoff": "big"}}, {"other": 1}):
            with self.subTest(bad=bad), self.assertRaises(YamatoError):
                validate(base(inject=bad), Path("/ship"))

    def test_inject_limits_shape_per_key(self):
        """T-073: [lines, chars] keys refuse a bare int (it used to crash `cap_text(*limit)` at run time);
        the single-int keys refuse a list. Both fail at validate with the key named."""
        for k in ("handoff", "log_tail", "last_report", "memory", "knowledge"):
            for bad in (500, [10], [10, 500, 1], [0, 500], [10, "x"], [True, 500], "big"):
                with self.subTest(key=k, bad=bad), self.assertRaisesRegex(YamatoError, f"inject.limits.{k} は"):
                    validate(base(inject={"limits": {k: bad}}), Path("/ship"))
            t = validate(base(inject={"limits": {k: [10, 500]}}), Path("/ship"))
            self.assertEqual(t["inject"]["limits"], {k: (10, 500)})
        for k in ("mine_items", "inbox_messages", "inbox_chars", "total_chars", "board_items", "fleet_items"):
            for bad in ([10, 500], 0, True):
                with self.subTest(key=k, bad=bad), self.assertRaisesRegex(YamatoError, f"inject.limits.{k} は"):
                    validate(base(inject={"limits": {k: bad}}), Path("/ship"))

    def test_inject_files_resolve_and_validate(self):
        """T-075 (D-089): `inject.files` names a path, `file:<名前>` refers to it."""
        ws = [{"name": "tmp", "path": "/tmp"}]
        t = validate(base(workspace="/tmp", inject={
            "files": {"charter": "charter.md", "home": "~/x.md", "abs": "/etc/hosts", "repo": "@tmp/docs/d.md"},
            "limits": {"files": {"default": [5, 50], "repo": [3, 30]}},
            "parts": ["handoff", "file:charter"]}), Path("/ship"))
        f = t["inject"]["files"]
        self.assertEqual(f["charter"], {"path": "charter.md", "abs": str(Path("/ship/charter.md"))})
        self.assertEqual(f["home"]["abs"], str(Path("~/x.md").expanduser()))
        self.assertEqual(f["abs"]["abs"], "/etc/hosts")
        self.assertEqual(f["repo"]["abs"], str(Path("/tmp").resolve() / "docs" / "d.md"))
        self.assertEqual(t["inject"]["limits"]["files"], {"default": (5, 50), "repo": (3, 30)})
        self.assertTrue(any("inject.files.charter" in w and "がない" in w for w in t["warnings"]))
        self.assertEqual(ws[0]["name"], "tmp")

    def test_inject_files_refusals(self):
        def inj(**kw):
            return base(inject={"files": {"a": "a.md"}, **kw})
        for bad in (
                {"parts": ["file:"]},                          # nothing after file:
                {"parts": ["file:nope"]},                      # not in inject.files
                {"files": {"default": "x.md"}},                # reserved
                {"files": {"a": 3}},                           # not a string
                {"files": {"a": ""}},                          # empty
                {"files": {"a": "@nowhere/x.md"}},             # no such workspace
                {"files": {"a": "@tmp"}},                      # no path after the workspace
                {"files": ["a.md"]},                           # not a mapping
                {"limits": {"files": {"b": [1, 2]}}},          # not in inject.files
                {"limits": {"files": {"a": 5}}},               # not a pair
                {"limits": {"files": {"default": [0, 5]}}},
                {"limits": {"files": {"a": [1, "x"]}}}):
            with self.subTest(bad=bad), self.assertRaises(YamatoError):
                validate(inj(**bad), Path("/ship"))
        with self.assertRaises(YamatoError):   # a role's list too
            validate(base(roles={"pm": {"shift": "persistent", "inject": ["file:zzz"]}}, inject={"files": {"a": "a.md"}}),
                     Path("/ship"))
        ok = validate(inj(parts=["file:a"]), Path("/ship"))
        self.assertEqual(ok["inject"]["parts"], ["file:a"])

    def test_old_charter_spellings_are_read_with_a_warning(self):
        """T-075: the part `charter`, the top-level `charter:` and `limits.charter` work for one version."""
        t = validate(base(charter="doc/c.md", inject={"parts": ["handoff", "charter"], "limits": {"charter": [7, 70]}},
                          roles={"pm": {"shift": "persistent", "inject": ["charter", "memory"]}, "impl": {}}),
                     Path("/ship"))
        self.assertEqual(t["inject"]["parts"], ["handoff", "file:charter"])
        self.assertEqual(t["roles"]["pm"]["inject"], ["file:charter", "memory"])
        self.assertEqual(t["inject"]["files"]["charter"]["path"], "doc/c.md")   # the top-level value fills it
        self.assertEqual(t["inject"]["limits"]["files"], {"charter": (7, 70)})
        for what in ("inject.parts", "roles.pm.inject", "inject.limits.charter", "トップの charter:"):
            self.assertTrue(any(what in w and "書き換えろ" in w for w in t["warnings"]), what)
        # no top-level value: charter.md; an explicit inject.files.charter wins over the old keys
        t = validate(base(inject={"parts": ["charter"]}), Path("/ship"))
        self.assertEqual(t["inject"]["files"]["charter"]["path"], "charter.md")
        t = validate(base(charter="old.md", inject={"files": {"charter": "new.md"}, "limits": {"charter": [7, 70], "files": {"charter": [9, 90]}}}),
                     Path("/ship"))
        self.assertEqual(t["inject"]["files"]["charter"]["path"], "new.md")
        self.assertEqual(t["inject"]["limits"]["files"]["charter"], (9, 90))
        with self.assertRaisesRegex(YamatoError, "inject.limits.files.charter は"):
            validate(base(inject={"limits": {"charter": 500}}), Path("/ship"))

    def test_inject_fleet_part_and_limit(self):
        """T-022: `fleet` (admiral の全艦の要約) is a selectable part with its own limit key."""
        from yamato.team import inject_parts

        t = validate(base(inject={"parts": ["fleet"], "limits": {"fleet_items": 5}},
                          roles={"pm": {"shift": "persistent"}, "impl": {}}), Path("/ship"))
        self.assertEqual(inject_parts(t, "pm"), ["fleet"])
        self.assertEqual(t["inject"]["limits"], {"fleet_items": 5})

    def test_zero_is_not_the_default(self):
        with self.assertRaises(YamatoError):
            validate(base(time_limit=0), Path("/ship"))
        self.assertEqual(validate(base(grace=0), Path("/ship"))["grace"], 0)

    def test_time_limit_none_is_the_admiral_only_exception(self):
        # D-013 (T-020): time_limit: none は上限を持たない艦を表す。既定・数値指定は変わらない
        self.assertIsNone(validate(base(time_limit="none"), Path("/ship"))["time_limit"])
        self.assertIsNone(validate(base(time_limit="NONE"), Path("/ship"))["time_limit"])
        self.assertEqual(validate(base(), Path("/ship"))["time_limit"], 3 * 3600)
        self.assertEqual(validate(base(time_limit="20m"), Path("/ship"))["time_limit"], 1200)

    def test_duplicate_seat_names(self):
        with self.assertRaises(YamatoError):
            expand_seats({"a-1": {"model": "sonnet", "shift": "per_task", "count": 1},
                          "a": {"model": "sonnet", "shift": "per_task", "count": 2}})


class DurationTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_duration("3h"), 10800)
        self.assertEqual(parse_duration("1h30m"), 5400)
        self.assertEqual(parse_duration("20m"), 1200)
        self.assertEqual(parse_duration("45s"), 45)
        self.assertEqual(parse_duration("15"), 900)
        self.assertEqual(parse_duration(15), 900)
        for bad in ("", "3x", "h", "3h junk"):
            with self.subTest(bad=bad), self.assertRaises(YamatoError):
                parse_duration(bad)


if __name__ == "__main__":
    unittest.main()

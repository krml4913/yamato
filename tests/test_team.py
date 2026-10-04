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
        with self.assertRaisesRegex(YamatoError, "memory.limits に一本化"):
            validate(base(inject={"limits": {"memory": [40, 1500]}}), Path("/ship"))
        for bad in ({"parts": ["nope"]}, {"limits": {"x": 1}}, {"limits": {"handoff": "big"}}, {"other": 1}):
            with self.subTest(bad=bad), self.assertRaises(YamatoError):
                validate(base(inject=bad), Path("/ship"))

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

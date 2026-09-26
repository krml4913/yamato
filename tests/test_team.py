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

    def test_relative_workspace_is_under_ship(self):
        t = validate(base(workspace="work"), Path("/ship"))
        self.assertTrue(t["workspace"].endswith("/ship/work"))

    def test_time_limit_and_grace(self):
        t = validate(base(time_limit="20m", grace="90s"), Path("/ship"))
        self.assertEqual((t["time_limit"], t["grace"]), (1200, 90))

    def test_rejects(self):
        cases = [
            base(hub="nope"),
            base(extra=1),
            base(roles={"pm": {"model": "haiku", "shift": "persistent"}}),
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

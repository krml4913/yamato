"""The sortie banner (banner.py): rendering per width, no color, non-TTY, the ways to turn it off, the cli calls."""
import io
import os
import re
import unittest
from contextlib import redirect_stdout
from unittest import mock

from tests.helpers import ShipTestCase
from yamato import admiral, banner, cli, seat

ANSI = re.compile(r"\033\[[0-9;]*m")
WIDTHS = list(range(1, 64)) + [79, 80, 81, 97, 98, 99, 100, 101, 119, 120, 121, 160, 240]
QUIET_ENV = ("CLAUDECODE", banner.NO_BANNER_ENV, "NO_COLOR")


class FakeTTY(io.StringIO):
    encoding = "utf-8"

    def isatty(self):
        return True


def render(columns, *, color=False, encoding="utf-8", kind="up", **kw):
    kw.setdefault("name", "yamato-dev")
    return banner.render(kind, columns=columns, color=color, encoding=encoding, **kw)


def plain(text):
    return ANSI.sub("", text)


def env(**extra):
    """Environment without the seat marker, the off switch and NO_COLOR (so the tests behave the same inside Claude Code)."""
    p = mock.patch.dict(os.environ, extra)
    p.start()
    for k in QUIET_ENV:
        if k not in extra:
            os.environ.pop(k, None)
    return p


class RenderTest(unittest.TestCase):
    def test_no_line_is_wider_than_the_terminal(self):
        for encoding in ("utf-8", "ascii"):
            for color in (False, True):
                for kind in ("up", "create"):
                    for columns in WIDTHS:
                        out = render(columns, color=color, encoding=encoding, kind=kind, template="dev",
                                     captain="lead", span="3h00m", until="09-26 21:40")
                        for line in plain(out).split("\n"):
                            self.assertLess(banner.display_width(line), max(columns, 1),
                                            f"{encoding} color={color} {kind} columns={columns}: {line!r}")

    def test_long_names_are_clipped_not_wrapped(self):
        for columns in (30, 45, 80, 120):
            out = render(columns, name="n" * 90, template="research", captain="c" * 60, span="3h00m",
                         until="09-26 21:40")
            for line in plain(out).split("\n"):
                self.assertLess(banner.display_width(line), columns)

    def test_size_follows_the_width(self):
        self.assertIn("╗", render(120))                     # large: sun, ship, big logo
        self.assertGreater(len(render(120).splitlines()), len(render(80).splitlines()))
        self.assertIn("╗", render(80))                      # medium
        self.assertGreater(len(render(80).splitlines()), len(render(45).splitlines()))
        small = render(45)
        self.assertNotIn("╗", small)                        # small: the letter logo
        self.assertIn("M M M", small)
        one = render(30)                                    # narrower than that: one line
        self.assertEqual(len(one.splitlines()), 1)
        self.assertIn("yamato-dev", one)
        self.assertEqual(render(8), "")                     # too narrow for anything: nothing

    def test_the_ship_info_is_under_the_art(self):
        for columns in (120, 80, 45):
            out = plain(render(columns, template="research", captain="editor", span="3h00m",
                               until="09-26 21:40"))
            for word in ("yamato-dev", "research", "editor", "3h00m", "09-26 21:40"):
                self.assertIn(word, out, f"columns={columns}")
        self.assertIn("出", plain(render(120)))
        self.assertIn("進", plain(render(120, kind="create")))
        # an item that is not known is not printed
        out = plain(render(120))
        self.assertNotIn("ひな形", out)
        self.assertNotIn("captain", out)
        self.assertNotIn("稼働", out)

    def test_create_shows_a_limit_and_no_clock(self):
        out = plain(render(120, kind="create", span="3h00m"))
        self.assertIn("稼働上限 3h00m", out)
        self.assertNotIn("まで", out)

    def test_no_color_means_no_escape_codes(self):
        for columns in (120, 80, 45, 30):
            self.assertNotIn("\033", render(columns, color=False))
        self.assertIn("\033[", render(120, color=True))
        self.assertTrue(render(120, color=True).count("\033[0m") > 10)

    def test_color_and_no_color_draw_the_same_shape(self):
        # color is only looks: same rows, same shape
        self.assertEqual(len(render(120, color=True).splitlines()), len(render(120).splitlines()))

    def test_the_ship_is_visible_without_color(self):
        out = render(120)
        self.assertIn("█", out)                             # the ship
        self.assertIn("░", out)                             # the sun behind it

    def test_ascii_terminal_gets_ascii_and_english(self):
        for columns in (120, 80, 45, 30):
            out = render(columns, encoding="ascii", template="dev", captain="lead", span="3h00m",
                         until="09-26 21:40")
            out.encode("ascii")                             # not one character the encoding cannot write
        out = render(120, encoding="ascii", template="dev")
        self.assertIn("#", out)
        self.assertIn("S O R T I E", out)

    def test_latin1_terminal_falls_back_too(self):
        render(120, encoding="latin-1").encode("latin-1")

    def test_unknown_encoding_is_taken_as_capable(self):
        self.assertIn("╗", render(120, encoding=None))

    def test_no_line_ends_with_a_space(self):
        for line in render(120).splitlines():
            self.assertEqual(line, line.rstrip(), repr(line))

    def test_display_width_counts_full_width_as_two(self):
        self.assertEqual(banner.display_width("abc"), 3)
        self.assertEqual(banner.display_width("出撃"), 4)
        self.assertEqual(banner.display_width("\033[1;97m出撃\033[0m"), 4)


class EnabledTest(unittest.TestCase):
    def test_only_a_terminal_gets_the_banner(self):
        self.assertTrue(banner.enabled(FakeTTY(), env={}))
        self.assertFalse(banner.enabled(io.StringIO(), env={}))       # a pipe, a redirect, a seat's Bash

    def test_quiet_and_the_environment_turn_it_off(self):
        self.assertFalse(banner.enabled(FakeTTY(), quiet=True, env={}))
        self.assertFalse(banner.enabled(FakeTTY(), env={banner.NO_BANNER_ENV: "1"}))
        self.assertTrue(banner.enabled(FakeTTY(), env={banner.NO_BANNER_ENV: "0"}))
        self.assertTrue(banner.enabled(FakeTTY(), env={banner.NO_BANNER_ENV: ""}))

    def test_a_seat_never_gets_it_even_on_a_terminal(self):
        self.assertFalse(banner.enabled(FakeTTY(), env={"CLAUDECODE": "1"}))

    def test_a_broken_stream_is_not_a_terminal(self):
        class Broken:
            def isatty(self):
                raise ValueError("closed")

        self.assertFalse(banner.enabled(Broken(), env={}))


class ShowTest(ShipTestCase):
    def setUp(self):
        super().setUp()
        p = env(COLUMNS="120")
        self.addCleanup(p.stop)

    def test_a_terminal_gets_the_banner_from_the_team_yaml(self):
        out = FakeTTY()
        banner.show("up", self.shipdir, span="2h", stream=out)
        text = plain(out.getvalue())
        self.assertIn("t1", text)                            # ship name
        self.assertIn("pm", text)                            # the captain seat (the dev template's hub)
        self.assertIn("2h00m", text)                         # --for wins over the team.yaml time_limit
        self.assertIn("\033[", out.getvalue())

    def test_without_for_the_team_time_limit_is_used(self):
        out = FakeTTY()
        banner.show("up", self.shipdir, stream=out)
        self.assertIn(f"{self.team()['time_limit'] // 3600}h00m", plain(out.getvalue()))

    def test_time_limit_none_shows_no_limit_instead_of_crashing(self):
        # T-020 / D-013: banner.show never raises even for the admiral's time_limit: none
        ty = self.shipdir / "team.yaml"
        ty.write_text(ty.read_text().replace("time_limit: 3h", "time_limit: none"))
        out = FakeTTY()
        banner.show("up", self.shipdir, stream=out)
        self.assertIn("上限なし", plain(out.getvalue()))

    def test_a_pipe_gets_nothing(self):
        out = io.StringIO()
        banner.show("up", self.shipdir, stream=out)
        self.assertEqual(out.getvalue(), "")

    def test_quiet_gets_nothing(self):
        out = FakeTTY()
        banner.show("up", self.shipdir, quiet=True, stream=out)
        self.assertEqual(out.getvalue(), "")

    def test_no_color_gets_the_banner_without_color(self):
        os.environ["NO_COLOR"] = "1"
        out = FakeTTY()
        banner.show("up", self.shipdir, stream=out)
        self.assertIn("╗", out.getvalue())
        self.assertNotIn("\033", out.getvalue())

    def test_the_width_of_the_terminal_is_used(self):
        os.environ["COLUMNS"] = "50"
        out = FakeTTY()
        banner.show("up", self.shipdir, stream=out)
        for line in plain(out.getvalue()).split("\n"):
            self.assertLess(banner.display_width(line), 50)

    def test_a_ship_that_cannot_be_read_never_raises(self):
        out = FakeTTY()
        banner.show("up", self.tmp / "no-such-ship", stream=out)
        self.assertEqual(out.getvalue(), "")
        banner.show("up", self.shipdir, span="nonsense", stream=out)   # a bad duration: `up` itself reports it
        self.assertEqual(out.getvalue(), "")

    def test_a_stream_that_fails_to_write_never_raises(self):
        class Full(FakeTTY):
            def write(self, s):
                raise OSError("no space left")

        banner.show("up", self.shipdir, stream=Full())


class CliTest(ShipTestCase):
    """The ``yamato up`` / ``ship create`` calls: shown on a terminal only, not from a seat, a pipe or with --quiet."""

    def setUp(self):
        super().setUp()
        for target in (mock.patch.object(seat, "spawn_watchdog"), mock.patch.object(seat, "_spawn_detached")):
            target.start()
            self.addCleanup(target.stop)
        p = env(COLUMNS="120")
        self.addCleanup(p.stop)

    def run_cli(self, stream, *argv):
        with redirect_stdout(stream):
            rc = cli.main(list(argv))
        return rc, stream.getvalue()

    def up(self, stream, *extra):
        return self.run_cli(stream, "up", str(self.shipdir), "--for", "1h", *extra)

    def stub_up(self):
        """``admiral.up`` replaced by one line of output: checks the wiring without starting a fake claude."""
        p = mock.patch.object(admiral, "up", side_effect=lambda *a: print("艦 t1 を起動") or 0)
        p.start()
        self.addCleanup(p.stop)

    def test_up_on_a_terminal_shows_the_banner_before_the_usual_lines(self):
        rc, out = self.up(FakeTTY())
        self.assertEqual(rc, 0)
        text = plain(out)
        self.assertIn("抜 錨", text)
        self.assertLess(text.index("抜 錨"), text.index("艦 t1 を起動"))    # banner first, then the usual output
        self.assertIn("captain 席 pm", text)

    def test_up_through_a_pipe_prints_exactly_what_it_always_did(self):
        rc, out = self.up(io.StringIO())
        self.assertEqual(rc, 0)
        self.assertNotIn("抜 錨", out)
        self.assertNotIn("╗", out)
        self.assertNotIn("\033", out)
        self.assertTrue(out.startswith("艦 t1 を起動"), out)

    def test_up_quiet_flag_and_the_environment(self):
        self.stub_up()
        rc, out = self.up(FakeTTY(), "--quiet")
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith("艦 t1 を起動"), out)
        os.environ[banner.NO_BANNER_ENV] = "1"
        rc, out = self.up(FakeTTY())
        self.assertTrue(out.startswith("艦 t1 を起動"), out)

    def test_up_from_a_seat_prints_no_banner_even_on_a_terminal(self):
        self.stub_up()
        os.environ["CLAUDECODE"] = "1"
        rc, out = self.up(FakeTTY())
        self.assertTrue(out.startswith("艦 t1 を起動"), out)

    def test_up_that_fails_still_fails_with_the_same_error(self):
        with mock.patch("sys.stderr", io.StringIO()) as err:
            rc, out = self.run_cli(FakeTTY(), "up", str(self.shipdir), "--for", "nonsense")
        self.assertEqual(rc, 1)
        self.assertIn("時間の書き方が不正", err.getvalue())
        self.assertNotIn("抜 錨", out)

    def create(self, stream, *extra, name="t2"):
        return self.run_cli(stream, "ship", "create", name, "--workspace", str(self.workspace), *extra)

    def test_ship_create_on_a_terminal_shows_the_banner_with_the_template(self):
        rc, out = self.create(FakeTTY())
        self.assertEqual(rc, 0)
        text = plain(out)
        self.assertIn("進 水", text)
        self.assertIn("ひな形 dev", text)
        self.assertLess(text.index("進 水"), text.index("艦 t2 を作った"))

    def test_ship_create_through_a_pipe_or_quiet_prints_no_banner(self):
        rc, out = self.create(io.StringIO())
        self.assertTrue(out.startswith("艦 t2 を作った"), out)
        rc, out = self.create(FakeTTY(), "--quiet", name="t3")
        self.assertTrue(out.startswith("艦 t3 を作った"), out)

    def test_ship_create_for_a_template_without_a_workspace(self):
        rc, out = self.run_cli(FakeTTY(), "ship", "create", "lab", "--template", "research")
        self.assertEqual(rc, 0)
        self.assertIn("ひな形 research", plain(out))
        self.assertIn("captain editor", plain(out))


if __name__ == "__main__":
    unittest.main()

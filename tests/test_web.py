"""web コンソールの土台 (T-105): routing と共通の枠。HTTP なしの dispatch で確かめる。"""
import unittest
from pathlib import Path

from yamato.web import frame, router

SHIPS = {"alpha": Path("/nonexistent/alpha"), "a<b": Path("/nonexistent/ab")}


def overview():
    return "<p>list</p>"


class RouterTest(unittest.TestCase):
    def test_root_is_the_overview(self):
        for p in ("/", "/index.html", "/?x=1"):
            self.assertEqual(router.dispatch(p, SHIPS, overview), (200, "<p>list</p>"))

    def test_ship_page_has_nav_and_escaped_name(self):
        status, page = router.dispatch("/ship/alpha", SHIPS, overview)
        self.assertEqual(status, 200)
        self.assertIn("<nav>", page)
        self.assertIn('href="/ship/alpha"', page)
        self.assertIn("一覧", page)
        status, page = router.dispatch("/ship/a%3Cb", SHIPS, overview)
        self.assertEqual(status, 200)
        self.assertNotIn("a<b", page)
        self.assertIn("a&lt;b", page)

    def test_unknown_or_odd_paths_are_404(self):
        for p in ("/ship/nope", "/ship/", "/ship", "/ship/alpha/x", "/ship/../alpha", "/ship/%2e%2e",
                  "/ship/alpha%2Fx", "/other/alpha", "//ship/alpha", "/x"):
            self.assertEqual(router.dispatch(p, SHIPS, overview)[0], 404, p)

    def test_frame_marks_current(self):
        self.assertIn('class="cur"', frame.nav(SHIPS, "/ship/alpha"))


if __name__ == "__main__":
    unittest.main()

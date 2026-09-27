"""hook の経路 (毎ターン呼ばれる) で banner / view を import しない (#13)。"""
import subprocess
import sys
import unittest
from pathlib import Path

SRC = str(Path(__file__).resolve().parent.parent / "src")
CODE = """
import sys
from yamato import cli
cli.main(["hook", "no-such-event", "/nonexistent", "impl"])
bad = [m for m in sys.modules if m in ("yamato.banner", "yamato.view") or m.startswith("yamato.view.")]
print("IMPORTED:" + ",".join(bad))
"""


class HookImportTest(unittest.TestCase):
    def test_hook_path_does_not_import_banner_or_view(self):
        r = subprocess.run([sys.executable, "-c", CODE], capture_output=True, text=True, env={"PYTHONPATH": SRC}, timeout=30)
        self.assertIn("IMPORTED:", r.stdout, r.stderr)
        self.assertEqual(r.stdout.strip().splitlines()[-1], "IMPORTED:", r.stdout)


if __name__ == "__main__":
    unittest.main()

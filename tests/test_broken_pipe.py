"""T-035: 出力を head などで途中で閉じても ./yamato がトレースバックを出さない。

入口 (./yamato) だけを直す task なので、実物の ./yamato を実際の OS パイプに
つなぎ、途中で読み手を閉じて本物の BrokenPipeError (SIGPIPE) を起こす。
CLI の中身 (yamato.cli.main / yamato.pretool.main) は大量に print するだけの
スタブに差し替え、艦フォルダなしで再現できるようにする。
"""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ENTRY = str(Path(__file__).resolve().parent.parent / "yamato")
SRC = str(Path(__file__).resolve().parent.parent / "src")


def _run_piped_through_head(stub_code: str, argv_tail: list[str]) -> str:
    """entry を stub_code で ``yamato.cli`` / ``yamato.pretool`` を差し替えた
    状態で実行し、標準出力を ``head -c 1`` に繋いで即座に読み手を閉じる。
    entry 自身の stderr を返す。"""
    driver = (
        "import runpy\n"
        "import sys\n"
        "import types\n"
        "\n"
        + textwrap.dedent(stub_code)
        + "\n"
        f"sys.argv = ['yamato'] + {argv_tail!r}\n"
        f"runpy.run_path({ENTRY!r}, run_name='__main__')\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(driver)
        driver_path = f.name
    try:
        env = dict(os.environ, PYTHONPATH=SRC)
        # シェルの `| head -c 1` は Windows に無い。読み手 (このテスト) が 1 byte 読んで閉じる
        proc = subprocess.Popen([sys.executable, driver_path], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env)
        try:
            proc.stdout.read(1)
            proc.stdout.close()
            err = proc.stderr.read()
            proc.wait(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            proc.stderr.close()
        return (err or b"").decode("utf-8", errors="replace")
    finally:
        Path(driver_path).unlink(missing_ok=True)


class BrokenPipeIsSilentTest(unittest.TestCase):
    def test_cli_path_is_silent_on_broken_pipe(self):
        stub = textwrap.dedent(
            """
            stub = types.ModuleType("yamato.cli")

            def main():
                for _ in range(200000):
                    print("x" * 200)
                return 0

            stub.main = main
            sys.modules["yamato.cli"] = stub
            """
        )
        stderr = _run_piped_through_head(stub, ["status", "dummy-ship"])
        self.assertNotIn("Traceback", stderr, stderr)
        self.assertNotIn("BrokenPipeError", stderr, stderr)
        self.assertNotIn("Exception ignored", stderr, stderr)

    def test_hook_pre_tool_use_path_is_silent_on_broken_pipe(self):
        stub = textwrap.dedent(
            """
            stub = types.ModuleType("yamato.pretool")

            def main(ship, seat):
                for _ in range(200000):
                    print("x" * 200)
                return 0

            stub.main = main
            sys.modules["yamato.pretool"] = stub
            """
        )
        stderr = _run_piped_through_head(
            stub, ["hook", "pre-tool-use", "/nonexistent-ship", "impl"]
        )
        self.assertNotIn("Traceback", stderr, stderr)
        self.assertNotIn("BrokenPipeError", stderr, stderr)
        self.assertNotIn("Exception ignored", stderr, stderr)


if __name__ == "__main__":
    unittest.main()

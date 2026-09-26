"""Run the unit tests one module per process: ``python3 -m tests.parallel [-j N] [-v]``.

The same tests as ``python3 -m unittest discover`` (which stays the reference); this only
spreads the modules over the CPUs. Output of each module is printed as a block, in order.
"""
from __future__ import annotations

import argparse
import io
import multiprocessing
import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _modules() -> list[str]:
    return sorted(f"tests.{p.stem}" for p in (ROOT / "tests").glob("test_*.py"))


def _run(args: tuple[str, int]) -> tuple[str, int, int, int, bool, str, float]:
    name, verbosity = args
    buf = io.StringIO()
    t0 = time.time()
    suite = unittest.defaultTestLoader.loadTestsFromName(name)
    res = unittest.TextTestRunner(stream=buf, verbosity=verbosity).run(suite)
    return (name, res.testsRun, len(res.failures), len(res.errors), res.wasSuccessful(), buf.getvalue(),
            time.time() - t0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m tests.parallel")
    ap.add_argument("-j", type=int, default=os.cpu_count() or 2, help="プロセス数 (既定: CPU 数)")
    ap.add_argument("-v", action="store_true", help="テストごとに名前を出す")
    a = ap.parse_args(argv)
    os.chdir(ROOT)
    t0 = time.time()
    with multiprocessing.get_context("spawn").Pool(a.j) as pool:
        results = pool.map(_run, [(m, 2 if a.v else 1) for m in _modules()], chunksize=1)
    ran = failed = 0
    ok = True
    for name, n, fails, errors, success, out, took in results:
        ran += n
        failed += fails + errors
        ok = ok and success
        if a.v or not success:
            print(f"=== {name} ({took:.1f}s)\n{out}", file=sys.stderr)
    slow = sorted(results, key=lambda r: -r[6])[:3]
    print(f"Ran {ran} tests in {time.time() - t0:.1f}s ({a.j} processes; slowest modules: "
          + ", ".join(f"{r[0]} {r[6]:.1f}s" for r in slow) + ")", file=sys.stderr)
    print("OK" if ok else f"FAILED ({failed})", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

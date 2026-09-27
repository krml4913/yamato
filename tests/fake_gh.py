#!/usr/bin/env python3
"""A stand-in for the ``gh`` CLI used by the worktree / pr tests.

State lives in $FAKE_GH_STATE (JSON): ``prs`` maps a PR number to
{state, mergeable, head}, ``checks_rc`` / ``checks_out`` answer ``pr checks``,
``merge_sleep`` slows ``pr merge`` down. Every call is appended to ``calls``.

Run as a script (``$YAMATO_GH``), or in-process through ``main()`` (the pr tests
route ``yamato.pr``'s gh call here to skip a python start-up per call).
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from yamato.util import lock_file, unlock_file   # noqa: E402 (path set up above)


def main(argv, *, cwd=None, env=None, out=None, err=None) -> int:
    env = os.environ if env is None else env
    cwd = cwd or os.getcwd()
    out = out or sys.stdout
    err = err or sys.stderr
    path = env["FAKE_GH_STATE"]

    def locked(fn):
        with open(path + ".lock", "a") as lk:
            lock_file(lk)
            try:
                try:
                    with open(path) as f:
                        st = json.load(f)
                except (FileNotFoundError, ValueError):
                    st = {}
                st.setdefault("prs", {})
                st.setdefault("calls", [])
                result = fn(st)
                with open(path, "w") as f:
                    json.dump(st, f)
                return result
            finally:
                unlock_file(lk)

    def record(st):
        st["calls"].append({"argv": argv, "cwd": cwd, "t": time.time()})
        return dict(st)

    st = locked(record)
    cmd = argv[:2]
    if cmd == ["pr", "create"]:
        head = argv[argv.index("--head") + 1]

        def create(st):
            n = str(max([int(k) for k in st["prs"]] or [0]) + 1)
            st["prs"][n] = {"state": "OPEN", "mergeable": "MERGEABLE", "head": head}
            return n
        n = locked(create)
        print(f"https://github.com/o/r/pull/{n}", file=out)
    elif cmd == ["pr", "checks"]:
        print(st.get("checks_out", "all checks passed"), file=out)
        return st.get("checks_rc", 0)
    elif cmd == ["pr", "view"]:
        pr = st["prs"].get(argv[2])
        if pr is None:
            print("no pull requests found", file=err)
            return 1
        print(json.dumps({"state": pr["state"], "mergeable": pr["mergeable"]}), file=out)
    elif cmd == ["pr", "merge"]:
        time.sleep(st.get("merge_sleep", 0))

        def merge(st):
            st["prs"][argv[2]]["state"] = "MERGED"
            st["calls"].append({"argv": ["merged", argv[2]], "t": time.time()})
        locked(merge)
        print(f"Merged pull request #{argv[2]}", file=out)
    else:
        print(f"fake gh: unknown {argv}", file=err)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

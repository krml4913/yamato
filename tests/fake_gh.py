#!/usr/bin/env python3
"""A stand-in for the ``gh`` CLI used by the worktree / pr tests.

State lives in $FAKE_GH_STATE (JSON): ``prs`` maps a PR number to
{state, mergeable, head}, ``checks_rc`` / ``checks_out`` answer ``pr checks``,
``merge_sleep`` slows ``pr merge`` down. Every call is appended to ``calls``.
"""
import fcntl
import json
import os
import sys
import time

path = os.environ["FAKE_GH_STATE"]
argv = sys.argv[1:]


def locked(fn):
    with open(path + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            st = json.load(open(path))
        except (FileNotFoundError, ValueError):
            st = {}
        st.setdefault("prs", {})
        st.setdefault("calls", [])
        result = fn(st)
        json.dump(st, open(path, "w"))
        return result


def record(st):
    st["calls"].append({"argv": argv, "cwd": os.getcwd(), "t": time.time()})
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
    print(f"https://github.com/o/r/pull/{n}")
elif cmd == ["pr", "checks"]:
    print(st.get("checks_out", "all checks passed"))
    sys.exit(st.get("checks_rc", 0))
elif cmd == ["pr", "view"]:
    pr = st["prs"].get(argv[2])
    if pr is None:
        print("no pull requests found", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"state": pr["state"], "mergeable": pr["mergeable"]}))
elif cmd == ["pr", "merge"]:
    time.sleep(st.get("merge_sleep", 0))

    def merge(st):
        st["prs"][argv[2]]["state"] = "MERGED"
        st["calls"].append({"argv": ["merged", argv[2]], "t": time.time()})
    locked(merge)
    print(f"Merged pull request #{argv[2]}")
else:
    print(f"fake gh: unknown {argv}", file=sys.stderr)
    sys.exit(2)

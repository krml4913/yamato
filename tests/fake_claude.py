#!/usr/bin/env python3
"""A stand-in for the ``claude`` CLI used by the lifecycle tests.

State lives in $FAKE_CLAUDE_STATE (JSON). Live sessions get the pid in
$FAKE_ALIVE_PID (the test runner itself), stopped ones pid None. Every call is
appended to state["calls"] with its argv and a few env vars. ``-p`` (headless)
is played by fake_claude_lib/print_mode.py.

Run as a script (``$YAMATO_CLAUDE``), or in-process through ``main()``:
tests.helpers routes ``yamato.claude._run`` here so a test does not pay a
python start-up per ``claude agents`` (``-p`` always runs as a real process).
"""
import fcntl
import json
import os
import sys
import time
import uuid


def main(argv, *, cwd=None, env=None, out=None, err=None) -> int:
    env = os.environ if env is None else env
    cwd = cwd or os.getcwd()
    out = out or sys.stdout
    err = err or sys.stderr
    path = env["FAKE_CLAUDE_STATE"]
    if "--bg" in argv and "--resume" not in argv:
        # slow_launch: a launch that takes a while, outside the lock below, so other
        # callers (``agents``) see the seat not yet alive meanwhile (the review B1 race)
        try:
            with open(path) as f:
                time.sleep(float(json.load(f).get("mode", {}).get("slow_launch", 0)))
        except (FileNotFoundError, ValueError):
            pass
    # serialise whole invocations so parallel callers in a test do not lose writes
    with open(path + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(path) as f:
                st = json.load(f)
        except (FileNotFoundError, ValueError):
            st = {"sessions": [], "calls": []}
        st["calls"].append({"argv": argv, "cwd": cwd,
                            "GH_TOKEN": env.get("GH_TOKEN"),
                            "CLAUDE_CODE_SESSION_ID": env.get("CLAUDE_CODE_SESSION_ID"),
                            "CLAUDE_CODE_CHILD_SESSION": env.get("CLAUDE_CODE_CHILD_SESSION"),
                            "stdin_is_devnull": os.path.samestat(os.fstat(0), os.stat(os.devnull))})
        if "-p" in argv:
            _save(path, st)
            fcntl.flock(lock, fcntl.LOCK_UN)   # a -p runs for a while: let other calls in
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_claude_lib"))
            import print_mode
            return print_mode.run(argv, st.get("mode", {}))
        return _handle(argv, st, path, cwd, int(env["FAKE_ALIVE_PID"]), out, err)


def _save(path, st):
    with open(path, "w") as f:
        json.dump(st, f)


def _handle(argv, st, path, cwd, alive_pid, out, err) -> int:
    mode = st.get("mode", {})

    def find(short):
        for s in st["sessions"]:
            if s["id"] == short or s["sessionId"].startswith(short):
                return s
        return None

    def new_session(name):
        sid = str(uuid.uuid4())
        s = {"pid": alive_pid, "id": sid[:8], "sessionId": sid, "name": name, "kind": "background",
             "status": "idle", "state": "working", "cwd": cwd,
             "startedAt": int(time.time() * 1000)}
        if mode.get("waitingFor"):
            s["waitingFor"] = mode["waitingFor"]
        s.update(mode.get("session") or {})   # e.g. a worker that died before init (verify-p0-c Q5)
        st["sessions"].append(s)
        return s

    if argv[:1] == ["agents"]:
        # resume_lag: the listing shows the pre-resume record for that many more calls
        shown = []
        for s in st["sessions"]:
            if s.get("_lag"):
                s["_lag"] -= 1
                shown.append(s["_before"])
                if not s["_lag"]:
                    del s["_lag"], s["_before"]
            else:
                shown.append(s)
        _save(path, st)
        print(json.dumps(shown), file=out)
    elif argv[:1] == ["stop"]:
        s = find(argv[1])
        if s:
            s["pid"] = None
            s["state"] = "done"
        _save(path, st)
        print(f"stopped {argv[1]}", file=out)
    elif argv[:1] == ["rm"]:
        st["sessions"] = [s for s in st["sessions"] if s["id"] != argv[1]]
        _save(path, st)
        print(f"removed {argv[1]}", file=out)
    elif "--resume" in argv:
        sid = argv[argv.index("--resume") + 1]
        s = next((x for x in st["sessions"] if x["sessionId"] == sid), None)
        if s is None or s["pid"] is not None or mode.get("copy"):
            c = new_session("copy")
            _save(path, st)
            print(f"note: session {sid[:8]} is already running in the background, so this started a copy as {c['id']}.",
                  file=out)
            print(f"backgrounded · {c['id']} · copy", file=out)
        else:
            if mode.get("resume_lag"):
                s["_before"], s["_lag"] = dict(s), mode["resume_lag"]
            s["pid"] = alive_pid
            s["startedAt"] = int(time.time() * 1000)   # updated on resume (verify-p0-a Q3)
            s.update(mode.get("session") or {})
            _save(path, st)
            print(f"note: woke session {sid[:8]} with its saved options (--name, --agent, --settings).", file=out)
            print(f"backgrounded · \x1b[36m{s['id']}\x1b[39m · {s['name']}", file=out)
    elif "--bg" in argv:
        if mode.get("untrusted"):
            print("Workspace not trusted. Run `claude` in x once and accept the trust prompt, then retry.", file=out)
            return 1
        s = new_session(argv[argv.index("--name") + 1])
        _save(path, st)
        if mode.get("noid"):
            print("session started", file=out)
            return 0
        # real claude colours the id when run from inside a Claude session's Bash (E2E run 1)
        print(f"backgrounded · \x1b[36m{s['id']}\x1b[39m · {s['name']}", file=out)
        print(f"warning: no agent named '{argv[argv.index('--agent') + 1]}' — spawning with default template",
              file=err)
    else:
        print("fake claude: unsupported " + " ".join(argv), file=err)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""A stand-in for the ``claude`` CLI used by the lifecycle tests.

State lives in $FAKE_CLAUDE_STATE (JSON). Live sessions get the pid in
$FAKE_ALIVE_PID (the test runner itself), stopped ones pid None. Every call is
appended to state["calls"] with its argv and a few env vars.
"""
import json
import os
import sys
import time
import uuid

path = os.environ["FAKE_CLAUDE_STATE"]
try:
    st = json.load(open(path))
except (FileNotFoundError, ValueError):
    st = {"sessions": [], "calls": []}
argv = sys.argv[1:]
st["calls"].append({"argv": argv, "cwd": os.getcwd(),
                    "GH_TOKEN": os.environ.get("GH_TOKEN"),
                    "CLAUDE_CODE_SESSION_ID": os.environ.get("CLAUDE_CODE_SESSION_ID")})
alive_pid = int(os.environ["FAKE_ALIVE_PID"])
mode = st.get("mode", {})


def save():
    json.dump(st, open(path, "w"))


def find(short):
    for s in st["sessions"]:
        if s["id"] == short or s["sessionId"].startswith(short):
            return s
    return None


def new_session(name):
    sid = str(uuid.uuid4())
    s = {"pid": alive_pid, "id": sid[:8], "sessionId": sid, "name": name, "kind": "background",
         "status": "idle", "state": "working", "cwd": os.getcwd(),
         "startedAt": int(time.time() * 1000)}
    if mode.get("waitingFor"):
        s["waitingFor"] = mode["waitingFor"]
    st["sessions"].append(s)
    return s


if argv[:1] == ["agents"]:
    print(json.dumps(st["sessions"]))
elif argv[:1] == ["stop"]:
    s = find(argv[1])
    if s:
        s["pid"] = None
        s["state"] = "done"
    save()
    print(f"stopped {argv[1]}")
elif argv[:1] == ["rm"]:
    st["sessions"] = [s for s in st["sessions"] if s["id"] != argv[1]]
    save()
    print(f"removed {argv[1]}")
elif "--resume" in argv:
    sid = argv[argv.index("--resume") + 1]
    s = next((x for x in st["sessions"] if x["sessionId"] == sid), None)
    if s is None or s["pid"] is not None or mode.get("copy"):
        c = new_session("copy")
        save()
        print(f"note: session {sid[:8]} is already running in the background, so this started a copy as {c['id']}.")
        print(f"backgrounded · {c['id']} · copy")
    else:
        s["pid"] = alive_pid
        save()
        print(f"note: woke session {sid[:8]} with its saved options (--name, --agent, --settings).")
        print(f"backgrounded · \x1b[36m{s['id']}\x1b[39m · {s['name']}")
elif "--bg" in argv:
    if mode.get("untrusted"):
        print("Workspace not trusted. Run `claude` in x once and accept the trust prompt, then retry.")
        sys.exit(1)
    s = new_session(argv[argv.index("--name") + 1])
    save()
    if mode.get("noid"):
        print("session started")
        sys.exit(0)
    # real claude colours the id when run from inside a Claude session's Bash (E2E run 1)
    print(f"backgrounded · \x1b[36m{s['id']}\x1b[39m · {s['name']}")
    print(f"warning: no agent named '{argv[argv.index('--agent') + 1]}' — spawning with default template",
          file=sys.stderr)
else:
    print("fake claude: unsupported " + " ".join(argv), file=sys.stderr)
    sys.exit(2)

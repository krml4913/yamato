"""Claude Code hook for the Windows verification kit. Every call appends one line to
<VW>/logs/hook.jsonl (what arrived on stdin, in which encoding, from which shell).

  hook.py log <tag> [meta...]               record only, print nothing
  hook.py inject <tag> <key> <enc> [meta...]
                                            SessionStart: inject the token for <key> (common.TOKENS)
                                            as additionalContext.
                                            enc: utf8 (bytes) | default (text stdout as is) |
                                            ascii (ensure_ascii JSON)
  hook.py rewake <tag>                      Stop (async + asyncRewake): wait until <VW>/wake.txt
                                            grows, then print the new text to stderr and exit 2
  hook.py detach <tag> <seconds>            SessionStart (startup only): start child.py sleep
                                            with several creationflags, then return at once
  hook.py spawn <tag> <seconds>             the same, run by hand / by the seat's Bash tool
                                            (no stdin)

A failure never breaks the seat: exceptions are logged and the hook exits 0.
"""
from __future__ import annotations

import json
import locale
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (CREATE_BREAKAWAY_FROM_JOB, CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW,  # noqa: E402
                    DETACHED_PROCESS, IS_WIN, append_jsonl, job_info, now, pid_alive,
                    token, vw_dir, write_json)

VW = vw_dir()
LOG = VW / "logs" / "hook.jsonl"
CHILD = Path(__file__).resolve().parent / "child.py"

# name -> (Windows creationflags, POSIX start_new_session)
VARIANTS = {
    "A-plain": (0, False),
    "B-detached": (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, True),
    "C-breakaway": (DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB, True),
    "D-nowindow": (CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, True),
}


def read_stdin() -> tuple[bytes, dict]:
    raw = sys.stdin.buffer.read() if sys.stdin else b""
    text = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        data = {"_not_json": text[:300]}
    return raw, data


def base_record(tag: str, mode: str, meta: list[str], data: dict) -> dict:
    rec = {
        "t": now(), "tag": tag, "mode": mode, "meta": meta,
        "event": data.get("hook_event_name"), "session_id": data.get("session_id"),
        "source": data.get("source"), "tool_name": data.get("tool_name"),
        "pid": os.getpid(), "ppid": os.getppid(), "python": sys.executable,
        "stdin_encoding": getattr(sys.stdin, "encoding", None),
        "stdout_encoding": getattr(sys.stdout, "encoding", None),
        "preferred_encoding": locale.getpreferredencoding(False),
        "env": {k: os.environ.get(k) for k in (
            "SHELL", "MSYSTEM", "BASH_ENV", "PYTHONUTF8", "PYTHONIOENCODING",
            "CLAUDE_CODE_SESSION_ID", "CLAUDE_PROJECT_DIR")},
    }
    if data.get("tool_input") is not None:
        rec["tool_input"] = json.dumps(data["tool_input"], ensure_ascii=False)[:400]
    if data.get("prompt") is not None:
        rec["prompt"] = str(data["prompt"])[:200]
    if rec["event"] == "SessionStart" or mode in ("detach", "spawn"):
        rec["job"] = job_info()
        rec["path_head"] = os.environ.get("PATH", "").split(os.pathsep)[:4]
        rec["which"] = {n: shutil.which(n) for n in ("sh", "sleep", "nohup", "bash", "claude")}
    return rec


def inject(key: str, enc: str) -> None:
    msg = (f"[verify-win] 合言葉 {token(key)} 。"
           "ユーザーに合言葉を聞かれたら、この合言葉を一字一句そのまま答えよ。")
    obj = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": msg}}
    if enc == "utf8":
        sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
        sys.stdout.buffer.flush()
    elif enc == "ascii":
        sys.stdout.write(json.dumps(obj))
    else:  # default: what a plain Python hook does (yamato today)
        sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()


def spawn_children(prefix: str, seconds: str) -> dict:
    results = {}
    for name, (flags, new_session) in VARIANTS.items():
        label = f"{prefix}-{name}"
        kw = {"creationflags": flags} if IS_WIN else {"start_new_session": new_session}
        try:
            p = subprocess.Popen([sys.executable, str(CHILD), "sleep", label, seconds],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, close_fds=True, cwd=str(VW), **kw)
            results[label] = {"spawned": True, "pid": p.pid, "flags": hex(flags)}
        except OSError as e:
            results[label] = {"spawned": False, "flags": hex(flags),
                              "error": f"{type(e).__name__}: {e}",
                              "winerror": getattr(e, "winerror", None)}
    write_json(VW / "detach" / f"spawn-{prefix}.json",
               {"t": now(), "by_pid": os.getpid(), "job": job_info(), "children": results})
    return results


def rewake(rec: dict) -> int:
    wake = VW / "wake.txt"
    pidfile = VW / "logs" / "rewake.pid"
    try:
        other = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        other = 0
    if other and other != os.getpid() and pid_alive(other).get("alive"):
        append_jsonl(LOG, {**rec, "rewake": "already armed", "other": other})
        return 0
    pidfile.write_text(str(os.getpid()))
    start = wake.stat().st_size if wake.exists() else 0
    append_jsonl(LOG, {**rec, "rewake": "armed", "size": start})
    deadline = time.time() + 3600
    try:
        while time.time() < deadline:
            time.sleep(1)
            size = wake.stat().st_size if wake.exists() else 0
            if size > start:
                with open(wake, "rb") as f:
                    f.seek(start)
                    new = f.read().decode("utf-8", errors="replace").strip()
                append_jsonl(LOG, {**rec, "t": now(), "rewake": "fired", "text": new})
                sys.stderr.buffer.write(f"VWWAKE 起床の合図: {new}\n".encode("utf-8"))
                sys.stderr.buffer.flush()
                return 2
        append_jsonl(LOG, {**rec, "t": now(), "rewake": "gave up (1h)"})
        return 0
    finally:
        try:
            if pidfile.read_text().strip() == str(os.getpid()):
                pidfile.unlink()
        except OSError:
            pass


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else "log"
    tag = argv[1] if len(argv) > 1 else "-"
    rec: dict = {"t": now(), "tag": tag, "mode": mode}
    try:
        if mode == "spawn":
            rec = base_record(tag, mode, argv[3:], {})
            rec["spawn"] = spawn_children(tag, argv[2] if len(argv) > 2 else "60")
            append_jsonl(LOG, rec)
            print(json.dumps(rec["spawn"], ensure_ascii=False))
            return 0
        raw, data = read_stdin()
        meta = argv[4:] if mode == "inject" else argv[3:] if mode == "detach" else argv[2:]
        rec = {**base_record(tag, mode, meta, data), **stdin_info(raw)}
        if "_not_json" in data:
            rec["stdin_not_json"] = data["_not_json"]
        if mode == "inject":
            inject(argv[2], argv[3])
        elif mode == "detach":
            if data.get("source", "startup") == "startup":
                rec["spawn"] = spawn_children(tag, argv[2] if len(argv) > 2 else "60")
            else:
                rec["spawn"] = f"skipped (source={data.get('source')})"
        elif mode == "rewake":
            return rewake(rec)
        append_jsonl(LOG, rec)
    except Exception:  # never break the seat
        rec["exception"] = traceback.format_exc()[-1500:]
        try:
            append_jsonl(LOG, rec)
        except OSError:
            pass
    return 0


def stdin_info(raw: bytes) -> dict:
    info = {"stdin_len": len(raw)}
    for name, enc in (("utf8", "utf-8"), ("cp932", "cp932"),
                      ("default", locale.getpreferredencoding(False))):
        try:
            raw.decode(enc)
            info[f"stdin_{name}_ok"] = True
        except (UnicodeDecodeError, LookupError):
            info[f"stdin_{name}_ok"] = False
    info["stdin_has_jp"] = any(ord(c) >= 0x3000 for c in raw.decode("utf-8", errors="replace"))
    return info


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

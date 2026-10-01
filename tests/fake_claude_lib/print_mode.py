"""The fake ``claude -p --output-format stream-json --verbose`` (docs/verify/verify-p1-d.md V1-V5).

Plays the lines run-headless reads: SessionStart hook_started / hook_response
(it really runs the SessionStart hooks of ``--settings``, so the inbox cursor
moves as with the real one), init, one API response split over two assistant
lines with the same message.id (as in the transcript, V9), a rate_limit_event
and the final ``result``. SIGTERM exits 143 with no result line (V4).

Modes (state["mode"]):
  p_sleep: seconds to sleep before the result (time-limit tests)
  p_no_hook: skip the SessionStart hook (as a ``--bare`` run would)
  p_api_error: api_error_status to report (is_error with subtype success, V5)
  p_no_result: exit 1 without a result line
  p_seat_stop: write handoff.md and run ``yamato seat-stop`` like a seat that finishes properly
  p_inbox_once: on the first -p only, append this text to the seat's inbox (mail during the shift)
  p_result: the final response text (the memory curate shift's proposal)
"""
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

RESULT_TEXT = "SEAT-OUTPUT: 席が最後に書いた応答"


def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _arg(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv else None


def _run_session_start(settings, sid):
    try:
        cfg = json.load(open(settings))
    except (OSError, ValueError):
        return
    env = dict(os.environ, CLAUDE_CODE_SESSION_ID=sid)
    for group in cfg.get("hooks", {}).get("SessionStart", []):
        for h in group.get("hooks", []):
            # exec form (`args` present): command + args, no shell; otherwise a shell string
            argv, shell = ([h["command"], *h["args"]], False) if "args" in h else (h["command"], True)
            subprocess.run(argv, shell=shell, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           input=json.dumps({"session_id": sid, "source": "startup"}))


def _transcript(sid, cwd):
    base = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / cwd.replace("/", "-")
    base.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    usage = {"input_tokens": 7, "output_tokens": 11, "cache_creation_input_tokens": 100, "cache_read_input_tokens": 200}
    with open(base / f"{sid}.jsonl", "a") as f:
        for block in ("thinking", "text"):   # one response, two lines, same id
            f.write(json.dumps({"type": "assistant", "timestamp": ts, "message": {
                "id": "msg_fake_1", "model": "claude-sonnet-fake", "usage": usage,
                "content": [{"type": block}]}}) + "\n")


def run(argv, mode):
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(143))
    # a SIGTERM that comes early waits until the transcript is written: the time-limit
    # tests use short limits and count usage from the transcript (POSIX only: Windows has
    # no pthread_sigmask, and terminate() there is not a SIGTERM)
    _mask = getattr(signal, "pthread_sigmask", None)
    if _mask:
        _mask(signal.SIG_BLOCK, {signal.SIGTERM})
    sid = _arg(argv, "--session-id") or str(uuid.uuid4())
    name = _arg(argv, "--name") or ""
    ship = _arg(argv, "--add-dir")
    seat = name.split(".", 1)[1] if "." in name else name
    _transcript(sid, os.getcwd())   # before the hooks: where a signal cannot be held off (Windows) it is the first thing written
    if not mode.get("p_no_hook"):
        _emit({"type": "system", "subtype": "hook_started", "hook_event": "SessionStart", "session_id": sid})
        _run_session_start(_arg(argv, "--settings"), sid)
        _emit({"type": "system", "subtype": "hook_response", "hook_event": "SessionStart",
               "hook_name": "SessionStart:startup", "exit_code": 0, "outcome": "success", "session_id": sid})
    _emit({"type": "system", "subtype": "init", "session_id": sid, "model": _arg(argv, "--model")})
    if _mask:
        _mask(signal.SIG_UNBLOCK, {signal.SIGTERM})
    for _ in range(2):
        _emit({"type": "assistant", "message": {"id": "msg_fake_1", "content": []}, "session_id": sid})
    _emit({"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed", "resetsAt": 1790403600, "rateLimitType": "five_hour",
        "unifiedWindows": {"five_hour": {"utilization": 0.27, "resetsAt": 1790403600}}}, "session_id": sid})
    text = mode.get("p_inbox_once")
    marker = Path(os.environ["FAKE_CLAUDE_STATE"] + ".inbox_once")
    if text and ship and not marker.exists():
        marker.write_text("1")
        box = Path(ship) / "seats" / seat / "inbox.jsonl"
        n = sum(1 for line in box.read_text(encoding="utf-8").splitlines() if line.strip()) + 1 if box.exists() else 1
        with open(box, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps({"n": n, "ts": time.time(), "from": "pm", "text": text}, ensure_ascii=False) + "\n")
    if mode.get("p_sleep"):
        time.sleep(float(mode["p_sleep"]))
    if mode.get("p_seat_stop") and ship:
        (Path(ship) / "seats" / seat / "handoff.md").write_text("# 引き継ぎ\n- 次: なし\n", encoding="utf-8")
        yamato = Path(__file__).resolve().parents[2] / "yamato"
        subprocess.run([sys.executable, str(yamato), "seat-stop", ship, seat],
                       env=dict(os.environ, CLAUDE_CODE_SESSION_ID=sid), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if mode.get("p_no_result"):
        return 1
    status = mode.get("p_api_error")
    _emit({"type": "result", "subtype": "success", "is_error": bool(status), "api_error_status": status,
           "terminal_reason": "api_error" if status else "completed",
           "result": "API Error: Rate limit reached" if status else (mode.get("p_result") or RESULT_TEXT),
           "num_turns": 3, "duration_ms": 1234, "total_cost_usd": 0.0 if status else 0.0421,
           "usage": {"input_tokens": 10, "output_tokens": 20, "cache_creation_input_tokens": 300,
                     "cache_read_input_tokens": 4000},
           "modelUsage": {"claude-sonnet-fake": {"inputTokens": 10}}, "session_id": sid})
    return 1 if status else 0

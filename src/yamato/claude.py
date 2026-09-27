"""Every call into Claude Code lives here (design §14: absorb CLI changes in one place).

The launch / resume / stop recipes follow docs/verify-p0-a.md and
docs/verify-p0-b.md exactly:
- liveness is ``pid != null`` (``state`` labels what the seat asks of a human, verify-p0-c Q5)
- ``claude --bg`` exits 0 even when the worker dies before init: a launch or resume
  counts only once the listing shows a pid and no ``state: failed`` (``started_failure``)
- ``--resume`` always gets the full sessionId, only after the old pid is gone,
  and ``started a copy`` in its output means failure (the copy is removed)
- ``--add-dir`` eats trailing values, so the prompt goes after ``--``
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from .util import YamatoError

# The caller's session identity must not leak into a new seat (technical, not
# policy). What else to drop (GH_TOKEN, ...) is team.yaml `env_unset`.
CALLER_ENV = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_ENTRYPOINT")
# ``-p`` runs in the caller's environment (a bg seat is started by the daemon), so
# the rest of a calling session's markers must go too: CLAUDE_CODE_CHILD_SESSION
# turns transcript saving off, which the timeout usage count reads (e2e-headless).
PRINT_CALLER_ENV = ("CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN",
                    "CLAUDE_CODE_BRIDGE_SESSION_ID", "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_EXECPATH",
                    "CLAUDE_PID")

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_BG_RE = re.compile(r"backgrounded\s+·\s+([0-9a-f]{8})\b")
_COPY_RE = re.compile(r"started a copy(?: of that conversation)? as ([0-9a-f]{8})")


def claude_bin() -> str:
    return os.environ.get("YAMATO_CLAUDE", "claude")


def _run(args: list[str], *, cwd: str | None = None, env: dict | None = None, timeout: int = 120):
    try:
        return subprocess.run([claude_bin(), *args], cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=timeout)
    except FileNotFoundError:
        raise YamatoError(f"claude コマンドが見つかりません ({claude_bin()})") from None
    except subprocess.TimeoutExpired:
        raise YamatoError(f"claude {' '.join(args[:2])} がタイムアウトしました") from None


def _output(cp) -> str:
    """stdout + stderr without ANSI colours (claude colours the id when it thinks it has a tty)."""
    return _ANSI_RE.sub("", (cp.stdout or "") + (cp.stderr or ""))


def seat_env(unset: list[str] | tuple = ()) -> dict:
    env = dict(os.environ)
    for k in (*CALLER_ENV, *unset):
        env.pop(k, None)
    return env


# --- observation -----------------------------------------------------------

def agents() -> list[dict]:
    cp = _run(["agents", "--json", "--all"], timeout=60)
    if cp.returncode != 0:
        raise YamatoError(f"claude agents --json が失敗しました: {cp.stderr.strip() or cp.stdout.strip()}")
    try:
        data = json.loads(cp.stdout or "[]")
    except ValueError:
        raise YamatoError("claude agents --json の出力を読めません") from None
    return data if isinstance(data, list) else []


def by_session(listing: list[dict]) -> dict:
    return {a.get("sessionId"): a for a in listing if a.get("sessionId")}


def is_alive(rec: dict | None) -> bool:
    """Liveness is ``pid != null`` (verify-p0-a Q3), double-checked with kill -0."""
    if not rec or rec.get("pid") is None:
        return False
    try:
        os.kill(int(rec["pid"]), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# after a launch / resume: how long the listing is watched (verify-p0-c Q5). A session with a
# pid and a ``status`` got through init; one with a pid only counts after the settle time,
# so a worker that dies before init can show it first
LAUNCH_SETTLE = 2.0
LAUNCH_CHECK_TIMEOUT = 20.0
LAUNCH_CHECK_POLL = 0.5


# what a resume changes in the listing: a new pid, and startedAt is updated (verify-p0-a Q3)
_LAUNCH_MARKS = ("pid", "state", "status", "startedAt", "detail")


def launch_marks(rec: dict | None) -> tuple | None:
    """The listing fields to compare before and after a resume (``started_failure``'s ``before``)."""
    return None if rec is None else tuple(rec.get(k) for k in _LAUNCH_MARKS)


def started_failure(session_id: str, since: float, before: tuple | None = None) -> str | None:
    """Why the session launched or resumed at ``since`` did not come up, or None when it did
    (a pid, no ``state: failed``, and a ``status`` or ``LAUNCH_SETTLE`` seconds on). ``failed``
    with no pid is a worker that died before init: ``claude --bg`` said ``backgrounded`` and exit 0.

    ``before``: ``launch_marks`` of the session just before a resume. Until the listing
    differs from it, the record is still the previous shift's (e.g. its ``state: failed``
    with no pid) and says nothing about this resume."""
    while True:
        rec = find(session_id)
        now = time.time()
        stale = before is not None and launch_marks(rec) == before
        alive, failed = is_alive(rec), (rec or {}).get("state") == "failed"
        detail = f": {rec['detail']}" if (rec or {}).get("detail") else ""
        if not stale and alive and not failed and (rec.get("status") or now >= since + LAUNCH_SETTLE):
            return None
        if not stale and now >= since + LAUNCH_SETTLE and failed and not alive:
            return f"worker が起動しなかった (state: failed, pid なし){detail}"
        if now >= since + LAUNCH_CHECK_TIMEOUT:
            if rec is None:
                return "claude agents にセッションが見つからない"
            if stale:
                return f"resume のあと claude agents の記録が変わらない (state: {rec.get('state') or '-'})"
            if failed:
                return f"セッションがエラーで止まっている (state: failed{detail})。モデル名などを確かめる"
            return f"pid が付かない (state: {rec.get('state') or '-'})"
        time.sleep(LAUNCH_CHECK_POLL)


def find(session_id: str) -> dict | None:
    return by_session(agents()).get(session_id)


def wait_gone(session_id: str, timeout: float = 30.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if not is_alive(find(session_id)):
            return True
        time.sleep(1)
    return False


# --- trust -----------------------------------------------------------------

def _claude_json() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(base) / ".claude.json" if base else Path.home() / ".claude.json"


def git_root(path: Path) -> Path | None:
    try:
        cp = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                            capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return Path(cp.stdout.strip()).resolve() if cp.returncode == 0 and cp.stdout.strip() else None


def main_repo_root(path: Path) -> Path | None:
    """The main working tree of the repo ``path`` is in (for a linked worktree, not the
    worktree itself); None outside git."""
    try:
        cp = subprocess.run(["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    common = Path(cp.stdout.strip()) if cp.returncode == 0 and cp.stdout.strip() else None
    if common is not None and common.name == ".git":
        return common.parent.resolve()
    return git_root(path)


def is_trusted(workspace: Path) -> bool | None:
    """Trust is per git root; a non-git dir is covered by a trusted ancestor (verify-p0-b Q4).
    A linked worktree inherits the trust of its main repo (verify-p1-d V6).

    ``~/.claude.json`` is Claude Code's internal file, not an interface (design §4): None
    means "cannot tell" (missing, unreadable, another shape). Then ``launch()`` decides
    from Claude's own ``Workspace not trusted``; only a clear False refuses beforehand."""
    try:
        data = json.loads(_claude_json().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, dict):
        return None

    def ok(p: Path) -> bool:
        entry = projects.get(str(p))
        return isinstance(entry, dict) and bool(entry.get("hasTrustDialogAccepted"))

    ws = Path(workspace).resolve()
    root = git_root(ws)
    if root is not None:
        return ok(root) or ok(main_repo_root(ws) or root)
    return any(ok(p) for p in (ws, *ws.parents))


def check_trust(workspace: Path) -> str | None:
    """Before a launch: refuse a clear "not trusted"; for "cannot tell", a warning to print
    (the launch goes on and ``launch()`` sees Claude's own answer)."""
    trusted = is_trusted(workspace)
    if trusted is False:
        raise YamatoError(untrusted_message(workspace))
    return None if trusted else unknown_trust_message(workspace)


# --- lifecycle -------------------------------------------------------------

def launch(*, cwd: str, name: str, role: str, agents_json: str, model: str,
           settings: str, add_dir: str, prompt: str, env_unset=(), remote_control: bool = False) -> tuple[str, str]:
    """Start a new background session; returns (short id, full sessionId).

    ``remote_control`` adds ``--remote-control``, which wins over the settings'
    ``remoteControlAtStartup: false`` (verify-p1-d V10)."""
    args = [
        "--bg", "--name", name,
        "--agent", role, "--agents", agents_json,
        "--model", model,
        "--setting-sources", "project,local",
        "--settings", settings,
        *(["--remote-control"] if remote_control else []),
        "--add-dir", add_dir,
        "--", prompt,
    ]
    started = time.time()
    cp = _run(args, cwd=cwd, env=seat_env(env_unset))
    out = _output(cp)
    if "Workspace not trusted" in out:
        raise YamatoError(untrusted_message(Path(cwd)))
    m = _BG_RE.search(out)
    if cp.returncode == 0 and not m:
        # the output format changed but a session may be running: adopt it rather than leak it
        fresh = [a for a in agents() if a.get("name") == name
                 and (a.get("startedAt") or 0) / 1000 >= started - 5 and a.get("sessionId")]
        if len(fresh) == 1:
            return fresh[0]["sessionId"][:8], fresh[0]["sessionId"]
    if cp.returncode != 0 or not m:
        raise YamatoError(f"席の起動に失敗しました (exit {cp.returncode}): {out.strip()}")
    short = m.group(1)
    for _ in range(20):
        for a in agents():
            if a.get("id") == short or str(a.get("sessionId", "")).startswith(short):
                return short, a["sessionId"]
        time.sleep(0.5)
    raise YamatoError(f"起動した席 {short} が claude agents に見つかりません")


def resume(session_id: str, prompt: str, env_unset=(), cwd: str | None = None) -> str:
    """Wake a stopped session under its own id. Raises if Claude started a copy."""
    cp = _run(["--resume", session_id, "--bg", "--", prompt], cwd=cwd, env=seat_env(env_unset))
    out = _output(cp)
    copy = _COPY_RE.search(out)
    if copy:
        discard(copy.group(1))
        raise YamatoError(f"resume がコピー {copy.group(1)} を作ったので失敗扱いにしました (コピーは stop + rm 済み): {out.strip()}")
    if cp.returncode != 0 or "backgrounded" not in out:
        raise YamatoError(f"resume に失敗しました (exit {cp.returncode}): {out.strip()}")
    return out


def headless_argv(*, session_id: str, name: str, role: str, agents_json: str, model: str,
                  settings: str, add_dir: str, prompt: str, max_budget_usd=None) -> list[str]:
    """One headless shift = one ``claude -p`` (design-p1 §4.2, verify-p1-d V1-V4).

    No ``--bare``: it skips OAuth, so a subscription run ends ``Not logged in``;
    the caller checks the SessionStart hook_response instead, which also catches
    a future ``-p`` that turns bare by default. stdin must be /dev/null (else a
    3 s wait); ``--add-dir`` eats trailing values, so the prompt goes after ``--``.
    """
    args = [
        claude_bin(), "-p", "--session-id", session_id, "--name", name,
        "--output-format", "stream-json", "--verbose",
        "--agent", role, "--agents", agents_json,
        "--model", model,
        "--setting-sources", "project,local",
        "--settings", settings,
        "--permission-prompts", "none",
    ]
    if max_budget_usd is not None:
        args += ["--max-budget-usd", str(max_budget_usd)]
    return args + ["--add-dir", add_dir, "--", prompt]


def stop(short_id: str) -> bool:
    return _run(["stop", short_id], timeout=60).returncode == 0


def rm(short_id: str) -> bool:
    return _run(["rm", short_id], timeout=60).returncode == 0


def discard(short_id: str) -> None:
    """Stop, wait for the pid to vanish, then rm (rm right after stop misbehaves)."""
    stop(short_id)
    end = time.time() + 30
    while time.time() < end:
        rec = next((a for a in agents() if a.get("id") == short_id
                    or str(a.get("sessionId", "")).startswith(short_id)), None)
        if not is_alive(rec):
            break
        time.sleep(1)
    rm(short_id)


def untrusted_message(workspace: Path) -> str:
    root = main_repo_root(Path(workspace)) or Path(workspace)
    return (f"workspace が Claude Code に trust されていません: {root}\n"
            f"  一度 `cd {root} && claude` を実行して trust のダイアログで承認してから、もう一度 up してください。"
            f"\n  (yamato は trust を自動では承認しません)")


def unknown_trust_message(workspace: Path) -> str:
    return (f"workspace の trust を {_claude_json()} から確かめられなかった: "
            f"{main_repo_root(Path(workspace)) or Path(workspace)}\n"
            "  そのまま起動し、trust されていなければ claude の出力で止める")


# --- transcripts -----------------------------------------------------------

def transcript_paths(session_id: str) -> list[Path]:
    """Where Claude Code keeps the session's transcripts (an internal layout, design §4):
    an empty list when they cannot be found or read, and the callers treat that as unknown."""
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    try:
        mains = list(base.glob(f"*/{session_id}.jsonl"))
        subs = []
        for m in mains:
            subs += list((m.parent / session_id).glob("subagents/*.jsonl"))
    except OSError:
        return []
    return mains + subs

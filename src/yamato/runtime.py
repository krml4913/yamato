"""Generate ``.runtime/``: team.json, agents.json and one settings file per seat.

Everything a seat needs is passed by launch flags; nothing is written into the
workspace repo or ``~/.claude`` (design §4.1). Hook commands carry the ship
path and the seat name as arguments, never as environment variables.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

from .team import DEFAULT_MODE, profile_of
from .util import YAMATO_BIN, YamatoError, atomic_write, write_json

# Policy (the deny list, worktree isolation, ...) lives in team.yaml, seeded by
# the template (mechanism-not-policy). The code only adds what the mechanism
# itself needs: accept cross-session messages, auto mode, the hooks, and the
# allow rule for the delayed self-stop (verify-p0-a Q3). A role's `trust:`
# profile (design-p1 §7.2) replaces the mode and adds its allow / deny / tools
# as written; yamato does not know what any profile is for.
HOOK_TIMEOUT_WAIT = 86400  # the async deadline watcher sleeps until the deadline


def runtime_dir(shipdir: Path) -> Path:
    return Path(shipdir) / ".runtime"


def settings_path(shipdir: Path, seat: str) -> Path:
    return runtime_dir(shipdir) / f"settings-{seat}.json"


def agents_path(shipdir: Path) -> Path:
    return runtime_dir(shipdir) / "agents.json"


def is_windows() -> bool:
    """One place to ask (tests replace it): the rules below differ on Windows only."""
    return os.name == "nt"


def posix_path(path) -> str:
    """The form a seat types on Windows: ``C:/Users/x`` (as_posix). Git Bash, the MSYS tools and
    native exes all take it; ``C:\\Users\\x`` does not survive bash (W4, work/windows-research.md
    §2.4). Unchanged elsewhere."""
    s = str(path)
    return s.replace("\\", "/") if is_windows() else s


def rule_path(path) -> str:
    """The path inside a ``Edit(//c/Users/x/...)`` rule: ``/c/Users/x`` (lowercase drive, ``/``
    separators), so the rule starts with ``//``. Only this form was denied on a real Windows
    machine (W0 [9]); ``/C:\\...`` and ``/C:/...`` were let through. Unchanged elsewhere."""
    s = posix_path(path)
    if is_windows():
        m = re.match(r"^([A-Za-z]):(/.*|)$", s)
        if m:
            s = f"/{m.group(1).lower()}{m.group(2)}"
    return s


def ship_arg(path) -> str:
    """A folder path as one shell word, in the form a seat types: as_posix, shell-quoted when it
    has a space (a Windows home with a space). The prompt text and the ``Bash(...)`` rules
    both use this, so an allow rule matches what the prompt tells the seat to type."""
    return shlex.quote(posix_path(path))


_PATH_SHIP = re.compile(r"(?<=/)\{\{ship\}\}")           # `/{{ship}}/...` in a path rule
_WORD_SHIP = re.compile(r"\{\{ship\}\}(?!/)")            # a whole argument: `... {{ship}} ...`


def _fill_ship(text: str, shipdir: Path, *, paths: bool) -> str:
    """``{{ship}}`` as an argument becomes ``ship_arg``; ``{{ship}}/sub/path`` is a file path
    the seat hands to a tool (Write, Read), so it stays unquoted. With ``paths`` (permission
    rules) ``/{{ship}}`` is the ``//c/...`` form instead."""
    if paths:
        text = _PATH_SHIP.sub(lambda _m: rule_path(shipdir), text)
    text = _WORD_SHIP.sub(lambda _m: ship_arg(shipdir), text)
    return text.replace("{{ship}}", posix_path(shipdir))


def yamato_invocation() -> str:
    """The two-word form of ``{{yamato}}``: the interpreter that is running this process,
    plus the script, each shell-quoted (as_posix on Windows). A seat's Bash tool can always
    run this (unlike the bare script path), because it does not depend on the shebang being
    interpretable -- Windows Git Bash has no ``python3`` by default (W1, work/windows-research.md
    §2.1). Used for both the role prompt text and the permission rules, so an allow rule
    always matches what the prompt tells the seat to type."""
    return f"{shlex.quote(posix_path(sys.executable))} {shlex.quote(posix_path(YAMATO_BIN))}"


def render_prompt(text: str, shipdir: Path, team: dict) -> str:
    return _fill_ship(text.replace("{{yamato}}", yamato_invocation()), shipdir, paths=False) \
        .replace("{{ship_name}}", team["name"]).replace("{{hub}}", team["hub"])


def render_rule(rule: str, shipdir: Path, seat: str) -> str:
    """A permission rule of team.yaml: ``{{ship}}`` / ``{{yamato}}`` / ``{{seat}}`` are filled in.

    Absolute paths are written as ``/{{ship}}/...`` so the rule starts with ``//``
    (verify-p1-d V1); on Windows that is ``//c/Users/...`` (W0 [9]). Inside ``Bash(...)`` the
    ship is the same string the prompt tells the seat to type (``ship_arg``). No ``$VAR`` is
    expanded here or by Claude Code. ``{{yamato}}`` is the same two-word form ``render_prompt``
    uses (see ``yamato_invocation``)."""
    return _fill_ship(rule.replace("{{yamato}}", yamato_invocation()), shipdir, paths=True) \
        .replace("{{seat}}", seat)


def build_agents(shipdir: Path, team: dict) -> dict:
    out = {}
    for role, spec in team["roles"].items():
        path = Path(shipdir) / "roles" / f"{role}.md"
        if not path.is_file():
            raise YamatoError(f"役割プロンプトがありません: {path}")
        out[role] = {
            "description": spec["description"],
            "prompt": render_prompt(path.read_text(encoding="utf-8"), shipdir, team),
            "model": spec["model"],
        }
        profile = profile_of(team, role)
        if profile and profile.get("tools") is not None:
            # the tools a role may use at all (verify-p1-d V7: honoured by bg and -p alike)
            out[role]["tools"] = list(profile["tools"])
    return out


def _merge(base: dict, extra: dict) -> dict:
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif isinstance(v, list) and isinstance(out.get(k), list):
            out[k] = out[k] + [x for x in v if x not in out[k]]
        else:
            out[k] = v
    return out


def build_settings(shipdir: Path, team: dict, seat: str) -> dict:
    ship = str(Path(shipdir))
    y = yamato_invocation()
    # `{{ship}}` in a rule is the ship folder (so a template can protect its records), `{{seat}}` this seat
    deny = [render_rule(r, shipdir, seat) for r in team.get("deny") or []]
    allow = [f"Bash({y} seat-stop:*)"]
    profile = profile_of(team, team["seats"][seat]["role"]) if seat in team["seats"] else None
    if profile:
        allow += [r for r in (render_rule(x, shipdir, seat) for x in profile["allow"]) if r not in allow]
        deny += [r for r in (render_rule(x, shipdir, seat) for x in profile["deny"]) if r not in deny]

    def hook(*args: str) -> dict:
        # exec form (command + args), not a shell string: Claude Code spawns shell-form
        # hooks via Git Bash on Windows, and the script's shebang cannot run there without
        # a `python3` on PATH (W1, work/windows-research.md §2.1). `sys.executable` is a
        # real executable on every OS, which exec form requires on Windows.
        return {"type": "command", "command": sys.executable, "args": [str(YAMATO_BIN), "hook", *args, ship, seat]}

    mech = {
        "crossSessionInbound": "accept",
        "permissions": {
            "defaultMode": (profile or {}).get("mode") or DEFAULT_MODE,
            "allow": allow,
            "deny": deny,
        },
        "hooks": {
            # two hooks: Claude Code takes 10,000 characters from each (verify-p0-c Q1)
            "SessionStart": [{"hooks": [hook("session-start"), hook("session-start-knowledge")]}],
            "UserPromptSubmit": [{"hooks": [hook("user-prompt-submit")]}],
            # the time limit inside a long turn (§0 B4); `yamato` answers it before loading the CLI
            "PreToolUse": [{"hooks": [hook("pre-tool-use")]}],
            "PreCompact": [{"hooks": [hook("pre-compact")]}],
            "Stop": [{"hooks": [
                hook("stop"),
                {**hook("wait-deadline"), "async": True, "asyncRewake": True, "timeout": HOOK_TIMEOUT_WAIT},
            ]}],
            "PermissionRequest": [{"hooks": [hook("deny-dialog")]}],
            "PermissionDenied": [{"hooks": [hook("log-denied")]}],
        },
    }
    if team.get("env_unset"):
        # a bg seat runs in the daemon's environment, so the caller's `env -u` misses it
        # (e2e-p1 D). The settings' `env` reaches the seat's tools: blank the names there
        mech["env"] = {k: "" for k in team["env_unset"]}
    if is_windows():
        # D-051 A: a Windows seat would reach for the PowerShell tool, which the `Bash(...)` rules
        # do not cover. Only Windows has the tool by default, so macOS settings stay as they were.
        # The settings' `env` is what a bg seat's tools see (the daemon's environment is not ours)
        mech["env"] = {**mech.get("env", {}), "CLAUDE_CODE_USE_POWERSHELL_TOOL": "0"}
    if needs_no_isolation(shipdir, team):
        mech["worktree"] = {"bgIsolation": "none"}
    # team.yaml `settings:` goes underneath; yamato's own keys win
    return _merge(team.get("settings") or {}, mech)


def cwd_settings_path(shipdir: Path, seat: str) -> Path:
    return runtime_dir(shipdir) / f"settings-{seat}.cwd.json"


def write_cwd_settings(shipdir: Path, team: dict, seat: str) -> Path:
    """The seat's settings with ``bgIsolation: none``, for a shift started in a given
    directory (``send --cwd``, design-p1 §8.2 の 2): the worktree is already there."""
    data = build_settings(shipdir, team, seat)
    data["worktree"] = {**(data.get("worktree") or {}), "bgIsolation": "none"}
    path = cwd_settings_path(shipdir, seat)
    write_json(path, data)
    return path


def needs_no_isolation(shipdir: Path, team: dict) -> bool:
    """No repo, or the ship folder inside the workspace repo: an automatic worktree
    would strand the records (verify-p0-b Q4 b/c). A fact, not a policy (audit D9)."""
    from .claude import git_root

    root = git_root(Path(team["workspace"]))
    if root is None:
        return True
    try:
        Path(shipdir).resolve().relative_to(root)
        return True
    except ValueError:
        return False

def generate(shipdir: Path, team: dict) -> None:
    rd = runtime_dir(shipdir)
    rd.mkdir(parents=True, exist_ok=True)
    write_json(rd / "team.json", team)
    atomic_write(agents_path(shipdir), json.dumps(build_agents(shipdir, team), ensure_ascii=False))
    for seat in team["seats"]:
        write_json(settings_path(shipdir, seat), build_settings(shipdir, team, seat))

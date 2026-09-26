"""Generate ``.runtime/``: team.json, agents.json and one settings file per seat.

Everything a seat needs is passed by launch flags; nothing is written into the
workspace repo or ``~/.claude`` (design §4.1). Hook commands carry the ship
path and the seat name as arguments, never as environment variables.
"""
from __future__ import annotations

import json
import shlex
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


def _cmd(*parts: str) -> str:
    return " ".join(shlex.quote(str(p)) for p in parts)


def render_prompt(text: str, shipdir: Path, team: dict) -> str:
    return (text.replace("{{yamato}}", str(YAMATO_BIN))
                .replace("{{ship}}", str(shipdir))
                .replace("{{ship_name}}", team["name"])
                .replace("{{hub}}", team["hub"]))


def render_rule(rule: str, shipdir: Path, seat: str) -> str:
    """A permission rule of team.yaml: ``{{ship}}`` / ``{{yamato}}`` / ``{{seat}}`` are filled in.

    Absolute paths are written as ``/{{ship}}/...`` so the rule starts with ``//``
    (verify-p1-d V1). No ``$VAR`` is expanded here or by Claude Code."""
    return (rule.replace("{{ship}}", str(Path(shipdir)))
                .replace("{{yamato}}", str(YAMATO_BIN))
                .replace("{{seat}}", seat))


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
    y = str(YAMATO_BIN)
    # `{{ship}}` in a rule is the ship folder (so a template can protect its records), `{{seat}}` this seat
    deny = [render_rule(r, shipdir, seat) for r in team.get("deny") or []]
    allow = [f"Bash({y} seat-stop:*)"]
    profile = profile_of(team, team["seats"][seat]["role"]) if seat in team["seats"] else None
    if profile:
        allow += [r for r in (render_rule(x, shipdir, seat) for x in profile["allow"]) if r not in allow]
        deny += [r for r in (render_rule(x, shipdir, seat) for x in profile["deny"]) if r not in deny]

    def hook(*args: str) -> dict:
        return {"type": "command", "command": _cmd(y, "hook", *args, ship, seat)}

    mech = {
        "crossSessionInbound": "accept",
        "permissions": {
            "defaultMode": (profile or {}).get("mode") or DEFAULT_MODE,
            "allow": allow,
            "deny": deny,
        },
        "hooks": {
            "SessionStart": [{"hooks": [hook("session-start")]}],
            "UserPromptSubmit": [{"hooks": [hook("user-prompt-submit")]}],
            "Stop": [{"hooks": [
                hook("stop"),
                {**hook("wait-deadline"), "async": True, "asyncRewake": True, "timeout": HOOK_TIMEOUT_WAIT},
            ]}],
            "PermissionRequest": [{"hooks": [hook("deny-dialog")]}],
            "PermissionDenied": [{"hooks": [hook("log-denied")]}],
        },
    }
    if needs_no_isolation(shipdir, team):
        mech["worktree"] = {"bgIsolation": "none"}
    # team.yaml `settings:` goes underneath; yamato's own keys win
    return _merge(team.get("settings") or {}, mech)


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

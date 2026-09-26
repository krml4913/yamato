"""Generate ``.runtime/``: team.json, agents.json and one settings file per seat.

Everything a seat needs is passed by launch flags; nothing is written into the
workspace repo or ``~/.claude`` (design §4.1). Hook commands carry the ship
path and the seat name as arguments, never as environment variables.
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path

from .util import YAMATO_BIN, YamatoError, atomic_write, write_json

# verify-p0-b "settings.json のひな形" plus yamato's own lifecycle commands:
# a seat stops itself only through `yamato seat-stop` (verify-p0-a Q3).
BASE_DENY = [
    "Bash(git push --force*)", "Bash(git push -f*)", "Bash(git reset --hard*)",
    "Bash(gh pr merge*)",
    "Bash(claude stop*)", "Bash(claude rm*)", "Bash(claude --resume*)",
    "Edit(.claude/**)", "Write(.claude/**)",
]
# ship files that only yamato commands may change
PROTECTED = ["team.yaml", "roster.json", "usage.jsonl", ".runtime/**",
             "seats/*/inbox.jsonl", "seats/*/inbox.cursor"]
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
    return out


def build_settings(shipdir: Path, team: dict, seat: str) -> dict:
    ship = str(Path(shipdir))
    y = str(YAMATO_BIN)
    protected = [f"{tool}(/{ship}/{p})" for p in PROTECTED for tool in ("Edit", "Write")]

    def hook(*args: str) -> dict:
        return {"type": "command", "command": _cmd(y, "hook", *args, ship, seat)}

    return {
        "crossSessionInbound": "accept",
        "permissions": {
            "defaultMode": "auto",
            # the only allow rule: the delayed self-stop (verify-p0-a Q3)
            "allow": [f"Bash({y} seat-stop:*)"],
            "deny": BASE_DENY + protected + list(team.get("deny") or []),
        },
        # workspace and ship folder may be the same repo; keep records in place (§0 I3)
        "worktree": {"bgIsolation": "none"},
        "hooks": {
            "SessionStart": [{"hooks": [hook("session-start")]}],
            "Stop": [{"hooks": [
                hook("stop"),
                {**hook("wait-deadline"), "async": True, "asyncRewake": True, "timeout": HOOK_TIMEOUT_WAIT},
            ]}],
            "PermissionRequest": [{"hooks": [hook("deny-dialog")]}],
            "PermissionDenied": [{"hooks": [hook("log-denied")]}],
        },
    }


def generate(shipdir: Path, team: dict) -> None:
    rd = runtime_dir(shipdir)
    rd.mkdir(parents=True, exist_ok=True)
    write_json(rd / "team.json", team)
    atomic_write(agents_path(shipdir), json.dumps(build_agents(shipdir, team), ensure_ascii=False))
    for seat in team["seats"]:
        write_json(settings_path(shipdir, seat), build_settings(shipdir, team, seat))

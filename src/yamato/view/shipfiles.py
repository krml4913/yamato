"""Everything the view layer reads from a ship folder lives here.

The formats belong to P0 (not merged yet). What is assumed is written next to
each reader; once P0 is on main, replace these bodies with calls into
``yamato.util.resolve_ship`` / ``yamato.roster`` / ``yamato.team`` and keep the
signatures.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


class ViewError(Exception):
    """A problem worth showing to the person looking at the pane."""


def yamato_home() -> Path:
    return Path(os.environ.get("YAMATO_HOME") or "~/yamato").expanduser()


def ship_dir(ref: str) -> Path:
    """A ship by name (``$YAMATO_HOME/<name>``, default ``~/yamato/<name>``) or by path.

    P0 also keeps a registry of ships created with ``--path``; that lookup comes
    with the switch to ``yamato.util.resolve_ship``.
    """
    if "/" in ref or ref.startswith(("~", ".")):
        return Path(ref).expanduser().resolve()
    return yamato_home() / ref


# --- roster.json -------------------------------------------------------------
# Assumed format (P0 ``yamato.roster``):
#   {
#     "seats": {
#       "<seat>": {"sessionId": "<full uuid>", "shortId": "<8 hex>",
#                  "name": "<ship>.<seat>", "state": "on_shift|stopping|off", ...}
#     },
#     "shifts": [...]
#   }
# Only ``seats.<seat>.sessionId`` is used: the seat's current shift. Whether it
# is alive is decided by ``claude agents``, not by ``state``.

def roster_session_id(shipdir: Path, seat: str) -> str | None:
    """The full sessionId the roster names as ``seat``'s current shift, or None."""
    try:
        data = json.loads((Path(shipdir) / "roster.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return None  # mid-write or broken: treat as "no shift" and look again next poll
    rec = (data.get("seats") or {}).get(seat) if isinstance(data, dict) else None
    sid = rec.get("sessionId") if isinstance(rec, dict) else None
    return sid if isinstance(sid, str) and sid else None


# --- team.yaml ---------------------------------------------------------------
# Assumed format (P0 ``yamato.team``):
#   roles:
#     pm:   { model: opus, shift: persistent }
#     impl:
#       model: sonnet
#       count: 2            # -> seats impl-1, impl-2 (count 1 or absent -> seat "impl")
# ``yamato up`` also writes the validated team to ``.runtime/team.json`` with
# ``seats`` already expanded; that is preferred when present. Otherwise the
# ``roles:`` block is read with the small parser below (stdlib has no YAML).

def team_seats(shipdir: Path) -> list[str]:
    """The ship's seats in team.yaml order."""
    shipdir = Path(shipdir)
    try:
        team = json.loads((shipdir / ".runtime" / "team.json").read_text(encoding="utf-8"))
        seats = team.get("seats")
        if isinstance(seats, dict) and seats:
            return list(seats)
    except (OSError, ValueError, AttributeError):
        pass
    try:
        text = (shipdir / "team.yaml").read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ViewError(f"艦が見つかりません: {shipdir}/team.yaml がない") from None
    seats = _seats_from_yaml(text)
    if not seats:
        raise ViewError(f"{shipdir}/team.yaml に roles がありません")
    return seats


_KEY_RE = re.compile(r"^(\s*)([A-Za-z0-9_-]+)\s*:(.*)$")
_COUNT_RE = re.compile(r"(?:^|[{,\s])count\s*:\s*(\d+)")


def _strip_comment(line: str) -> str:
    return re.sub(r"\s+#.*$", "", line) if not line.lstrip().startswith("#") else ""


def _seats_from_yaml(text: str) -> list[str]:
    """Read ``roles:`` -> role names and their ``count``; expand to seat names."""
    roles: list[list] = []  # [role, count]
    in_roles = False
    role_indent = None
    for raw in text.splitlines():
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        m = _KEY_RE.match(line)
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_roles = bool(m and m.group(2) == "roles")
            role_indent = None
            continue
        if not in_roles:
            continue
        if role_indent is None:
            role_indent = indent
        if indent == role_indent and m:
            c = _COUNT_RE.search(m.group(3))
            roles.append([m.group(2), int(c.group(1)) if c else 1])
        elif indent > role_indent and roles:
            c = _COUNT_RE.search(line)
            if c:
                roles[-1][1] = int(c.group(1))
    seats: list[str] = []
    for role, count in roles:
        seats += [role] if count == 1 else [f"{role}-{i}" for i in range(1, count + 1)]
    return seats

"""team.yaml: load, validate and expand roles into seats.

The validated result is written to ``.runtime/team.json`` by ``yamato up`` so
the per-turn hooks never have to import YAML.
"""
from __future__ import annotations

from pathlib import Path

from .util import YamatoError, check_name, parse_duration, read_json

SHIFTS = ("per_task", "persistent")
STATES = ("open", "active", "blocked", "done")
TOP_KEYS = {"name", "hub", "workspace", "charter", "roles", "time_limit", "grace", "deny", "board",
            "settings", "seat_stop"}
SEAT_STOP_DEFAULTS = {"require_handoff": True, "require_delivery": True}
ROLE_KEYS = {"model", "shift", "count", "description"}
# auto mode is unavailable on Haiku (verify-p0-b §総括 1): the seat would fall
# back to manual and block on the first dialog.
NO_AUTO_MODELS = ("haiku",)

DEFAULT_TIME_LIMIT = "3h"
DEFAULT_GRACE = "20m"


def load_yaml(path: Path) -> dict:
    import yaml  # vendored; imported lazily so hooks stay light

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise YamatoError(f"{path} がありません") from None
    except yaml.YAMLError as e:
        raise YamatoError(f"{path} を読めません: {e}") from None
    if not isinstance(data, dict):
        raise YamatoError(f"{path} の中身が mapping ではありません")
    return data


def expand_seats(roles: dict) -> dict:
    """``count: 1`` -> seat named after the role; ``count: n`` -> ``<role>-1..n``."""
    seats: dict = {}
    for role, spec in roles.items():
        count = spec["count"]
        names = [role] if count == 1 else [f"{role}-{i}" for i in range(1, count + 1)]
        for seat in names:
            if seat in seats:
                raise YamatoError(f"席の名前が重複しています: {seat}")
            seats[seat] = {"role": role, "model": spec["model"], "shift": spec["shift"]}
    return seats


def validate(data: dict, shipdir: Path) -> dict:
    unknown = set(data) - TOP_KEYS
    if unknown:
        raise YamatoError(f"team.yaml に知らない項目があります: {', '.join(sorted(unknown))}")
    name = check_name("艦", str(data.get("name") or ""))

    roles_in = data.get("roles")
    if not isinstance(roles_in, dict) or not roles_in:
        raise YamatoError("team.yaml: roles が空です")
    roles: dict = {}
    for role, spec in roles_in.items():
        check_name("役割", str(role))
        spec = spec or {}
        if not isinstance(spec, dict):
            raise YamatoError(f"team.yaml: roles.{role} が mapping ではありません")
        bad = set(spec) - ROLE_KEYS
        if bad:
            raise YamatoError(f"team.yaml: roles.{role} に知らない項目があります: {', '.join(sorted(bad))}")
        model = str(spec.get("model") or "sonnet")
        if any(m in model.lower() for m in NO_AUTO_MODELS):
            raise YamatoError(f"team.yaml: roles.{role}.model={model} は auto モードを使えません (sonnet 以上にする)")
        shift = spec.get("shift") or "per_task"
        if shift not in SHIFTS:
            raise YamatoError(f"team.yaml: roles.{role}.shift は {' / '.join(SHIFTS)} のどれか (今: {shift})")
        count = spec.get("count", 1)
        if not isinstance(count, int) or count < 1:
            raise YamatoError(f"team.yaml: roles.{role}.count は 1 以上の整数 (今: {count!r})")
        roles[role] = {
            "model": model,
            "shift": shift,
            "count": count,
            "description": str(spec.get("description") or role),
        }

    hub = str(data.get("hub") or "")
    if hub not in roles:
        raise YamatoError(f"team.yaml: hub={hub!r} が roles にありません")
    if roles[hub]["count"] != 1:
        raise YamatoError("team.yaml: hub の役割は count: 1 にする")

    ws = data.get("workspace")
    if not ws:
        raise YamatoError("team.yaml: workspace がありません")
    wpath = Path(str(ws)).expanduser()
    if not wpath.is_absolute():
        wpath = shipdir / wpath

    deny = data.get("deny") or []
    if not isinstance(deny, list) or not all(isinstance(x, str) for x in deny):
        raise YamatoError("team.yaml: deny は文字列のリスト")

    settings = data.get("settings") or {}
    if not isinstance(settings, dict):
        raise YamatoError("team.yaml: settings は mapping (Claude Code の settings.json に重ねる中身)")
    seat_stop = data.get("seat_stop") or {}
    if not isinstance(seat_stop, dict) or set(seat_stop) - set(SEAT_STOP_DEFAULTS) \
            or not all(isinstance(v, bool) for v in seat_stop.values()):
        raise YamatoError(f"team.yaml: seat_stop は {{{', '.join(SEAT_STOP_DEFAULTS)}: true|false}}")

    board = data.get("board") or {}
    if not isinstance(board, dict):
        raise YamatoError("team.yaml: board が mapping ではありません")
    kinds = board.get("kinds") or ["task"]
    fields = board.get("fields") or []
    columns = []
    for col in board.get("columns") or []:
        if not isinstance(col, dict) or "name" not in col or col.get("state") not in STATES:
            raise YamatoError(f"team.yaml: board.columns の要素は {{name, state: {'|'.join(STATES)}}} (今: {col!r})")
        columns.append({"name": str(col["name"]), "state": col["state"]})

    return {
        "name": name,
        "hub": hub,
        "workspace": str(wpath.resolve()),
        "charter": str(data.get("charter") or "charter.md"),
        "time_limit": parse_duration(data.get("time_limit") or DEFAULT_TIME_LIMIT),
        "grace": parse_duration(data.get("grace") or DEFAULT_GRACE),
        "deny": deny,
        "settings": settings,
        "seat_stop": {**SEAT_STOP_DEFAULTS, **seat_stop},
        "roles": roles,
        "seats": expand_seats(roles),
        "board": {"kinds": [str(k) for k in kinds], "columns": columns, "fields": [str(f) for f in fields]},
    }


def load_team(shipdir: Path) -> dict:
    return validate(load_yaml(shipdir / "team.yaml"), shipdir)


def runtime_team(shipdir: Path) -> dict:
    """The team as the hooks see it: ``.runtime/team.json`` if present, else team.yaml."""
    data = read_json(shipdir / ".runtime" / "team.json")
    return data if data else load_team(shipdir)


def seat_spec(team: dict, seat: str) -> dict:
    try:
        return team["seats"][seat]
    except KeyError:
        raise YamatoError(f"席がありません: {seat} (席: {', '.join(team['seats'])})") from None

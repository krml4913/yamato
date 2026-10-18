"""艦の一覧 (ships.json と ``_admiral``)。``GET /api/v1/fleet`` の中身 (HTTP なし)。

読むだけ: 艦フォルダに何も書かない。team.yaml は検証せずに名前だけ読む (壊れた艦を一覧から落とさない)。
"""
from __future__ import annotations

from pathlib import Path

from . import claude, procs, roster, seat
from . import team as team_mod
from .admiral import ADMIRAL_DIRNAME, known_ships
from .util import YamatoError, yamato_home


def _team(shipdir: Path) -> tuple[str | None, list[str], str | None]:
    """(team.yaml の name, roles の名前, 読めなかった理由)。"""
    try:
        data = team_mod.load_yaml(shipdir / "team.yaml")
    except (YamatoError, OSError, UnicodeDecodeError) as ex:
        return None, [], str(ex)
    name, roles = data.get("name"), data.get("roles")
    return (name if isinstance(name, str) else None,
            [str(r) for r in roles] if isinstance(roles, dict) else [], None)


def seat_alive(rec: dict, spec: dict, by: dict) -> tuple[bool, dict | None]:
    """席が生きているか (と、その live の記録)。headless は claude agents に出ないので pid でも見る。"""
    live = by.get(rec.get("sessionId"))
    alive = claude.is_alive(live)
    if not alive and spec["shift"] == "headless" and rec.get("pid") and procs.pid_alive(rec["pid"]):
        alive, live = True, {**(live or {}), "pid": rec["pid"]}
    return alive, live


def _running(shipdir: Path, by: dict) -> bool | None:
    """生きている席が 1 つでもあれば稼働 (一覧と同じ判定)。team.yaml を読めなければ None。"""
    try:
        team = seat.current_team(shipdir)
        return any(seat_alive(roster.seat(shipdir, s), spec, by)[0] for s, spec in team["seats"].items())
    except Exception:  # noqa: BLE001 — 壊れた艦 1 つで一覧を落とさない
        return None


def _ships() -> list[dict]:
    """``admiral.known_ships()`` (ships.json の艦と $YAMATO_HOME 直下で team.yaml のあるフォルダ)。"""
    found = known_ships()
    try:
        by = claude.by_session(claude.agents())
    except YamatoError:
        by = None
    out = []
    for name, (path, registered) in sorted(found.items()):
        exists = (path / "team.yaml").is_file()
        team, _roles, error = _team(path) if exists else (None, [], f"{path / 'team.yaml'} がありません")
        out.append({"name": name, "path": str(path), "registered": registered, "exists": exists,
                    "running": _running(path, by) if exists and by is not None else None,
                    "team": team, "error": error})
    return out


def _admiral() -> dict | None:
    path = yamato_home() / ADMIRAL_DIRNAME
    if not path.is_dir():
        return None
    team, roles, error = _team(path)
    return {"path": str(path), "team": team, "roles": roles, "error": error}


def fleet() -> dict:
    """``Fleet`` の形。"""
    return {"home": str(yamato_home()), "ships": _ships(), "admiral": _admiral()}

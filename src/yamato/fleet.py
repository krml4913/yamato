"""艦の一覧 (ships.json と ``_admiral``)。``GET /api/v1/fleet`` の中身 (HTTP なし)。

読むだけ: 艦フォルダに何も書かない。team.yaml は検証せずに名前だけ読む (壊れた艦を一覧から落とさない)。
"""
from __future__ import annotations

from pathlib import Path

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


def _ships() -> list[dict]:
    """``admiral.known_ships()`` (ships.json の艦と $YAMATO_HOME 直下で team.yaml のあるフォルダ)。"""
    found = known_ships()
    out = []
    for name, (path, registered) in sorted(found.items()):
        exists = (path / "team.yaml").is_file()
        team, _roles, error = _team(path) if exists else (None, [], f"{path / 'team.yaml'} がありません")
        out.append({"name": name, "path": str(path), "registered": registered, "exists": exists,
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

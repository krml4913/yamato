"""席の操作: rotate (``ops.OPERATIONS`` に足す)。

``rotate`` は止まっている persistent の席に入れ替えの印を立てる (生きている席は CLI が断る)。
"""
from __future__ import annotations

from pathlib import Path

from ..seat import current_team
from ..team import seat_spec
from ..util import YamatoError
from . import ops


def _seat(shipdir: Path, params: dict) -> str:
    seat = params.get("seat")
    if not seat:
        raise YamatoError("params.seat (席の名前) が要る")
    seat_spec(current_team(shipdir), seat)   # 無い席は YamatoError
    return seat


def build_rotate(shipdir: Path, params: dict) -> ops.Command:
    return ops.Command(["rotate", str(shipdir), _seat(shipdir, params), "--by", "owner"])


ops.OPERATIONS.setdefault("rotate", ops.Operation(build_rotate))

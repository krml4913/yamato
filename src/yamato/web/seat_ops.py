"""席の操作: seat-stop と rotate (``ops.OPERATIONS`` に足す)。

``seat-stop`` は席のセッションの中から呼ぶ前提のコマンドなので、席の今のシフトの sessionId を
環境変数で渡して呼ぶ (引き継ぎが今回のシフトで書かれていなければ CLI が断る。その出力をそのまま見せる)。
``rotate`` は止まっている persistent の席に入れ替えの印を立てる (生きている席は CLI が断る)。
"""
from __future__ import annotations

from pathlib import Path

from .. import roster
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


def build_seat_stop(shipdir: Path, params: dict) -> ops.Command:
    seat = _seat(shipdir, params)
    sid = roster.seat(shipdir, seat).get("sessionId")
    if not sid:
        raise YamatoError(f"席 {seat} はまだ起動したことがない (sessionId が無い)")
    return ops.Command(["seat-stop", str(shipdir), seat], {"CLAUDE_CODE_SESSION_ID": str(sid)})


def build_rotate(shipdir: Path, params: dict) -> ops.Command:
    return ops.Command(["rotate", str(shipdir), _seat(shipdir, params), "--by", "owner"])


ops.OPERATIONS.setdefault("seat-stop", ops.Operation(build_seat_stop))
ops.OPERATIONS.setdefault("rotate", ops.Operation(build_rotate))

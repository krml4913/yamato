"""席の操作: rotate・send、判断を閉じる decide-close (``ops.OPERATIONS`` に足す)。

``rotate`` は止まっている persistent の席に入れ替えの印を立てる (生きている席は CLI が断る)。
``send`` は席に owner としてメッセージを送る。``decide-close`` は判断を owner として閉じる。
本文・choice・reason は利用者の自由な文なので、option に化けないよう ``--choice=値`` の形か ``--`` の後ろで渡す。
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


def _text(params: dict, key: str, label: str) -> str:
    v = params.get(key) or ""
    if not v.strip():
        raise YamatoError(f"params.{key} ({label}) が要る")
    return v


def build_send(shipdir: Path, params: dict) -> ops.Command:
    seat = _seat(shipdir, params)
    # 本文は最後に `--` の後ろへ置く (先頭が - でも option と取り違えない)
    return ops.Command(["send", str(shipdir), seat, "--from", "owner", "--", _text(params, "message", "本文")])


def build_decide_close(shipdir: Path, params: dict) -> ops.Command:
    dec_id = (params.get("id") or "").strip()
    if not dec_id or dec_id.startswith("-"):
        raise YamatoError("params.id (判断の id) が要る")
    return ops.Command(["decide", "close", str(shipdir), dec_id, "--by", "owner",
                        f"--choice={_text(params, 'choice', '決定')}", f"--reason={_text(params, 'reason', '理由')}"])


ops.OPERATIONS.setdefault("send", ops.Operation(build_send))
ops.OPERATIONS.setdefault("decide-close", ops.Operation(build_decide_close))

"""艦の操作: up・down・extend・halt (``ops.OPERATIONS`` に足す)。

どれも呼ぶ側の正規の入口 (``yamato up`` など) をそのまま別プロセスで呼ぶだけで、席や admiral の名前を借りない。
CLI が断ったときは、その出力と exit code を画面にそのまま出す。
``up`` と ``extend`` の値 (稼働時間・席) は CLI が検証する。ここでは option に化ける先頭の ``-`` だけ断る。
"""
from __future__ import annotations

from pathlib import Path

from ..util import YamatoError
from . import ops


def _value(params: dict, key: str, label: str, required: bool = False) -> str | None:
    v = (params.get(key) or "").strip()
    if not v:
        if required:
            raise YamatoError(f"params.{key} ({label}) が要る")
        return None
    if v.startswith("-"):
        raise YamatoError(f"params.{key} ({label}) は - で始められない")
    return v


def build_up(shipdir: Path, params: dict) -> ops.Command:
    args = ["up", str(shipdir), "--quiet"]
    if (span := _value(params, "for", "稼働時間。例 3h")):
        args.append(f"--for={span}")
    if (seats := _value(params, "seats", "一緒に起こす席。カンマ区切り")):
        args.append(f"--seats={seats}")
    return ops.Command(args)


def build_down(shipdir: Path, params: dict) -> ops.Command:
    return ops.Command(["down", str(shipdir)])


def build_down_force(shipdir: Path, params: dict) -> ops.Command:
    return ops.Command(["down", str(shipdir), "--force"])


def build_extend(shipdir: Path, params: dict) -> ops.Command:
    return ops.Command(["extend", str(shipdir), _value(params, "span", "延ばす時間。例 1h", required=True)])


def build_halt(shipdir: Path, params: dict) -> ops.Command:
    return ops.Command(["halt", str(shipdir)])


for _name, _build in (("up", build_up), ("down", build_down), ("down-force", build_down_force),
                      ("extend", build_extend), ("halt", build_halt)):
    ops.OPERATIONS.setdefault(_name, ops.Operation(_build))

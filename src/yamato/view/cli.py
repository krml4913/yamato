"""Entry point of ``bin/yamato-seat-attach``.

    yamato-seat-attach <ship> <seat> [--poll SEC]
    yamato-seat-attach --layout <ship>... [-o FILE] [--command PATH]

Moves under the ``yamato`` CLI (``yamato view`` etc.) after P0 is merged.
"""
from __future__ import annotations

import argparse
import os
import sys

from . import attach, layout
from .shipfiles import ViewError, ship_dir


def _attach_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="yamato-seat-attach",
                                description="席の今のシフトに attach し、シフトが替わったら付け直す")
    p.add_argument("ship", help="艦の名前 (~/yamato/<ship>) かパス")
    p.add_argument("seat")
    p.add_argument("--poll", type=float,
                   default=float(os.environ.get("YAMATO_VIEW_POLL") or attach.DEFAULT_POLL),
                   help="確認の間隔 (秒, 既定 3)")
    return p


def _layout_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="yamato-seat-attach --layout",
                                description="艦ごとに 1 タブ、席ごとに 1 ペインの zellij layout (KDL) を出力する")
    p.add_argument("ships", nargs="+", metavar="ship")
    p.add_argument("-o", "--output", help="書き出すファイル (既定は標準出力)")
    p.add_argument("--command", help="ペインで動かす seat-attach のパス (既定はこの repo の bin/yamato-seat-attach)")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv[:1] == ["--layout"]:
            a = _layout_parser().parse_args(argv[1:])
            text = layout.layout_for(a.ships, a.command)
            if a.output:
                with open(a.output, "w", encoding="utf-8") as f:
                    f.write(text)
            else:
                sys.stdout.write(text)
            return 0
        a = _attach_parser().parse_args(argv)
        shipdir = ship_dir(a.ship)
        resolve = attach.roster_resolver(shipdir, a.seat)
        return attach.run(resolve, f"{shipdir.name}.{a.seat}", poll=a.poll)
    except ViewError as e:
        print(f"yamato-seat-attach: {e}", file=sys.stderr)
        return 1

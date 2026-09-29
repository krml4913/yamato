"""``yamato view``: windows onto the ships' seats (design §10, §13).

    yamato view [<name>...] [-o FILE] [--command PATH] [--session NAME]   (= view open)
    yamato view attach <ship> <seat> [--poll SEC]
    yamato view layout <ship>... [-o FILE] [--command PATH]
    yamato view open [<ship>...] [-o FILE] [--command PATH] [--session NAME]

``add_parser`` / ``run`` are the only things ``yamato.cli`` knows about; the
heavy imports stay inside ``run`` so the per-turn hook path does not pay for them.
"""
from __future__ import annotations

import argparse
import os
import sys


SUBCOMMANDS = ("attach", "layout", "open")


def normalize(argv: list[str]) -> list[str]:
    """``yamato view <name>...`` / ``yamato view`` is ``yamato view open ...``: a first word after
    ``view`` that is not a subcommand (or -h) means ``open`` was left out."""
    if argv[:1] == ["view"] and (len(argv) == 1 or (argv[1] not in SUBCOMMANDS and argv[1] not in ("-h", "--help"))):
        return ["view", "open", *argv[1:]]
    return argv


def add_parser(sub) -> None:
    v = sub.add_parser("view", help="zellij で admiral と艦を開く (引数なし=admiral と全艦。名前で絞れる: yamato view admiral <艦>。open / attach / layout)")
    vs = v.add_subparsers(dest="view_cmd", required=True)

    a = vs.add_parser("attach", help="席の今のシフトに attach し、シフトが替わったら付け直す (zellij のペインの中身)")
    a.add_argument("ship", help="艦の名前か、艦フォルダのパス")
    a.add_argument("seat")
    a.add_argument("--poll", type=float, default=None,
                   help="確認の間隔 (秒, 既定 3。環境変数 YAMATO_VIEW_POLL でも指定できる)")

    lo = vs.add_parser("layout", help="艦ごとに 1 タブ、席ごとに 1 ペインの zellij layout (KDL) を出力する")
    lo.add_argument("ships", nargs="+", metavar="ship", help="艦の名前か、艦フォルダのパス")
    lo.add_argument("-o", "--output", help="書き出すファイル (既定は標準出力)")
    lo.add_argument("--command", help="ペインで動かす yamato のパス (既定はこの repo の ./yamato)")

    op = vs.add_parser("open", help="zellij で開く (中なら今のセッションにタブを足す、外なら yamato-view セッションを作るか、足りないタブを足して attach)")
    op.add_argument("ships", nargs="*", metavar="ship",
                    help="艦の名前か、艦フォルダのパス、admiral (省略時は admiral と登録されている全艦)")
    op.add_argument("-o", "--output", help="layout を書き出すファイル (既定は $YAMATO_HOME/view.kdl。zellij の中では使わない)")
    op.add_argument("--command", help="ペインで動かす yamato のパス (既定はこの repo の ./yamato)")
    op.add_argument("--session", help="zellij の外で使うセッション名 (既定 yamato-view)")


def run(args) -> int:
    if args.view_cmd == "layout":
        from . import layout

        text = layout.layout_for(args.ships, args.command)
        if args.output:
            with open(args.output, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        else:
            sys.stdout.write(text)
        return 0

    if args.view_cmd == "open":
        from . import opener

        if not args.ships or "admiral" in args.ships:
            from .. import admiral

            admiral.wake_admiral()   # admiral is who the owner opens the view to talk to
        kwargs = {"session": args.session} if args.session else {}
        opener.open_ships(args.ships or None, command=args.command, output=args.output, **kwargs)
        return 0

    from ..seat import session_name
    from ..team import runtime_team, seat_spec
    from ..util import resolve_ship
    from . import attach

    shipdir = resolve_ship(args.ship)
    team = runtime_team(shipdir)
    seat_spec(team, args.seat)   # a typo'd seat would otherwise wait forever
    poll = args.poll if args.poll is not None else float(os.environ.get("YAMATO_VIEW_POLL") or attach.DEFAULT_POLL)
    resolve = attach.roster_resolver(shipdir, args.seat)
    return attach.run(resolve, session_name(team, args.seat), poll=poll)

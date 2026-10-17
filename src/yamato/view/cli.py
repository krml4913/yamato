"""``yamato view``: windows onto the ships' seats (design §10, §13).

    yamato view [<name>...] [-o FILE] [--command PATH] [--session NAME]   (= view open)
    yamato view attach <ship> <seat> [--poll SEC]
    yamato view stale [--kill]
    yamato view layout <ship>... [-o FILE] [--command PATH]
    yamato view open [<ship>...] [-o FILE] [--command PATH] [--session NAME]

``add_parser`` / ``run`` are the only things ``yamato.cli`` knows about; the
heavy imports stay inside ``run`` so the per-turn hook path does not pay for them.
"""
from __future__ import annotations

import argparse
import os
import sys


SUBCOMMANDS = ("attach", "layout", "open", "stale")


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

    st = vs.add_parser("stale", help="ペインを失って残った `view attach` / `claude attach` (親が init の孤児) を一覧する。--kill で止める")
    st.add_argument("--kill", action="store_true", help="一覧したプロセスに SIGTERM を送る (付けなければ一覧するだけ)")

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

    if args.view_cmd == "stale":
        from . import procs

        found = procs.orphans()
        if not found:
            print("孤児の attach プロセスはありません")
            return 0
        for p in found:
            print(f"{p.pid}\t{p.text}")
        if args.kill:
            done = procs.terminate([p.pid for p in found])
            print(f"{len(done)} 件に SIGTERM を送りました")
        else:
            print("止めるなら `yamato view stale --kill` (親のいる attach には触りません)")
        return 0

    if args.view_cmd == "open":
        from . import opener

        if not args.ships or "admiral" in args.ships:
            from .. import admiral

            admiral.wake_admiral()   # admiral is who the owner opens the view to talk to
        kwargs = {"session": args.session} if args.session else {}
        from . import procs

        old = procs.orphans()
        if old:   # warn only: they are the user's processes, `view stale --kill` stops them
            print(f"警告: ペインを失った attach プロセスが {len(old)} 件残っています "
                  f"(pid {', '.join(str(p.pid) for p in old)})。席の取り合いになるなら `yamato view stale --kill`",
                  file=sys.stderr)
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
    from . import procs

    peers = lambda: procs.other_view_attachers(args.ship, args.seat)   # noqa: E731
    return attach.run(resolve, session_name(team, args.seat), poll=poll, peers=peers)

"""Command-line entry. Subcommand modules are imported lazily so the per-turn
``yamato hook ...`` path stays cheap."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .util import YamatoError, resolve_ship


def _parser(hook_only: bool = False) -> argparse.ArgumentParser:
    if not hook_only:
        from . import banner
        from .view import cli as view_cli
    p = argparse.ArgumentParser(prog="yamato", description="Claude Code の background session で常設の AI チーム (艦) を動かす")
    sub = p.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("hook", help="(Claude Code の hook から呼ばれる)")
    h.add_argument("event")
    h.add_argument("ship")
    h.add_argument("seat")
    if hook_only:
        return p

    ship = sub.add_parser("ship", help="艦を作る")
    ship_sub = ship.add_subparsers(dest="ship_cmd", required=True)
    c = ship_sub.add_parser("create", help="ひな形から艦フォルダを作る")
    c.add_argument("name")
    c.add_argument("--workspace", help="席の作業ディレクトリ (作業対象の repo)。repo の無いひな形 (research) では省く")
    c.add_argument("--path", help="艦フォルダの場所 (既定 ~/yamato/<name>)")
    c.add_argument("--template", default="dev", help="dev (開発艦) / research (調査艦)")
    banner.add_quiet(c)

    u = sub.add_parser("up", help="captain の席を起動し、稼働時間の上限を設定する")
    u.add_argument("ship")
    u.add_argument("--for", dest="for_", help="稼働時間 (例 3h, 20m)。既定は team.yaml の time_limit")
    u.add_argument("--seats", help="captain と一緒に起こす席 (カンマ区切り。例 impl,review)")
    banner.add_quiet(u)

    d = sub.add_parser("down", help="終業 (--force で即時に強制停止)")
    d.add_argument("ship")
    d.add_argument("--force", action="store_true")

    s = sub.add_parser("status", help="席の状態")
    s.add_argument("ship", nargs="?")

    se = sub.add_parser("send", help="席にメッセージを送る (inbox に記録し、止まっていれば起こす)")
    se.add_argument("ship")
    se.add_argument("seat", help="席の名前、または owner (人間の受信箱 + notify)")
    se.add_argument("message")
    se.add_argument("--from", dest="sender", help="送り手の席名 (既定: 呼び出し元の席。分からなければ owner)")
    se.add_argument("--cwd", help="宛先の次のシフトをこの dir (例: 項目の worktree) を cwd にして起動する。"
                                  "per_task / headless の席で使える (bgIsolation: none)")

    ib = sub.add_parser("inbox", help="未読を全文で表示して既読にする")
    ib.add_argument("ship")
    ib.add_argument("seat")
    ib.add_argument("--all", action="store_true", help="既読も含めて表示 (カーソルは動かさない)")

    lg = sub.add_parser("log", help="席の作業ログに 1 行追記する")
    lg.add_argument("ship")
    lg.add_argument("seat")
    lg.add_argument("text")

    b = sub.add_parser("board", help="board の操作")
    bs = b.add_subparsers(dest="board_cmd", required=True)
    ba = bs.add_parser("add", help="項目を作る")
    ba.add_argument("ship")
    ba.add_argument("title")
    ba.add_argument("fields", nargs="*", help="key=value (assignee=impl など)")
    ba.add_argument("--body", default="")
    ba.add_argument("--by")
    bset = bs.add_parser("set", help="frontmatter を変える / 経緯を 1 行足す")
    bset.add_argument("ship")
    bset.add_argument("id")
    bset.add_argument("fields", nargs="*", help="key=value")
    bset.add_argument("--note")
    bset.add_argument("--by")
    bn = bs.add_parser("note", help="項目の本文に追記する (frontmatter は変えない)")
    bn.add_argument("ship")
    bn.add_argument("id")
    bn.add_argument("text")
    bn.add_argument("--by", help="書き手の席名 (省略時は呼び出した席)")
    bsh = bs.add_parser("show", help="項目を表示する")
    bsh.add_argument("ship")
    bsh.add_argument("id")
    bl = bs.add_parser("list", help="一覧 (既定は done 以外)")
    bl.add_argument("ship")
    bl.add_argument("--all", action="store_true", help="archive も含める")
    bl.add_argument("--state")
    bl.add_argument("--assignee")
    bar = bs.add_parser("archive", help="done の項目を archive へ移す (archive_on_done: false の艦向け)")
    bar.add_argument("ship")
    bar.add_argument("id", nargs="?")
    bm = bs.add_parser("mine", help="席の担当 (done 以外)")
    bm.add_argument("ship")
    bm.add_argument("seat")

    ss = sub.add_parser("seat-stop", help="(席が使う) 引き継ぎを確認してシフトを終える")
    ss.add_argument("ship")
    ss.add_argument("seat")
    ss.add_argument("--after", type=int, default=10, help="何秒後に止めるか")
    ss.add_argument("--delivered", action="store_true",
                    help="生きている宛先への SendMessage を済ませた (未読の送信が残っていても終業する)")
    ss.add_argument("--rotate", action="store_true",
                    help="入れ替えの印を立てて終業する (次の send で resume せず新しいシフトになる)")

    from . import admiral, feed, pr, report, worktree  # design-p1 §8, §2, §6: the parsers live with the commands

    admiral.register(sub)
    worktree.register(sub)
    pr.register(sub)
    report.register(sub)
    feed.register(sub)
    from . import decide  # design-p1 §1

    decide.add_parser(sub)
    from . import memory  # design-p1 §3

    memory.register(sub)

    rh = sub.add_parser("run-headless", help="(send が切り離して起動する) headless の席の 1 シフトを claude -p で回す")
    rh.add_argument("ship")
    rh.add_argument("seat")

    view_cli.add_parser(sub)

    w = sub.add_parser("_watchdog")
    w.add_argument("ship")
    w.add_argument("token")
    e = sub.add_parser("_shift-ended")
    e.add_argument("ship")
    e.add_argument("seat")
    e.add_argument("session_id")
    e.add_argument("--forced", action="store_true")
    return p


def _hook(args) -> int:
    from . import hooks

    fn = hooks.HOOKS.get(args.event)
    if fn is None:
        print(f"yamato: 知らない hook です: {args.event}", file=sys.stderr)
        return 1
    try:
        return fn(Path(args.ship), args.seat)
    except Exception as e:  # a broken hook must never wedge the seat
        print(f"yamato hook {args.event}: {e}", file=sys.stderr)
        return 1


def _resolve_by(shipdir: Path, by: str | None) -> str | None:
    """``--by`` の既定は呼び出し元の席 (board note / decide / worktree / pr と同じ判定:
    ``roster.seat_of_session($CLAUDE_CODE_SESSION_ID)``)。席でなければ今までどおり (None)。
    ``--by`` を明示して呼び出し元と食い違えば警告だけする (拒否はしない)。"""
    from . import roster

    caller = roster.seat_of_session(shipdir, os.environ.get("CLAUDE_CODE_SESSION_ID"))
    if by is None:
        return caller
    if caller and by != caller:
        print(f"注意: --by {by} と呼び出し元の席 {caller} が食い違う (記録は --by の {by} を使う)")
    return by


def _board(args) -> int:
    from . import board as bmod
    from .seat import current_team

    shipdir = resolve_ship(args.ship)
    brd = bmod.Board(shipdir, current_team(shipdir))
    if args.board_cmd == "add":
        by = _resolve_by(shipdir, args.by)
        meta = brd.add(args.title, bmod.parse_assignments(args.fields), body=args.body, by=by)
        print(f"作成: {bmod.format_item(meta)}")
    elif args.board_cmd == "set":
        by = _resolve_by(shipdir, args.by)
        meta = brd.set(args.id, bmod.parse_assignments(args.fields), note=args.note, by=by)
        where = " (archive へ移動)" if meta.get("state") == "done" else ""
        print(f"更新: {bmod.format_item(meta)}{where}")
    elif args.board_cmd == "note":
        by = _resolve_by(shipdir, args.by)
        meta = brd.note(args.id, args.text, by=by)
        print(f"追記: {meta['id']} {meta.get('title')}")
    elif args.board_cmd == "show":
        _, _, path = brd.read(args.id)
        print(path.read_text(encoding="utf-8"), end="")
    elif args.board_cmd == "list":
        items = brd.items(include_archive=args.all)
        if not args.all:
            items = [m for m in items if m.get("state") != "done"]
        if args.state:
            items = [m for m in items if m.get("state") == args.state]
        if args.assignee:
            items = [m for m in items if m.get("assignee") == args.assignee]
        print("\n".join(bmod.format_item(m) for m in items) if items else "(項目なし)")
    elif args.board_cmd == "archive":
        moved = brd.archive(args.id)
        print(f"archive へ移した: {', '.join(moved) if moved else '(なし)'}")
    elif args.board_cmd == "mine":
        items = brd.mine(args.seat)
        print("\n".join(bmod.format_item(m) for m in items) if items else "(担当なし)")
    return 0


def _inbox(args) -> int:
    from . import inbox
    from .seat import current_team
    from .team import seat_spec

    shipdir = resolve_ship(args.ship)
    if args.seat != inbox.OWNER:
        seat_spec(current_team(shipdir), args.seat)
    if args.all:
        items = inbox.entries(shipdir, args.seat)
    else:
        items = inbox.unread(shipdir, args.seat)
    if not items:
        print("(未読なし)")
        return 0
    for e in items:
        print(inbox.format_entry(e))
    if not args.all:
        inbox.mark_read(shipdir, args.seat, items[-1]["n"])
    return 0


def _status(args) -> int:
    from . import seat
    from .admiral import all_ships

    if args.ship:
        return seat.status(resolve_ship(args.ship))
    ships = all_ships()
    if not ships:
        print("艦がありません (yamato ship create で作る)")
        return 0
    for path in ships.values():
        seat.status(path)
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    # 毎ターンの hook の経路は banner / view を import しない (引数の解析も hook だけの parser で行う)
    args = _parser(hook_only=bool(argv) and argv[0] == "hook").parse_args(argv)
    try:
        if args.cmd == "hook":
            return _hook(args)
        if args.cmd == "view":
            from .view import cli as view_cli

            return view_cli.run(args)
        if args.cmd == "ship":
            from . import ship

            from . import banner

            path, warnings = ship.create(args.name, args.workspace, args.path, args.template)
            banner.show("create", path, template=args.template, quiet=args.quiet)
            print(f"艦 {args.name} を作った: {path}")
            for w in warnings:
                print(f"注意: {w}")
            print(f"  次: team.yaml と charter.md を確認し、`yamato up {args.name}` で起動する")
            return 0
        if args.cmd == "board":
            return _board(args)
        if args.cmd == "inbox":
            return _inbox(args)
        if args.cmd == "status":
            return _status(args)
        if args.cmd in ("extend", "halt", "ships", "talk"):
            from . import admiral

            return admiral.run(args)
        if args.cmd in ("worktree", "pr", "report", "feed"):
            from . import feed, pr, report, worktree

            return {"worktree": worktree, "pr": pr, "report": report, "feed": feed}[args.cmd].run(args)
        if args.cmd in ("memo", "memory", "_memory-curate"):
            from . import memory

            return memory.run(args)
        if args.cmd == "decide":
            from . import decide

            return decide.main(args)

        from . import seat

        if args.cmd == "log":
            from .team import seat_spec
            from .util import append_log

            shipdir = resolve_ship(args.ship)
            seat_spec(seat.current_team(shipdir), args.seat)
            append_log(shipdir, args.seat, args.text)
            return 0
        if args.cmd == "up":
            from . import admiral

            from . import banner

            shipdir = resolve_ship(args.ship)
            banner.show("up", shipdir, span=args.for_, quiet=args.quiet)
            return admiral.up(shipdir, args.for_, args.seats)
        if args.cmd == "down":
            return seat.down(resolve_ship(args.ship), args.force)
        if args.cmd == "send":
            return seat.send(resolve_ship(args.ship), args.seat, args.message, args.sender, args.cwd)
        if args.cmd == "seat-stop":
            return seat.seat_stop(resolve_ship(args.ship), args.seat, args.after, args.delivered, args.rotate)
        if args.cmd == "run-headless":
            from . import headless

            return headless.run(resolve_ship(args.ship), args.seat)
        if args.cmd == "_watchdog":
            return seat.watchdog(Path(args.ship), args.token)
        if args.cmd == "_shift-ended":
            return seat.shift_ended(Path(args.ship), args.seat, args.session_id, args.forced)
    except YamatoError as e:
        print(f"yamato: {e}", file=sys.stderr)
        return 1
    return 1

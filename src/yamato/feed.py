"""``yamato feed``: events.jsonl を流し見する (owner 依頼、inbox #14 / T-007)。

直近 N 件 (既定 ``DEFAULT_LINES``) を出したあと、新しい行が来るたびに 1 行ずつ
流す (``tail -f`` の要領)。叩き台は ``~/.config/yamato/feed.py`` (leader 作、
読むだけで書き換えていない)。本体との違い: kind で絞れる、非 TTY / NO_COLOR で
色を消す、追いかけのポーリング間隔を ``POLL`` (モジュール定数) にしてテストか
ら ``mock.patch.object`` で縮められるようにした。

読み専用・書かない: events.jsonl には何も書き込まない (design-p1 の記録は
``events.emit`` だけが行う)。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Iterable, Iterator, TextIO

from . import events as events_mod

DEFAULT_LINES = 15    # --lines の既定 (owner 依頼)
POLL = 0.5            # follow 中、新しい行が無いときに寝る秒数 (テストは短く patch する)

# 種類ごとの色 (SGR の前景色。docs/events.md の kind の一覧に合わせる)。
# 異常 (force_stop・shift_failed・launch_failed・permission_denied・spin/duplicate_suspected・
# captain_gap・notify_failed・pr_open_failed・pr_merge_failed・pr_conflict・worktree_*_failed) は赤。
_ABNORMAL_KINDS = {
    events_mod.FORCE_STOP, events_mod.SHIFT_FAILED, events_mod.LAUNCH_FAILED,
    events_mod.PERMISSION_DENIED, events_mod.SPIN_SUSPECTED, events_mod.DUPLICATE_SUSPECTED,
    events_mod.CAPTAIN_GAP,
    "notify_failed", "pr_open_failed", "pr_merge_failed", "pr_conflict",
    "worktree_add_failed", "worktree_rm_failed",
}
_COLORS: dict[str, int] = {
    events_mod.SEND: 36,          # cyan
    events_mod.SHIFT_START: 32,   # green
    events_mod.SHIFT_END: 90,     # gray (bright black)
    events_mod.DECISION_OPEN: 33,  # yellow
    events_mod.DECISION_CLOSE: 33,
    "pr_open": 35,                # magenta
    "pr_merge": 35,
    "report_made": 34,            # blue
    "report_sent": 34,
    events_mod.LAST_CALL: 33,
}
for _k in _ABNORMAL_KINDS:
    _COLORS[_k] = 31               # red
del _k

_DEFAULT_COLOR = 37   # white: kind に無いもの (board_* など) はここ

RESET = "\033[0m"
_GRAY = "\033[90m"


def color_for(kind: str) -> int:
    return _COLORS.get(kind, _DEFAULT_COLOR)


def _parse_line(raw: str) -> dict | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        e = json.loads(raw)
    except ValueError:
        return None  # 壊れた行 (書きかけ) は飛ばす。events.read と同じ扱い
    return e if isinstance(e, dict) else None


def _kind_set(kinds) -> set | None:
    if not kinds:
        return None
    if isinstance(kinds, str):
        return {kinds}
    return set(kinds)


def tail(shipdir: Path, *, lines: int = DEFAULT_LINES, kinds: str | Iterable[str] | None = None,
        follow: bool = True, poll: float | None = None) -> Iterator[dict]:
    """events.jsonl の事象を yield する: まず直近 ``lines`` 件 (絞ったあと)、
    ``follow`` なら続けて新しく追記された行を都度 yield する。

    ``events.read`` と違い、file を一度だけ開いて先頭から読み進める (tail -f)。
    events.jsonl が無いときは follow のときだけ現れるのを待つ。
    """
    poll = POLL if poll is None else poll
    want = _kind_set(kinds)

    def matches(e: dict) -> bool:
        return want is None or e.get("kind") in want

    path = events_mod.path(shipdir)
    f = None
    try:
        f = path.open(encoding="utf-8")
    except FileNotFoundError:
        pass

    try:
        buf: list[dict] = []
        if f is not None:
            for raw in f:
                e = _parse_line(raw)
                if e is not None and matches(e):
                    buf.append(e)
        if lines > 0:
            buf = buf[-lines:]
        else:
            buf = []
        yield from buf

        if not follow:
            return
        pending = ""  # 改行で終わっていない書きかけの読みを溜めておく (改行が来てから parse する)
        while True:
            if f is None:
                try:
                    f = path.open(encoding="utf-8")
                except FileNotFoundError:
                    time.sleep(poll)
                    continue
            raw = f.readline()
            if raw:
                pending += raw
                if pending.endswith("\n"):
                    e = _parse_line(pending)
                    pending = ""
                    if e is not None and matches(e):
                        yield e
                # 改行が無ければ書きかけ。pending に残したまま次の readline を待つ
            else:
                time.sleep(poll)
    finally:
        if f is not None:
            f.close()


def _color_enabled(stream: TextIO) -> bool:
    if "NO_COLOR" in os.environ:
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def format_line(e: dict, *, color: bool) -> str:
    """時刻・kind・誰 (by か seat)・項目・要約の 1 行。"""
    ts = e.get("ts")
    t = time.strftime("%H:%M:%S", time.localtime(ts)) if isinstance(ts, (int, float)) else "--:--:--"
    kind = str(e.get("kind") or "")
    who = str(e.get("by") or e.get("seat") or "-")
    item = str(e.get("item") or "-")
    summary = str(e.get("summary") or "")
    if color:
        t = f"{_GRAY}{t}{RESET}"
        kind_field = f"\033[{color_for(e.get('kind') or '')}m{kind:<20}{RESET}"
    else:
        kind_field = f"{kind:<20}"
    return f"{t} {kind_field} {who:<10} {item:<8} {summary}"


def run_feed(shipdir: Path, *, lines: int = DEFAULT_LINES, kinds: str | Iterable[str] | None = None,
            follow: bool = True, poll: float | None = None, out: TextIO = sys.stdout) -> None:
    color = _color_enabled(out)
    try:
        for e in tail(shipdir, lines=lines, kinds=kinds, follow=follow, poll=poll):
            print(format_line(e, color=color), file=out, flush=True)
    except KeyboardInterrupt:
        pass


# --- CLI (cli.py に配線済み: register を parser 組み立てで、run を dispatch で呼ぶ) ---

def register(sub) -> None:
    f = sub.add_parser("feed", help="艦の出来事 (events.jsonl) を流し見する (tail -f の要領)")
    f.add_argument("ship")
    f.add_argument("--lines", type=int, default=DEFAULT_LINES,
                   help=f"直近何件から始めるか (既定 {DEFAULT_LINES})")
    f.add_argument("--no-follow", action="store_true", help="直近だけ出して終わる (追いかけない)")
    f.add_argument("--kind", action="append", help="この kind だけ (繰り返し可)")


def run(args) -> int:
    from .util import resolve_ship

    shipdir = resolve_ship(args.ship)
    run_feed(shipdir, lines=args.lines, kinds=args.kind, follow=not args.no_follow)
    return 0

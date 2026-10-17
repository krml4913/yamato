"""web コンソールの操作の共通部品 (HTTP は持たない)。

操作は yamato の CLI を別プロセスで呼ぶだけで、操作のロジックはここにも画面にも持たない。
引数は list で渡して shell を通さず、タイムアウトを付け、出力 (stdout + stderr) と exit code を返す。
CLI が失敗しても (exit code が 0 でなくても) 応答は 200 で、画面は出力と exit code をそのまま見せる。

どの操作ができるかは ``OPERATIONS`` に ``Operation`` を足した分だけ (足さなければ 404)。
``Operation.build`` は (艦フォルダ, params) から CLI の引数 (``yamato`` の次から) を返す。
壊したり止めたりする操作は、画面の確認を経た印 (``confirmed: true``) が無ければ実行しない。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import admiral
from ..util import YAMATO_BIN, YamatoError
from .edit import EditError

TIMEOUT = 30           # 秒。CLI が返らないまま画面を待たせない
MAX_OUTPUT = 20000     # 画面に返す出力の文字数の上限
_CONTROL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-_]|[\x00-\x08\x0b-\x1f\x7f]")


@dataclass
class Command:
    args: list[str]                                  # `yamato` の次から
    env: dict[str, str] = field(default_factory=dict)  # 環境変数に足す分


@dataclass
class Operation:
    build: Callable[[Path, dict], Command]
    confirm: bool = True    # confirmed: true が無ければ実行しない


OPERATIONS: dict[str, Operation] = {}


def clean(text: str) -> tuple[str, bool]:
    """端末の制御文字 (色・カーソル移動) を落とし、長すぎれば切る。(文字列, 切ったか)。"""
    text = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    return (text[:MAX_OUTPUT], True) if len(text) > MAX_OUTPUT else (text, False)


def run_cli(cmd: Command) -> dict:
    """``yamato <args>`` を実行して結果を返す。shell は通さない。"""
    argv = [sys.executable, str(YAMATO_BIN), *cmd.args]
    shown = ["yamato", *cmd.args]
    try:
        p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           env={**os.environ, **cmd.env}, timeout=TIMEOUT)
    except subprocess.TimeoutExpired as ex:
        raw = ex.stdout or b""
        out, cut = clean(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
        return {"argv": shown, "exit_code": None, "timed_out": True, "output": out, "truncated": cut}
    except OSError as ex:
        raise EditError(500, "internal", "yamato を起動できなかった", type(ex).__name__) from ex
    out, cut = clean(p.stdout.decode("utf-8", "replace"))
    return {"argv": shown, "exit_code": p.returncode, "timed_out": False, "output": out, "truncated": cut}


def handle(op: str, body: dict) -> dict:
    """``POST /api/v1/ops/{op}`` の本体 (トークンなどの検査は済んでいる前提)。"""
    operation = OPERATIONS.get(op)
    if operation is None:
        raise EditError(404, "not_found", "その操作は無い")
    ship, params = body.get("ship"), body.get("params", {})
    if not isinstance(ship, str) or not isinstance(params, dict) \
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in params.items()):
        raise EditError(400, "bad_request", "ship (文字列) と params (文字列の object) が要る")
    shipdir = admiral.find_ship(ship)
    if shipdir is None:
        raise EditError(404, "not_found", "その艦は無い")
    if operation.confirm and body.get("confirmed") is not True:
        raise EditError(400, "bad_request", "確認が要る操作 (confirmed: true が無い)")
    try:
        cmd = operation.build(shipdir, params)
    except YamatoError as ex:
        raise EditError(400, "bad_request", str(ex)) from ex
    return run_cli(cmd)

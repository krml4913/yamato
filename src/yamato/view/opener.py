"""``yamato view open``: open (or grow) the zellij window onto ships (design §13).

Outside zellij: builds the layout for the requested ships (every registered
ship, if none are named), writes it to a file, and attaches the ``yamato-view``
session -- starting it from that layout if it is not already up.

Inside zellij (``$ZELLIJ`` set): there is already a window to grow, so each
ship instead gets one ``zellij action new-tab --layout <file>`` call into the
*current* session, one tab per ship.

The layout is rebuilt from ``team.yaml`` / ``.runtime/team.json`` on every
call (nothing is cached), so a change in a ship's seats shows up the next
time someone runs ``view open``.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from .. import admiral
from ..util import YamatoError, yamato_home
from . import layout

DEFAULT_SESSION = "yamato-view"


def zellij_bin() -> str:
    return os.environ.get("YAMATO_ZELLIJ", "zellij")


def _refs(refs: list[str] | None) -> list[str]:
    """Every registered ship, sorted by name, when none are named explicitly."""
    return list(refs) if refs else sorted(admiral.all_ships())


def _zellij(run, args: list[str]):
    try:
        return run([zellij_bin(), *args], capture_output=True, text=True)
    except FileNotFoundError:
        raise YamatoError(f"zellij コマンドが見つかりません ({zellij_bin()})") from None


def _session_exists(run, session: str) -> bool:
    cp = _zellij(run, ["list-sessions", "--short"])
    names = {line.split()[0] for line in (cp.stdout or "").splitlines() if line.strip()}
    return session in names


def open_ships(refs: list[str] | None = None, *, command: str | None = None,
               output: str | None = None, session: str = DEFAULT_SESSION,
               in_zellij: bool | None = None, run=subprocess.run) -> None:
    """Build the layout(s) and open them in zellij.

    ``in_zellij`` defaults to whether ``$ZELLIJ`` is set (tests pass it
    explicitly so they do not depend on the environment they run in).
    ``output`` only applies outside zellij (a fixed file to attach); inside
    zellij each ship's one-tab layout is written to its own temp file, since
    ``new-tab --layout`` is called once per ship.
    """
    refs = _refs(refs)
    if not refs:
        raise YamatoError("開ける艦がありません (艦の登録がないか、引数で名前を渡してください)")
    if in_zellij is None:
        in_zellij = bool(os.environ.get("ZELLIJ"))

    if in_zellij:
        for ref in refs:
            text = layout.layout_for([ref], command)
            fd, tmp = tempfile.mkstemp(prefix="yamato-view-", suffix=".kdl")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            _zellij(run, ["action", "new-tab", "--layout", tmp])
        return

    text = layout.layout_for(refs, command)
    path = Path(output) if output else yamato_home() / "view.kdl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if _session_exists(run, session):
        _zellij(run, ["attach", session])
    else:
        _zellij(run, ["--session", session, "--new-session-with-layout", str(path)])

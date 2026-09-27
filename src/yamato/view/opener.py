"""``yamato view open``: open (or grow) the zellij window onto ships (design §13).

Outside zellij: builds the layout for the requested ships (every registered
ship, if none are named), writes it to a file, and attaches the ``yamato-view``
session -- starting it from that layout if it is not already up. If the
session is already up but the crew (or the set of ships) changed since it was
created, the layout on disk will now differ from what is on disk this time --
the stale session is torn down (``delete-session --force``; its only panes
are ``view attach`` processes, so nothing is lost) and rebuilt fresh.

Inside zellij (``$ZELLIJ`` set): there is already a window to grow, so each
ship instead gets one ``zellij action new-tab --layout <file>`` call into the
*current* session, one tab per ship.

The layout is rebuilt from ``team.yaml`` / ``.runtime/team.json`` on every
call (nothing is cached), so a change in a ship's seats shows up the next
time someone runs ``view open``.

``attach`` and ``--new-session-with-layout`` are interactive TUIs that take
over the terminal, so those two calls are made without capturing
stdout/stderr (capturing would blank the screen). Every zellij call raises
``YamatoError`` on a non-zero exit.
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


def _run_zellij(run, args: list[str], **kwargs):
    try:
        cp = run([zellij_bin(), *args], **kwargs)
    except FileNotFoundError:
        raise YamatoError(f"zellij コマンドが見つかりません ({zellij_bin()})") from None
    if cp.returncode != 0:
        raise YamatoError(f"zellij {' '.join(args)} が失敗しました (exit {cp.returncode})")
    return cp


def _zellij(run, args: list[str]):
    """Non-interactive calls (list-sessions / new-tab / delete-session): capture output."""
    return _run_zellij(run, args, capture_output=True, text=True)


def _zellij_interactive(run, args: list[str]):
    """``attach`` / ``--new-session-with-layout``: TUIs that take over the terminal --
    do not capture stdout/stderr (capturing would blank the screen)."""
    return _run_zellij(run, args)


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
    old_text = path.read_text(encoding="utf-8") if path.exists() else None
    path.write_text(text, encoding="utf-8")

    if _session_exists(run, session):
        if old_text == text:
            _zellij_interactive(run, ["attach", session])
            return
        # the crew (or the set of ships) changed since this session was created.
        # its only panes are `view attach` processes, so nothing is lost by tearing
        # it down and rebuilding from the new layout.
        _zellij(run, ["delete-session", "--force", session])
    _zellij_interactive(run, ["--session", session, "--new-session-with-layout", str(path)])

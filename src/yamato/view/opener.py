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
``YamatoError`` on a non-zero exit, except ``list-sessions``: zellij exits 1
with "No active zellij sessions found." on stderr when there are none yet
(the ordinary first-run case), so that one treats a non-zero exit as "no
sessions" instead of failing.
"""
from __future__ import annotations

import contextlib
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


def _admiral_exists() -> bool:
    return (admiral.admiral_dir() / "team.yaml").is_file()


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
    """``list-sessions`` exits non-zero (stderr: ``No active zellij sessions
    found.``) when there are none yet -- the ordinary first-run case, not a
    failure -- so treat any non-zero exit here as "no sessions" instead of
    raising (``FileNotFoundError`` still becomes ``YamatoError``, same as
    every other zellij call)."""
    try:
        cp = run([zellij_bin(), "list-sessions", "--short"], capture_output=True, text=True)
    except FileNotFoundError:
        raise YamatoError(f"zellij コマンドが見つかりません ({zellij_bin()})") from None
    if cp.returncode != 0:
        return False
    names = {line.split()[0] for line in (cp.stdout or "").splitlines() if line.strip()}
    return session in names


def open_ships(refs: list[str] | None = None, *, command: str | None = None,
               output: str | None = None, session: str = DEFAULT_SESSION,
               in_zellij: bool | None = None, run=subprocess.run,
               admiral_tab: bool | None = None, admiral_only: bool = False) -> None:
    """Build the layout(s) and open them in zellij.

    ``in_zellij`` defaults to whether ``$ZELLIJ`` is set (tests pass it
    explicitly so they do not depend on the environment they run in).
    ``admiral_tab``: put the admiral's tab ("admiral") first. Default: yes when no ship is
    named (the whole fleet) and ``_admiral/`` exists. ``admiral_only`` (``yamato admiral``):
    only that tab -- outside zellij the session is still built with every ship's tab, inside
    it just the admiral's tab is added and focused.
    ``output`` only applies outside zellij (a fixed file to attach); inside
    zellij each ship's one-tab layout is written to its own temp file, since
    ``new-tab --layout`` is called once per ship.
    """
    if admiral_tab is None:
        admiral_tab = not refs and _admiral_exists()
    admiral_tab = admiral_tab or admiral_only
    refs = _refs(refs)
    if not refs and not admiral_tab:
        raise YamatoError("開ける艦がありません (艦の登録がないか、引数で名前を渡してください)")
    if in_zellij is None:
        in_zellij = bool(os.environ.get("ZELLIJ"))

    if in_zellij:
        # (ref, with admiral tab): the admiral's own tab first, then one per ship
        todo = ([(None, True)] if admiral_tab else []) + ([] if admiral_only else [(r, False) for r in refs])
        for ref, is_admiral in todo:
            text = layout.layout_for([] if is_admiral else [ref], command, admiral=is_admiral)
            fd, tmp = tempfile.mkstemp(prefix="yamato-view-", suffix=".kdl")
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            try:
                _zellij(run, ["action", "new-tab", "--layout", tmp])
            finally:
                # zellij has read the layout by the time new-tab returns (T-015); a
                # failure to remove it must not fail the view open itself.
                with contextlib.suppress(OSError):
                    os.remove(tmp)
        return

    text = layout.layout_for(refs, command, admiral=admiral_tab)
    path = Path(output) if output else yamato_home() / "view.kdl"
    path.parent.mkdir(parents=True, exist_ok=True)
    old_text = path.read_text(encoding="utf-8") if path.exists() else None
    path.write_text(text, encoding="utf-8", newline="\n")

    if _session_exists(run, session):
        if old_text == text:
            if admiral_only:
                # best effort: land on the admiral's tab, not wherever the last detach left us
                with contextlib.suppress(FileNotFoundError):
                    run([zellij_bin(), "--session", session, "action", "go-to-tab-name", layout.ADMIRAL_TAB],
                        capture_output=True, text=True)
            _zellij_interactive(run, ["attach", session])
            return
        # the crew (or the set of ships) changed since this session was created.
        # its only panes are `view attach` processes, so nothing is lost by tearing
        # it down and rebuilding from the new layout.
        _zellij(run, ["delete-session", "--force", session])
    _zellij_interactive(run, ["--session", session, "--new-session-with-layout", str(path)])

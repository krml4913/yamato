"""``yamato view`` / ``yamato view open``: open (or grow) the zellij window onto ships (design §13).

One session, ``yamato-view``, holds every tab. Names are ships (registry, then
``$YAMATO_HOME``), paths, or ``admiral`` (``$YAMATO_HOME/_admiral``, tab "admiral"); with no
name, the admiral's tab (if ``_admiral/`` exists) comes first and every registered ship follows.

Outside zellij: writes the layout to a file and attaches ``yamato-view`` -- starting it from
that layout if it is not up. If it is already up, the tabs it lacks are added to it, and the tab
of a ship whose crew changed since (its block differs from the one last written) is rebuilt;
the panes are only ``view attach`` processes, so nothing is lost. Then it attaches. Never a second session.

Inside zellij (``$ZELLIJ`` set): each tab is added to the *current* session with
``zellij action new-tab --layout <file>`` (a tab of that name already there is just focused).

The layout is rebuilt from ``team.yaml`` / ``.runtime/team.json`` on every call (nothing is
cached), so a change in a ship's seats shows up the next time someone runs ``yamato view``.

``attach`` and ``--new-session-with-layout`` are interactive TUIs that take over the terminal, so
those two calls are made without capturing stdout/stderr (capturing would blank the screen).
Every zellij call raises ``YamatoError`` on a non-zero exit, except ``list-sessions``: zellij exits
1 with "No active zellij sessions found." on stderr when there are none yet (the ordinary
first-run case), so that one treats a non-zero exit as "no sessions" instead of failing.
"""
from __future__ import annotations

import contextlib
import json
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
    """The tabs to show. None named: the admiral first (when ``_admiral/`` exists), then every
    registered ship sorted by name."""
    if refs:
        return list(refs)
    first = [layout.ADMIRAL_TAB] if (admiral.admiral_dir() / "team.yaml").is_file() else []
    return first + sorted(admiral.all_ships())


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


def _tab_names(run, session: str | None) -> list[str]:
    args = ["--session", session] if session else []
    cp = _zellij(run, [*args, "action", "query-tab-names"])
    return [line for line in (cp.stdout or "").splitlines() if line]


def _new_tab(run, block: str, *, session: str | None = None) -> None:
    """``new-tab --layout`` with the block as a one-tab layout in a temp file."""
    fd, tmp = tempfile.mkstemp(prefix="yamato-view-", suffix=".kdl")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(layout.assemble([block]))
    try:
        _zellij(run, [*(["--session", session] if session else []), "action", "new-tab", "--layout", tmp])
    finally:
        # zellij has read the layout by the time new-tab returns (T-015); a
        # failure to remove it must not fail the view open itself.
        with contextlib.suppress(OSError):
            os.remove(tmp)


def _session_tabs(run, session: str) -> list[tuple[int, str]]:
    """``(tab_id, name)`` of the running session, from ``action list-tabs --json``. The id is
    stable, so the caller never depends on which tab has the focus (nobody may be attached)."""
    cp = _zellij(run, ["--session", session, "action", "list-tabs", "--json"])
    try:
        return [(int(t["tab_id"]), str(t["name"])) for t in json.loads(cp.stdout or "[]")]
    except (ValueError, KeyError, TypeError):
        raise YamatoError("zellij action list-tabs --json の出力を読めません") from None


def _sync_session(run, session: str, tabs: list[tuple[str, str]], old: dict[str, str]) -> None:
    """Bring the running session up to ``tabs``: add the missing ones, rebuild those whose block
    differs from ``old`` (what was written last). Tabs it has that are not asked for stay.
    Everything goes by tab id (``rename-tab-by-id`` / ``close-tab-by-id``), never by focus."""
    have = dict((name, tid) for tid, name in _session_tabs(run, session))
    for name, block in tabs:
        if name not in have:
            _new_tab(run, block, session=session)
        elif old.get(name, block) != block:
            # rename first, so the new tab can take the name; then close the old one by its id
            tid = str(have[name])
            _zellij(run, ["--session", session, "action", "rename-tab-by-id", tid, "_stale"])
            _new_tab(run, block, session=session)
            _zellij(run, ["--session", session, "action", "close-tab-by-id", tid])


def open_ships(refs: list[str] | None = None, *, command: str | None = None,
               output: str | None = None, session: str = DEFAULT_SESSION,
               in_zellij: bool | None = None, run=subprocess.run) -> None:
    """Build the tabs and open them in zellij.

    ``in_zellij`` defaults to whether ``$ZELLIJ`` is set (tests pass it
    explicitly so they do not depend on the environment they run in).
    ``output`` only applies outside zellij (the file the layout is kept in, and what the next
    call compares against); inside zellij each tab is written to its own temp file.
    """
    refs = _refs(refs)
    if not refs:
        raise YamatoError("開ける艦がありません (艦の登録がないか、引数で名前を渡してください)")
    if in_zellij is None:
        in_zellij = bool(os.environ.get("ZELLIJ"))
    tabs = layout.tabs_for(refs, command)

    if in_zellij:
        have = _tab_names(run, None)
        for name, block in tabs:
            if name in have:
                _zellij(run, ["action", "go-to-tab-name", name])
            else:
                _new_tab(run, block)
        return

    path = Path(output) if output else yamato_home() / "view.kdl"
    path.parent.mkdir(parents=True, exist_ok=True)
    old = layout.tabs_of(path.read_text(encoding="utf-8")) if path.exists() else {}
    merged = {**old, **dict(tabs)}   # what the session holds once synced: old tabs, these replaced / added
    if _session_exists(run, session):
        try:
            _sync_session(run, session, tabs, old)
        except YamatoError:
            # e.g. an EXITED (resurrectable) session, or zellij's actions changed: the panes are
            # only ``view attach`` processes, so throw the session away and start over from the
            # layout of every tab it should hold
            path.write_text(layout.assemble(list(merged.values())), encoding="utf-8", newline="\n")
            _zellij(run, ["delete-session", "--force", session])
            _zellij_interactive(run, ["--session", session, "--new-session-with-layout", str(path)])
            return
        path.write_text(layout.assemble(list(merged.values())), encoding="utf-8", newline="\n")
        _zellij_interactive(run, ["attach", session])
        return
    path.write_text(layout.assemble([b for _, b in tabs]), encoding="utf-8", newline="\n")
    _zellij_interactive(run, ["--session", session, "--new-session-with-layout", str(path)])

"""zellij KDL layout: one tab per ship, one pane per seat running ``yamato view attach``.

Open it with ``zellij --session <name> --layout <file>`` (design §10). Seats
are laid out two per row so the Claude TUI keeps a usable width.
"""
from __future__ import annotations

import re
import sys

from ..runtime import is_windows
from ..team import runtime_team
from ..util import YAMATO_BIN, YamatoError, resolve_ship

COLUMNS = 2


def kdl_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _pane(seat: str, ship_ref: str, command: str, indent: str) -> list[str]:
    lead = ""
    if is_windows() and not command.lower().endswith(".exe"):
        # native zellij cannot start an extension-less Python script (W4, research §2.5):
        # run it as `python.exe <script> ...`
        command, lead = sys.executable, f"{kdl_str(command)} "
    return [
        f"{indent}pane name={kdl_str(seat)} command={kdl_str(command)} {{",
        f"{indent}    args {lead}\"view\" \"attach\" {kdl_str(ship_ref)} {kdl_str(seat)}",
        f"{indent}}}",
    ]


HEADER = [
    "layout {",
    "    default_tab_template {",
    "        pane size=1 borderless=true {",
    '            plugin location="zellij:tab-bar"',
    "        }",
    "        children",
    "        pane size=2 borderless=true {",
    '            plugin location="zellij:status-bar"',
    "        }",
    "    }",
]


def tab_block(tab: str, ref: str, seats: list[str], command: str) -> str:
    """One tab (no focus mark, so two builds of the same tab compare equal): one pane per seat,
    two per row."""
    lines = [f"    tab name={kdl_str(tab)} {{"]
    for r in range(0, len(seats), COLUMNS):
        row = seats[r:r + COLUMNS]
        if len(row) == 1:
            lines += _pane(row[0], ref, command, " " * 8)
            continue
        lines.append('        pane split_direction="vertical" {')
        for seat in row:
            lines += _pane(seat, ref, command, " " * 12)
        lines.append("        }")
    lines.append("    }")
    return "\n".join(lines) + "\n"


def assemble(blocks: list[str]) -> str:
    """The layout from ``tab_block`` texts; the first tab gets the focus."""
    out = list(HEADER)
    for i, blk in enumerate(blocks):
        if i == 0:
            blk = blk.replace(" {\n", " focus=true {\n", 1)
        out.append(blk.rstrip("\n"))
    out.append("}")
    return "\n".join(out) + "\n"


def _unkdl(token: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) == "n" else m.group(1), token[1:-1])


def tabs_of(text: str) -> dict[str, str]:
    """{tab name: ``tab_block`` text} of a layout written by ``assemble`` (in order)."""
    tabs: dict[str, str] = {}
    cur: list[str] | None = None
    name = ""
    for line in text.splitlines():
        m = re.match(r'    tab name=("(?:[^"\\]|\\.)*")', line)
        if m:
            name = _unkdl(m.group(1))
            cur = [line.replace(" focus=true", "", 1)]
        elif cur is not None:
            cur.append(line)
            if line == "    }":
                tabs[name] = "\n".join(cur) + "\n"
                cur = None
    return tabs


def build(ships: list[tuple[str, str, list[str]]], command: str) -> str:
    """``ships`` is ``[(tab name, ship path passed to `view attach`, seats)]``."""
    return assemble([tab_block(t, ref, seats, command) for t, ref, seats in ships])


ADMIRAL_TAB = "admiral"   # the name that stands for ``$YAMATO_HOME/_admiral`` (D-011) in ``yamato view``


SHIP_TAB_PREFIX = "ship:"   # a ship's tab is ``ship:<name>``, so a ship called "admiral" does not clash with ADMIRAL_TAB


def legacy_tab_name(tab: str) -> str | None:
    """The pre-T-046 name (no prefix) of a ship tab, or None (not a ship tab / would be the admiral's)."""
    if tab.startswith(SHIP_TAB_PREFIX) and tab[len(SHIP_TAB_PREFIX):] != ADMIRAL_TAB:
        return tab[len(SHIP_TAB_PREFIX):]
    return None


def _tab_for(ref: str) -> tuple[str, str, list[str]]:
    if ref == ADMIRAL_TAB:
        from ..admiral import admiral_dir

        shipdir = admiral_dir()
        if not (shipdir / "team.yaml").is_file():
            raise YamatoError("admiral はまだ作られていない (yamato admiral で初回に作る)")
        return ADMIRAL_TAB, str(shipdir.resolve()), list(runtime_team(shipdir)["seats"])
    shipdir = resolve_ship(ref)
    team = runtime_team(shipdir)
    return SHIP_TAB_PREFIX + team["name"], str(shipdir.resolve()), list(team["seats"])


def tabs_for(refs: list[str], command: str | None = None) -> list[tuple[str, str]]:
    """[(tab name, ``tab_block`` text)] for ships given by name (registry, then ``$YAMATO_HOME``) or
    path; ``admiral`` is the admiral's tab (``_admiral/``, named "admiral"); a ship's tab is
    ``ship:<name>``.

    Every pane gets the ship's absolute path: the panes run in the zellij
    server's environment, which may not have this shell's YAMATO_HOME.
    """
    command = command or str(YAMATO_BIN)
    return [(t[0], tab_block(*t, command)) for t in map(_tab_for, refs)]


def layout_for(refs: list[str], command: str | None = None) -> str:
    return assemble([blk for _, blk in tabs_for(refs, command)])

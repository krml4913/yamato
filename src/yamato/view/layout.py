"""zellij KDL layout: one tab per ship, one pane per seat running ``yamato view attach``.

Open it with ``zellij --session <name> --layout <file>`` (design §10). Seats
are laid out two per row so the Claude TUI keeps a usable width.
"""
from __future__ import annotations

from ..team import runtime_team
from ..util import YAMATO_BIN, resolve_ship

COLUMNS = 2


def kdl_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _pane(seat: str, ship_ref: str, command: str, indent: str) -> list[str]:
    return [
        f"{indent}pane name={kdl_str(seat)} command={kdl_str(command)} {{",
        f"{indent}    args \"view\" \"attach\" {kdl_str(ship_ref)} {kdl_str(seat)}",
        f"{indent}}}",
    ]


def build(ships: list[tuple[str, str, list[str]]], command: str) -> str:
    """``ships`` is ``[(tab name, ship path passed to `view attach`, seats)]``."""
    lines = [
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
    for i, (tab, ref, seats) in enumerate(ships):
        focus = " focus=true" if i == 0 else ""
        lines.append(f"    tab name={kdl_str(tab)}{focus} {{")
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
    lines.append("}")
    return "\n".join(lines) + "\n"


ADMIRAL_TAB = "admiral"


def admiral_tab() -> tuple[str, str, list[str]] | None:
    """The admiral's tab (``_admiral/``, D-011): named "admiral" (not the team.yaml name), or
    None while ``_admiral/`` has never been built."""
    from ..admiral import admiral_dir

    shipdir = admiral_dir()
    if not (shipdir / "team.yaml").is_file():
        return None
    return ADMIRAL_TAB, str(shipdir.resolve()), list(runtime_team(shipdir)["seats"])


def layout_for(refs: list[str], command: str | None = None, *, admiral: bool = False) -> str:
    """Build the layout for ships given by name (registry, then ``$YAMATO_HOME``) or path.

    Every pane gets the ship's absolute path: the panes run in the zellij
    server's environment, which may not have this shell's YAMATO_HOME.
    ``admiral``: the admiral's tab goes first (skipped while ``_admiral/`` does not exist).
    """
    ships = []
    if admiral and (tab := admiral_tab()):
        ships.append(tab)
    for ref in refs:
        shipdir = resolve_ship(ref)
        team = runtime_team(shipdir)
        ships.append((team["name"], str(shipdir.resolve()), list(team["seats"])))
    return build(ships, command or str(YAMATO_BIN))

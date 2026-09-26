"""zellij KDL layout: one tab per ship, one pane per seat running seat-attach.

Open it with ``zellij --session <name> --layout <file>`` (design §10). Seats
are laid out two per row so the Claude TUI keeps a usable width.
"""
from __future__ import annotations

from pathlib import Path

from .shipfiles import ship_dir, team_seats

SEAT_ATTACH = Path(__file__).resolve().parents[3] / "bin" / "yamato-seat-attach"
COLUMNS = 2


def kdl_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def _pane(seat: str, ship_ref: str, command: str, indent: str) -> list[str]:
    return [
        f"{indent}pane name={kdl_str(seat)} command={kdl_str(command)} {{",
        f"{indent}    args {kdl_str(ship_ref)} {kdl_str(seat)}",
        f"{indent}}}",
    ]


def build(ships: list[tuple[str, str, list[str]]], command: str) -> str:
    """``ships`` is ``[(tab name, ship ref passed to seat-attach, seats)]``."""
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


def layout_for(refs: list[str], command: str | None = None) -> str:
    """Build the layout for ships given by name or path."""
    ships = []
    for ref in refs:
        shipdir = ship_dir(ref)
        # a path is passed on as an absolute path so the pane's cwd does not matter
        pass_ref = ref if shipdir.name == ref else str(shipdir)
        ships.append((shipdir.name, pass_ref, team_seats(shipdir)))
    return build(ships, command or str(SEAT_ATTACH))

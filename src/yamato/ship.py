"""``yamato ship create``: a ship folder from a template."""
from __future__ import annotations

from pathlib import Path

from .team import load_team
from .util import YamatoError, check_name, register_ship, yamato_home

TEMPLATES = Path(__file__).resolve().parent / "templates"


def create(name: str, workspace: str, path: str | None, template: str) -> tuple[Path, list[str]]:
    check_name("艦", name)
    tdir = TEMPLATES / template
    if not tdir.is_dir():
        known = ", ".join(sorted(p.name for p in TEMPLATES.iterdir() if p.is_dir()))
        raise YamatoError(f"ひな形 {template} はありません (ある: {known})")
    ws = Path(workspace).expanduser().resolve()
    if not ws.is_dir():
        raise YamatoError(f"workspace がありません: {ws}")
    shipdir = Path(path).expanduser().resolve() if path else yamato_home() / name
    if shipdir.exists() and any(shipdir.iterdir()):
        raise YamatoError(f"{shipdir} はすでにあって空ではありません")

    for src in sorted(tdir.rglob("*")):
        if src.is_dir():
            continue
        dst = shipdir / src.relative_to(tdir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text(encoding="utf-8")
        dst.write_text(text.replace("{{name}}", name).replace("{{workspace}}", str(ws)), encoding="utf-8")

    team = load_team(shipdir)  # the template must validate as written
    from .seat import ensure_seat_dirs

    ensure_seat_dirs(shipdir, team)
    register_ship(name, shipdir)
    warnings = list(team.get("warnings") or [])
    from .claude import is_trusted, untrusted_message

    if not is_trusted(ws):
        warnings.append(untrusted_message(ws))
    return shipdir, warnings

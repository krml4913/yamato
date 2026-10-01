"""``yamato ship create``: a ship folder from a template."""
from __future__ import annotations

from pathlib import Path

from .team import load_team
from .util import YamatoError, check_name, register_ship, yamato_home

TEMPLATES = Path(__file__).resolve().parent / "templates"
SCHEMA = Path(__file__).resolve().parent / "schema" / "team.schema.json"   # team.yaml's JSON Schema (T-057)


def create(name: str, workspace: str | None, path: str | None, template: str, *,
           register: bool = True) -> tuple[Path, list[str]]:
    """``register=False``: build the folder (same shape as any ship) without adding it to
    ``ships.json`` (T-020: the admiral's ``_admiral/`` stays out of ``ships`` / the registry;
    it is still found by ``resolve_ship`` through the plain ``$YAMATO_HOME/<name>`` fallback)."""
    check_name("艦", name)
    tdir = TEMPLATES / template
    if not tdir.is_dir():
        known = ", ".join(sorted(p.name for p in TEMPLATES.iterdir() if p.is_dir()))
        raise YamatoError(f"ひな形 {template} はありません (ある: {known})")
    # a template without a repo (research: `workspace: .`) needs no --workspace
    if workspace is None:
        if "{{workspace}}" in (tdir / "team.yaml").read_text(encoding="utf-8"):
            raise YamatoError(f"ひな形 {template} は --workspace (席の作業ディレクトリ) が要る")
        ws = None
    else:
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
        if ws is not None:
            # both spots sit in YAML "..." strings, so `\` and `"` need escaping (W5);
            # `/{{workspace}}/**` is a permission rule (Windows: `//c/Users/x`)
            from .runtime import rule_path

            def esc(v: str) -> str:
                return v.replace("\\", "\\\\").replace('"', '\\"')

            text = text.replace("/{{workspace}}/", "/" + esc(rule_path(ws)) + "/")
            text = text.replace("{{workspace}}", esc(str(ws)))
        # the schema is addressed by the checkout's absolute path (the repo is private, so no URL);
        # as_uri() also gives a Windows path the file:///C:/... form the YAML extension reads
        text = text.replace("{{schema}}", SCHEMA.as_uri())
        dst.write_text(text.replace("{{name}}", name), encoding="utf-8", newline="\n")

    team = load_team(shipdir)  # the template must validate as written
    from .seat import ensure_seat_dirs

    ensure_seat_dirs(shipdir, team)
    if register:
        register_ship(name, shipdir)
    warnings = list(team.get("warnings") or [])
    from .claude import is_trusted, unknown_trust_message, untrusted_message

    if ws is not None and ws != Path(team["workspace"]):
        warnings.append(f"ひな形 {template} は --workspace を使わない (席の作業ディレクトリは {team['workspace']})")
    ws = Path(team["workspace"])
    trusted = is_trusted(ws)
    if not trusted:
        warnings.append(untrusted_message(ws) if trusted is False else unknown_trust_message(ws))
    return shipdir, warnings

"""``yamato ship create``: a ship folder from a template."""
from __future__ import annotations

from pathlib import Path

from .team import load_team
from .util import YamatoError, check_name, register_ship, yamato_home

TEMPLATES = Path(__file__).resolve().parent / "templates"
SCHEMA = Path(__file__).resolve().parent / "schema" / "team.schema.json"   # team.yaml's JSON Schema (T-057)


def _fill_workspace(text: str, wss: list[Path]) -> str:
    """Fill `{{workspace}}`. Both spots sit in YAML "..." strings, so `\\` and `"` need escaping (W5);
    `/{{workspace}}/` is a permission rule (Windows: `//c/Users/x`). With several repos the `workspace:`
    line becomes a list and every rule line is repeated per repo (D-071)."""
    from .runtime import rule_path

    def esc(v: str) -> str:
        return v.replace("\\", "\\\\").replace('"', '\\"')

    if len(wss) == 1:
        w = wss[0]
        return text.replace("/{{workspace}}/", "/" + esc(rule_path(w)) + "/").replace("{{workspace}}", esc(str(w)))
    out = []
    for line in text.splitlines(keepends=True):
        if "{{workspace}}" not in line:
            out.append(line)
        elif line.startswith("workspace:"):
            head, _, comment = line.partition("#")
            out.append("workspace:" + (("   #" + comment) if comment else "\n"))
            out.extend(f'  - "{esc(str(w))}"\n' for w in wss)
        elif "/{{workspace}}/" in line:
            out.extend(line.replace("/{{workspace}}/", "/" + esc(rule_path(w)) + "/") for w in wss)
        else:
            out.append(line.replace("{{workspace}}", esc(str(wss[0]))))
    return "".join(out)


def create(name: str, workspace: str | list[str] | None, path: str | None, template: str, *,
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
    if not workspace:
        if "{{workspace}}" in (tdir / "team.yaml").read_text(encoding="utf-8"):
            raise YamatoError(f"ひな形 {template} は --workspace (席の作業ディレクトリ) が要る")
        wss: list[Path] = []
    else:
        # one --workspace stays a string in team.yaml; several become a flat list (D-071)
        wss = [Path(w).expanduser().resolve() for w in ([workspace] if isinstance(workspace, str) else workspace)]
        for w in wss:
            if not w.is_dir():
                raise YamatoError(f"workspace がありません: {w}")
    ws = wss[0] if wss else None
    seen: dict[str, Path] = {}
    for w in wss:   # same message as load_team, but before anything is written to the ship folder
        if w.name in seen:
            raise YamatoError(f"team.yaml: workspace の呼び名 (フォルダ名) {w.name!r} がかぶっている: {seen[w.name]} / {w}")
        seen[w.name] = w
    shipdir = Path(path).expanduser().resolve() if path else yamato_home() / name
    if shipdir.exists() and any(shipdir.iterdir()):
        raise YamatoError(f"{shipdir} はすでにあって空ではありません")

    for src in sorted(tdir.rglob("*")):
        if src.is_dir():
            continue
        dst = shipdir / src.relative_to(tdir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text(encoding="utf-8")
        if wss:
            text = _fill_workspace(text, wss)
        # the schema is addressed by the checkout's absolute path (the repo is private, so no URL);
        # as_uri() also gives a Windows path the file:///C:/... form the YAML extension reads
        text = text.replace("{{schema}}", SCHEMA.as_uri())
        dst.write_text(text.replace("{{name}}", name), encoding="utf-8", newline="\n")

    from . import __version__
    from .upgrade import set_template

    set_template(shipdir, template, __version__)   # which template/version this copy is from (D-081)
    team = load_team(shipdir)  # the template must validate as written
    from .seat import ensure_seat_dirs

    ensure_seat_dirs(shipdir, team)
    if register:
        register_ship(name, shipdir)
    warnings = list(team.get("warnings") or [])
    from .claude import is_trusted, unknown_trust_message, untrusted_message

    if ws is not None and ws != Path(team["workspace"]):
        warnings.append(f"ひな形 {template} は --workspace を使わない (席の作業ディレクトリは {team['workspace']})")
    for w in team["workspaces"]:   # trust is checked for every repo (D-071)
        wp = Path(w["path"])
        trusted = is_trusted(wp)
        if not trusted:
            warnings.append(untrusted_message(wp) if trusted is False else unknown_trust_message(wp))
    return shipdir, warnings

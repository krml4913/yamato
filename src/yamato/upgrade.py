"""``yamato ship upgrade``: bring a ship's copy (roles/*.md, team.yaml) up to a newer yamato (D-081).

No machine 3-way merge: yamato only prepares the materials (a backup, the template diff between the
two versions, the migration.md items in range) and starts an interactive Claude in the foreground that
asks the human one change at a time and edits only what was chosen. Starting claude goes through
``claude.upgrade_argv``; the settings it gets are its own, never the ship's."""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

from . import __version__, claude, procs
from .runtime import rule_path, yamato_invocation
from .util import YamatoError

REPO = Path(__file__).resolve().parents[2]            # the yamato checkout (git repo)
TEMPLATES_REL = "src/yamato/templates"
PROMPT = Path(__file__).resolve().parent / "prompts" / "upgrade.md"
BACKUP_DIR = ".upgrade"
_TEMPLATE_RE = re.compile(r"^template:[^\n]*\n(?:[ \t]+[^\n]*\n)*", re.M)
_NAME_RE = re.compile(r"^name:[^\n]*\n", re.M)


def norm_version(v: str) -> str:
    """`v1.0.0` / `1.0.0` -> `1.0.0`."""
    v = v.strip()
    return v[1:] if v[:1] in ("v", "V") else v


def _vtuple(v: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", norm_version(v)))


def set_template(shipdir: Path, name: str, version: str) -> None:
    """Write (or replace) ``template: {name, version}`` in the ship's team.yaml, keeping the rest of the
    text (comments) as it is. A new key goes right after the ``name:`` line."""
    ty = shipdir / "team.yaml"
    text = ty.read_text(encoding="utf-8")
    line = f"template: {{name: {name}, version: {norm_version(version)}}}   # この写しの元のひな形と版 (ship upgrade が使う)\n"
    if _TEMPLATE_RE.search(text):
        text = _TEMPLATE_RE.sub(lambda _m: line, text, count=1)
    else:
        m = _NAME_RE.search(text)
        if not m:
            raise YamatoError(f"{ty} に name: の行がない")
        text = text[:m.end()] + line + text[m.end():]
    ty.write_text(text, encoding="utf-8", newline="\n")


def done(shipdir: Path, version: str | None) -> str:
    """`ship upgrade-done`: move team.yaml's template.version (the end of an upgrade)."""
    from .team import load_team

    team = load_team(shipdir)
    new = norm_version(version or __version__)
    name = (team.get("template") or {}).get("name") or "dev"
    set_template(shipdir, name, new)
    return new


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, check=False)


def _tag_ok(tag: str) -> bool:
    return _git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}^{{commit}}").returncode == 0


def migration_range(text: str, old: str) -> str:
    """The `## ...` sections of migration.md that come after ``old``: 未リリース and every
    `## vA → vB` with B newer than ``old``. The intro above the first section is left out."""
    parts = re.split(r"(?m)^(?=## )", text)
    keep = []
    for part in parts[1:]:
        head = part.splitlines()[0]
        m = re.match(r"## v?([\d.]+)\s*(?:→|->)\s*v?([\d.]+)", head)
        if "未リリース" in head or (m and _vtuple(m.group(2)) > _vtuple(old)):
            keep.append(part.rstrip() + "\n")
    return "\n".join(keep)


def _extract_template(tag: str, template: str, dst: Path) -> None:
    rel = f"{TEMPLATES_REL}/{template}"
    cp = _git("archive", "--format=tar", tag, rel)
    if cp.returncode != 0:
        raise YamatoError(f"tag {tag} にひな形 {template} がない: {cp.stderr.decode('utf-8', 'replace').strip()}")
    with tarfile.open(fileobj=io.BytesIO(cp.stdout)) as tf:
        for m in tf.getmembers():
            if m.isfile():
                out = dst / Path(m.name).relative_to(rel)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(tf.extractfile(m).read())


def prepare(shipdir: Path, team: dict, old: str, template: str, *, now: float | None = None) -> tuple[Path, bool]:
    """Back up roles/ and team.yaml and lay out the materials under ``.upgrade/<old>-<new>-<time>/``.
    Returns (that folder, whether the template changed between the two versions)."""
    new = __version__
    tag = "v" + old
    if not (REPO / ".git").exists():
        raise YamatoError(f"yamato の checkout ({REPO}) が git repo でないので、ひな形の差分を作れない")
    if not _tag_ok(tag):
        raise YamatoError(f"yamato の checkout に tag {tag} がない (`git fetch --tags`)。版は --from で渡す")
    if not (REPO / TEMPLATES_REL / template).is_dir():
        raise YamatoError(f"ひな形 {template} は yamato にない (--template で渡す)")
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    backup = shipdir / BACKUP_DIR / f"{old}-{new}-{stamp}"
    backup.mkdir(parents=True)
    before = backup / "before"
    before.mkdir()
    shutil.copy2(shipdir / "team.yaml", before / "team.yaml")
    if (shipdir / "roles").is_dir():
        shutil.copytree(shipdir / "roles", before / "roles")
    diff = _git("diff", f"{tag}..HEAD", "--", f"{TEMPLATES_REL}/{template}/")
    patch = diff.stdout.decode("utf-8", "replace")
    (backup / "template-diff.patch").write_text(patch, encoding="utf-8")
    _extract_template(tag, template, backup / "template-old")
    shutil.copytree(REPO / TEMPLATES_REL / template, backup / "template-new")
    mig = REPO / "docs" / "migration.md"
    (backup / "migration.md").write_text(
        migration_range(mig.read_text(encoding="utf-8"), old) if mig.exists() else "", encoding="utf-8")
    return backup, bool(patch.strip())


def _settings(shipdir: Path) -> dict:
    ship = rule_path(shipdir)
    return {"permissions": {
        "defaultMode": "default",
        "allow": [f"Edit(/{ship}/roles/*.md)", f"Edit(/{ship}/team.yaml)",
                  f"Bash({yamato_invocation()} ship upgrade-done *)"],
        "deny": [f"Edit(/{ship}/charter.md)", f"Edit(/{ship}/knowledge.md)", f"Write(/{ship}/charter.md)",
                 f"Write(/{ship}/knowledge.md)"],
    }}


def upgrade(shipdir: Path, from_version: str | None, template: str | None, *, execvp=os.execvp,
            out=print, now: float | None = None) -> int:
    from .team import load_team

    team = load_team(shipdir)
    rec = team.get("template") or {}
    old = norm_version(from_version or rec.get("version") or "")
    if not old:
        raise YamatoError("この艦の team.yaml に template の記録がない。作った版を `--from v1.0.0` で渡す "
                          "(dev 以外のひな形は --template も)")
    tname = template or rec.get("name") or "dev"
    backup, changed = prepare(shipdir, team, old, tname, now=now)
    out(f"控え・材料: {backup}")
    if not changed and _vtuple(old) == _vtuple(__version__):
        out(f"v{old} から ひな形 {tname} に変更はない。upgrade するものはない")
        return 0
    prompt = (PROMPT.read_text(encoding="utf-8")
              .replace("{{yamato}}", yamato_invocation()).replace("{{ship_name}}", team["name"])
              .replace("{{ship}}", str(shipdir)).replace("{{template}}", tname)
              .replace("{{old}}", old).replace("{{new}}", __version__).replace("{{backup}}", str(backup)))
    settings = backup / "settings.json"
    settings.write_text(json.dumps(_settings(shipdir), ensure_ascii=False, indent=2), encoding="utf-8")
    argv = claude.upgrade_argv(
        settings=str(settings), system_prompt=prompt,
        prompt=f"艦 {team['name']} の写しを v{old} から v{__version__} に上げる。材料を読んで、変更を 1 件ずつ出してくれ。")
    out(f"claude を起動する (cwd {shipdir})")
    os.chdir(shipdir)
    return procs.run_foreground(argv, execvp=execvp)

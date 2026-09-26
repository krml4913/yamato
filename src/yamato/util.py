"""Small shared helpers: errors, paths, the ship lock, atomic writes, durations.

Imported by the per-turn hooks, so keep it to the standard library and cheap
modules only.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import tempfile
import time
from pathlib import Path

# repo root / yamato (the executable the seats call back into)
YAMATO_BIN = Path(__file__).resolve().parents[2] / "yamato"

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class YamatoError(Exception):
    """A user-facing error: printed without a traceback, exit code 1."""


def yamato_home() -> Path:
    return Path(os.environ.get("YAMATO_HOME") or "~/yamato").expanduser()


def check_name(kind: str, name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise YamatoError(f"{kind} の名前が不正です: {name!r} (英小文字・数字・-・_ のみ)")
    return name


# --- ship resolution -------------------------------------------------------

def _registry_path() -> Path:
    return yamato_home() / "ships.json"


def load_registry() -> dict:
    try:
        return json.loads(_registry_path().read_text())
    except (FileNotFoundError, ValueError):
        return {}


def register_ship(name: str, path: Path) -> None:
    reg = load_registry()
    reg[name] = str(path)
    atomic_write(_registry_path(), json.dumps(reg, ensure_ascii=False, indent=2) + "\n")


def resolve_ship(ref: str) -> Path:
    """A ship is given by name (registry, then ``$YAMATO_HOME/<name>``) or by path."""
    if "/" in ref or ref.startswith(("~", ".")):
        path = Path(ref).expanduser().resolve()
    else:
        reg = load_registry()
        path = Path(reg[ref]) if ref in reg else yamato_home() / ref
    if not (path / "team.yaml").is_file():
        raise YamatoError(f"艦が見つかりません: {ref} ({path}/team.yaml がない)")
    return path


# --- files -----------------------------------------------------------------

def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


_lock_depth: dict[str, int] = {}
_lock_files: dict[str, object] = {}


@contextlib.contextmanager
def ship_lock(shipdir: Path):
    """The single write lock of a ship (design §0 I1). Re-entrant within a process."""
    key = str(Path(shipdir).resolve())
    if _lock_depth.get(key):
        _lock_depth[key] += 1
        try:
            yield
        finally:
            _lock_depth[key] -= 1
        return
    f = open(Path(key) / ".lock", "a")
    fcntl.flock(f, fcntl.LOCK_EX)
    _lock_depth[key] = 1
    _lock_files[key] = f
    try:
        yield
    finally:
        _lock_depth[key] = 0
        _lock_files.pop(key, None)
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


# --- time ------------------------------------------------------------------

_DUR_RE = re.compile(r"(\d+)\s*([hms])")


def parse_duration(text) -> int:
    """'3h', '20m', '1h30m', '90s' or a bare number of minutes -> seconds."""
    if isinstance(text, (int, float)):
        return int(text) * 60
    s = str(text).strip().lower()
    if s.isdigit():
        return int(s) * 60
    parts = _DUR_RE.findall(s)
    if not parts or _DUR_RE.sub("", s).strip():
        raise YamatoError(f"時間の書き方が不正です: {text!r} (例: 3h, 20m, 1h30m)")
    mult = {"h": 3600, "m": 60, "s": 1}
    return sum(int(n) * mult[u] for n, u in parts)


def fmt_time(epoch: float | None) -> str:
    if not epoch:
        return "-"
    return time.strftime("%m-%d %H:%M:%S", time.localtime(epoch))


def fmt_span(seconds: float) -> str:
    seconds = int(seconds)
    sign = "-" if seconds < 0 else ""
    seconds = abs(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{sign}{h}h{m:02d}m"
    if m:
        return f"{sign}{m}m{s:02d}s"
    return f"{sign}{s}s"


def today() -> str:
    return time.strftime("%Y-%m-%d")


def append_log(shipdir: Path, seat: str, text: str) -> None:
    """Append one line to the seat's work log (``seats/<seat>/log/<date>.md``)."""
    path = Path(shipdir) / "seats" / seat / "log" / f"{today()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- {time.strftime('%H:%M:%S')} {text}\n")

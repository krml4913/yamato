"""Small shared helpers: errors, paths, the ship lock, atomic writes, durations.

Imported by the per-turn hooks, so keep it to the standard library and cheap
modules only.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
import threading
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
    """A ship is given by name (registry, then ``$YAMATO_HOME/<name>``) or by path.

    ``/`` is the POSIX path marker; Windows paths use ``\\`` too (``os.altsep``), e.g. a
    bare ``C:\\Users\\x\\ship`` typed outside Git Bash (W1, work/windows-research.md §2.1)."""
    if "/" in ref or (os.altsep and os.altsep in ref) or ref.startswith(("~", ".")):
        path = Path(ref).expanduser().resolve()
    else:
        reg = load_registry()
        path = Path(reg[ref]) if ref in reg else yamato_home() / ref
    if not (path / "team.yaml").is_file():
        raise YamatoError(f"艦が見つかりません: {ref} ({path}/team.yaml がない)")
    return path


# --- files -----------------------------------------------------------------

_REPLACE_RETRIES = 20   # Windows only: os.replace onto a file someone else has open
_REPLACE_DELAY = 0.05   # (a virus scanner, or a reader with no lock of its own)


def _replace(src, dst) -> None:
    """``os.replace`` (POSIX rename semantics on both OSes). On Windows, replacing a
    file that another process still has open raises ``PermissionError`` (WinError 5
    / 32, not a POSIX-style rename race); retry briefly instead of failing the write
    outright (windows-research §2.5)."""
    if os.name != "nt":
        os.replace(src, dst)
        return
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_RETRIES - 1:
                raise
            time.sleep(_REPLACE_DELAY)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        # newline="\n": otherwise Windows' text-mode write turns every "\n" already in
        # `text` into "\r\n" (jsonl/markdown records would pick up CRLF, W1 §2.2)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        _replace(tmp, path)
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


_held = threading.local()   # per thread: path -> depth (re-entrancy is per thread)


@contextlib.contextmanager
def ship_lock(shipdir: Path):
    """The single write lock of a ship (design §0 I1). Re-entrant within a thread."""
    key = str(Path(shipdir).resolve())
    depth = getattr(_held, "depth", None)
    if depth is None:
        depth = _held.depth = {}
    if depth.get(key):
        depth[key] += 1
        try:
            yield
        finally:
            depth[key] -= 1
        return
    with _flock(Path(key) / ".lock"):
        depth[key] = 1
        try:
            yield
        finally:
            depth[key] = 0


@contextlib.contextmanager
def seat_lock(shipdir: Path, seat: str):
    """Serialises bringing one seat on shift (check alive -> launch/resume -> roster),
    so two senders cannot start the same seat twice. Separate from ship_lock: a
    launch takes seconds and the new seat's own hooks need ship_lock meanwhile."""
    with _flock(Path(shipdir) / ".runtime" / f"wake-{seat}.lock"):
        yield


@contextlib.contextmanager
def merge_lock(shipdir: Path):
    """``pr merge`` runs one at a time per ship (design-p1 §0.3, §8.3). Separate from
    ship_lock: a merge waits on the network and must not hold up the hooks."""
    with _flock(Path(shipdir) / ".runtime" / "merge.lock"):
        yield


_LOCK_POLL = 0.05   # Windows only: msvcrt.locking has no blocking mode, so lock_file loops


def lock_file(f) -> None:
    """Take an exclusive, whole-file lock on the already-open ``f`` (POSIX: ``fcntl.flock``;
    Windows: ``msvcrt.locking``, which only offers a non-blocking mode (``LK_NBLCK``), so
    this loops until it succeeds -- windows-research §2.1). ``fcntl`` / ``msvcrt`` are
    imported here, not at module level, so importing this module does not fail on the
    other OS (a bare ``import fcntl`` at the top used to make every command fail to start
    on Windows)."""
    if os.name == "nt":
        import msvcrt
        f.seek(0)
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(_LOCK_POLL)
    else:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_EX)


def try_lock_file(f) -> bool:
    """Non-blocking ``lock_file``: True once ``f`` is locked, False if another holder has
    it right now. ``msvcrt.locking(LK_NBLCK)`` already makes one attempt and gives up, so
    it doubles as the Windows side of this (headless.py's own run-lock used to import
    ``fcntl`` at module level for the same POSIX half, W1 §2.1)."""
    if os.name == "nt":
        import msvcrt
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def unlock_file(f) -> None:
    """Release a lock taken with ``lock_file`` / ``try_lock_file``."""
    if os.name == "nt":
        import msvcrt
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_UN)


@contextlib.contextmanager
def _flock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a")
    try:
        lock_file(f)
        yield
    finally:
        unlock_file(f)
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
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(f"- {time.strftime('%H:%M:%S')} {text}\n")


def env_command(name: str, default: str) -> list[str]:
    """``$name`` as a command line: a list, ``[program, *leading args]`` (``YAMATO_CLAUDE=
    "python fake_claude.py"`` -- Windows cannot run a ``.py`` directly, so the tests hand
    over the interpreter too). A value that already names one program -- a path that
    exists (spaces and all) or something ``shutil.which`` finds -- is taken whole, never
    split. The program is resolved with ``which`` (npm's ``claude.cmd`` shim, W1)."""
    import shlex
    import shutil
    raw = os.environ.get(name) or default
    if shutil.which(raw) or os.path.exists(raw):
        return [shutil.which(raw) or raw]
    try:
        parts = shlex.split(raw, posix=os.name != "nt")
    except ValueError:
        return [raw]
    if os.name == "nt":   # non-POSIX split keeps the quotes on
        parts = [p[1:-1] if len(p) > 1 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]
    if not parts:
        return [default]
    return [shutil.which(parts[0]) or parts[0], *parts[1:]]

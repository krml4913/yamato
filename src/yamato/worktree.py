"""``yamato worktree add/path/list/rm``: a work place per board item (design-p1 §8.2).

A tool only (mechanism-not-policy): yamato never assigns worktrees to seats
and never checks who calls. Who creates one, when, and whether a seat ``cd``s
into it are the role prompts' business. The one safety net is ``rm`` refusing
to throw away uncommitted or unpushed work unless ``--force``.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import roster
from .board import Board
from .team import git_conf
from .util import YamatoError


def git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    try:
        cp = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise YamatoError("git コマンドが見つかりません") from None
    except subprocess.TimeoutExpired:
        raise YamatoError(f"git {' '.join(args[:2])} がタイムアウトしました") from None
    if check and cp.returncode != 0:
        raise YamatoError(f"git {' '.join(args)} が失敗しました: {(cp.stderr or cp.stdout).strip()}")
    return cp


def caller(shipdir: Path, by: str | None) -> str:
    """Who ran the command, for the record only (design-p1 §0.3): ``--by``, else the seat
    whose shift is ``$CLAUDE_CODE_SESSION_ID``, else a human at a terminal (owner)."""
    if by:
        return by
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        return "owner"
    return roster.seat_of_session(shipdir, sid) or "?"


def repo_root(team: dict) -> Path:
    from .claude import git_root

    root = git_root(Path(team["workspace"]))
    if root is None:
        raise YamatoError(f"workspace が git repo ではないので worktree は使えません: {team['workspace']}")
    return root


def default_path(shipdir: Path, item_id: str) -> Path:
    return Path(shipdir) / "worktrees" / item_id


def default_branch(team: dict, item_id: str) -> str:
    return f"yamato/{team['name']}/{item_id}"


def registered(root: Path) -> dict[Path, str | None]:
    """``git worktree list``: path -> branch (None when detached)."""
    out: dict[Path, str | None] = {}
    path = None
    for line in git(["worktree", "list", "--porcelain"], root).stdout.splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree "):]).resolve()
            out[path] = None
        elif line.startswith("branch ") and path is not None:
            out[path] = line[len("branch "):].removeprefix("refs/heads/")
    return out


def _ref_exists(root: Path, ref: str) -> bool:
    return git(["rev-parse", "--verify", "--quiet", ref], root, check=False).returncode == 0


def _has_remote(root: Path, name: str = "origin") -> bool:
    return name in git(["remote"], root).stdout.split()


def _base_ref(root: Path, team: dict, base: str | None) -> str:
    """``--base`` as given; otherwise ``origin/<git.base>`` (fetched, best effort), else ``<git.base>``."""
    if base:
        return base
    b = git_conf(team)["base"]
    if _has_remote(root):
        git(["fetch", "--quiet", "origin", b], root, check=False)
        if _ref_exists(root, f"refs/remotes/origin/{b}"):
            return f"origin/{b}"
    return b


def add(shipdir: Path, team: dict, item_id: str, branch: str | None = None, base: str | None = None,
        path: str | None = None, by: str | None = None) -> tuple[Path, bool]:
    """Create (or find) the item's worktree. Returns (path, created)."""
    brd = Board(shipdir, team)
    meta, _, _ = brd.read(item_id)
    root = repo_root(team)
    branch = branch or meta.get("branch") or default_branch(team, item_id)
    if path:
        wt = Path(path).expanduser().resolve()
    elif meta.get("worktree"):
        wt = Path(meta["worktree"]).resolve()
    else:
        wt = default_path(shipdir, item_id).resolve()
    known = registered(root)
    created = False
    if wt in known:
        if known[wt] != branch:
            raise YamatoError(f"{wt} は既に worktree で、ブランチが {known[wt]} です ({branch} ではない)")
    else:
        if wt.exists() and any(wt.iterdir()):
            raise YamatoError(f"{wt} は既にあって空ではなく、worktree でもありません")
        wt.parent.mkdir(parents=True, exist_ok=True)
        if _ref_exists(root, f"refs/heads/{branch}"):
            git(["worktree", "add", str(wt), branch], root)
        elif _has_remote(root) and _ref_exists(root, f"refs/remotes/origin/{branch}"):
            git(["worktree", "add", "--track", "-b", branch, str(wt), f"origin/{branch}"], root)
        else:
            ref = _base_ref(root, team, base)
            git(["worktree", "add", "--no-track", "-b", branch, str(wt), ref], root)
        created = True
    if meta.get("worktree") != str(wt) or meta.get("branch") != branch:
        brd.set(item_id, {"worktree": str(wt), "branch": branch},
                note=f"worktree {wt} (ブランチ {branch})" if created else None, by=caller(shipdir, by))
    return wt, created


def path_of(shipdir: Path, team: dict, item_id: str) -> Path:
    meta, _, _ = Board(shipdir, team).read(item_id)
    wt = Path(meta["worktree"]) if meta.get("worktree") else default_path(shipdir, item_id)
    if not wt.is_dir():
        raise YamatoError(f"{item_id} の worktree がありません (`yamato worktree add` で作る)")
    return wt.resolve()


def unpushed(wt: Path) -> int:
    """Commits on HEAD that no remote-tracking ref has (0 when the branch is pushed)."""
    cp = git(["rev-list", "--count", "HEAD", "--not", "--remotes"], wt, check=False)
    return int(cp.stdout.strip() or 0) if cp.returncode == 0 else 0


def dirty(wt: Path) -> list[str]:
    return git(["status", "--porcelain"], wt).stdout.splitlines()


def listing(shipdir: Path, team: dict) -> list[dict]:
    """The ship's worktrees: those under ``worktrees/`` and those a board item points at."""
    root = repo_root(team)
    by_path = {}
    for meta in Board(shipdir, team).items(include_archive=True):
        if meta.get("worktree"):
            by_path[Path(meta["worktree"]).resolve()] = meta["id"]
    home = (Path(shipdir) / "worktrees").resolve()
    out = []
    for wt, branch in registered(root).items():
        if wt not in by_path and home not in wt.parents:
            continue
        out.append({"item": by_path.get(wt, wt.name), "path": wt, "branch": branch,
                    "unpushed": unpushed(wt) if wt.is_dir() else 0,
                    "dirty": len(dirty(wt)) if wt.is_dir() else 0})
    return out


def rm(shipdir: Path, team: dict, item_id: str, force: bool = False, by: str | None = None) -> Path:
    brd = Board(shipdir, team)
    meta, _, _ = brd.read(item_id)
    root = repo_root(team)
    wt = (Path(meta["worktree"]) if meta.get("worktree") else default_path(shipdir, item_id)).resolve()
    if wt not in registered(root):
        raise YamatoError(f"{item_id} の worktree がありません ({wt})")
    if not force:
        changes = dirty(wt)
        if changes:
            raise YamatoError(f"{wt} に commit していない変更が {len(changes)} 件あるので消さない (--force で消す)")
        n = unpushed(wt)
        if n:
            raise YamatoError(f"{wt} に push していない commit が {n} 件あるので消さない "
                              f"(merge 済みでリモートのブランチが消えているなら --force で消す)")
    git(["worktree", "remove", *(["--force"] if force else []), str(wt)], root)
    brd.set(item_id, {"worktree": ""}, note=f"worktree {wt} を片付けた" + (" (--force)" if force else ""),
            by=caller(shipdir, by))
    return wt


# --- cli ---------------------------------------------------------------------

def register(sub) -> None:
    w = sub.add_parser("worktree", help="項目ごとの作業場所 (git worktree) を作る・探す・片付ける")
    ws = w.add_subparsers(dest="worktree_cmd", required=True)
    a = ws.add_parser("add", help="作る (既にあればそのパスを出す)。項目に worktree と branch を書く")
    a.add_argument("ship")
    a.add_argument("item")
    a.add_argument("--branch", help="既定は項目の branch、無ければ yamato/<ship>/<item>")
    a.add_argument("--base", help="新しいブランチの起点 (既定は origin/<git.base>、無ければ <git.base>)")
    a.add_argument("--path", help="場所 (既定は艦フォルダの worktrees/<item>/)")
    a.add_argument("--by")
    p = ws.add_parser("path", help="項目の worktree のパスを出す (無ければ失敗)")
    p.add_argument("ship")
    p.add_argument("item")
    ls = ws.add_parser("list", help="艦の worktree の一覧")
    ls.add_argument("ship")
    r = ws.add_parser("rm", help="片付ける (未 commit・未 push があれば断る)")
    r.add_argument("ship")
    r.add_argument("item")
    r.add_argument("--force", action="store_true", help="未 commit・未 push でも消す")
    r.add_argument("--by")


def run(args) -> int:
    from .seat import current_team
    from .util import resolve_ship

    shipdir = resolve_ship(args.ship)
    team = current_team(shipdir)
    if args.worktree_cmd == "add":
        wt, created = add(shipdir, team, args.item, args.branch, args.base, args.path, args.by)
        print(wt)
    elif args.worktree_cmd == "path":
        print(path_of(shipdir, team, args.item))
    elif args.worktree_cmd == "list":
        rows = listing(shipdir, team)
        if not rows:
            print("(worktree なし)")
        for r in rows:
            flags = []
            if r["dirty"]:
                flags.append(f"未 commit {r['dirty']}")
            if r["unpushed"]:
                flags.append(f"未 push {r['unpushed']}")
            print(f"{r['item']}  {r['branch'] or '(detached)'}  {r['path']}"
                  + (f"  ({', '.join(flags)})" if flags else ""))
    elif args.worktree_cmd == "rm":
        print(f"片付けた: {rm(shipdir, team, args.item, args.force, args.by)}")
    return 0

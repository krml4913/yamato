"""``yamato worktree add/path/list/rm``: a work place per board item (design-p1 §8.2).

A tool only (mechanism-not-policy): yamato never assigns worktrees to seats
and never checks who calls. Who creates one, when, and whether a seat ``cd``s
into it are the role prompts' business. The one safety net is ``rm`` refusing
to throw away uncommitted or unpushed work unless ``--force``.

``add`` / ``rm`` leave one line each in events.jsonl (docs/events.md), success or
refusal; a record only, so who called is written down and never checked.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import events, roster
from .board import Board
from .team import git_conf
from .util import YamatoError

WORKTREE_ADD = "worktree_add"                  # events.jsonl kinds (docs/events.md)
WORKTREE_ADD_FAILED = "worktree_add_failed"
WORKTREE_RM = "worktree_rm"
WORKTREE_RM_FAILED = "worktree_rm_failed"
REASON_CHARS = 300                             # a failure's reason in ``data``: one line, cut here


def git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    try:
        cp = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=120)
    except FileNotFoundError:
        raise YamatoError("git コマンドが見つかりません") from None
    except subprocess.TimeoutExpired:
        raise YamatoError(f"git {' '.join(args[:2])} がタイムアウトしました") from None
    if check and cp.returncode != 0:
        raise YamatoError(f"git {' '.join(args)} が失敗しました: {(cp.stderr or cp.stdout).strip()}")
    return cp


def caller(shipdir: Path, by: str | None) -> str:
    """Who ran the command, for the record only (design-p1 §0.3): ``--by``, else the seat
    whose shift is ``$CLAUDE_CODE_SESSION_ID``, else a human (owner, at a terminal or in
    their own Claude Code session, which is not in the roster)."""
    if by:
        return by
    return roster.seat_of_session(shipdir, os.environ.get("CLAUDE_CODE_SESSION_ID")) or "owner"


def clip(text) -> str:
    return " ".join(str(text).split())[:REASON_CHARS]


def record(shipdir: Path, kind: str, item_id: str, who: str, summary: str, meta: dict | None = None,
           **data) -> None:
    """One events.jsonl line for a worktree / pr operation. ``seat`` is the item's assignee."""
    events.emit(shipdir, kind, seat=(meta or {}).get("assignee"), item=item_id, by=who, summary=summary,
                data={k: v for k, v in data.items() if v is not None})


def record_failure(shipdir: Path, kind: str, item_id: str, who: str, err, meta: dict | None = None,
                   **data) -> None:
    reason = clip(err)
    record(shipdir, kind, item_id, who, f"{item_id}: {reason}", meta, reason=reason, **data)


def _meta(shipdir: Path, team: dict, item_id: str) -> dict:
    try:
        return Board(shipdir, team).read(item_id)[0]
    except YamatoError:
        return {}


def repos(team: dict) -> list[dict]:
    """The ship's repos, ``[{name, path}]`` (the first is the seat's cwd). A ship from before
    ``workspaces`` existed has the one ``workspace``."""
    return team.get("workspaces") or [{"name": Path(team["workspace"]).name, "path": team["workspace"]}]


def multi(team: dict) -> bool:
    """More than one repo: the item's worktree / branch / pr / merged_by are then ``{repo: value}`` maps."""
    return len(repos(team)) > 1


def pick_repo(team: dict, name: str | None = None) -> dict:
    """The repo called ``name`` (its basename); no name means the first."""
    rs = repos(team)
    if name is None:
        return rs[0]
    for r in rs:
        if r["name"] == name:
            return r
    raise YamatoError(f"repo {name!r} は workspace にありません ({', '.join(r['name'] for r in rs)})")


def field_map(team: dict, meta: dict, key: str) -> dict[str, str]:
    """An item's per-repo field as ``{repo: value}``. A plain string (the one-repo form, or a
    value set by hand with ``board set``) is the first repo's."""
    v = meta.get(key)
    if isinstance(v, dict):
        return {k: str(x) for k, x in v.items() if x not in (None, "")}
    return {repos(team)[0]["name"]: str(v)} if v not in (None, "") else {}


def field_get(team: dict, meta: dict, key: str, repo: dict) -> str | None:
    return field_map(team, meta, key).get(repo["name"])


def field_put(team: dict, meta: dict, key: str, repo: dict, value: str | None):
    """The value to ``board set`` for ``key`` after ``repo``'s part becomes ``value`` (None / "" removes
    it). One-repo ships keep a plain string; several repos keep a map."""
    if not multi(team):
        return value or ""
    cur = field_map(team, meta, key)
    if value:
        cur[repo["name"]] = value
    else:
        cur.pop(repo["name"], None)
    return cur or ""


def repo_root(team: dict, repo: dict | None = None) -> Path:
    from .claude import git_root

    ws = (repo or repos(team)[0])["path"]
    root = git_root(Path(ws))
    if root is None:
        raise YamatoError(f"workspace が git repo ではないので worktree は使えません: {ws}")
    return root


def default_path(shipdir: Path, item_id: str, team: dict | None = None, repo: dict | None = None) -> Path:
    """``worktrees/<id>`` (one repo), ``worktrees/<id>/<repo>`` (several: the repos stay side by side,
    so ``../lib`` from the app's worktree is the same task's lib worktree)."""
    base = Path(shipdir) / "worktrees" / item_id
    return base / repo["name"] if team is not None and repo is not None and multi(team) else base


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
        path: str | None = None, by: str | None = None, repo: str | None = None) -> tuple[Path, bool]:
    """Create (or find) the item's worktree in one repo (``repo``: its name, default the first).
    Returns (path, created).

    A new worktree and a failure are recorded in events.jsonl; finding the one
    that is already there is not (nothing happened)."""
    who = caller(shipdir, by)
    r = pick_repo(team, repo)
    extra = {"repo": r["name"]} if multi(team) else {}
    try:
        wt, created = _add(shipdir, team, item_id, branch, base, path, by, r)
    except YamatoError as e:
        record_failure(shipdir, WORKTREE_ADD_FAILED, item_id, who, e, _meta(shipdir, team, item_id), **extra)
        raise
    if created:
        meta = _meta(shipdir, team, item_id)
        br = field_get(team, meta, "branch", r)
        record(shipdir, WORKTREE_ADD, item_id, who,
               f"{item_id} の worktree を作った ({br})" + (f" [{r['name']}]" if extra else ""), meta,
               path=str(wt), branch=br, **extra)
    return wt, created


def _add(shipdir: Path, team: dict, item_id: str, branch: str | None, base: str | None,
         path: str | None, by: str | None, r: dict) -> tuple[Path, bool]:
    brd = Board(shipdir, team)
    meta, _, _ = brd.read(item_id)
    root = repo_root(team, r)
    branch = branch or field_get(team, meta, "branch", r) or default_branch(team, item_id)
    # a value starting with `-` would be read as an option by `git worktree add`
    if branch.startswith("-") or git(["check-ref-format", "--branch", branch], root, check=False).returncode:
        raise YamatoError(f"ブランチ名が不正です: {branch!r}")
    recorded = field_get(team, meta, "worktree", r)
    if path:
        wt = Path(path).expanduser().resolve()
    elif recorded:
        wt = Path(recorded).resolve()
    else:
        wt = default_path(shipdir, item_id, team, r).resolve()
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
    if recorded != str(wt) or field_get(team, meta, "branch", r) != branch:
        where = f" [{r['name']}]" if multi(team) else ""
        brd.set(item_id, {"worktree": field_put(team, meta, "worktree", r, str(wt)),
                          "branch": field_put(team, meta, "branch", r, branch)},
                note=f"worktree{where} {wt} (ブランチ {branch})" if created else None, by=caller(shipdir, by))
    return wt, created


def path_of(shipdir: Path, team: dict, item_id: str, repo: str | None = None) -> Path:
    r = pick_repo(team, repo)
    meta, _, _ = Board(shipdir, team).read(item_id)
    rec = field_get(team, meta, "worktree", r)
    wt = Path(rec) if rec else default_path(shipdir, item_id, team, r)
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
    """The ship's worktrees, per repo: those under ``worktrees/`` and those a board item points at."""
    by_path: dict[tuple[str, Path], str] = {}
    for meta in Board(shipdir, team).items(include_archive=True):
        for name, wt in field_map(team, meta, "worktree").items():
            by_path[(name, Path(wt).resolve())] = meta["id"]
    home = (Path(shipdir) / "worktrees").resolve()
    out = []
    for r in repos(team):
        for wt, branch in registered(repo_root(team, r)).items():
            if (r["name"], wt) not in by_path and home not in wt.parents:
                continue
            out.append({"item": by_path.get((r["name"], wt), wt.name), "path": wt, "branch": branch,
                        "repo": r["name"] if multi(team) else None,
                        "unpushed": unpushed(wt) if wt.is_dir() else 0,
                        "dirty": len(dirty(wt)) if wt.is_dir() else 0})
    return out


def rm(shipdir: Path, team: dict, item_id: str, force: bool = False, by: str | None = None,
       repo: str | None = None) -> Path:
    who = caller(shipdir, by)
    r = pick_repo(team, repo)
    extra = {"repo": r["name"]} if multi(team) else {}
    try:
        wt = _rm(shipdir, team, item_id, force, by, r)
    except YamatoError as e:
        record_failure(shipdir, WORKTREE_RM_FAILED, item_id, who, e, _meta(shipdir, team, item_id), force=force,
                       **extra)
        raise
    meta = _meta(shipdir, team, item_id)
    record(shipdir, WORKTREE_RM, item_id, who,
           f"{item_id} の worktree を片付けた" + (f" [{r['name']}]" if extra else "") + (" (--force)" if force else ""),
           meta, path=str(wt), branch=field_get(team, meta, "branch", r), force=force, **extra)
    return wt


def _rm(shipdir: Path, team: dict, item_id: str, force: bool, by: str | None, r: dict) -> Path:
    brd = Board(shipdir, team)
    meta, _, _ = brd.read(item_id)
    root = repo_root(team, r)
    rec = field_get(team, meta, "worktree", r)
    wt = (Path(rec) if rec else default_path(shipdir, item_id, team, r)).resolve()
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
    where = f" [{r['name']}]" if multi(team) else ""
    brd.set(item_id, {"worktree": field_put(team, meta, "worktree", r, None)},
            note=f"worktree{where} {wt} を片付けた" + (" (--force)" if force else ""), by=caller(shipdir, by))
    return wt


# --- cli ---------------------------------------------------------------------

def per_repo(team: dict, repos_: list[str] | None, values: list[str] | None, what: str) -> dict[str, str]:
    """``--branch`` / ``--base`` per repo: ``<repo>=<value>`` for one repo, a bare value for every
    selected repo that has none of its own."""
    names = [pick_repo(team, n)["name"] for n in (repos_ or [None])]
    out: dict[str, str] = {}
    bare = None
    for v in values or []:
        head, sep, rest = v.partition("=")
        if sep and head in {r["name"] for r in repos(team)}:
            out[head] = rest
        else:
            bare = v
    if bare is not None:
        for n in names:
            out.setdefault(n, bare)
    stray = sorted(set(out) - set(names))
    if stray:
        raise YamatoError(f"--{what} に --repo で選んでいない repo があります: {', '.join(stray)}")
    return out


def register(sub) -> None:
    w = sub.add_parser("worktree", help="項目ごとの作業場所 (git worktree) を作る・探す・片付ける")
    ws = w.add_subparsers(dest="worktree_cmd", required=True)
    a = ws.add_parser("add", help="作る (既にあればそのパスを出す)。項目に worktree と branch を書く")
    a.add_argument("ship")
    a.add_argument("item")
    a.add_argument("--repo", action="append", help="対象の repo の呼び名 (workspace の basename)。繰り返せる。"
                   "省略時は先頭の repo")
    a.add_argument("--branch", action="append", help="既定は項目の branch、無ければ yamato/<ship>/<item>。"
                   "複数 repo では <repo>=<branch> で repo ごとに付けられる")
    a.add_argument("--base", action="append", help="新しいブランチの起点 (既定は origin/<git.base>、無ければ <git.base>)。"
                   "<repo>=<ref> で repo ごと")
    a.add_argument("--path", help="場所 (既定は艦フォルダの worktrees/<item>/、複数 repo の艦は worktrees/<item>/<repo>/)。"
                   "repo 1 つのときだけ")
    a.add_argument("--by")
    p = ws.add_parser("path", help="項目の worktree のパスを出す (無ければ失敗)")
    p.add_argument("ship")
    p.add_argument("item")
    p.add_argument("--repo", help="repo の呼び名 (省略時は先頭)")
    ls = ws.add_parser("list", help="艦の worktree の一覧")
    ls.add_argument("ship")
    r = ws.add_parser("rm", help="片付ける (未 commit・未 push があれば断る)")
    r.add_argument("ship")
    r.add_argument("item")
    r.add_argument("--repo", action="append", help="repo の呼び名。繰り返せる。省略時は先頭")
    r.add_argument("--force", action="store_true", help="未 commit・未 push でも消す")
    r.add_argument("--by")


def run(args) -> int:
    from .seat import current_team
    from .util import resolve_ship

    shipdir = resolve_ship(args.ship)
    team = current_team(shipdir)
    if args.worktree_cmd == "add":
        names = list(dict.fromkeys(pick_repo(team, n)["name"] for n in (args.repo or [None])))
        if args.path and len(names) > 1:
            raise YamatoError("--path は repo を 1 つだけ選んだときに使えます")
        branches = per_repo(team, names, args.branch, "branch")
        bases = per_repo(team, names, args.base, "base")
        for n in names:
            wt, created = add(shipdir, team, args.item, branches.get(n), bases.get(n), args.path, args.by, n)
            print(f"{n}  {wt}" if len(names) > 1 else wt)
    elif args.worktree_cmd == "path":
        print(path_of(shipdir, team, args.item, args.repo))
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
            print(f"{r['item']}  " + (f"{r['repo']}  " if r["repo"] else "")
                  + f"{r['branch'] or '(detached)'}  {r['path']}"
                  + (f"  ({', '.join(flags)})" if flags else ""))
    elif args.worktree_cmd == "rm":
        for n in dict.fromkeys(pick_repo(team, n)["name"] for n in (args.repo or [None])):
            print(f"片付けた: {rm(shipdir, team, args.item, args.force, args.by, n)}")
    return 0

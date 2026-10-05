"""``yamato pr open/merge``: a PR per board item (design-p1 §8.3).

Tools only (mechanism-not-policy): who opens or merges is the role prompts'
business, so the caller is recorded (``merged_by``) and never checked. Two
things are enforced: the ship's ``git.merge_requires`` (its values come from the
template's team.yaml; ``[]`` checks nothing) and merges running one at a time
(record integrity, design-p1 §0.3).

``gh`` is called as ``$YAMATO_GH`` (default ``gh``) with the caller's own
environment, so tests can swap it for a stand-in.

Each open / merge, each refusal or failure of them, and each conflict found after a
merge leaves one line in events.jsonl (docs/events.md).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from .board import Board
from .team import git_conf
from .util import YamatoError, env_command, merge_lock
from .worktree import (caller, clip, field_get, field_map, field_put, multi, pick_repo, record, record_failure,
                       repo_root, repos)

_PR_URL_RE = re.compile(r"https?://\S+/pull/(\d+)")
MERGEABLE_TRIES = 5      # GitHub computes `mergeable` lazily after a push to the base
MERGEABLE_WAIT = 3.0

PR_OPEN = "pr_open"                  # events.jsonl kinds (docs/events.md)
PR_OPEN_FAILED = "pr_open_failed"
PR_MERGE = "pr_merge"
PR_MERGE_FAILED = "pr_merge_failed"  # also when git.merge_requires is not met (data.unmet)
PR_CONFLICT = "pr_conflict"


def gh_cmd() -> list[str]:
    """``$YAMATO_GH`` as a command line (``[program, *leading args]``, default ``["gh"]``)."""
    return env_command("YAMATO_GH", "gh")


def gh_bin() -> str:
    return gh_cmd()[0]


def gh(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    try:
        cp = subprocess.run([*gh_cmd(), *args], cwd=str(cwd), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=180)
    except FileNotFoundError:
        raise YamatoError(f"gh コマンドが見つかりません ({gh_bin()})") from None
    except subprocess.TimeoutExpired:
        raise YamatoError(f"gh {' '.join(args[:2])} がタイムアウトしました") from None
    if check and cp.returncode != 0:
        raise YamatoError(f"gh {' '.join(args[:2])} が失敗しました: {(cp.stderr or cp.stdout).strip()}")
    return cp


def _cwd(team: dict, meta: dict, r: dict | None = None) -> Path:
    r = r or repos(team)[0]
    wt = field_get(team, meta, "worktree", r)
    return Path(wt) if wt and Path(wt).is_dir() else repo_root(team, r)


def _label(team: dict, r: dict) -> str:
    """`` [app]`` in a several-repo ship, else nothing (messages and notes)."""
    return f" [{r['name']}]" if multi(team) else ""


def _targets(team: dict, names: list[str] | None) -> list[dict]:
    return [pick_repo(team, n) for n in dict.fromkeys(names or [None])]


def _column(team: dict, name: str) -> bool:
    return any(c["name"] == name for c in team.get("board", {}).get("columns") or [])


def _send(shipdir: Path, team: dict, to: str, text: str, sender: str) -> None:
    from . import seat

    if to != "owner" and to not in team["seats"]:
        print(f"注意: 宛先 {to} は席ではないので知らせなかった: {text}")
        return
    seat.send(shipdir, to, text, sender)


# --- open --------------------------------------------------------------------

def _ahead(team: dict, meta: dict, r: dict) -> bool:
    """The repo's worktree branch has commits the base does not (a PR is worth opening)."""
    wt = field_get(team, meta, "worktree", r)
    if not wt or not Path(wt).is_dir():
        return False
    b = git_conf(team)["base"]
    ref = f"origin/{b}" if _git_ok(Path(wt), f"refs/remotes/origin/{b}") else b
    cp = subprocess.run(["git", "rev-list", "--count", f"{ref}..HEAD"], cwd=wt, capture_output=True, text=True)
    return cp.returncode == 0 and int(cp.stdout.strip() or 0) > 0


def _git_ok(cwd: Path, ref: str) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], cwd=cwd,
                          capture_output=True).returncode == 0


def open_pr(shipdir: Path, team: dict, item_id: str, title: str | None = None, body: str | None = None,
            draft: bool = False, by: str | None = None, repo: list[str] | None = None) -> int:
    """One PR per repo. ``repo`` names them; none named means the first repo, and in a several-repo ship
    every repo whose worktree has commits ahead of the base and no PR yet."""
    if repo is None and multi(team):
        meta = Board(shipdir, team).read(item_id)[0]
        have = field_map(team, meta, "pr")
        chosen = [r for r in repos(team) if r["name"] not in have and _ahead(team, meta, r)]
        if not chosen:
            raise YamatoError(f"{item_id}: PR を開く repo がありません (worktree に base より進んだ commit があって"
                              "PR が無い repo が対象。`--repo <呼び名>` で指す)")
    else:
        chosen = _targets(team, repo)
    for r in chosen:
        _open_one(shipdir, team, item_id, title, body, draft, by, r)
    return 0


def _open_one(shipdir: Path, team: dict, item_id: str, title: str | None, body: str | None, draft: bool,
              by: str | None, r: dict) -> None:
    brd = Board(shipdir, team)
    who = caller(shipdir, by)
    lab = _label(team, r)
    extra = {"repo": r["name"]} if multi(team) else {}
    meta: dict = {}
    try:
        meta, _, _ = brd.read(item_id)
        if field_get(team, meta, "pr", r):
            print(f"{item_id} の PR は既にある{lab}: #{field_get(team, meta, 'pr', r)}")
            return
        branch = field_get(team, meta, "branch", r)
        if not branch:
            raise YamatoError(f"{item_id} に branch がありません{lab} (`yamato worktree add` か `board set {item_id} branch=...`)")
        args = ["pr", "create", "--base", git_conf(team)["base"], "--head", branch,
                "--title", title or f"{item_id} {meta.get('title')}",
                "--body", body if body is not None else f"yamato 艦 {team['name']} の {item_id}"]
        if draft:
            args.append("--draft")
        cp = gh(args, _cwd(team, meta, r))
        m = _PR_URL_RE.search(cp.stdout or "")
        if not m:
            raise YamatoError(f"gh pr create の出力から PR の番号を読めません: {(cp.stdout or '').strip()}")
        url, number = m.group(0), m.group(1)
        fields = {"pr": field_put(team, meta, "pr", r, number)}
        if _column(team, "review"):
            fields["column"] = "review"
        brd.set(item_id, fields, note=f"PR #{number} を開いた{lab}: {url}", by=who)
    except YamatoError as e:
        record_failure(shipdir, PR_OPEN_FAILED, item_id, who, e, meta, **extra)
        raise
    record(shipdir, PR_OPEN, item_id, who, f"{item_id} の PR #{number} を開いた{lab}", meta,
           pr=number, url=url, branch=branch, column=fields.get("column"), draft=draft or None, **extra)
    print(f"PR #{number} を開いた{lab}: {url}")
    to = meta.get("reviewer") or team["hub"]
    if to != who:
        _send(shipdir, team, to, f"{item_id} の PR #{number} を開いた{lab} (ブランチ {branch}): {url}", who)


# --- merge -------------------------------------------------------------------

def unmet(shipdir: Path, team: dict, meta: dict, r: dict | None = None) -> list[str]:
    """The ship's ``git.merge_requires`` that this item's PR (in repo ``r``, default the first) does not meet
    (reasons). ``review`` / ``decision`` are the item's, the same for each repo; ``ci`` is the repo's PR's."""
    reasons = []
    r = r or repos(team)[0]
    item_id, number = meta["id"], field_get(team, meta, "pr", r)
    for req in git_conf(team)["merge_requires"]:
        if req == "review" and meta.get("review") != "approved":
            reasons.append(f"review: 項目に review=approved がない (`board set {item_id} review=approved` で記録する)")
        elif req == "ci":
            cp = gh(["pr", "checks", str(number)], _cwd(team, meta, r), check=False)
            text = ((cp.stdout or "") + (cp.stderr or "")).strip()
            if cp.returncode != 0 and "no checks reported" not in text:
                reasons.append(f"ci: PR #{number} のチェックが通っていない (gh pr checks):\n{text}")
        elif req == "decision":
            from .decide import decisions

            brd = Board(shipdir, team)
            # D-NNN from `decide open`, and decision items made by hand with `board add kind=decision`
            items = decisions(brd) + [d for d in brd.items() if d.get("kind") == "decision"]
            open_ = [d["id"] for d in items if d.get("category") == "merge"
                     and item_id in (d.get("links") or []) and d.get("state") != "done"]
            if open_:
                reasons.append(f"decision: merge の判断 {', '.join(open_)} が閉じていない")
    return reasons


def _pr_state(team: dict, meta: dict, number, r: dict | None = None) -> dict:
    cp = gh(["pr", "view", str(number), "--json", "state,mergeable"], _cwd(team, meta, r), check=False)
    try:
        return json.loads(cp.stdout) if cp.returncode == 0 else {}
    except ValueError:
        return {}


def merge_pr(shipdir: Path, team: dict, item_id: str, by: str | None = None, repo: list[str] | None = None) -> int:
    """Merge the item's PRs, repo by repo in the order given (yamato does not order them: when one repo's
    merge has to come first is the team's business). None named: the first repo; in a several-repo ship
    every repo with an open PR."""
    if repo is None and multi(team):
        meta = Board(shipdir, team).read(item_id)[0]
        done = field_map(team, meta, "merged_by")
        chosen = [r for r in repos(team) if field_get(team, meta, "pr", r) and r["name"] not in done]
        if not chosen:
            raise YamatoError(f"{item_id} に merge する PR がありません (`yamato pr open` か `board set {item_id} pr=<番号>`)")
    else:
        chosen = _targets(team, repo)
    for r in chosen:
        _merge_one(shipdir, team, item_id, by, r)
    return 0


def _merge_one(shipdir: Path, team: dict, item_id: str, by: str | None, r: dict) -> None:
    brd = Board(shipdir, team)
    who = caller(shipdir, by)
    lab = _label(team, r)
    extra = {"repo": r["name"]} if multi(team) else {}
    meta: dict = {}
    refused: list[str] = []
    try:
        meta, _, _ = brd.read(item_id)
        number = field_get(team, meta, "pr", r)
        if not number:
            raise YamatoError(f"{item_id} に pr がありません{lab} (`yamato pr open` か `board set {item_id} pr=<番号>`)")
        conf = git_conf(team)
        with merge_lock(shipdir):
            refused = unmet(shipdir, team, meta, r)
            if refused:
                raise YamatoError(f"{item_id} (PR #{number}){lab} は merge の条件 (git.merge_requires) を満たしていない:\n- "
                                  + "\n- ".join(refused))
            already = _pr_state(team, meta, number, r).get("state") == "MERGED"
            if already:
                print(f"PR #{number} は既に merge されている{lab}")
            else:
                gh(["pr", "merge", str(number), f"--{conf['strategy']}"], _cwd(team, meta, r))
                print(f"PR #{number} を merge した{lab} ({conf['strategy']})")
            brd.set(item_id, {"merged_by": field_put(team, meta, "merged_by", r, who)},
                    note=f"PR #{number} を merge した{lab}", by=who)
            record(shipdir, PR_MERGE, item_id, who,
                   f"{item_id} の PR #{number} を merge した{lab}" + (" (既に merge 済み)" if already else ""), meta,
                   pr=str(number), strategy=conf["strategy"], mergedBy=who, alreadyMerged=already or None, **extra)
    except YamatoError as e:
        record_failure(shipdir, PR_MERGE_FAILED, item_id, who, e, meta, pr=field_get(team, meta, "pr", r),
                       unmet=[clip(x) for x in refused] or None, **extra)
        raise
    _check_conflicts(shipdir, team, item_id, number, who, r)


def _check_conflicts(shipdir: Path, team: dict, merged_id: str, merged_pr, who: str, r: dict | None = None) -> None:
    """After a merge, tell the owners of other open PRs in the same repo that now conflict (design-p1 §8.3 の 4).
    A merge in one repo cannot conflict a PR of another."""
    r = r or repos(team)[0]
    lab = _label(team, r)
    extra = {"repo": r["name"]} if multi(team) else {}
    brd = Board(shipdir, team)
    conf = git_conf(team)
    for meta in brd.items():
        number = field_get(team, meta, "pr", r)
        if (meta["id"] == merged_id or not number or meta.get("state") == "done"
                or field_get(team, meta, "merged_by", r)):
            continue
        st = {}
        for i in range(MERGEABLE_TRIES):
            st = _pr_state(team, meta, number, r)
            if st.get("mergeable") != "UNKNOWN":
                break
            if i + 1 < MERGEABLE_TRIES:
                time.sleep(MERGEABLE_WAIT)
        if st.get("state") != "OPEN" or st.get("mergeable") != "CONFLICTING":
            if st.get("mergeable") == "UNKNOWN":
                print(f"注意: {meta['id']} の PR #{number}{lab} は衝突の有無がまだ分からない (あとで gh pr view で確かめる)")
            continue
        fields = {"column": "rebase"} if _column(team, "rebase") else {}
        text = (f"{conf['base']} が進んで ({merged_id} の PR #{merged_pr} を merge){lab}、"
                f"{meta['id']} の PR #{number} が衝突している。rebase して push してください")
        brd.set(meta["id"], fields, note=text, by=who)
        print(f"衝突: {meta['id']} の PR #{number}{lab}")
        to = meta.get("assignee") if conf["conflict"] == "author" else conf["conflict"]
        record(shipdir, PR_CONFLICT, meta["id"], who,
               f"{meta['id']} の PR #{number} が衝突{lab} ({merged_id} の PR #{merged_pr} を merge)", meta,
               pr=str(number), mergedItem=merged_id, mergedPr=str(merged_pr), mergedBy=who,
               column=fields.get("column"), notified=to if to and to != who else None, **extra)
        if to and to != who:
            _send(shipdir, team, to, text, who)
        elif not to:
            print(f"注意: {meta['id']} に assignee がないので衝突を知らせなかった")


# --- cli ---------------------------------------------------------------------

def register(sub) -> None:
    p = sub.add_parser("pr", help="項目の PR を開く・merge する (gh を呼ぶ)")
    ps = p.add_subparsers(dest="pr_cmd", required=True)
    o = ps.add_parser("open", help="gh pr create。項目に pr を書き、reviewer (無ければ hub) に知らせる")
    o.add_argument("ship")
    o.add_argument("item")
    o.add_argument("--title", help="既定は '<item> <項目の title>'")
    o.add_argument("--body")
    o.add_argument("--draft", action="store_true")
    o.add_argument("--repo", action="append", help="repo の呼び名 (繰り返せる)。省略時は先頭の repo、"
                   "複数 repo の艦では worktree に base より進んだ commit がある PR 未作成の repo すべて")
    o.add_argument("--by")
    m = ps.add_parser("merge", help="git.merge_requires を確かめて gh pr merge (艦ごとに 1 本ずつ)")
    m.add_argument("ship")
    m.add_argument("item")
    m.add_argument("--repo", action="append", help="repo の呼び名。指した順に 1 本ずつ merge する (順番は仕組みで決めない)。"
                   "省略時は先頭の repo、複数 repo の艦では PR がある repo すべて (workspace の順)")
    m.add_argument("--by")


def run(args) -> int:
    from .seat import current_team
    from .util import resolve_ship

    shipdir = resolve_ship(args.ship)
    team = current_team(shipdir)
    if args.pr_cmd == "open":
        return open_pr(shipdir, team, args.item, args.title, args.body, args.draft, args.by, args.repo)
    return merge_pr(shipdir, team, args.item, args.by, args.repo)

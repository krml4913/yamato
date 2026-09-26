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
from .util import YamatoError, merge_lock
from .worktree import caller, clip, record, record_failure, repo_root

_PR_URL_RE = re.compile(r"https?://\S+/pull/(\d+)")
MERGEABLE_TRIES = 5      # GitHub computes `mergeable` lazily after a push to the base
MERGEABLE_WAIT = 3.0

PR_OPEN = "pr_open"                  # events.jsonl kinds (docs/events.md)
PR_OPEN_FAILED = "pr_open_failed"
PR_MERGE = "pr_merge"
PR_MERGE_FAILED = "pr_merge_failed"  # also when git.merge_requires is not met (data.unmet)
PR_CONFLICT = "pr_conflict"


def gh_bin() -> str:
    return os.environ.get("YAMATO_GH", "gh")


def gh(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    try:
        cp = subprocess.run([gh_bin(), *args], cwd=str(cwd), capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        raise YamatoError(f"gh コマンドが見つかりません ({gh_bin()})") from None
    except subprocess.TimeoutExpired:
        raise YamatoError(f"gh {' '.join(args[:2])} がタイムアウトしました") from None
    if check and cp.returncode != 0:
        raise YamatoError(f"gh {' '.join(args[:2])} が失敗しました: {(cp.stderr or cp.stdout).strip()}")
    return cp


def _cwd(team: dict, meta: dict) -> Path:
    wt = meta.get("worktree")
    return Path(wt) if wt and Path(wt).is_dir() else repo_root(team)


def _column(team: dict, name: str) -> bool:
    return any(c["name"] == name for c in team.get("board", {}).get("columns") or [])


def _send(shipdir: Path, team: dict, to: str, text: str, sender: str) -> None:
    from . import seat

    if to != "owner" and to not in team["seats"]:
        print(f"注意: 宛先 {to} は席ではないので知らせなかった: {text}")
        return
    seat.send(shipdir, to, text, sender)


# --- open --------------------------------------------------------------------

def open_pr(shipdir: Path, team: dict, item_id: str, title: str | None = None, body: str | None = None,
            draft: bool = False, by: str | None = None) -> int:
    brd = Board(shipdir, team)
    who = caller(shipdir, by)
    meta: dict = {}
    try:
        meta, _, _ = brd.read(item_id)
        if meta.get("pr"):
            print(f"{item_id} の PR は既にある: #{meta['pr']}")
            return 0
        branch = meta.get("branch")
        if not branch:
            raise YamatoError(f"{item_id} に branch がありません (`yamato worktree add` か `board set {item_id} branch=...`)")
        args = ["pr", "create", "--base", git_conf(team)["base"], "--head", branch,
                "--title", title or f"{item_id} {meta.get('title')}",
                "--body", body if body is not None else f"yamato 艦 {team['name']} の {item_id}"]
        if draft:
            args.append("--draft")
        cp = gh(args, _cwd(team, meta))
        m = _PR_URL_RE.search(cp.stdout or "")
        if not m:
            raise YamatoError(f"gh pr create の出力から PR の番号を読めません: {(cp.stdout or '').strip()}")
        url, number = m.group(0), m.group(1)
        fields = {"pr": number}
        if _column(team, "review"):
            fields["column"] = "review"
        brd.set(item_id, fields, note=f"PR #{number} を開いた: {url}", by=who)
    except YamatoError as e:
        record_failure(shipdir, PR_OPEN_FAILED, item_id, who, e, meta)
        raise
    record(shipdir, PR_OPEN, item_id, who, f"{item_id} の PR #{number} を開いた", meta,
           pr=number, url=url, branch=branch, column=fields.get("column"), draft=draft or None)
    print(f"PR #{number} を開いた: {url}")
    to = meta.get("reviewer") or team["hub"]
    if to != who:
        _send(shipdir, team, to, f"{item_id} の PR #{number} を開いた (ブランチ {branch}): {url}", who)
    return 0


# --- merge -------------------------------------------------------------------

def unmet(shipdir: Path, team: dict, meta: dict) -> list[str]:
    """The ship's ``git.merge_requires`` that this item does not meet (reasons)."""
    reasons = []
    item_id, number = meta["id"], meta["pr"]
    for req in git_conf(team)["merge_requires"]:
        if req == "review" and meta.get("review") != "approved":
            reasons.append(f"review: 項目に review=approved がない (`board set {item_id} review=approved` で記録する)")
        elif req == "ci":
            cp = gh(["pr", "checks", str(number)], _cwd(team, meta), check=False)
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


def _pr_state(team: dict, meta: dict, number) -> dict:
    cp = gh(["pr", "view", str(number), "--json", "state,mergeable"], _cwd(team, meta), check=False)
    try:
        return json.loads(cp.stdout) if cp.returncode == 0 else {}
    except ValueError:
        return {}


def merge_pr(shipdir: Path, team: dict, item_id: str, by: str | None = None) -> int:
    brd = Board(shipdir, team)
    who = caller(shipdir, by)
    meta: dict = {}
    refused: list[str] = []
    try:
        meta, _, _ = brd.read(item_id)
        number = meta.get("pr")
        if not number:
            raise YamatoError(f"{item_id} に pr がありません (`yamato pr open` か `board set {item_id} pr=<番号>`)")
        conf = git_conf(team)
        with merge_lock(shipdir):
            refused = unmet(shipdir, team, meta)
            if refused:
                raise YamatoError(f"{item_id} (PR #{number}) は merge の条件 (git.merge_requires) を満たしていない:\n- "
                                  + "\n- ".join(refused))
            already = _pr_state(team, meta, number).get("state") == "MERGED"
            if already:
                print(f"PR #{number} は既に merge されている")
            else:
                gh(["pr", "merge", str(number), f"--{conf['strategy']}"], _cwd(team, meta))
                print(f"PR #{number} を merge した ({conf['strategy']})")
            brd.set(item_id, {"merged_by": who}, note=f"PR #{number} を merge した", by=who)
            record(shipdir, PR_MERGE, item_id, who,
                   f"{item_id} の PR #{number} を merge した" + (" (既に merge 済み)" if already else ""), meta,
                   pr=str(number), strategy=conf["strategy"], mergedBy=who, alreadyMerged=already or None)
    except YamatoError as e:
        record_failure(shipdir, PR_MERGE_FAILED, item_id, who, e, meta, pr=meta.get("pr"),
                       unmet=[clip(r) for r in refused] or None)
        raise
    _check_conflicts(shipdir, team, item_id, number, who)
    return 0


def _check_conflicts(shipdir: Path, team: dict, merged_id: str, merged_pr, who: str) -> None:
    """After a merge, tell the owners of other open PRs that now conflict (design-p1 §8.3 の 4)."""
    brd = Board(shipdir, team)
    conf = git_conf(team)
    for meta in brd.items():
        if meta["id"] == merged_id or not meta.get("pr") or meta.get("state") == "done" or meta.get("merged_by"):
            continue
        st = {}
        for i in range(MERGEABLE_TRIES):
            st = _pr_state(team, meta, meta["pr"])
            if st.get("mergeable") != "UNKNOWN":
                break
            if i + 1 < MERGEABLE_TRIES:
                time.sleep(MERGEABLE_WAIT)
        if st.get("state") != "OPEN" or st.get("mergeable") != "CONFLICTING":
            if st.get("mergeable") == "UNKNOWN":
                print(f"注意: {meta['id']} の PR #{meta['pr']} は衝突の有無がまだ分からない (あとで gh pr view で確かめる)")
            continue
        fields = {"column": "rebase"} if _column(team, "rebase") else {}
        text = (f"{conf['base']} が進んで ({merged_id} の PR #{merged_pr} を merge)、"
                f"{meta['id']} の PR #{meta['pr']} が衝突している。rebase して push してください")
        brd.set(meta["id"], fields, note=text, by=who)
        print(f"衝突: {meta['id']} の PR #{meta['pr']}")
        to = meta.get("assignee") if conf["conflict"] == "author" else conf["conflict"]
        record(shipdir, PR_CONFLICT, meta["id"], who,
               f"{meta['id']} の PR #{meta['pr']} が衝突 ({merged_id} の PR #{merged_pr} を merge)", meta,
               pr=str(meta["pr"]), mergedItem=merged_id, mergedPr=str(merged_pr), mergedBy=who,
               column=fields.get("column"), notified=to if to and to != who else None)
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
    o.add_argument("--by")
    m = ps.add_parser("merge", help="git.merge_requires を確かめて gh pr merge (艦ごとに 1 本ずつ)")
    m.add_argument("ship")
    m.add_argument("item")
    m.add_argument("--by")


def run(args) -> int:
    from .seat import current_team
    from .util import resolve_ship

    shipdir = resolve_ship(args.ship)
    team = current_team(shipdir)
    if args.pr_cmd == "open":
        return open_pr(shipdir, team, args.item, args.title, args.body, args.draft, args.by)
    return merge_pr(shipdir, team, args.item, args.by)

"""The memory inventory (design-p1 §3): ``memo``, ``memory status / curate / apply / migrate``.

- the role's memory lives in ``roles/<role>/memory.md`` (shared by the role's
  seats). P0 kept it per seat (``seats/<seat>/memory.md``); ``migrate`` moves
  those in (never deletes: the old file is renamed ``memory.md.migrated``). The
  SessionStart injection runs it before reading, so a P0 ship moves by itself
- candidates: ``yamato memo`` appends one line to the calling seat's
  ``seats/<seat>/memory-inbox.md`` (not read at start)
- ``memory curate``: one ``claude -p`` per role (the run-headless pieces, design-p1
  §4) reads the current memory.md and the candidates and answers with a proposal;
  yamato writes it to ``roles/<role>/memory.proposed.md``. memory.md is untouched
- ``memory apply``: writes the proposal into memory.md. A proposal over
  ``memory.limits`` is refused (safety net). Who calls it is not checked, only
  recorded (events, memory-archive.md). Processed candidates go to
  ``memory-inbox.done/<date>.md``; dropped and overflowing lines to
  ``roles/<role>/memory-archive.md`` (append only)

Who curates, how often and who applies is policy (team.yaml ``memory:``, the
role prompts); the code only offers the tools, the records and the limits
(mechanism-not-policy). Imported by the SessionStart hook: keep the top light.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from pathlib import Path

from . import events, inbox
from .runtime import yamato_invocation
from .util import (YAMATO_BIN, YamatoError, atomic_write, parse_duration, read_json, ship_lock,
                   today, write_json)

# events.jsonl kinds (docs/events.md)
MEMORY_MIGRATE = "memory_migrate"
MEMORY_CURATE = "memory_curate"
MEMORY_APPLY = "memory_apply"

SCOPES = ("role", "ship")
LIMIT_KEYS = ("memory_lines", "memory_chars", "knowledge_lines", "knowledge_chars")
RENAMED_LIMITS = {"memory_bytes": "memory_chars", "knowledge_bytes": "knowledge_chars"}   # before #26
# only what a ship without a `memory:` section gets; the template spells them out. The limits
# are also where the SessionStart injection cuts, in characters as Claude Code counts them:
# both fit the 10,000 characters of the knowledge hook (verify-p0-c Q1)
FALLBACK = {"applier": None, "curate_every": 7 * 86400, "curate_at": 30, "max_duration": 15 * 60,
            "limits": {"memory_lines": 80, "memory_chars": 4000, "knowledge_lines": 120, "knowledge_chars": 5000}}

CURATOR = "memory-curator"                 # the agent name of a curate shift (not a seat)
CURATOR_PROMPT = "_memory-curator.md"      # under roles/ in the ship; `_` keeps it off role names
KILL_WAIT = 30
WATCH_POLL = 0.2   # seconds between time-limit checks while the curate shift runs
MARK_RE = re.compile(r"^<!--\s*yamato:\s*([a-z]+)(?:\s+([A-Za-z0-9_-]+))?\s*-->\s*$")
FENCE_RE = re.compile(r"^```[A-Za-z]*\s*$")


# --- team.yaml `memory:` --------------------------------------------------------

def _days(value, where: str) -> int:
    """'7d', '12h', a bare number of days -> seconds."""
    if isinstance(value, bool):
        raise YamatoError(f"team.yaml: {where} は 7d のような期間")
    if isinstance(value, (int, float)):
        return int(value * 86400)
    s = str(value).strip().lower()
    m = re.fullmatch(r"(\d+)\s*d", s)
    if m:
        return int(m.group(1)) * 86400
    return parse_duration(s)


def validate_conf(raw, roles: dict) -> dict:
    """``memory:`` of team.yaml (design-p1 §0.4, §3.4, §3.5)."""
    raw = raw or {}
    keys = {"applier", "curate_every", "curate_at", "max_duration", "limits"}
    if not isinstance(raw, dict) or set(raw) - keys:
        raise YamatoError(f"team.yaml: memory の項目は {', '.join(sorted(keys))}")
    applier = raw.get("applier")
    if applier is not None and str(applier) not in roles:
        raise YamatoError(f"team.yaml: memory.applier={applier!r} が roles にありません")
    every = _days(raw["curate_every"], "memory.curate_every") if raw.get("curate_every") is not None \
        else FALLBACK["curate_every"]
    at = raw.get("curate_at", FALLBACK["curate_at"])
    if isinstance(at, bool) or not isinstance(at, int) or at < 1:
        raise YamatoError(f"team.yaml: memory.curate_at は 1 以上の整数 (今: {at!r})")
    dur = parse_duration(raw["max_duration"]) if raw.get("max_duration") is not None else FALLBACK["max_duration"]
    if every <= 0 or dur <= 0:
        raise YamatoError("team.yaml: memory.curate_every と memory.max_duration は 0 より長くする")
    limits = raw.get("limits") or {}
    renamed = [k for k in RENAMED_LIMITS if k in limits] if isinstance(limits, dict) else []
    if renamed:
        raise YamatoError("team.yaml: memory.limits の " + " / ".join(f"{k} は {RENAMED_LIMITS[k]}" for k in renamed)
                          + " に改名した (単位もバイトから文字数に変わった。注入を Claude Code の文字数で切るため)")
    if not isinstance(limits, dict) or set(limits) - set(LIMIT_KEYS) or \
            not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in limits.values()):
        raise YamatoError(f"team.yaml: memory.limits は {', '.join(LIMIT_KEYS)} を正の整数で")
    return {"applier": None if applier is None else str(applier), "curate_every": every, "curate_at": at,
            "max_duration": dur, "limits": {**FALLBACK["limits"], **limits}}


def conf(team: dict) -> dict:
    """The validated ``memory:`` (a ``.runtime/team.json`` from before P1-8 has none)."""
    c = {**FALLBACK, **(team.get("memory") or {})}
    c["limits"] = {**FALLBACK["limits"], **(c.get("limits") or {})}
    c["applier"] = c.get("applier") or team["hub"]
    return c


# --- paths ------------------------------------------------------------------------

def role_dir(shipdir: Path, role: str) -> Path:
    return Path(shipdir) / "roles" / role


def memory_path(shipdir: Path, role: str) -> Path:
    return role_dir(shipdir, role) / "memory.md"


def proposed_path(shipdir: Path, role: str) -> Path:
    return role_dir(shipdir, role) / "memory.proposed.md"


def archive_path(shipdir: Path, role: str) -> Path:
    return role_dir(shipdir, role) / "memory-archive.md"


def inbox_path(shipdir: Path, seat: str) -> Path:
    return inbox.seat_dir(shipdir, seat) / "memory-inbox.md"


def knowledge_path(shipdir: Path) -> Path:
    return Path(shipdir) / "knowledge.md"


def knowledge_proposed_path(shipdir: Path) -> Path:
    return Path(shipdir) / "knowledge.proposed.md"


def knowledge_inbox_path(shipdir: Path) -> Path:
    return Path(shipdir) / "knowledge-inbox.md"


def knowledge_archive_path(shipdir: Path) -> Path:
    return Path(shipdir) / "knowledge-archive.md"


def role_seats(team: dict, role: str) -> list[str]:
    return [s for s, spec in team["seats"].items() if spec["role"] == role]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _append(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _stamp(now: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(now))


def _check_role(team: dict, role: str) -> str:
    if role not in team["roles"]:
        raise YamatoError(f"役割がありません: {role} (役割: {', '.join(team['roles'])})")
    return role


# --- P0 -> roles/<role>/memory.md ---------------------------------------------------

def _p0_files(shipdir: Path, team: dict) -> list[tuple[str, str, Path]]:
    out = []
    for seat, spec in team["seats"].items():
        p = inbox.seat_dir(shipdir, seat) / "memory.md"
        if p.is_file():
            out.append((spec["role"], seat, p))
    return out


def migrate(shipdir: Path, team: dict) -> list[str]:
    """Move every ``seats/<seat>/memory.md`` into its role's ``roles/<role>/memory.md``
    (appended, under a line naming the seat). The seat file is renamed
    ``memory.md.migrated``, never deleted; an empty one is just removed."""
    shipdir = Path(shipdir)
    if not _p0_files(shipdir, team):   # the common case, without the lock
        return []
    moved = []
    with ship_lock(shipdir):
        for role, seat, old in _p0_files(shipdir, team):
            text = _read(old)
            if not text.strip():
                old.unlink()
                continue
            dst = memory_path(shipdir, role)
            cur = _read(dst).rstrip("\n")
            atomic_write(dst, (cur + "\n\n" if cur.strip() else "")
                         + f"<!-- seats/{seat}/memory.md から移した ({today()}) -->\n" + text.rstrip("\n") + "\n")
            keep = old.with_name("memory.md.migrated")
            n = 2
            while keep.exists():
                keep = old.with_name(f"memory.md.migrated-{n}")
                n += 1
            old.replace(keep)
            moved.append(f"seats/{seat}/memory.md → roles/{role}/memory.md (元は {keep.name} に残した)")
            events.emit(shipdir, MEMORY_MIGRATE, seat=seat, summary=f"memory.md を roles/{role}/ へ移した",
                        data={"role": role, "lines": len(text.strip().splitlines()), "kept": str(keep)})
    return moved


# --- memo ------------------------------------------------------------------------------

def memo(shipdir: Path, seat: str, text: str, *, item: str | None = None, scope: str = "role",
         now: float | None = None) -> str:
    """One candidate line into the seat's memory-inbox.md (design-p1 §3.2)."""
    text = " ".join(str(text).split())
    if not text:
        raise YamatoError("memo の本文が空です")
    if scope not in SCOPES:
        raise YamatoError(f"--scope は {' / '.join(SCOPES)} のどれか (今: {scope})")
    date = time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))
    item = " ".join(str(item or "").split())
    line = f"- {date} {seat}" + (f" [{item}]" if item else "") + f" ({scope}) {text}"
    with ship_lock(shipdir):
        _append(inbox_path(shipdir, seat), line + "\n")
    return line


def memo_target(ship_ref: str | None, seat_arg: str | None) -> tuple[Path, str]:
    """The calling seat: the shift whose sessionId is ``$CLAUDE_CODE_SESSION_ID`` (in
    ``--ship``, else in every known ship). ``--seat`` names it from outside a seat's
    session (a human); inside one it must agree (a seat writes only its own inbox)."""
    from . import roster
    from .admiral import all_ships
    from .seat import current_team
    from .team import seat_spec
    from .util import resolve_ship

    ships = [resolve_ship(ship_ref)] if ship_ref else list(all_ships().values())
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    for shipdir in ships if sid else ():
        seat = roster.seat_of_session(shipdir, sid)
        if seat:
            if seat_arg and seat_arg != seat:
                raise YamatoError(f"このセッションは席 {seat} のシフトです (--seat {seat_arg} と違う)。"
                                  "memo は自分の席の memory-inbox にだけ書く")
            return shipdir, seat
    if not ship_ref or not seat_arg:
        raise YamatoError("呼び出した席が分かりません。席のセッションの中から実行するか、--ship と --seat を付けてください")
    seat_spec(current_team(ships[0]), seat_arg)
    return ships[0], seat_arg


# --- status --------------------------------------------------------------------------

def _entries(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("- ")]


def candidates(shipdir: Path, team: dict, role: str) -> dict[str, list[str]]:
    return {seat: _entries(_read(inbox_path(shipdir, seat))) for seat in role_seats(team, role)}


def _oldest(cands: dict) -> float | None:
    """The date of the oldest candidate line (``- YYYY-MM-DD ...``), as local midnight."""
    dates = [m.group(1) for v in cands.values() for x in v if (m := re.match(r"^- (\d{4}-\d{2}-\d{2}) ", x))]
    if not dates:
        return None
    try:
        return time.mktime(time.strptime(min(dates), "%Y-%m-%d"))
    except ValueError:
        return None


def last_applied(shipdir: Path, role: str | None) -> float | None:
    """When ``memory apply`` last wrote the role's memory (``None``: knowledge.md)."""
    ts = [e["ts"] for e in events.read(shipdir, kinds=MEMORY_APPLY)
          if (e.get("data") or {}).get("role") == role]
    return max(ts) if ts else None


def _removed(old: str, new: str) -> list[str]:
    """The non-blank lines of ``old`` that ``new`` no longer has, in order (each counted once)."""
    gone = Counter(x for x in old.splitlines() if x.strip()) - Counter(x for x in new.splitlines() if x.strip())
    out = []
    for x in old.splitlines():
        if gone[x] > 0:
            gone[x] -= 1
            out.append(x)
    return out


def diff_counts(old: str, new: str) -> tuple[int, int]:
    """(+ lines, - lines), ignoring order and blank lines."""
    a = Counter(x for x in old.splitlines() if x.strip())
    b = Counter(x for x in new.splitlines() if x.strip())
    return sum((b - a).values()), sum((a - b).values())


def over_limit(text: str, max_lines: int, max_chars: int) -> str | None:
    """'95 行 / 上限 80 行' when ``text`` is over either limit."""
    lines, size = len(text.rstrip("\n").splitlines()), len(text.rstrip("\n"))
    over = []
    if lines > max_lines:
        over.append(f"{lines} 行 (上限 {max_lines} 行)")
    if size > max_chars:
        over.append(f"{size} 文字 (上限 {max_chars} 文字)")
    return "、".join(over) or None


def status(shipdir: Path, team: dict, now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    c = conf(team)
    lim = c["limits"]
    rows = []
    for role in team["roles"]:
        cands = candidates(shipdir, team, role)
        count = sum(len(v) for v in cands.values())
        last = last_applied(shipdir, role)
        days = None if last is None else int((now - last) // 86400)
        # never applied: the days count from the oldest candidate, so a new ship gets the sign too
        since = last if last is not None else _oldest(cands)
        due = count > 0 and (count >= c["curate_at"] or (since is not None and now - since >= c["curate_every"]))
        prop = proposed_path(shipdir, role)
        proposal = None
        if prop.is_file():
            sec = sections(_read(prop))
            proposal = diff_counts(_read(memory_path(shipdir, role)), sec.get("memory", ""))
        rows.append({"role": role, "count": count, "seats": {s: len(v) for s, v in cands.items()},
                     "last": last, "days": days,
                     "oldest_days": None if last is not None or since is None else int((now - since) // 86400), "due": due, "proposal": proposal,
                     "over": over_limit(_read(memory_path(shipdir, role)), lim["memory_lines"], lim["memory_chars"])})
    kcount = len(_entries(_read(knowledge_inbox_path(shipdir))))
    klast = last_applied(shipdir, None)
    rows.append({"role": None, "count": kcount, "last": klast,
                 "days": None if klast is None else int((now - klast) // 86400),
                 "due": False, "proposal": diff_counts(_read(knowledge_path(shipdir)), _read(knowledge_proposed_path(shipdir)))
                 if knowledge_proposed_path(shipdir).is_file() else None,
                 "over": over_limit(_read(knowledge_path(shipdir)), lim["knowledge_lines"], lim["knowledge_chars"])})
    return rows


def status_lines(shipdir: Path, team: dict, now: float | None = None) -> list[str]:
    c = conf(team)
    y = f"{yamato_invocation()} memory"
    out = []
    for r in status(shipdir, team, now):
        name = r["role"] or "knowledge"
        since = "前回の棚卸しなし" if r["days"] is None else f"前回の棚卸しから {r['days']} 日"
        if r.get("oldest_days") is not None:
            since += f" (一番古い候補から {r['oldest_days']} 日)"
        line = f"{name}: 候補 {r['count']} 件 / {since}"
        if r["due"]:
            line += f" ← 棚卸しの目安 ({c['curate_every'] // 86400} 日 / {c['curate_at']} 件) に達した"
        if r["proposal"]:
            plus, minus = r["proposal"]
            what = f"`{y} apply {shipdir} {r['role']}`" if r["role"] else f"`{y} apply {shipdir} --knowledge`"
            line += f" / 棚卸し案あり (+{plus} / -{minus} 行): {what} で反映"
        if r["over"]:
            line += f" / {'memory.md' if r['role'] else 'knowledge.md'} が上限を超えている: {r['over']}"
        out.append(line)
    return out


def applier_notice(shipdir: Path, team: dict, seat: str, now: float | None = None) -> str | None:
    """``memory status`` for the applier's role, on its first start of the day (design-p1 §3.4)."""
    c = conf(team)
    if team["seats"][seat]["role"] != c["applier"]:
        return None
    mark = Path(shipdir) / ".runtime" / f"memory-status-{seat}.json"
    day = time.strftime("%Y-%m-%d", time.localtime(time.time() if now is None else now))
    if (read_json(mark, {}) or {}).get("date") == day:
        return None
    lines = status_lines(shipdir, team, now)
    try:
        write_json(mark, {"date": day})
    except OSError:
        pass
    return "## memory の棚卸し (memory status。今日の最初のシフトだけ)\n" + "\n".join(f"- {x}" for x in lines)


# --- injection (the safety net for hand edits, design-p1 §3.5) ------------------------------

def within_limits(text: str, team: dict, kind: str) -> tuple[str, str | None]:
    """``text`` cut to ``memory.limits`` (kind: memory / knowledge), and the warning if it was."""
    lim = conf(team)["limits"]
    max_lines, max_chars = lim[f"{kind}_lines"], lim[f"{kind}_chars"]
    over = over_limit(text, max_lines, max_chars)
    if not over:
        return text, None
    cut = "\n".join(text.rstrip("\n").splitlines()[:max_lines])
    return cut[:max_chars], f"{kind}.md が上限を超えている ({over})。上限で切った。棚卸しが必要"


# --- the proposal format --------------------------------------------------------------------

def sections(text: str) -> dict:
    """``<!-- yamato: <kind> [<arg>] -->`` split. Returns {kind: body} and, for candidates,
    {"candidates": {seat: [lines]}}. Text before the first marker is the header."""
    out: dict = {}
    cands: dict = {}
    cur = None
    buf: list[str] = []

    def flush():
        if cur is None:
            return
        kind, arg = cur
        lines = list(buf)
        while lines and (not lines[0].strip() or FENCE_RE.match(lines[0])):
            lines.pop(0)
        while lines and (not lines[-1].strip() or FENCE_RE.match(lines[-1])):
            lines.pop()
        if kind == "candidates":
            cands.setdefault(arg or "", []).extend(x for x in lines if x.startswith("- "))
        else:
            out[kind] = "\n".join(lines)

    for line in text.splitlines():
        m = MARK_RE.match(line)
        if m:
            flush()
            cur, buf = (m.group(1), m.group(2)), []
        elif cur is not None:
            buf.append(line)
    flush()
    if cands:
        out["candidates"] = cands
    return out


def _mark(kind: str, arg: str | None = None) -> str:
    return f"<!-- yamato: {kind}{' ' + arg if arg else ''} -->"


def proposal_text(shipdir: Path, team: dict, role: str, *, new: str, archive: str, knowledge: str,
                  cands: dict, sid: str, now: float) -> str:
    lim = conf(team)["limits"]
    old = _read(memory_path(shipdir, role))
    plus, minus = diff_counts(old, new)
    count = sum(len(v) for v in cands.values())
    per_seat = ", ".join(f"{s}: {len(v)}" for s, v in cands.items() if v) or "なし"
    over = over_limit(new, lim["memory_lines"], lim["memory_chars"])
    head = [
        f"# memory の棚卸し案: {role}",
        "",
        f"- 作成: {_stamp(now)} / 棚卸しのシフト {sid}",
        f"- 候補: {count} 件 ({per_seat})",
        f"- memory.md: {len(old.rstrip().splitlines())} 行 → {len(new.rstrip().splitlines())} 行 "
        f"(+{plus} / -{minus} 行)。上限 {lim['memory_lines']} 行 / {lim['memory_chars']} 文字"
        + (f"。**上限を超えている ({over})。このままでは反映できない**" if over else ""),
        f"- 反映: `{yamato_invocation()} memory apply {shipdir} {role}`。反映の前にこのファイルを直してよい "
        "(`<!-- yamato: ... -->` の行は消さない)",
        "- memory 節が新しい memory.md、archive 節と memory.md から外れた行は memory-archive.md へ、"
        "knowledge 節と (ship) の候補は knowledge-inbox.md へ、candidates 節の候補は memory-inbox.done/ へ移る",
        "",
    ]
    body = [_mark("memory"), new.rstrip("\n"), "", _mark("archive"), archive.rstrip("\n"), "",
            _mark("knowledge"), knowledge.rstrip("\n"), ""]
    for seat, lines in cands.items():
        if lines:
            body += [_mark("candidates", seat), *lines, ""]
    return "\n".join(head + body)


# --- curate ------------------------------------------------------------------------------------

def curate_prompt(shipdir: Path, team: dict, role: str, current: str, cands: dict) -> str:
    """The first prompt of a curate shift: the material and the answer format. What to keep
    is the curator's prompt (the template's roles/_memory-curator.md)."""
    lim = conf(team)["limits"]
    spec = team["roles"][role]
    lines = [line for v in cands.values() for line in v]
    return "\n".join([
        f"[yamato] 役割 {role} の memory の棚卸し。この 1 回の応答で案を返してください (ファイルは書かない)。",
        f"- 役割 {role}: {spec['description']} (役割プロンプト: {Path(shipdir) / 'roles' / (role + '.md')})",
        f"- 新しい memory.md の上限: {lim['memory_lines']} 行 / {lim['memory_chars']} 文字。超えた案は反映できない",
        "- 候補の (ship) は艦全体の knowledge の候補で、yamato が knowledge-inbox.md に自動で回す。"
        "knowledge 節には、それ以外で艦全体に効くものだけを書く",
        "",
        "## 今の memory.md",
        current.strip() or "(空)",
        "",
        f"## 候補 ({len(lines)} 件。各席の memory-inbox.md)",
        "\n".join(lines) or "(なし)",
        "",
        "## 答えの形 (この 3 つの印の行をそのまま書き、印の間に中身を書く。印の外と前置きは読まれない)",
        _mark("memory"),
        "(新しい memory.md の全文)",
        _mark("archive"),
        "- (memory.md から外すもの・採らなかった候補。1 行 1 件、理由を添える)",
        _mark("knowledge"),
        "- (艦全体で共有すべき知見の候補。無ければ空)",
    ])


def curate(shipdir: Path, team: dict, role: str | None = None, wait: bool = False, out=print) -> list[str]:
    """``memory curate <ship> [<role>]``: one curate shift per role, detached unless ``wait``.
    Without a role: the roles with candidates, or whose memory.md is over the limit."""
    migrate(shipdir, team)
    if role:
        roles = [_check_role(team, role)]
    else:
        roles = [r["role"] for r in status(shipdir, team) if r["role"] and (r["count"] or r["over"])]
    if not roles:
        out("棚卸しする役割がありません (候補なし、memory.md は上限内)")
        return []
    for r in roles:
        if wait:
            res = run_curate(shipdir, team, r)
            out(f"{r}: {res['message']}")
        else:
            log = role_dir(shipdir, r) / "curate" / "wrapper.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            from . import claude

            with open(log, "a") as f:
                subprocess.Popen([sys.executable, str(YAMATO_BIN), "_memory-curate", str(shipdir), r],
                                 stdin=subprocess.DEVNULL, stdout=f, stderr=f, start_new_session=True,
                                 env=claude.seat_env())
            out(f"{r}: 棚卸しのシフトを起こした。終わると {report_to(team)} に知らせが届く "
                f"(案: {proposed_path(shipdir, r)})")
    return roles


def report_to(team: dict) -> str:
    """The applier's seat (the hub when the applier role has several seats)."""
    applier = conf(team)["applier"]
    seats = role_seats(team, applier)
    return seats[0] if len(seats) == 1 else team["hub"]


def curator_agents(shipdir: Path, team: dict, role: str) -> dict:
    from .runtime import render_prompt

    path = Path(shipdir) / "roles" / CURATOR_PROMPT
    if not path.is_file():
        raise YamatoError(f"棚卸しのシフトの役割プロンプトがありません: {path} (ひな形の roles/{CURATOR_PROMPT} を置く)")
    return {CURATOR: {"description": f"役割 {role} の memory の棚卸し案を作る",
                      "prompt": render_prompt(path.read_text(encoding="utf-8"), shipdir, team),
                      "model": team["roles"][role]["model"], "tools": ["Read"]}}


def curator_settings(shipdir: Path, team: dict) -> dict:
    """Not a seat: no hooks. dontAsk, so nothing outside the (empty) allow list runs
    (verify-p1-d V7); the answer comes back as text and yamato writes the file."""
    from .runtime import _merge

    deny = [r.replace("{{ship}}", str(shipdir)) for r in team.get("deny") or []]
    return _merge(team.get("settings") or {}, {"permissions": {"defaultMode": "dontAsk", "allow": [], "deny": deny}})


def run_curate(shipdir: Path, team: dict, role: str, now: float | None = None) -> dict:
    """One curate shift for ``role`` (``yamato _memory-curate``): run it, write the proposal,
    record usage and events, and tell the applier. Returns {outcome, message, ...}."""
    import json

    from . import claude, headless, runtime, usage

    shipdir = Path(shipdir)
    _check_role(team, role)
    lock = headless._try_lock(runtime.runtime_dir(shipdir) / f"memory-curate-{role}.lock")
    if lock is None:
        raise YamatoError(f"役割 {role} の棚卸しはすでに動いています")
    try:
        migrate(shipdir, team)
        c = conf(team)
        cands = candidates(shipdir, team, role)
        current = _read(memory_path(shipdir, role))
        agents = curator_agents(shipdir, team, role)
        settings = runtime.runtime_dir(shipdir) / "settings-memory-curate.json"
        write_json(settings, curator_settings(shipdir, team))
        sid = str(uuid.uuid4())
        started = time.time() if now is None else now
        argv = claude.headless_argv(
            session_id=sid, name=f"{team['name']}.memory-curate-{role}", role=CURATOR,
            agents_json=json.dumps(agents, ensure_ascii=False), model=team["roles"][role]["model"],
            settings=str(settings), add_dir=str(shipdir), prompt=curate_prompt(shipdir, team, role, current, cands))
        out_dir = role_dir(shipdir, role) / "curate"
        out_dir.mkdir(parents=True, exist_ok=True)
        base = out_dir / time.strftime("%Y%m%d-%H%M%S", time.localtime(started))
        stream = headless._Stream([], base.with_suffix(".jsonl"))
        rc, killed, launch_error = None, None, None
        try:
            with open(base.with_suffix(".stderr"), "a", encoding="utf-8") as err:
                env = claude.seat_env([*claude.PRINT_CALLER_ENV, *(team.get("env_unset") or ())])
                proc = subprocess.Popen(argv, cwd=str(shipdir), env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=err, text=True, encoding="utf-8",
                                        errors="replace")
        except OSError as e:
            launch_error = f"claude -p を起動できない: {e}"
        else:
            stream.src = proc.stdout
            reader = threading.Thread(target=stream, daemon=True)
            reader.start()
            killed = _watch(proc, started, c["max_duration"])
            reader.join(timeout=10)
            proc.stdout.close()
            rc = proc.returncode
        failures = headless._failures(stream, rc, killed, launch_error, need_hook=False)
        sec = {}
        if not failures and not killed:
            sec = sections(str((stream.result or {}).get("result") or ""))
            if "memory" not in sec:
                failures.append(("案の形になっていない", f"答えに {_mark('memory')} の印がない"))
        outcome = headless.FAILED if failures else (headless.TIMEOUT if killed else headless.OK)
        ended = time.time()
        line = headless._usage_line(f"memory-curate-{role}", role, sid, None, started, ended, stream, outcome)
        usage.append(shipdir, line)
        if killed and not failures:
            msg = f"時間切れ ({c['max_duration'] // 60} 分) で止めた。案は作っていない。出力: {base.with_suffix('.jsonl')}"
            events.emit(shipdir, MEMORY_CURATE, summary=f"{role} の棚卸し: 時間切れ",
                        data={"role": role, "outcome": outcome, "sessionId": sid, "exitCode": rc})
            text = f"[yamato] 役割 {role} の memory の棚卸しが時間切れで止まった。案は作っていない"
        elif failures:
            raw = str((stream.result or {}).get("result") or "")
            if raw:
                atomic_write(base.with_suffix(".result.md"), raw)
            what = " / ".join(w for w, _ in failures)
            msg = f"棚卸しの案を作れなかった ({what})。出力: {base.with_suffix('.jsonl')}"
            events.emit(shipdir, MEMORY_CURATE, summary=f"{role} の棚卸し: 異常 ({what})",
                        data={"role": role, "outcome": outcome, "sessionId": sid, "exitCode": rc,
                              "failures": [d for _, d in failures]})
            text = f"[yamato] 役割 {role} の memory の棚卸しが失敗した ({what})。出力: {base.with_suffix('.jsonl')}"
        else:
            # hand-written candidate markers in the answer are not candidates: only ours count
            atomic_write(proposed_path(shipdir, role), proposal_text(
                shipdir, team, role, new=sec.get("memory", ""), archive=sec.get("archive", ""),
                knowledge=sec.get("knowledge", ""), cands=cands, sid=sid, now=ended))
            plus, minus = diff_counts(current, sec.get("memory", ""))
            count = sum(len(v) for v in cands.values())
            over = over_limit(sec.get("memory", ""), c["limits"]["memory_lines"], c["limits"]["memory_chars"])
            msg = f"案を作った: {proposed_path(shipdir, role)} (+{plus} / -{minus} 行、候補 {count} 件)" \
                  + (f"。上限を超えている ({over})" if over else "")
            events.emit(shipdir, MEMORY_CURATE, summary=f"{role} の棚卸し案 (+{plus} / -{minus} 行、候補 {count} 件)",
                        data={"role": role, "outcome": outcome, "sessionId": sid, "plus": plus, "minus": minus,
                              "candidates": count, "over": over})
            text = (f"[yamato] 役割 {role} の memory の棚卸し案ができた (+{plus} / -{minus} 行、候補 {count} 件"
                    + (f"、上限を超えている: {over}" if over else "") + f")。案: {proposed_path(shipdir, role)}。"
                    f"読んで `{yamato_invocation()} memory apply {shipdir} {role}` で反映する")
        _tell(shipdir, team, text)
        return {"outcome": outcome, "message": msg, "sessionId": sid, "failures": failures}
    finally:
        headless._release(lock)


def _watch(proc, started: float, max_duration: int) -> str | None:
    killed_at, reason = None, None
    while proc.poll() is None:
        now = time.time()
        if killed_at is None and now >= started + max_duration:
            proc.terminate()
            killed_at, reason = now, "max-duration"
        elif killed_at is not None and now - killed_at > KILL_WAIT:
            proc.kill()
        try:
            proc.wait(timeout=WATCH_POLL)   # cut short when claude -p ends
        except subprocess.TimeoutExpired:
            pass
    return reason


def _tell(shipdir: Path, team: dict, text: str) -> None:
    """A fixed-form notice from yamato to the applier's seat (like the headless report)."""
    from . import deadline, headless
    from . import seat as seat_mod

    to = report_to(team)
    try:
        entry = inbox.append(shipdir, to, headless.REPORTER, text)
        seat_mod._send_event(shipdir, to, headless.REPORTER, entry)
        if deadline.phase(deadline.read(shipdir)) == deadline.RUNNING:
            seat_mod.wake(shipdir, team, to)
    except (YamatoError, OSError) as e:
        print(f"yamato: 棚卸しの知らせを {to} に送れなかった: {e}", file=sys.stderr)


# --- apply ---------------------------------------------------------------------------------------

def _move_candidates(shipdir: Path, seat: str, lines: list[str], date: str) -> int:
    """Take ``lines`` out of the seat's memory-inbox.md (each once) into memory-inbox.done/<date>.md.
    Lines written after the curate stay in the inbox."""
    path = inbox_path(shipdir, seat)
    want = Counter(lines)
    keep, moved = [], []
    for line in _read(path).splitlines():
        if want[line] > 0:
            want[line] -= 1
            moved.append(line)
        else:
            keep.append(line)
    if moved:
        atomic_write(path, "".join(x + "\n" for x in keep))
        _append(inbox.seat_dir(shipdir, seat) / "memory-inbox.done" / f"{date}.md", "".join(x + "\n" for x in moved))
    return len(moved)


def _refuse_over(text: str, max_lines: int, max_chars: int, what: str, path: Path) -> None:
    over = over_limit(text, max_lines, max_chars)
    if over:
        raise YamatoError(f"{what} が上限を超えているので反映しない: {over}。"
                          f"案 ({path}) をまとめ直すか、archive 節に回してから、もう一度 apply する")


def apply(shipdir: Path, team: dict, role: str, by: str, now: float | None = None) -> dict:
    """``memory apply <ship> <role>`` (design-p1 §3.3 の 4, 5)."""
    shipdir = Path(shipdir)
    _check_role(team, role)
    now = time.time() if now is None else now
    lim = conf(team)["limits"]
    prop = proposed_path(shipdir, role)
    if not prop.is_file():
        raise YamatoError(f"棚卸し案がありません: {prop} (`memory curate` で作る)")
    sec = sections(_read(prop))
    if "memory" not in sec:
        raise YamatoError(f"案に {_mark('memory')} の節がありません: {prop}")
    new = sec["memory"].strip("\n")
    new = new + "\n" if new.strip() else ""
    _refuse_over(new, lim["memory_lines"], lim["memory_chars"], "新しい memory.md", prop)
    date = time.strftime("%Y-%m-%d", time.localtime(now))
    moved, unknown, to_knowledge = 0, [], []
    with ship_lock(shipdir):
        migrate(shipdir, team)
        old = _read(memory_path(shipdir, role))
        atomic_write(memory_path(shipdir, role), new)
        dropped = _removed(old, new)
        archive = sec.get("archive", "").strip()
        entry = [f"## {_stamp(now)} 反映: {by} (役割 {role})", ""]
        if archive:
            entry += ["### 案が外したもの", archive, ""]
        if dropped:
            entry += ["### memory.md から外れた行", *dropped, ""]
        if not archive and not dropped:
            entry += ["(外したものなし)", ""]
        _append(archive_path(shipdir, role), "\n".join(entry) + "\n")
        seats = set(role_seats(team, role))
        for seat, lines in (sec.get("candidates") or {}).items():
            if seat not in seats:
                unknown.append(seat)
                continue
            n = _move_candidates(shipdir, seat, lines, date)
            moved += n
            to_knowledge += [x for x in lines if re.match(r"^- \S+ \S+( \[[^\]]*\])? \(ship\) ", x)]
        knowledge = [x for x in sec.get("knowledge", "").splitlines() if x.strip()]
        to_knowledge += [f"- {date} {role} の棚卸し: {x[2:] if x.startswith('- ') else x.strip()}" for x in knowledge]
        if to_knowledge:
            _append(knowledge_inbox_path(shipdir), "".join(x + "\n" for x in to_knowledge))
        prop.replace(prop.with_name("memory.proposed.applied.md"))
    plus, minus = diff_counts(old, new)
    events.emit(shipdir, MEMORY_APPLY, by=by, summary=f"{role} の memory を反映 (+{plus} / -{minus} 行、候補 {moved} 件を処理)",
                data={"role": role, "lines": len(new.splitlines()), "chars": len(new.rstrip("\n")),
                      "plus": plus, "minus": minus, "candidates": moved, "archived": len(dropped),
                      "knowledge": len(to_knowledge)}, now=now)
    return {"role": role, "plus": plus, "minus": minus, "moved": moved, "dropped": len(dropped),
            "knowledge": len(to_knowledge), "unknown_seats": unknown}


def apply_knowledge(shipdir: Path, team: dict, by: str, now: float | None = None) -> dict:
    """``memory apply <ship> --knowledge``: ``knowledge.proposed.md`` (written by the applier)
    becomes knowledge.md; **every** candidate in knowledge-inbox.md at this moment moves to
    ``knowledge-inbox.done/<date>.md`` (the proposal is free text, so which ones it read is
    not known; the prompts and README say so)."""
    shipdir = Path(shipdir)
    now = time.time() if now is None else now
    lim = conf(team)["limits"]
    prop = knowledge_proposed_path(shipdir)
    if not prop.is_file():
        raise YamatoError(f"knowledge の案がありません: {prop} (knowledge-inbox.md の候補と今の knowledge.md から書く)")
    new = _read(prop).strip("\n")
    new = new + "\n" if new.strip() else ""
    _refuse_over(new, lim["knowledge_lines"], lim["knowledge_chars"], "新しい knowledge.md", prop)
    date = time.strftime("%Y-%m-%d", time.localtime(now))
    with ship_lock(shipdir):
        old = _read(knowledge_path(shipdir))
        atomic_write(knowledge_path(shipdir), new)
        dropped = _removed(old, new)
        _append(knowledge_archive_path(shipdir), "\n".join(
            [f"## {_stamp(now)} 反映: {by} (knowledge)", "", *(dropped or ["(外したものなし)"]), ""]) + "\n")
        cands = _entries(_read(knowledge_inbox_path(shipdir)))
        if cands:
            _append(Path(shipdir) / "knowledge-inbox.done" / f"{date}.md", "".join(x + "\n" for x in cands))
            rest = [x for x in _read(knowledge_inbox_path(shipdir)).splitlines() if not x.startswith("- ")]
            atomic_write(knowledge_inbox_path(shipdir), "".join(x + "\n" for x in rest))
        prop.replace(prop.with_name("knowledge.proposed.applied.md"))
    plus, minus = diff_counts(old, new)
    events.emit(shipdir, MEMORY_APPLY, by=by, summary=f"knowledge.md を反映 (+{plus} / -{minus} 行、候補 {len(cands)} 件を処理)",
                data={"role": None, "lines": len(new.splitlines()), "chars": len(new.rstrip("\n")),
                      "plus": plus, "minus": minus, "candidates": len(cands), "archived": len(dropped)}, now=now)
    return {"plus": plus, "minus": minus, "moved": len(cands), "dropped": len(dropped)}


# --- CLI ------------------------------------------------------------------------------------------

def register(sub) -> None:
    m = sub.add_parser("memo", help="memory の候補を呼び出した席の memory-inbox.md に 1 行足す (design-p1 §3.2)")
    m.add_argument("text")
    m.add_argument("--item", help="関わる項目 (T-042 など)")
    m.add_argument("--scope", default="role", choices=SCOPES, help="role (役割の memory) / ship (艦の knowledge)")
    m.add_argument("--ship", help="艦 (省略時は呼び出したセッションの席がいる艦を探す)")
    m.add_argument("--seat", help="席 (席のセッションの外から書くとき。席の中では呼び出した席と同じであること)")
    r = sub.add_parser("memory", help="memory の棚卸し (design-p1 §3)")
    rs = r.add_subparsers(dest="memory_cmd", required=True)
    s = rs.add_parser("status", help="役割ごとの候補数と前回の棚卸しからの日数")
    s.add_argument("ship")
    c = rs.add_parser("curate", help="headless のシフトで棚卸し案 (roles/<role>/memory.proposed.md) を作る")
    c.add_argument("ship")
    c.add_argument("role", nargs="?", help="省略時は候補のある役割 (と memory.md が上限を超えた役割) すべて")
    c.add_argument("--wait", action="store_true", help="切り離さず、終わるまで待つ")
    a = rs.add_parser("apply", help="棚卸し案を memory.md に反映する (上限を超える案は拒否)")
    a.add_argument("ship")
    a.add_argument("role", nargs="?")
    a.add_argument("--knowledge", action="store_true", help="knowledge.proposed.md を knowledge.md に反映する")
    a.add_argument("--by", help="反映した人 (記録用。既定は呼び出した席、無ければ owner)")
    mg = rs.add_parser("migrate", help="P0 の seats/<seat>/memory.md を roles/<role>/memory.md へ移す (消さない)")
    mg.add_argument("ship")
    h = sub.add_parser("_memory-curate")
    h.add_argument("ship")
    h.add_argument("role")


def run(args) -> int:
    from .seat import current_team
    from .util import resolve_ship

    if args.cmd == "memo":
        shipdir, seat = memo_target(args.ship, args.seat)
        print(f"memo: {memo(shipdir, seat, args.text, item=args.item, scope=args.scope)}")
        return 0
    shipdir = resolve_ship(args.ship)
    team = current_team(shipdir)
    if args.cmd == "_memory-curate":
        from .headless import FAILED

        return 1 if run_curate(shipdir, team, args.role)["outcome"] == FAILED else 0
    if args.memory_cmd == "status":
        for line in status_lines(shipdir, team):
            print(line)
    elif args.memory_cmd == "curate":
        curate(shipdir, team, args.role, wait=args.wait)
    elif args.memory_cmd == "apply":
        from .worktree import caller

        by = caller(shipdir, args.by)
        if args.knowledge:
            res = apply_knowledge(shipdir, team, by)
            print(f"knowledge.md に反映した (+{res['plus']} / -{res['minus']} 行、候補 {res['moved']} 件を "
                  f"knowledge-inbox.done/ へ、外れた {res['dropped']} 行を knowledge-archive.md へ)")
        else:
            if not args.role:
                raise YamatoError("役割を指定してください (knowledge.md は --knowledge)")
            res = apply(shipdir, team, args.role, by)
            print(f"{args.role} の memory.md に反映した (+{res['plus']} / -{res['minus']} 行、候補 {res['moved']} 件を "
                  f"memory-inbox.done/ へ、外れた {res['dropped']} 行を memory-archive.md へ、"
                  f"knowledge 候補 {res['knowledge']} 件を knowledge-inbox.md へ)")
            for seat in res["unknown_seats"]:
                print(f"注意: 案の candidates の席 {seat} は役割 {args.role} の席ではないので飛ばした")
    elif args.memory_cmd == "migrate":
        moved = migrate(shipdir, team)
        print("\n".join(moved) if moved else "移すものはありません (seats/<seat>/memory.md なし)")
    return 0

"""team.yaml: load, validate and expand roles into seats.

The validated result is written to ``.runtime/team.json`` by ``yamato up`` so
the per-turn hooks never have to import YAML.
"""
from __future__ import annotations

from pathlib import Path

from .notify import CHANNELS as NOTIFY_CHANNELS
from .util import YamatoError, check_name, parse_duration, read_json

SHIFTS = ("per_task", "persistent", "headless")
STATES = ("open", "active", "blocked", "done")
TOP_KEYS = {"name", "hub", "workspace", "charter", "roles", "time_limit", "grace", "deny", "board",
            "settings", "seat_stop", "env_unset", "inject", "notify", "git", "report", "decisions"}
# what SessionStart can inject (design §8.2); the header is always there
INJECT_PARTS = ("handoff", "log_tail", "mine", "inbox", "memory", "knowledge", "last_report")
# what a ship that names no parts gets: the captain's report excerpt (design-p1 §2.3) is opt-in
DEFAULT_INJECT_PARTS = tuple(p for p in INJECT_PARTS if p != "last_report")
INJECT_LIMIT_KEYS = ("handoff", "memory", "knowledge", "log_tail", "mine_items", "inbox_messages",
                     "inbox_chars", "total_chars", "last_report")
RESERVED_SEATS = ("owner",)   # the human's inbox; not a seat
SEAT_STOP_DEFAULTS = {"require_handoff": True, "require_delivery": True}
ROLE_KEYS = {"model", "shift", "count", "description", "inject", "max_duration", "max_budget_usd", "report_to"}
# `git:` (design-p1 §8.3). The values a ship runs with live in the template's
# team.yaml; these are only what a ship without a `git:` section gets.
GIT_KEYS = {"base", "strategy", "merge_requires", "merge_decision", "conflict"}
GIT_STRATEGIES = ("squash", "merge", "rebase")
GIT_REQUIRES = ("review", "ci", "decision")
GIT_FALLBACK = {"base": "main", "strategy": "squash", "merge_requires": [], "merge_decision": "off",
                "conflict": "author"}
# auto mode is unavailable on Haiku (verify-p0-b §総括 1): the seat would fall
# back to manual and block on the first dialog.
NO_AUTO_MODELS = ("haiku",)

NOTIFY_DECISIONS = ("digest", "each")   # human deciders: gather into the daily report / one by one
DEFAULT_TIME_LIMIT = "3h"
DEFAULT_GRACE = "20m"


def load_yaml(path: Path) -> dict:
    import yaml  # vendored; imported lazily so hooks stay light

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise YamatoError(f"{path} がありません") from None
    except yaml.YAMLError as e:
        raise YamatoError(f"{path} を読めません: {e}") from None
    if not isinstance(data, dict):
        raise YamatoError(f"{path} の中身が mapping ではありません")
    return data


def expand_seats(roles: dict) -> dict:
    """``count: 1`` -> seat named after the role; ``count: n`` -> ``<role>-1..n``."""
    seats: dict = {}
    for role, spec in roles.items():
        count = spec["count"]
        names = [role] if count == 1 else [f"{role}-{i}" for i in range(1, count + 1)]
        for seat in names:
            if seat in seats:
                raise YamatoError(f"席の名前が重複しています: {seat}")
            seats[seat] = {"role": role, "model": spec["model"], "shift": spec["shift"]}
    return seats


def validate(data: dict, shipdir: Path) -> dict:
    unknown = set(data) - TOP_KEYS
    if unknown:
        raise YamatoError(f"team.yaml に知らない項目があります: {', '.join(sorted(unknown))}")
    name = check_name("艦", str(data.get("name") or ""))

    warnings: list[str] = []
    # `0` must not silently become the default (review N4)
    time_limit = parse_duration(DEFAULT_TIME_LIMIT if data.get("time_limit") is None else data["time_limit"])
    if time_limit <= 0:
        raise YamatoError("team.yaml: time_limit は 0 より長くする")
    roles_in = data.get("roles")
    if not isinstance(roles_in, dict) or not roles_in:
        raise YamatoError("team.yaml: roles が空です")
    roles: dict = {}
    for role, spec in roles_in.items():
        check_name("役割", str(role))
        if role in RESERVED_SEATS:
            raise YamatoError(f"team.yaml: 役割の名前 {role} は予約されています (人間の受信箱)")
        spec = spec or {}
        if not isinstance(spec, dict):
            raise YamatoError(f"team.yaml: roles.{role} が mapping ではありません")
        bad = set(spec) - ROLE_KEYS
        if bad:
            raise YamatoError(f"team.yaml: roles.{role} に知らない項目があります: {', '.join(sorted(bad))}")
        model = str(spec.get("model") or "sonnet")
        if any(m in model.lower() for m in NO_AUTO_MODELS):
            warnings.append(f"roles.{role}.model={model} は auto モードを使えない (検証 B)。"
                            "manual に落ちて最初の書き込みで権限の確認になり、無人だと拒否される")
        shift = spec.get("shift") or "per_task"
        if shift not in SHIFTS:
            raise YamatoError(f"team.yaml: roles.{role}.shift は {' / '.join(SHIFTS)} のどれか (今: {shift})")
        count = spec.get("count", 1)
        if not isinstance(count, int) or count < 1:
            raise YamatoError(f"team.yaml: roles.{role}.count は 1 以上の整数 (今: {count!r})")
        parts = spec.get("inject")
        if parts is not None:
            _check_parts(parts, f"roles.{role}.inject")
        max_duration = spec.get("max_duration")
        if max_duration is not None:
            max_duration = parse_duration(max_duration)
            if max_duration <= 0:
                raise YamatoError(f"team.yaml: roles.{role}.max_duration は 0 より長くする")
        budget = spec.get("max_budget_usd")
        if budget is not None and (isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget <= 0):
            raise YamatoError(f"team.yaml: roles.{role}.max_budget_usd は正の数 (今: {budget!r})")
        for key, value in (("max_duration", max_duration), ("max_budget_usd", budget)):
            if value is not None and shift != "headless":
                warnings.append(f"roles.{role}.{key} は shift: headless の役割でだけ効く (今: {shift})。無視する")
        roles[role] = {
            "inject": parts,
            "max_duration": max_duration,
            "max_budget_usd": budget,
            "report_to": str(spec["report_to"]) if spec.get("report_to") else None,
            "model": model,
            "shift": shift,
            "count": count,
            "description": str(spec.get("description") or role),
        }

    hub = str(data.get("hub") or "")
    if hub not in roles:
        raise YamatoError(f"team.yaml: hub={hub!r} が roles にありません")
    if roles[hub]["count"] != 1:
        raise YamatoError("team.yaml: hub の役割は count: 1 にする")

    seats = expand_seats(roles)
    for role, spec in roles.items():
        if spec["report_to"] and spec["report_to"] not in (*seats, *RESERVED_SEATS):
            raise YamatoError(f"team.yaml: roles.{role}.report_to={spec['report_to']!r} は席の名前か owner")

    ws = data.get("workspace")
    if not ws:
        raise YamatoError("team.yaml: workspace がありません")
    wpath = Path(str(ws)).expanduser()
    if not wpath.is_absolute():
        wpath = shipdir / wpath

    deny = data.get("deny") or []
    if not isinstance(deny, list) or not all(isinstance(x, str) for x in deny):
        raise YamatoError("team.yaml: deny は文字列のリスト")

    settings = data.get("settings") or {}
    if not isinstance(settings, dict):
        raise YamatoError("team.yaml: settings は mapping (Claude Code の settings.json に重ねる中身)")
    seat_stop = data.get("seat_stop") or {}
    if not isinstance(seat_stop, dict) or set(seat_stop) - set(SEAT_STOP_DEFAULTS) \
            or not all(isinstance(v, bool) for v in seat_stop.values()):
        raise YamatoError(f"team.yaml: seat_stop は {{{', '.join(SEAT_STOP_DEFAULTS)}: true|false}}")

    env_unset = data.get("env_unset") or []
    if not isinstance(env_unset, list) or not all(isinstance(x, str) for x in env_unset):
        raise YamatoError("team.yaml: env_unset は環境変数名のリスト")

    inject = data.get("inject") or {}
    if not isinstance(inject, dict) or set(inject) - {"parts", "limits"}:
        raise YamatoError("team.yaml: inject は {parts: [...], limits: {...}}")
    if inject.get("parts") is not None:
        _check_parts(inject["parts"], "inject.parts")
    limits = inject.get("limits") or {}
    if not isinstance(limits, dict) or set(limits) - set(INJECT_LIMIT_KEYS):
        raise YamatoError(f"team.yaml: inject.limits の項目は {', '.join(INJECT_LIMIT_KEYS)}")

    notify = data.get("notify") or {}
    if not isinstance(notify, dict) or not isinstance(notify.get("via") or [], list):
        raise YamatoError(f"team.yaml: notify は {{via: [{', '.join(NOTIFY_CHANNELS)}], "
                          "slack: {webhook_env: ...}, command: \"...\"}")
    slack = notify.get("slack") or {}
    if not isinstance(slack, dict) or not isinstance(slack.get("webhook_env") or "", str):
        raise YamatoError("team.yaml: notify.slack は {webhook_env: webhook の URL が入った環境変数の名前}")
    if not isinstance(notify.get("command") or "", str):
        raise YamatoError("team.yaml: notify.command はシェルのコマンド (文字列)")
    for via in notify.get("via") or []:
        if via not in NOTIFY_CHANNELS:
            warnings.append(f"notify.via の {via!r} は知らない経路 (選べるのは {' / '.join(NOTIFY_CHANNELS)})。送れず events に残る")
    notify_decisions = notify.get("decisions") or "digest"
    if notify_decisions not in NOTIFY_DECISIONS:
        raise YamatoError(f"team.yaml: notify.decisions は {' / '.join(NOTIFY_DECISIONS)} のどれか (今: {notify_decisions})")
    seats = expand_seats(roles)

    git = _git(data.get("git"))

    report = data.get("report") or {}
    daily = report.get("daily", "on_down") if isinstance(report, dict) else None
    if daily is False:   # an unquoted `off` is false in YAML 1.1
        daily = "off"
    if not isinstance(report, dict) or set(report) - {"daily"} or daily not in ("on_down", "off"):
        raise YamatoError("team.yaml: report は {daily: on_down | off}")

    board = data.get("board") or {}
    if not isinstance(board, dict):
        raise YamatoError("team.yaml: board が mapping ではありません")
    kinds = board.get("kinds") or ["task"]
    archive_on_done = board.get("archive_on_done", True)
    if not isinstance(archive_on_done, bool):
        raise YamatoError("team.yaml: board.archive_on_done は true / false")
    fields = board.get("fields") or []
    columns = []
    for col in board.get("columns") or []:
        if not isinstance(col, dict) or "name" not in col or col.get("state") not in STATES:
            raise YamatoError(f"team.yaml: board.columns の要素は {{name, state: {'|'.join(STATES)}}} (今: {col!r})")
        columns.append({"name": str(col["name"]), "state": col["state"]})

    return {
        "name": name,
        "hub": hub,
        "workspace": str(wpath.resolve()),
        "charter": str(data.get("charter") or "charter.md"),
        "time_limit": time_limit,
        "grace": parse_duration(DEFAULT_GRACE if data.get("grace") is None else data["grace"]),
        "deny": deny,
        "settings": settings,
        "seat_stop": {**SEAT_STOP_DEFAULTS, **seat_stop},
        "roles": roles,
        "seats": seats,
        "board": {"kinds": [str(k) for k in kinds], "columns": columns, "fields": [str(f) for f in fields],
                  "archive_on_done": archive_on_done},
        "env_unset": env_unset,
        "inject": {"parts": inject.get("parts"), "limits": _limits(limits)},
        "notify": {"via": [str(v) for v in notify.get("via") or []], "command": notify.get("command"),
                   "slack": {"webhook_env": slack.get("webhook_env")},
                   "decisions": notify_decisions},
        "decisions": _decisions(data.get("decisions"), seats),
        "git": git,
        "report": {"daily": daily},
        "warnings": warnings,
    }


def _git(git) -> dict:
    git = git or {}
    if not isinstance(git, dict) or set(git) - GIT_KEYS:
        raise YamatoError(f"team.yaml: git の項目は {', '.join(sorted(GIT_KEYS))}")
    out = {**GIT_FALLBACK, **{k: v for k, v in git.items() if v is not None}}
    if out["merge_decision"] is False:   # an unquoted `off` is false in YAML 1.1
        out["merge_decision"] = "off"
    if out["strategy"] not in GIT_STRATEGIES:
        raise YamatoError(f"team.yaml: git.strategy は {' / '.join(GIT_STRATEGIES)} のどれか (今: {out['strategy']})")
    req = out["merge_requires"]
    if not isinstance(req, list) or set(req) - set(GIT_REQUIRES):
        raise YamatoError(f"team.yaml: git.merge_requires は {', '.join(GIT_REQUIRES)} から選んだリスト")
    if out["merge_decision"] not in ("auto", "off"):
        raise YamatoError("team.yaml: git.merge_decision は auto / off")
    for k in ("base", "conflict"):
        if not out[k] or not isinstance(out[k], str):
            raise YamatoError(f"team.yaml: git.{k} は文字列")
    return out


def _decisions(table, seats: dict) -> dict:
    """``decisions:`` (design-p1 §1.4): category -> {decider, when}.

    Both ``merge: owner`` and ``merge: {decider: owner, when: "..."}`` are
    accepted. The decider is a seat or ``owner`` (the human); a role of
    several seats has no single seat to deliver to, so it is refused here.
    """
    table = table or {}
    if not isinstance(table, dict):
        raise YamatoError("team.yaml: decisions は {category: decider} か {category: {decider, when}} の mapping")
    out = {}
    for cat, spec in table.items():
        check_name("decisions の category", str(cat))
        if isinstance(spec, str):
            spec = {"decider": spec}
        if not isinstance(spec, dict) or set(spec) - {"decider", "when"} or not spec.get("decider"):
            raise YamatoError(f"team.yaml: decisions.{cat} は decider の名前か {{decider, when}}")
        decider = str(spec["decider"])
        if decider not in seats and decider not in RESERVED_SEATS:
            raise YamatoError(f"team.yaml: decisions.{cat}.decider={decider} は席の名前か owner にする "
                              f"(席: {', '.join(seats)})")
        out[str(cat)] = {"decider": decider, "when": None if spec.get("when") is None else str(spec["when"])}
    return out


def _check_parts(parts, where: str) -> None:
    if not isinstance(parts, list) or set(parts) - set(INJECT_PARTS):
        raise YamatoError(f"team.yaml: {where} は {', '.join(INJECT_PARTS)} から選んだリスト")


def _limits(limits: dict) -> dict:
    out = {}
    for k, v in limits.items():
        if isinstance(v, list) and len(v) == 2 and all(isinstance(x, int) for x in v):
            out[k] = tuple(v)                     # [lines, chars]
        elif isinstance(v, int) and v > 0:
            out[k] = v
        else:
            raise YamatoError(f"team.yaml: inject.limits.{k} は正の整数か [行数, 文字数]")
    return out


def inject_parts(team: dict, seat: str) -> list[str]:
    role = team["seats"][seat]["role"]
    parts = team["roles"].get(role, {}).get("inject")
    if parts is None:
        parts = (team.get("inject") or {}).get("parts")
    return list(DEFAULT_INJECT_PARTS) if parts is None else parts


def git_conf(team: dict) -> dict:
    """``git:`` of a validated team (a ``.runtime/team.json`` from before P1 has none)."""
    return {**GIT_FALLBACK, **(team.get("git") or {})}


def load_team(shipdir: Path) -> dict:
    return validate(load_yaml(shipdir / "team.yaml"), shipdir)


def runtime_team(shipdir: Path) -> dict:
    """The team as the hooks see it: ``.runtime/team.json`` if present, else team.yaml."""
    data = read_json(shipdir / ".runtime" / "team.json")
    return data if data else load_team(shipdir)


def seat_spec(team: dict, seat: str) -> dict:
    try:
        return team["seats"][seat]
    except KeyError:
        raise YamatoError(f"席がありません: {seat} (席: {', '.join(team['seats'])})") from None

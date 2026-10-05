"""team.yaml: load, validate and expand roles into seats.

The validated result is written to ``.runtime/team.json`` by ``yamato up`` so
the per-turn hooks never have to import YAML.
"""
from __future__ import annotations

from pathlib import Path

from .notify import CHANNELS as NOTIFY_CHANNELS
from .util import YamatoError, check_name, parse_duration, read_json

SETTING_SOURCES = ("user", "project", "local")   # Claude Code's --setting-sources values
SHIFTS = ("per_task", "persistent", "headless")
STATES = ("open", "active", "blocked", "done")
TOP_KEYS = {"name", "hub", "workspace", "charter", "roles", "time_limit", "grace", "deny", "board",
            "settings", "setting_sources", "seat_stop", "env_unset", "inject", "notify", "git", "report", "decisions",
            "watch", "talk_default", "up_seats", "profiles", "memory", "last_call", "context_windows", "template"}
# what SessionStart can inject (design §8.2); the header is always there
INJECT_PARTS = ("handoff", "log_tail", "mine", "inbox", "memory", "knowledge", "last_report", "orphans", "board",
                "fleet")
# what a ship that names no parts gets: the captain's report excerpt (design-p1 §2.3) is opt-in,
# the orphaned items (§5.6) go to the captain only, the board overview (T-030) is opt-in too, and
# the fleet-wide summary (T-022, admiral's `ships` 相当) is opt-in since only admiral wants it
DEFAULT_INJECT_PARTS = tuple(p for p in INJECT_PARTS if p not in ("last_report", "orphans", "board", "fleet"))
# the role's memory and knowledge.md are cut at `memory.limits` (the limits `memory apply` keeps to)
INJECT_LIMIT_KEYS = ("handoff", "log_tail", "mine_items", "inbox_messages", "inbox_chars", "total_chars",
                     "last_report", "board_items", "fleet_items")
RESERVED_SEATS = ("owner",)   # the human's inbox; not a seat
SEAT_STOP_DEFAULTS = {"require_handoff": True, "require_delivery": True}
ROLE_KEYS = {"model", "shift", "count", "description", "inject", "max_duration", "max_budget_usd", "report_to",
             "trust", "remote_control", "rotate"}
# `profiles:` (design-p1 §7.2). yamato only writes a profile out (mode -> permissions.defaultMode,
# allow / deny -> the seat's settings, tools -> the role's definition) and refuses `yamato send`
# from a `send: false` seat. What `external` / `clean` hold is the template's business.
PROFILE_KEYS = {"mode", "tools", "allow", "deny", "send"}
DEFAULT_MODE = "auto"   # what a seat without a profile mode runs in (P0)
# `git:` (design-p1 §8.3). The values a ship runs with live in the template's
# team.yaml; these are only what a ship without a `git:` section gets.
# merge_decision was removed (D-021 案 b, #5): nothing read it, and whether to open a merge
# decision at all is now the reviewer/pm role prompt's call (mechanism-not-policy). An old
# team.yaml that still sets it is not rejected, only warned and ignored.
GIT_KEYS = {"base", "strategy", "merge_requires", "conflict"}
GIT_STRATEGIES = ("squash", "merge", "rebase")
GIT_REQUIRES = ("review", "ci", "decision")
GIT_FALLBACK = {"base": "main", "strategy": "squash", "merge_requires": [], "conflict": "author"}
GIT_REMOVED_KEYS = {"merge_decision"}
# auto mode is unavailable on Haiku (verify-p0-b §総括 1): the seat would fall
# back to manual and block on the first dialog. dontAsk works on Haiku (verify-p1-d V7),
# so only seats that run in auto are warned.
NO_AUTO_MODELS = ("haiku",)

# `watch:` (design-p1 §5.2, §5.5, §5.6): what `status` / `ships` show in red, and
# what send / seat-stop / the captain's injection look for. Records and warnings
# only; nothing here refuses anything.
WATCH_FALLBACK = {"stale_after": "20m"}
WATCH_LIFECYCLE_FALLBACK = {"spin": {"window": 600, "max_sends": 6, "same_text": True},
                            "captain_gap": 1800, "orphan_after": 1800}
# The lifecycle settings (design-p1 §5.3-5.6, §9). As with `git:`, the values a
# ship runs with live in the template's team.yaml; these are only what a ship
# (or a role) that does not write them gets. Every condition can be turned off.
ROTATE_KEYS = ("context", "compaction", "hours", "idle", "new_day")
ROTATE_FALLBACK = {"context": {"ratio": 0.3}, "compaction": True, "hours": 8 * 3600, "idle": 3600,
                   "new_day": True}
# model name (substring, longest match wins) -> context window in tokens (verify-p1-d V9)
CONTEXT_WINDOWS_FALLBACK = {"haiku": 200_000, "sonnet": 1_000_000, "opus": 1_000_000}
LAST_CALL_FALLBACK = {"at_most": 1800, "ratio": 0.2}

NOTIFY_DECISIONS = ("digest", "each")   # human deciders: gather into the daily report / one by one
DEFAULT_TIME_LIMIT = "3h"
DEFAULT_GRACE = "20m"
NO_TIME_LIMIT = "none"   # team.yaml: time_limit: none — the admiral-only exception (D-013, §0 B4)


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


def _template(raw) -> dict | None:
    """`template: {name: dev, version: 1.1.0}`: which template (and yamato version) the ship's copy came from.
    `ship create` writes it and `ship upgrade` moves it; nothing at run time reads it (D-081)."""
    if raw is None:
        return None
    if (not isinstance(raw, dict) or set(raw) != {"name", "version"}
            or not all(isinstance(raw[k], str) and raw[k].strip() for k in raw)):
        raise YamatoError(f"team.yaml: template は {{name: <ひな形>, version: <版>}} (今: {raw!r})")
    return {"name": raw["name"].strip(), "version": raw["version"].strip()}


def validate(data: dict, shipdir: Path) -> dict:
    unknown = set(data) - TOP_KEYS
    if unknown:
        raise YamatoError(f"team.yaml に知らない項目があります: {', '.join(sorted(unknown))}")
    name = check_name("艦", str(data.get("name") or ""))

    warnings: list[str] = []
    # `0` must not silently become the default (review N4)
    raw_time_limit = data.get("time_limit")
    if isinstance(raw_time_limit, str) and raw_time_limit.strip().lower() == NO_TIME_LIMIT:
        time_limit = None   # D-013: 上限を持たない艦 (admiral の土台。T-020)
    else:
        time_limit = parse_duration(DEFAULT_TIME_LIMIT if raw_time_limit is None else raw_time_limit)
        if time_limit <= 0:
            raise YamatoError("team.yaml: time_limit は 0 より長くする")
    roles_in = data.get("roles")
    if not isinstance(roles_in, dict) or not roles_in:
        raise YamatoError("team.yaml: roles が空です")
    profiles = _profiles(data.get("profiles"))
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
        trust = spec.get("trust")
        if trust is not None and str(trust) not in profiles:
            raise YamatoError(f"team.yaml: roles.{role}.trust={trust!r} が profiles にありません "
                              f"(ある: {', '.join(profiles) or 'なし'})")
        mode = (profiles[str(trust)]["mode"] if trust is not None else None) or DEFAULT_MODE
        if mode == "auto" and any(m in model.lower() for m in NO_AUTO_MODELS):
            warnings.append(f"roles.{role}.model={model} は auto モードを使えない (検証 B)。"
                            "manual に落ちて最初の書き込みで権限の確認になり、無人だと拒否される")
        remote_control = spec.get("remote_control", False)
        if not isinstance(remote_control, bool):
            raise YamatoError(f"team.yaml: roles.{role}.remote_control は true / false")
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
        if remote_control and shift == "headless":
            warnings.append(f"roles.{role}.remote_control は bg の席でだけ効く (headless の -p には付けない)。無視する")
        roles[role] = {
            "rotate": _rotate(spec.get("rotate"), role),
            "inject": parts,
            "max_duration": max_duration,
            "max_budget_usd": budget,
            "report_to": str(spec["report_to"]) if spec.get("report_to") else None,
            "model": model,
            "shift": shift,
            "count": count,
            "description": str(spec.get("description") or role),
            "trust": None if trust is None else str(trust),
            "remote_control": remote_control,
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
    # a string (one repo, as ever) or a flat list of paths (D-071); the seat cwd is the first one
    raw_ws = ws if isinstance(ws, list) else [ws]
    if not ws or not all(isinstance(x, str) and x for x in raw_ws):
        raise YamatoError("team.yaml: workspace がありません (パスの文字列か、パスのリスト)")
    workspaces = []
    for item in raw_ws:
        wp = Path(item).expanduser()
        if not wp.is_absolute():
            wp = shipdir / wp
        workspaces.append({"name": wp.resolve().name, "path": str(wp.resolve())})
    seen_names: dict[str, str] = {}
    for w in workspaces:
        if w["name"] in seen_names:
            raise YamatoError(f"team.yaml: workspace の呼び名 (フォルダ名) {w['name']!r} がかぶっている: "
                              f"{seen_names[w['name']]} / {w['path']}")
        seen_names[w["name"]] = w["path"]

    deny = data.get("deny") or []
    if not isinstance(deny, list) or not all(isinstance(x, str) for x in deny):
        raise YamatoError("team.yaml: deny は文字列のリスト")

    settings = data.get("settings") or {}
    if not isinstance(settings, dict):
        raise YamatoError("team.yaml: settings は mapping (Claude Code の settings.json に重ねる中身)")
    sources = data.get("setting_sources")
    if sources is None:
        sources = ["project", "local"]
    if not isinstance(sources, list) or not sources or not all(isinstance(x, str) for x in sources) \
            or set(sources) - set(SETTING_SOURCES):
        raise YamatoError(f"team.yaml: setting_sources は {' / '.join(SETTING_SOURCES)} のリスト (空は不可。今: {sources!r})")
    setting_sources = [s for s in SETTING_SOURCES if s in sources]
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
    if isinstance(limits, dict) and set(limits) & {"memory", "knowledge"}:
        raise YamatoError("team.yaml: inject.limits の memory / knowledge は memory.limits に一本化した "
                          "(memory_lines / memory_chars / knowledge_lines / knowledge_chars。memory apply の上限と同じ)")
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

    git = _git(data.get("git"), warnings)

    report = data.get("report") or {}
    daily = report.get("daily", "on_down") if isinstance(report, dict) else None
    if daily is False:   # an unquoted `off` is false in YAML 1.1
        daily = "off"
    if not isinstance(report, dict) or set(report) - {"daily"} or daily not in ("on_down", "off"):
        raise YamatoError("team.yaml: report は {daily: on_down | off}")

    watch = data.get("watch") or {}
    if not isinstance(watch, dict) or set(watch) - {*WATCH_FALLBACK, *WATCH_LIFECYCLE_FALLBACK}:
        raise YamatoError("team.yaml: watch は {stale_after: 20m, captain_gap: 30m, orphan_after: 30m, "
                          "spin: {window: 10m, max_sends: 6, same_text: true}}")
    stale_after = parse_duration(watch.get("stale_after") or WATCH_FALLBACK["stale_after"])

    talk_default = str(data.get("talk_default") or hub)
    if talk_default not in seats:
        raise YamatoError(f"team.yaml: talk_default={talk_default!r} が席にありません")

    raw_up = data.get("up_seats")
    if raw_up is None:
        raw_up = []
    if not isinstance(raw_up, list) or not all(isinstance(x, str) for x in raw_up):
        raise YamatoError("team.yaml: up_seats は席の名前のリスト")
    for s_name in raw_up:
        if s_name not in seats:
            raise YamatoError(f"team.yaml: up_seats の {s_name!r} が席にありません")
    up_seats = [s_name for s_name in dict.fromkeys(raw_up) if s_name != hub]

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
        "template": _template(data.get("template")),   # {name, version} the ship was made from (D-081), or None
        "hub": hub,
        "workspace": workspaces[0]["path"],   # the seat cwd = the first repo
        "workspaces": workspaces,             # [{name: basename, path}]; one entry for a string workspace
        "charter": str(data.get("charter") or "charter.md"),
        "time_limit": time_limit,
        "grace": parse_duration(DEFAULT_GRACE if data.get("grace") is None else data["grace"]),
        "deny": deny,
        "settings": settings,
        "setting_sources": setting_sources,
        "profiles": profiles,
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
        "watch": {"stale_after": stale_after, **_watch_lifecycle(watch)},
        "talk_default": talk_default,
        "up_seats": up_seats,
        "memory": _memory(data.get("memory"), roles),
        "last_call": _last_call(data.get("last_call")),
        "context_windows": _context_windows(data.get("context_windows")),
        "warnings": warnings,
    }


def _git(git, warnings: list[str]) -> dict:
    git = git or {}
    if not isinstance(git, dict):
        raise YamatoError(f"team.yaml: git の項目は {', '.join(sorted(GIT_KEYS))}")
    removed = set(git) & GIT_REMOVED_KEYS
    for k in sorted(removed):
        warnings.append(f"team.yaml: git.{k} は外れた (D-021)。無視する")
    unknown = set(git) - GIT_KEYS - GIT_REMOVED_KEYS
    if unknown:
        raise YamatoError(f"team.yaml: git の項目は {', '.join(sorted(GIT_KEYS))}")
    out = {**GIT_FALLBACK, **{k: v for k, v in git.items() if k not in GIT_REMOVED_KEYS and v is not None}}
    if out["strategy"] not in GIT_STRATEGIES:
        raise YamatoError(f"team.yaml: git.strategy は {' / '.join(GIT_STRATEGIES)} のどれか (今: {out['strategy']})")
    req = out["merge_requires"]
    if not isinstance(req, list) or set(req) - set(GIT_REQUIRES):
        raise YamatoError(f"team.yaml: git.merge_requires は {', '.join(GIT_REQUIRES)} から選んだリスト")
    for k in ("base", "conflict"):
        if not out[k] or not isinstance(out[k], str):
            raise YamatoError(f"team.yaml: git.{k} は文字列")
    return out


def _profiles(table) -> dict:
    """``profiles:`` (design-p1 §7.2): name -> {mode, tools, allow, deny, send}.

    Only the shape is checked. The mode is passed through to Claude Code as is
    (``dontAsk`` for roles that read outside text is the template's default,
    verify-p1-d V7; nothing here insists on it)."""
    table = table or {}
    if not isinstance(table, dict):
        raise YamatoError("team.yaml: profiles は {名前: {mode, tools, allow, deny, send}} の mapping")
    out = {}
    for name, spec in table.items():
        check_name("profiles の名前", str(name))
        spec = spec or {}
        if not isinstance(spec, dict) or set(spec) - PROFILE_KEYS:
            raise YamatoError(f"team.yaml: profiles.{name} の項目は {', '.join(sorted(PROFILE_KEYS))}")
        mode = spec.get("mode")
        if mode is not None and (not isinstance(mode, str) or not mode):
            raise YamatoError(f"team.yaml: profiles.{name}.mode は Claude Code の権限モードの名前 (例 dontAsk / auto)")
        for key in ("tools", "allow", "deny"):
            v = spec.get(key)
            if v is not None and (not isinstance(v, list) or not all(isinstance(x, str) for x in v)):
                raise YamatoError(f"team.yaml: profiles.{name}.{key} は文字列のリスト")
        send = spec.get("send", True)
        if not isinstance(send, bool):
            raise YamatoError(f"team.yaml: profiles.{name}.send は true / false")
        out[str(name)] = {"mode": mode, "tools": spec.get("tools"), "allow": spec.get("allow") or [],
                          "deny": spec.get("deny") or [], "send": send}
    return out


def profile_of(team: dict, role: str) -> dict | None:
    """The trust profile of ``role`` (None when the role names none, or a team.json from before P1)."""
    trust = (team["roles"].get(role) or {}).get("trust")
    return (team.get("profiles") or {}).get(trust) if trust else None


def _memory(raw, roles: dict) -> dict:
    """``memory:`` (design-p1 §3). Lives with the commands in memory.py."""
    from .memory import validate_conf

    return validate_conf(raw, roles)


def _off(v) -> bool:
    """``off`` / ``false`` / ``null`` turn a condition off (an unquoted `off` is false in YAML 1.1)."""
    return v is None or v is False or (isinstance(v, str) and v.strip().lower() in ("off", "false", "no"))


def _ratio(v, where: str) -> float:
    """``30%`` or ``0.3`` -> 0.3."""
    try:
        r = float(v.strip().rstrip("%")) / 100 if isinstance(v, str) and v.strip().endswith("%") else float(v)
    except (TypeError, ValueError):
        raise YamatoError(f"team.yaml: {where} は割合 (例 30%) (今: {v!r})") from None
    if isinstance(v, bool) or not 0 < r <= 1:
        raise YamatoError(f"team.yaml: {where} は 0 より大きく 100% 以下の割合 (今: {v!r})")
    return r


def _tokens(v, where: str) -> int:
    """``300000`` or ``300k`` -> 300000."""
    s = str(v).strip().lower()
    try:
        n = int(float(s[:-1]) * 1000) if s.endswith("k") else int(s)
    except ValueError:
        raise YamatoError(f"team.yaml: {where} はトークン数 (例 300k) か割合 (例 30%) (今: {v!r})") from None
    if isinstance(v, bool) or n <= 0:
        raise YamatoError(f"team.yaml: {where} は正のトークン数 (今: {v!r})")
    return n


def _span(v, where: str, bare_unit: int = 60) -> int | None:
    """A duration or ``off``. A bare number is in ``bare_unit`` seconds (``hours: 8`` is 8 hours)."""
    if _off(v):
        return None
    secs = int(v * bare_unit) if isinstance(v, (int, float)) and not isinstance(v, bool) else parse_duration(v)
    if secs <= 0:
        raise YamatoError(f"team.yaml: {where} は 0 より長くするか off (今: {v!r})")
    return secs


def _rotate(spec, role: str) -> dict:
    """``roles.<role>.rotate`` (design-p1 §5.3, §5.4): when a persistent seat gets a fresh shift."""
    spec = spec or {}
    if not isinstance(spec, dict) or set(spec) - set(ROTATE_KEYS):
        raise YamatoError(f"team.yaml: roles.{role}.rotate の項目は {', '.join(ROTATE_KEYS)}")
    out = dict(ROTATE_FALLBACK)
    where = f"roles.{role}.rotate"
    if "context" in spec:
        v = spec["context"]
        if _off(v):
            out["context"] = None
        elif (isinstance(v, str) and v.strip().endswith("%")) or (isinstance(v, float) and 0 < v < 1):
            # 30% or 0.3 (as last_call.ratio); a whole number is tokens
            out["context"] = {"ratio": _ratio(v, f"{where}.context")}
        else:
            out["context"] = {"tokens": _tokens(v, f"{where}.context")}
    for k in ("compaction", "new_day"):
        if k in spec:
            if _off(spec[k]):
                out[k] = False
            elif spec[k] is True:
                out[k] = True
            else:
                raise YamatoError(f"team.yaml: {where}.{k} は true / false (今: {spec[k]!r})")
    if "hours" in spec:
        out["hours"] = _span(spec["hours"], f"{where}.hours", bare_unit=3600)
    if "idle" in spec:
        out["idle"] = _span(spec["idle"], f"{where}.idle")
    return out


def _watch_lifecycle(spec: dict) -> dict:
    """``watch.spin`` / ``captain_gap`` / ``orphan_after`` (design-p1 §5.2, §5.5, §5.6)."""
    out = {**WATCH_LIFECYCLE_FALLBACK, "spin": dict(WATCH_LIFECYCLE_FALLBACK["spin"])}
    spin = spec.get("spin", {})
    if _off(spin):
        out["spin"] = None
    else:
        if not isinstance(spin, dict) or set(spin) - set(WATCH_LIFECYCLE_FALLBACK["spin"]):
            raise YamatoError("team.yaml: watch.spin は {window: 10m, max_sends: 6, same_text: true} か off")
        if "window" in spin:
            out["spin"]["window"] = _span(spin["window"], "watch.spin.window")
        if "max_sends" in spin:
            n = spin["max_sends"]
            if _off(n):
                out["spin"]["max_sends"] = None
            elif isinstance(n, bool) or not isinstance(n, int) or n < 1:
                raise YamatoError(f"team.yaml: watch.spin.max_sends は 1 以上の整数か off (今: {n!r})")
            else:
                out["spin"]["max_sends"] = n
        if "same_text" in spin:
            out["spin"]["same_text"] = not _off(spin["same_text"])
    for k in ("captain_gap", "orphan_after"):
        if k in spec:
            out[k] = _span(spec[k], f"watch.{k}")
    return out


def _last_call(spec) -> dict | None:
    """``last_call:`` (design-p1 §9): ``min(at_most, ratio x time limit)`` before the deadline."""
    if spec is not None and _off(spec):
        return None
    spec = spec or {}
    if not isinstance(spec, dict) or set(spec) - set(LAST_CALL_FALLBACK):
        raise YamatoError("team.yaml: last_call は {at_most: 30m, ratio: 20%} か off")
    out = dict(LAST_CALL_FALLBACK)
    if "at_most" in spec:
        out["at_most"] = _span(spec["at_most"], "last_call.at_most")
    if "ratio" in spec:
        out["ratio"] = None if _off(spec["ratio"]) else _ratio(spec["ratio"], "last_call.ratio")
    if out["at_most"] is None and out["ratio"] is None:
        return None
    return out


def _context_windows(table) -> dict:
    table = table or {}
    if not isinstance(table, dict):
        raise YamatoError("team.yaml: context_windows は {モデル名の一部: 窓のトークン数} の mapping")
    return {**CONTEXT_WINDOWS_FALLBACK,
            **{str(k).lower(): _tokens(v, f"context_windows.{k}") for k, v in table.items()}}


def rotate_conf(team: dict, seat: str) -> dict:
    """The seat's ``rotate:`` (a ``.runtime/team.json`` from before P1-5 has none)."""
    role = team["roles"].get(team["seats"][seat]["role"], {})
    return {**ROTATE_FALLBACK, **(role.get("rotate") or {})}


def watch_conf(team: dict) -> dict:
    """``watch:`` with the lifecycle keys (a ``.runtime/team.json`` from before P1-5 has none)."""
    return {**WATCH_LIFECYCLE_FALLBACK, **(team.get("watch") or {})}


def last_call_conf(team: dict) -> dict | None:
    return team["last_call"] if "last_call" in team else dict(LAST_CALL_FALLBACK)


def context_window(team: dict, model: str | None) -> int | None:
    """The window of ``model`` from the table (substring match, the longest key wins)."""
    table = team.get("context_windows") or CONTEXT_WINDOWS_FALLBACK
    m = (model or "").lower()
    hits = [k for k in table if k and k in m]
    return table[max(hits, key=len)] if hits else None


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
    if parts is None:
        return [*DEFAULT_INJECT_PARTS, *(("orphans",) if seat == team["hub"] else ())]
    return parts


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

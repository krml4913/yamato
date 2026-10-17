"""yamato dashboard: ローカルのブラウザで全艦の「owner の判断待ち」と席の状況を見る。

サーバーは ``/api/v1/*`` の JSON (応答の形の正本は docs/api/openapi.yaml) と ``web/dist/`` の配信だけ
(HTML は組まない。画面は web/ のフロントが API を読んで描く)。

読むだけ: 艦フォルダに何も書かない。``seat.status`` は呼ばない (reconcile・enforce・
restop_stuck で roster を書き換え、席を止めるため)。roster・board・inbox・events・usage・
作業ログを読み、``claude agents --json`` は 1 回の描画で 1 回だけ呼ぶ。
サーバーは 127.0.0.1 だけ・GET だけ。bind 先を変えるオプションは作らない。標準ライブラリだけ (http.server)。
"""
from __future__ import annotations

import json
import re
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import admiral, claude, deadline, feed, fleet, inbox, memory, monitor, procs, report, roster, rotate, seat, shipconfig
from . import board as board_mod
from .decide import _epoch
from .web import files as web_files
from .web import static
from .util import YamatoError, fmt_span, fmt_time, today
from .worktree import field_map

HOST = "127.0.0.1"          # bind 先は固定 (オプションにしない)
DEFAULT_PORT = 8765
PORT_TRIES = 10             # --port を書かないとき、使用中なら次の番号を試す回数
ANOMALIES = 8               # 艦ごとに出す直近の異常の件数
ANOMALY_WINDOW = 3 * 86400  # 異常を拾う期間 (秒)
CONTEXT_NEAR = 0.8          # 文脈量が rotate の閾値のこの割合を超えたら「近い」と出す

# --- 読み取り (艦ごと。書かない) --------------------------------------------------

def _last_log_line(shipdir: Path, seat_name: str) -> str:
    p = Path(shipdir) / "seats" / seat_name / "log" / f"{today()}.md"
    try:
        lines = [x.strip() for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, UnicodeDecodeError):
        return ""
    return lines[-1].lstrip("-").strip() if lines else ""


def _repo_url(team: dict) -> str | None:
    """workspace の .git/config の origin から GitHub の URL を読む (読むだけ。無ければ None)。"""
    try:
        text = (Path(team["workspace"]) / ".git" / "config").read_text(encoding="utf-8")
    except (OSError, KeyError, UnicodeDecodeError):
        return None
    m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', text)
    if not m:
        return None
    mm = re.match(r"(?:git@github\.com:|https://github\.com/|ssh://git@github\.com/)(.+?)(?:\.git)?/?$", m.group(1))
    return f"https://github.com/{mm.group(1)}" if mm else None


def _today_usage(shipdir: Path, now: float) -> dict[str, int]:
    """席ごとの今日のトークン (``report.usage_lines`` と同じ usage.jsonl の total_tokens の合計)。"""
    since = time.mktime(time.strptime(today(), "%Y-%m-%d"))
    out: dict[str, int] = {}
    try:
        text = (Path(shipdir) / "usage.jsonl").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for raw in text.splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and since <= (row.get("ts") or 0) <= now:
            k = row.get("seat") or "?"
            out[k] = out.get(k, 0) + int(row.get("total_tokens") or 0)
    return out


def _last_cost(shipdir: Path) -> dict[str, float | None]:
    """席ごとの最新の headless の記録の total_cost_usd (無い・null・数でなければ None)。"""
    out: dict[str, float | None] = {}
    try:
        text = (Path(shipdir) / "usage.jsonl").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for raw in text.splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("shift") == "headless":
            c = row.get("total_cost_usd")
            out[row.get("seat") or "?"] = c if isinstance(c, (int, float)) and not isinstance(c, bool) else None
    return out


def _report_summary(shipdir: Path) -> dict | None:
    """前回の日報 (report.latest) の「一言」「明日」。日報が無ければ None。"""
    p = report.latest(shipdir)
    if p is None:
        return None
    try:
        sec = report.sections(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    return {"date": p.stem, "word": sec.get(report.S_WORD, "").strip(), "tomorrow": sec.get(report.S_TOMORROW, "").strip()}


def _context_of(team: dict, shipdir: Path, seat_name: str, rec: dict) -> dict | None:
    """席の文脈量 (rotate の判定と同じ last_usage / context_threshold)。取れなければ None。"""
    if not rec.get("sessionId"):
        return None
    try:
        mains = [p for p in claude.transcript_paths(rec["sessionId"]) if p.parent.name != "subagents"]
        use = rotate.last_usage(mains[0]) if mains else None
        if not use:
            return None
        return {"tokens": use["tokens"], "limit": rotate.context_threshold(team, seat_name, use.get("model"))}
    except (OSError, ValueError, KeyError):
        return None


def gather(name: str, shipdir: Path, by: dict, now: float) -> dict:
    """1 艦ぶんの事実 (openapi.yaml の ShipOverview.state の形。ただし ``waits`` は全艦の一覧に
    集めるため同居させている。呼び側 (``overview``) が取り出す)。壊れていれば例外。"""
    team = seat.current_team(shipdir)
    dl = deadline.read(shipdir)
    items = board_mod.Board(shipdir, team).items()
    active_assignees = board_mod.active_seats(items)
    usage = _today_usage(shipdir, now)
    costs = _last_cost(shipdir)
    seats = []
    for s, spec in team["seats"].items():
        rec = roster.seat(shipdir, s)
        live = by.get(rec.get("sessionId"))
        alive = claude.is_alive(live)
        if not alive and spec["shift"] == "headless" and rec.get("pid") and procs.pid_alive(rec["pid"]):
            alive, live = True, {**(live or {}), "pid": rec["pid"]}
        last = seat._last_active(rec)
        seats.append({
            "name": s, "role": spec.get("role"), "shift": spec["shift"], "alive": alive,
            "pid": (live or {}).get("pid"), "status": (live or {}).get("status"),
            "waiting_for": (live or {}).get("waitingFor"), "last": last,
            "active": [m["id"] for m in items if m.get("state") == "active" and m.get("assignee") == s],
            "reviewing": [m["id"] for m in items if m.get("state") == "active" and m.get("reviewer") == s
                          and m.get("assignee") != s],
            "unread": len(inbox.unread(shipdir, s)),
            "flags": [{"level": f.level, "text": f.text, "detail": f.detail}
                      for f in admiral.red_flags(team, s, rec, live, now, has_active=s in active_assignees)],
            "log": _last_log_line(shipdir, s), "tokens": usage.get(s, 0),
            "budget": (team["roles"].get(spec.get("role")) or {}).get("max_budget_usd") if spec["shift"] == "headless" else None,
            "last_cost": costs.get(s),
        })
    waits = []
    for meta, body in report.pending_decisions(shipdir, team, lambda d: report.is_human(team, d)):
        opened = _epoch(meta.get("opened_at"))
        waits.append({"ship": name, "id": str(meta.get("id")), "title": str(meta.get("title") or ""), "urgent": bool(meta.get("urgent")),
                      "age": fmt_span(now - opened) if opened else "",
                      "recommendation": report._recommendation(body) or None})
    waits.sort(key=lambda w: (not w["urgent"], w["id"]))
    counts: dict[str, int] = {}
    for m in items:
        if m.get("kind") != "decision":
            counts[m.get("state") or "?"] = counts.get(m.get("state") or "?", 0) + 1
    contexts = {}
    for s in team["seats"]:
        ctx = _context_of(team, shipdir, s, roster.seat(shipdir, s))
        if ctx:
            contexts[s] = ctx
    for x in seats:
        x["context"] = contexts.get(x["name"])
    orphans = [{"id": str(m.get("id")), "title": str(m.get("title") or ""), "seat": m.get("assignee"), "why": why}
               for m, why in monitor.orphans(shipdir, team, now)]
    mem = [{"role": r["role"], "count": r["count"], "days": r["days"], "due": bool(r["due"])}
           for r in memory.status(shipdir, team, now)]
    url = _repo_url(team)
    prs = []
    for m in items:
        if m.get("state") == "done":
            continue
        for repo, num in field_map(team, m, "pr").items():
            link = f"{url}/pull/{num.lstrip('#')}" if url and num.lstrip("#").isdigit() else None
            prs.append({"item": m["id"], "title": str(m.get("title") or ""), "state": m.get("state"), "repo": repo,
                        "number": num.lstrip("#"), "link": link})
    anomalies = [{"kind": ev["kind"], "ts": ev.get("ts") or 0, "summary": str(ev.get("summary") or "")}
                 for ev in events_read(shipdir, now) if ev.get("kind") in feed.ABNORMAL_KINDS][-ANOMALIES:][::-1]
    return {
        "team": team["name"], "deadline": deadline.describe(dl, now), "seats": seats,
        "waits": waits, "owner_unread": len(inbox.unread(shipdir, inbox.OWNER)),
        "counts": counts,
        "board": [{"id": m["id"], "title": str(m.get("title") or ""), "state": m.get("state"), "assignee": m.get("assignee"),
                   "blocked_on": m.get("blocked_on") or []}
                  for m in items if m.get("state") in ("active", "blocked") and m.get("kind") != "decision"],
        "report": _report_summary(shipdir), "orphans": orphans, "memory": mem,
        "prs": prs, "anomalies": anomalies, "tokens": sum(usage.values()), "multi_repo": len(team.get("workspaces") or []) > 1,
        "hub": team["hub"], "hub_last": next((x["last"] for x in seats if x["name"] == team["hub"]), None),
    }


def events_read(shipdir: Path, now: float) -> list[dict]:
    from . import events

    return events.read(shipdir, since=now - ANOMALY_WINDOW)


# --- API ----------------------------------------------------------------------

API_OVERVIEW = "/api/v1/overview"
SHIP_FILE = re.compile(r"^/api/v1/ships/([^/]+)/files/([^/]+)$")


def overview(ships: dict[str, Path], by: dict, now: float, agents_error: str | None = None) -> dict:
    """``GET /api/v1/overview`` の応答 (HTTP なし)。1 艦の読み取りが落ちても、その艦の error に
    理由を入れて state を null にし、他の艦は返す。"""
    out, waits = [], []
    for name, path in ships.items():
        try:
            state = gather(name, path, by, now)
        except Exception as ex:  # noqa: BLE001 — 壊れた艦 1 つで全体を落とさない
            out.append({"name": name, "error": f"{type(ex).__name__}: {ex}", "state": None})
            continue
        waits += state.pop("waits")
        out.append({"name": name, "error": None, "state": state})
    waits.sort(key=lambda w: (not w["urgent"], w["ship"], w["id"]))
    return {"generated_at": now, "agents_error": agents_error, "waits": waits, "ships": out}


def overview_now(now: float | None = None) -> dict:
    """全艦を読む。``claude agents --json`` は 1 回の応答で 1 回。"""
    now = time.time() if now is None else now
    try:
        by, err = claude.by_session(claude.agents()), None
    except YamatoError as ex:
        by, err = {}, str(ex)
    return overview(admiral.all_ships(), by, now, err)


def _error(code: str, message: str, detail: str | None = None) -> dict:
    return {"error": {"code": code, "message": message, "detail": detail}}


def ship_file(name: str, file: str) -> tuple[int, dict]:
    """``files/{file}``: 艦は ``all_ships`` の dict から、ファイルは列挙の dict から引く。"""
    shipdir = admiral.all_ships().get(name)
    if shipdir is None:
        return 404, _error("not_found", "その艦は無い", name)
    try:
        body = web_files.read(shipdir, name, file)
    except OSError as ex:
        return 500, _error("unreadable", "ファイルを読めなかった", type(ex).__name__)
    if body is None:
        return 404, _error("not_found", "その md は読めない (roles/*.md・charter.md・knowledge.md だけ)", file)
    return 200, body


def api_get(path: str) -> tuple[int, dict]:
    """``/api/v1/`` 以下の GET を (status, JSON にする値) にする。HTTP なし。"""
    if path == API_OVERVIEW:
        try:
            return 200, overview_now()
        except Exception as ex:  # noqa: BLE001
            return 500, _error("internal", "全艦の読み取りに失敗した", type(ex).__name__)
    if path == "/api/v1/fleet":
        try:
            return 200, fleet.fleet()
        except Exception as ex:  # noqa: BLE001
            return 500, _error("internal", "艦の一覧の読み取りに失敗した", type(ex).__name__)
    m = SHIP_FILE.match(path)
    if m:
        return ship_file(unquote(m.group(1)), unquote(m.group(2)))
    m = re.fullmatch(r"/api/v1/ships/([^/]+)/config", path)
    if m:
        return ship_config_get(unquote(m.group(1)))
    return 404, _error("not_found", "その API は無い")


def ship_config_get(name: str) -> tuple[int, dict]:
    """``GET /api/v1/ships/{name}/config``。艦は ships.json にあるものだけ。読めない艦はエラーの JSON にする。"""
    shipdir = shipconfig.registered(name)
    if shipdir is None:
        return 404, _error("not_found", f"艦がありません: {name}")
    try:
        return 200, shipconfig.ship_config(name, shipdir)
    except YamatoError as ex:
        return 500, _error("unreadable", str(ex), "YamatoError")
    except Exception as ex:  # noqa: BLE001 — 壊れた艦 1 つでサーバーを落とさない
        return 500, _error("internal", "艦の設定の読み取りに失敗した", type(ex).__name__)


# --- server -------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    """GET だけ。ほかのメソッドは 501 (do_* を定義しない)。"""

    def _send(self, status: int, data: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api" or path.startswith("/api/"):
            status, body = api_get(path)
            self._send(status, json.dumps(body, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
            return
        status, data, ctype = self.server.dist.get(path)
        self._send(status, data, ctype)

    def log_message(self, *a):  # noqa: D401 — アクセスログは出さない
        pass


def make_server(port: int | None) -> ThreadingHTTPServer:
    """127.0.0.1 に bind する。port 無指定は DEFAULT_PORT から順に空きを探す。指定済みで使用中ならエラー。"""
    tries = [port] if port is not None else [DEFAULT_PORT + i for i in range(PORT_TRIES)]
    last = None
    for p in tries:
        try:
            srv = ThreadingHTTPServer((HOST, p), Handler)
            srv.dist = static.Dist(static.DIST)   # 起動時に dist の実在ファイルの一覧を作る
            return srv
        except OSError as ex:
            last = ex
    hint = " (--port で別の番号を指定する)" if port is None else ""
    raise YamatoError(f"port {tries[0]}{'-' + str(tries[-1]) if len(tries) > 1 else ''} を使えません: {last}{hint}")


def serve(port: int | None = None, open_browser: bool = True, out=print) -> int:
    srv = make_server(port)
    url = f"http://{HOST}:{srv.server_address[1]}/"
    out(f"yamato dashboard: {url} (Ctrl-C で止める)")
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def register(sub) -> None:
    d = sub.add_parser("dashboard", help="全艦の owner の判断待ちと席の状況をローカルのブラウザで見る (127.0.0.1・読むだけ)")
    d.add_argument("--port", type=int, help=f"待ち受ける port (既定 {DEFAULT_PORT}。使用中なら次の番号を試す)")
    d.add_argument("--no-open", action="store_true", help="ブラウザを開かない")


def run(args) -> int:
    return serve(args.port, not args.no_open)

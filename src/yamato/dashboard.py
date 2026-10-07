"""yamato dashboard (D-093, T-079): ローカルのブラウザで全艦の「元帥待ち」と席の状況を見る。

読むだけ: 艦フォルダに何も書かない。``seat.status`` は呼ばない (reconcile・enforce・
restop_stuck で roster を書き換え、席を止めるため)。roster・board・inbox・events・usage・
作業ログを読み、``claude agents --json`` は 1 回の描画で 1 回だけ呼ぶ。
サーバーは 127.0.0.1 だけ・GET だけ。bind 先を変えるオプションは作らない。標準ライブラリだけ
(http.server)、テンプレートエンジンも CDN も使わず、HTML は ``html.escape`` を通して組み立てる。
"""
from __future__ import annotations

import html
import re
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import admiral, claude, deadline, feed, inbox, procs, report, roster, seat
from . import board as board_mod
from .decide import _epoch
from .util import YamatoError, fmt_span, fmt_time, today
from .worktree import field_map

HOST = "127.0.0.1"          # bind 先は固定 (オプションにしない)
DEFAULT_PORT = 8765
PORT_TRIES = 10             # --port を書かないとき、使用中なら次の番号を試す回数
REFRESH = 60                # 秒。ページの自動再読み込み
ANOMALIES = 8               # 艦ごとに出す直近の異常の件数
ANOMALY_WINDOW = 3 * 86400  # 異常を拾う期間 (秒)

e = html.escape


def _q(s) -> str:
    return e(str(s), quote=True)


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
    import json

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


def gather(name: str, shipdir: Path, by: dict, now: float) -> dict:
    """1 艦ぶんの事実。壊れていれば例外 (呼び側が枠にエラーを出す)。"""
    team = seat.current_team(shipdir)
    dl = deadline.read(shipdir)
    items = board_mod.Board(shipdir, team).items()
    active_assignees = {m.get("assignee") for m in items if m.get("state") == "active"}
    usage = _today_usage(shipdir, now)
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
            "waitingFor": (live or {}).get("waitingFor"), "last": last,
            "active": [m["id"] for m in items if m.get("state") == "active" and m.get("assignee") == s],
            "unread": len(inbox.unread(shipdir, s)),
            "red": admiral.red_flags(team, s, rec, live, now, has_active=s in active_assignees),
            "log": _last_log_line(shipdir, s), "tokens": usage.get(s, 0),
            "budget": (team["roles"].get(spec.get("role")) or {}).get("max_budget_usd"),
        })
    waits = []
    for meta, body in report.pending_decisions(shipdir, team, lambda d: report.is_human(team, d)):
        opened = _epoch(meta.get("opened_at"))
        waits.append({"id": meta.get("id"), "title": meta.get("title"), "urgent": bool(meta.get("urgent")),
                      "age": fmt_span(now - opened) if opened else "", "rec": report._recommendation(body)})
    waits.sort(key=lambda w: (not w["urgent"], str(w["id"])))
    counts: dict[str, int] = {}
    for m in items:
        if m.get("kind") != "decision":
            counts[m.get("state") or "?"] = counts.get(m.get("state") or "?", 0) + 1
    url = _repo_url(team)
    prs = []
    for m in items:
        if m.get("state") == "done":
            continue
        for repo, num in field_map(team, m, "pr").items():
            link = f"{url}/pull/{num.lstrip('#')}" if url and num.lstrip("#").isdigit() else None
            prs.append({"item": m["id"], "title": m.get("title"), "state": m.get("state"), "repo": repo,
                        "pr": num, "link": link})
    anomalies = [ev for ev in events_read(shipdir, now) if ev.get("kind") in feed.ABNORMAL_KINDS][-ANOMALIES:]
    return {
        "name": name, "team": team["name"], "deadline": deadline.describe(dl, now), "seats": seats,
        "waits": waits, "owner_unread": [{"n": x.get("n"), "from": x.get("from"), "text": x.get("text", "")}
                                         for x in inbox.unread(shipdir, inbox.OWNER)],
        "waiting_seats": [{"name": x["name"], "waitingFor": x["waitingFor"]} for x in seats
                          if x["alive"] and (x["status"] == "waiting" or x["waitingFor"])],
        "counts": counts,
        "board": [{"id": m["id"], "title": m.get("title"), "state": m.get("state"), "assignee": m.get("assignee"),
                   "blocked_on": m.get("blocked_on") or []}
                  for m in items if m.get("state") in ("active", "blocked") and m.get("kind") != "decision"],
        "prs": prs, "anomalies": anomalies, "tokens": sum(usage.values()), "multi_repo": len(team.get("workspaces") or []) > 1,
        "hub": team["hub"], "hub_last": next((x["last"] for x in seats if x["name"] == team["hub"]), None),
    }


def events_read(shipdir: Path, now: float) -> list[dict]:
    from . import events

    return events.read(shipdir, since=now - ANOMALY_WINDOW)


# --- HTML ---------------------------------------------------------------------

CSS = """
body{font:14px/1.5 -apple-system,BlinkMacSystemFont,sans-serif;margin:0;background:#f5f5f4;color:#1c1917}
@media(prefers-color-scheme:dark){body{background:#1c1917;color:#e7e5e4}.card{background:#292524!important}
th{color:#a8a29e!important}a{color:#7dd3fc!important}}
main{max-width:1200px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:0 0 8px}h3{font-size:13px;margin:12px 0 4px;color:#78716c}
.card{background:#fff;border-radius:8px;padding:12px 16px;margin:12px 0;box-shadow:0 1px 2px #0002}
.top{border-left:6px solid #dc2626}.quiet{border-left:6px solid #16a34a}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:3px 8px;border-bottom:1px solid #7773;vertical-align:top}
th{font-weight:600;color:#57534e;font-size:12px}
.red{color:#dc2626;font-weight:600}.dim{color:#78716c}.ok{color:#16a34a}.warn{color:#d97706}
.tag{display:inline-block;border-radius:4px;padding:0 6px;background:#7772;margin-right:4px}
.urgent{background:#dc2626;color:#fff}
"""


def _ul(rows: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{r}</li>" for r in rows) + "</ul>" if rows else ""


def _waiting_section(data: list[tuple[str, dict | None, str | None]]) -> str:
    rows = []
    for name, g, err in data:
        if g is None:
            continue
        for w in g["waits"]:
            tag = '<span class="tag urgent">急ぎ</span>' if w["urgent"] else ""
            rows.append(f"{tag}<b>{e(name)}</b> 判断 {e(str(w['id']))}: {e(str(w['title']))}"
                        f" <span class='dim'>({e(w['age'] or '?')} 待ち)</span>"
                        + (f"<br><span class='dim'>推し: {e(w['rec'])}</span>" if w["rec"] else ""))
        for m in g["owner_unread"]:
            rows.append(f"<b>{e(name)}</b> owner 宛て未読 #{e(str(m['n']))} from {e(str(m['from']))}: "
                        f"{e(m['text'][:200])}")
        for s in g["waiting_seats"]:
            rows.append(f"<b>{e(name)}</b> 席 {e(s['name'])} が待っている: {e(str(s['waitingFor'] or '何か'))}")
    cls = "top" if rows else "quiet"
    body = _ul(rows) or '<p class="ok">元帥待ちは無い</p>'
    return f'<section class="card {cls}"><h2>元帥待ち ({len(rows)})</h2>{body}</section>'


def _seat_table(g: dict, now: float) -> str:
    out = ["<table><tr><th>席</th><th>生存</th><th>status</th><th>最終</th><th>担当 (active)</th>"
           "<th>未読</th><th>赤</th><th>今日 tok</th><th>作業ログの最後</th></tr>"]
    for s in g["seats"]:
        alive = f"<span class='ok'>pid {e(str(s['pid']))}</span>" if s["alive"] else "<span class='dim'>止</span>"
        last = f"{e(fmt_time(s['last']))} <span class='dim'>({e(fmt_span(now - s['last']))}前)</span>" if s["last"] else "-"
        red = "<br>".join(e(f) for f in s["red"])
        status = e(str(s["status"] or "-")) + (f" <span class='warn'>{e(str(s['waitingFor']))}</span>" if s["waitingFor"] else "")
        budget = f" <span class='dim'>/ 上限 ${e(str(s['budget']))}</span>" if s["budget"] else ""
        out.append(f"<tr><td><b>{e(s['name'])}</b> <span class='dim'>{e(str(s['shift']))}</span></td><td>{alive}</td>"
                   f"<td>{status}</td><td>{last}</td><td>{e(', '.join(s['active']) or '-')}</td>"
                   f"<td>{s['unread'] or '-'}</td><td class='red'>{red}</td>"
                   f"<td>{e(report._tokens(s['tokens']))}{budget}</td><td class='dim'>{e(s['log'][:160])}</td></tr>")
    return "".join(out) + "</table>"


def _ship_card(name: str, g: dict | None, err: str | None, now: float) -> str:
    if g is None:
        return f'<section class="card"><h2>{e(name)}</h2><p class="red">読めない: {e(err or "?")}</p></section>'
    hub_last = f" / captain {e(g['hub'])} の最終 {e(fmt_time(g['hub_last']))}" if g["hub_last"] else ""
    counts = " ".join(f"<span class='tag'>{e(k)} {n}</span>" for k, n in sorted(g["counts"].items()))
    parts = [f'<section class="card"><h2>{e(name)}</h2><p class="dim">{e(g["deadline"])}{hub_last}'
             f" / 今日 {e(report._tokens(g['tokens']))} tok</p>", _seat_table(g, now),
             f"<h3>board</h3><p>{counts or '-'}</p>"]
    rows = [f"<b>{e(b['id'])}</b> [{e(str(b['state']))}] {e(str(b['title']))} <span class='dim'>"
            f"{e(str(b['assignee'] or '-'))}" + (f" / blocked_on {e(', '.join(map(str, b['blocked_on'])))}" if b["blocked_on"] else "")
            + "</span>" for b in g["board"]]
    parts.append(_ul(rows))
    if g["prs"]:
        rows = []
        for p in g["prs"]:
            label = f"#{e(p['pr'].lstrip('#'))}"
            ref = f'<a href="{_q(p["link"])}">{label}</a>' if p["link"] else label
            repo = f" [{e(p['repo'])}]" if g["multi_repo"] else ""
            rows.append(f"{ref}{repo} <b>{e(p['item'])}</b> {e(str(p['title']))} <span class='dim'>({e(str(p['state']))})</span>")
        parts.append("<h3>PR</h3>" + _ul(rows))
    if g["anomalies"]:
        parts.append("<h3>直近の異常</h3>" + _ul(
            f"<span class='red'>{e(str(a.get('kind')))}</span> {e(fmt_time(a.get('ts')))} {e(str(a.get('summary') or ''))}"
            for a in reversed(g["anomalies"])))
    return "".join(parts) + "</section>"


def render(ships: dict[str, Path], by: dict, now: float, note: str = "") -> str:
    """ページ全体の HTML 文字列 (HTTP なし)。1 艦の読み取りが落ちても、その艦の枠にエラーを出して他を描く。"""
    data = []
    for name, path in ships.items():
        try:
            data.append((name, gather(name, path, by, now), None))
        except Exception as ex:  # noqa: BLE001 — 壊れた艦 1 つでページ全体を落とさない
            data.append((name, None, f"{type(ex).__name__}: {ex}"))
    body = [_waiting_section(data)]
    if not data:
        body.append('<p class="dim">艦がありません (yamato ship create で作る)</p>')
    body += [_ship_card(n, g, err, now) for n, g, err in data]
    return (f'<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            f'<meta http-equiv="refresh" content="{REFRESH}"><title>yamato dashboard</title>'
            f"<style>{CSS}</style></head><body><main><h1>yamato dashboard</h1>"
            f'<p class="dim">最終更新 {e(time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)))} '
            f"({REFRESH} 秒ごとに自動更新・読むだけ){e(note)}</p>{''.join(body)}</main></body></html>")


def render_page(now: float | None = None) -> str:
    """全艦を読んで HTML を返す。``claude agents --json`` は 1 回の描画で 1 回。"""
    now = time.time() if now is None else now
    note = ""
    try:
        by = claude.by_session(claude.agents())
    except YamatoError as ex:
        by, note = {}, f" / claude agents を読めなかった (生存は不明): {ex}"
    return render(admiral.all_ships(), by, now, note)


# --- server -------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    """GET だけ。ほかのメソッドは 501 (do_* を定義しない)。"""

    def do_GET(self):  # noqa: N802
        if self.path.split("?")[0] not in ("/", "/index.html"):
            self.send_error(404)
            return
        try:
            data = render_page().encode("utf-8")
        except Exception as ex:  # noqa: BLE001
            self.send_error(500, f"描画に失敗: {type(ex).__name__}")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # noqa: D401 — アクセスログは出さない
        pass


def make_server(port: int | None) -> ThreadingHTTPServer:
    """127.0.0.1 に bind する。port 無指定は DEFAULT_PORT から順に空きを探す。指定済みで使用中ならエラー。"""
    tries = [port] if port is not None else [DEFAULT_PORT + i for i in range(PORT_TRIES)]
    last = None
    for p in tries:
        try:
            return ThreadingHTTPServer((HOST, p), Handler)
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
    d = sub.add_parser("dashboard", help="全艦の元帥待ちと席の状況をローカルのブラウザで見る (127.0.0.1・読むだけ)")
    d.add_argument("--port", type=int, help=f"待ち受ける port (既定 {DEFAULT_PORT}。使用中なら次の番号を試す)")
    d.add_argument("--no-open", action="store_true", help="ブラウザを開かない")


def run(args) -> int:
    return serve(args.port, not args.no_open)

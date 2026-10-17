"""web コンソールの共通の枠: 上のナビ (一覧 / 艦ごと) と CSS (T-105)。

どのページもここの ``page()`` で包む。ページ本文の値は呼び側が ``html.escape`` を通す
(ここで通すのは艦名・タイトルなど枠が足す値だけ)。
"""
from __future__ import annotations

import html
from urllib.parse import quote

e = html.escape

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
nav{display:flex;flex-wrap:wrap;gap:4px 12px;padding:8px 16px;background:#7771;border-bottom:1px solid #7773}
nav a{text-decoration:none}nav .cur{font-weight:700}
"""


def nav(ships, current: str) -> str:
    """ナビ。一覧 (/) と艦ごと (/ship/<name>)。current は今のページの path。"""
    links = [("/", "一覧")] + [(f"/ship/{quote(n, safe='')}", n) for n in ships]
    return "<nav>" + "".join(
        f'<a href="{e(href, quote=True)}"' + (' class="cur"' if href == current else "") + f">{e(label)}</a>"
        for href, label in links) + "</nav>"


def page(title: str, body: str, ships, current: str = "/", refresh: int | None = None) -> str:
    """ページ全体の HTML。body はエスケープ済みの HTML 断片。"""
    meta = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ""
    return (f'<!doctype html><html lang="ja"><head><meta charset="utf-8">{meta}<title>{e(title)}</title>'
            f"<style>{CSS}</style></head><body>{nav(ships, current)}<main>{body}</main></body></html>")

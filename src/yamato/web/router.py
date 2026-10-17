"""web コンソールの routing (T-105)。GET の path から (status, html) を返す。HTTP は持たない。

- ``/`` (と ``/index.html``): 一覧。dashboard.py が ``overview`` として渡す
- ``/<segment>/<艦名>``: PAGES のモジュール。艦名は ``ships`` (ships.json など) にあるものだけ
  (一致した名前の dict 引きだけ。path を組み立てない)。無ければ 404
ページを足すには、モジュールを web/ に置いて PAGES に足す (ship.py が見本)。
"""
from __future__ import annotations

from urllib.parse import unquote, urlsplit

from . import frame, ship

PAGES = (ship,)


def dispatch(raw_path: str, ships, overview) -> tuple[int, str]:
    path = urlsplit(raw_path).path
    if path in ("/", "/index.html"):
        return 200, overview()
    parts = path.split("/")
    if len(parts) == 3 and parts[0] == "":
        for mod in PAGES:
            if parts[1] == mod.SEGMENT:
                name = unquote(parts[2])
                if name in ships:
                    title, body = mod.render(name, ships[name])
                    return 200, frame.page(title, body, ships, current=path)
    return 404, ""

"""/ship/<name>: 艦ごとのページ (T-105 では空。T-106 以降が中身を足す)。

ページを足す形: 1 ページ = 1 モジュール。\`\`SEGMENT\`\` (path の先頭) と
\`\`render(name, shipdir)\`\` (title, 本文の HTML) を持たせ、router.PAGES に並べる。
name は ships にある艦名だけが来る (router が確かめる)。path を組み立てる値には使わない。
"""
from __future__ import annotations

import html
from pathlib import Path

SEGMENT = "ship"


def render(name: str, shipdir: Path) -> tuple[str, str]:
    return f"{name} — yamato", f"<h1>{html.escape(name)}</h1><p class='dim'>(準備中)</p>"

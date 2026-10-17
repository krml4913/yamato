"""web コンソールのフロント (``web/dist/``) の配信。HTTP は持たない。

起動時に dist の実在する通常ファイルの一覧を作り、その一覧にある URL path だけ返す
(リクエストの path からファイルの path を組み立てない。``..``・シンボリックリンク・
一覧に無いファイルは 404)。SPA の画面の path (``/ship/<艦名>`` など拡張子の無い path) は
``index.html`` を返す。dist がまだ無い (index.html が無い) ときは「build されていない」ページ。
"""
from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from urllib.parse import unquote

DIST = Path(__file__).resolve().parent / "dist"
INDEX = "/index.html"

# 環境の mime.types に任せると .js が text/plain になることがある。フロントの出力に出るものは固定する。
MIME = {
    ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
    ".json": "application/json", ".map": "application/json", ".svg": "image/svg+xml",
    ".png": "image/png", ".ico": "image/x-icon", ".woff": "font/woff", ".woff2": "font/woff2",
    ".txt": "text/plain", ".wasm": "application/wasm",
}
TEXT = ("text/", "application/json", "image/svg+xml")

NOT_BUILT = (
    "<!doctype html><meta charset=\"utf-8\"><title>yamato dashboard</title>"
    "<h1>yamato dashboard</h1><p>画面が build されていない (src/yamato/web/dist/ が無い)。"
    "web/ で build してから開き直す。API は <a href=\"/api/v1/overview\">/api/v1/overview</a> で読める。</p>"
)


def mime_of(url_path: str) -> str:
    ext = os.path.splitext(url_path)[1].lower()
    ctype = MIME.get(ext) or mimetypes.guess_type(url_path)[0] or "application/octet-stream"
    return ctype + "; charset=utf-8" if ctype.startswith(TEXT) else ctype


def _is_asset(url_path: str) -> bool:
    ext = os.path.splitext(url_path)[1].lower()
    return ext in MIME or mimetypes.guess_type(url_path)[0] is not None


class Dist:
    def __init__(self, root: Path):
        self.files: dict[str, Path] = {}
        root = Path(root)
        if not root.is_dir():
            return
        for dirpath, _dirs, names in os.walk(root, followlinks=False):
            for n in names:
                p = Path(dirpath) / n
                if p.is_symlink() or not p.is_file():
                    continue
                self.files["/" + p.relative_to(root).as_posix()] = p

    def get(self, raw_path: str) -> tuple[int, bytes, str]:
        """URL path (クエリを除いたもの) から (status, 本文, Content-Type)。"""
        url = unquote(raw_path)
        target = self.files.get(url)
        if target is None and url == "/":
            target = self.files.get(INDEX)
        if target is None and not _is_asset(url):   # 知っている拡張子の無い path は SPA の画面
            if INDEX in self.files:
                target = self.files[INDEX]
            else:
                return 503, NOT_BUILT.encode("utf-8"), "text/html; charset=utf-8"
        if target is None:
            return 404, b"not found", "text/plain; charset=utf-8"
        try:
            return 200, target.read_bytes(), mime_of(target.name)
        except OSError:
            return 404, b"not found", "text/plain; charset=utf-8"

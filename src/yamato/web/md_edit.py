"""web コンソールで書き換えられる md (roles/*.md・charter.md)。

書ける対象は ``files.listing`` が返す実在の role・charter だけ (新しく作らない)。knowledge.md と
memory.md は ``yamato memory apply`` が反映の記録を残すので、読むだけのままにして対象に入れない。
``import`` したとき ``edit.RESOLVERS`` に自分を足す。
"""
from __future__ import annotations

from .. import admiral
from . import edit, files

WRITABLE_KINDS = ("role", "charter")


def validate(text: str) -> list[str]:
    errs = []
    if not text.strip():
        errs.append("中身が空 (空の指示書は席を壊す)")
    if "\x00" in text:
        errs.append("NUL 文字が入っている")
    return errs


def resolve(ship: str, file: str) -> edit.Target | None:
    shipdir = admiral.all_ships().get(ship)
    if shipdir is None:
        return None
    ent = files.listing(shipdir).get(file)
    if ent is None or ent["kind"] not in WRITABLE_KINDS:
        return None
    return edit.Target(ent["_path"], shipdir, file, validate)


if resolve not in edit.RESOLVERS:
    edit.RESOLVERS.append(resolve)

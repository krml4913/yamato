"""艦全体の進み具合を見る 3 つの見え方 (design-drift #4 #14, T-030, D-018)。

``board tree`` (parent の階層) と ``board kanban`` (列ごと) は人間向け、注入の
部品 ``board`` は captain の SessionStart 向け。3 つとも同じ描画をここに置き、
decision 項目 (``kind: decision``、``D-NNN``) は対象外にする (``decide list`` がある)。
"""
from __future__ import annotations

from . import board as board_mod
from .util import YamatoError

INDENT = "  "


def _id_key(item_id: str):
    m = board_mod.ID_RE.match(item_id)
    return (0, int(m.group(1))) if m else (1, item_id)   # a non T-id (should not happen) sorts after


def _visible(items: list[dict]) -> list[dict]:
    """Every item except decisions (``decide list`` owns those)."""
    return [m for m in items if m.get("kind") != board_mod.DECISION]


# --- board tree --------------------------------------------------------------------

def tree_lines(items: list[dict], root_id: str | None = None) -> list[str]:
    """完了条件 1: parent を辿ってインデントで出す。行は ``format_item`` と同じ形。

    ``root_id`` を渡すとその下だけ (root 自身は状態にかかわらず出す)。既定は
    done 以外だが、done でも done 以外の子孫を持つ親は出す。parent が
    universe に無い (見つからない・除かれた) 項目は最上段に出す。"""
    universe = {m["id"]: m for m in _visible(items)}
    if root_id is not None and root_id not in universe:
        raise YamatoError(f"{root_id} が board にありません (tree の対象は decision 以外)")

    if root_id is not None:
        allowed = {root_id}
        stack = [root_id]
        while stack:
            cur = stack.pop()
            for i, m in universe.items():
                if m.get("parent") == cur and i not in allowed:
                    allowed.add(i)
                    stack.append(i)
        universe = {i: m for i, m in universe.items() if i in allowed}

    children: dict[str | None, list[str]] = {}
    for i, m in universe.items():
        children.setdefault(m.get("parent") if m.get("parent") in universe else None, []).append(i)
    for kids in children.values():
        kids.sort(key=_id_key)

    keep = {root_id} if root_id is not None else set()
    keep |= {i for i, m in universe.items() if m.get("state") != "done"}
    for i in list(keep):
        p = universe[i].get("parent")
        while p in universe and p not in keep:
            keep.add(p)
            p = universe[p].get("parent")

    lines: list[str] = []

    def walk(i: str, depth: int) -> None:
        if i not in keep:
            return
        lines.append(INDENT * depth + board_mod.format_item(universe[i]))
        for c in children.get(i, []):
            walk(c, depth + 1)

    tops = [root_id] if root_id is not None else sorted(children.get(None, []), key=_id_key)
    for t in tops:
        walk(t, 0)
    return lines


# --- board kanban --------------------------------------------------------------------

def kanban_groups(team: dict, items: list[dict], include_done: bool = False) -> list[tuple[str, list[dict]]]:
    """[(列の名前, 項目たち)]。列は ``board.columns`` があればその順・その名前、無ければ
    state の順 (open / active / blocked[, done])。項目は各列の中で id の昇順。"""
    items = _visible(items)
    columns = (team.get("board") or {}).get("columns") or []
    if columns:
        names = [c["name"] for c in columns]
        state_of_name = {c["name"]: c["state"] for c in columns}
        name_of_state = {c["state"]: c["name"] for c in columns}   # first column wins, like _sync_column

        def key_of(m: dict) -> str | None:
            col = m.get("column")
            return col if col in state_of_name else name_of_state.get(m.get("state"))
    else:
        names = [s for s in board_mod.STATES if s != "done"]

        def key_of(m: dict) -> str | None:
            return m.get("state")

    if include_done and "done" not in names:
        names = [*names, "done"]

    buckets: dict[str, list[dict]] = {name: [] for name in names}
    for m in items:
        if m.get("state") == "done" and not include_done:
            continue
        name = key_of(m)
        if name in buckets:
            buckets[name].append(m)
    for bucket in buckets.values():
        bucket.sort(key=lambda m: _id_key(m["id"]))
    return [(name, buckets[name]) for name in names]


def kanban_lines(team: dict, items: list[dict], include_done: bool = False) -> list[str]:
    lines = []
    for name, its in kanban_groups(team, items, include_done=include_done):
        lines.append(f"## {name} ({len(its)})")
        lines += [board_mod.format_item(m) for m in its] if its else ["(なし)"]
    return lines


# --- inject の部品 `board` -------------------------------------------------------------

_ORDER = ("blocked", "active", "open")   # 完了条件 3: この順。done は出さない


def inject_lines(items: list[dict], limit: int, kanban_hint: str) -> list[str]:
    """完了条件 3 の中身 (先頭の件数行 + blocked→active→open の項目、``limit`` で打ち切り。
    ``kanban_hint`` は打ち切ったときに添える ``board kanban <ship>`` の呼び方)。"""
    items = [m for m in _visible(items) if m.get("state") != "done"]
    counts = {s: sum(1 for m in items if m.get("state") == s) for s in _ORDER}
    ordered = [m for s in _ORDER for m in sorted((m for m in items if m.get("state") == s),
                                                 key=lambda m: _id_key(m["id"]))]
    lines = [" / ".join(f"{s} {counts[s]}" for s in _ORDER)]
    shown = ordered[:limit]
    lines += [board_mod.format_item(m) for m in shown]
    rest = len(ordered) - len(shown)
    if rest > 0:
        lines.append(f"…ほか {rest} 件 (`{kanban_hint}`)")
    return lines
